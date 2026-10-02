# pylint: disable=redefined-outer-name, protected-access

import asyncio
import functools
import json
import logging
import signal
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from mqttrpc.dispatcher import Dispatcher

from wb.diag.rpc_server import (
    CLEAR_RETAINED_TIMEOUT_S,
    EXIT_INVALIDARGUMENT,
    EXIT_SUCCESS,
    AsyncMQTTRPCServer,
    serve,
)

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def signal_handlers(monkeypatch):
    """
    Every test here builds a server, which installs SIGINT/SIGTERM handlers on its loop: record them
    instead, so the pytest process keeps its own signal disposition.
    """
    handlers = {}

    def record(_loop, sig, callback, *args):
        handlers[sig] = functools.partial(callback, *args)

    monkeypatch.setattr(asyncio.SelectorEventLoop, "add_signal_handler", record)
    return handlers


@pytest.fixture
def server():
    with patch("wb.diag.rpc_server.MQTTClient"):
        rpc_server = AsyncMQTTRPCServer({"broker": "unix:///var/run/mosquitto.sock"}, Dispatcher(), logger)
    yield rpc_server
    rpc_server.asyncio_loop.close()


def test_start_waits_for_an_unavailable_broker(server):
    server.client.start.assert_called_once_with(retry_first_connection=True)


def test_on_connect_publishes_endpoints_and_subscribes(server):
    server._on_connect(None, None, None, 0)

    for service, method in server.dispatcher.keys():
        assert (
            call(f"/rpc/v1/diag/{service}/{method}", "1", retain=True) in server.client.publish.call_args_list
        )
        assert call(f"/rpc/v1/diag/{service}/{method}/+") in server.client.subscribe.call_args_list
    assert server.client.subscribe.call_count == 2


def test_on_connect_failure_keeps_retrying(server):
    server._on_connect(None, None, None, 1)

    server.client.publish.assert_not_called()
    assert server.exit_code == EXIT_SUCCESS


@pytest.mark.parametrize("reason_code", [4, 5])
def test_on_connect_rejected_login_stops_with_exit_code_2(server, reason_code):
    """
    The callback runs in paho's thread, so the loop is stopped through call_soon_threadsafe.
    """
    server._on_connect(None, None, None, reason_code)

    assert server.run() == EXIT_INVALIDARGUMENT
    server.client.publish.assert_not_called()


def test_run_cancels_the_running_collection(server):
    server._diag_collecting_task = server.asyncio_loop.create_task(asyncio.sleep(3600))
    server.asyncio_loop.call_soon(server.asyncio_loop.stop)

    assert server.run() == EXIT_SUCCESS
    assert server._diag_collecting_task.cancelled()


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_run_stops_on_signal(server, signal_handlers, sig):
    """
    The recorded handler is what asyncio would run inside the loop on delivery; a missing
    registration raises KeyError here instead of leaving run() unbounded.
    """
    server.asyncio_loop.call_soon(signal_handlers[sig])

    assert server.run() == EXIT_SUCCESS


def test_stop_clears_retains_when_connected(server):
    server.stop()

    assert server.client.publish.call_args_list == [
        call("/wb-diag-collect/artifact", payload=None, retain=False, qos=1),
        *[
            call(f"/rpc/v1/diag/{service}/{method}", retain=True)
            for service, method in server.dispatcher.keys()
        ],
    ]
    # every clear is confirmed within the shared deadline before the client stops
    waits = server.client.publish.return_value.wait_for_publish.call_args_list
    assert len(waits) == 2
    assert all(0 < wait.args[0] <= CLEAR_RETAINED_TIMEOUT_S for wait in waits)
    server.client.stop.assert_called_once_with()


def test_stop_reports_unconfirmed_retains(server, caplog):
    """
    The broker went away between the check and the publish (paho raises), or never confirms
    (timeout): the stop logs an error and still ends cleanly.
    """
    server.client.publish.return_value.wait_for_publish.side_effect = RuntimeError("not connected")
    with caplog.at_level(logging.ERROR):
        server.stop()
    assert "not confirmed: not connected" in caplog.text
    server.client.stop.assert_called_once_with()

    server.client.reset_mock()
    server.client.publish.return_value.wait_for_publish.side_effect = None  # reset_mock() keeps it
    server.client.publish.return_value.is_published.return_value = False
    with caplog.at_level(logging.ERROR):
        server.stop()
    assert "not confirmed within 5 s" in caplog.text
    server.client.stop.assert_called_once_with()


def test_stop_without_broker_logs_error(server, caplog):
    server.client.is_connected.return_value = False

    with caplog.at_level(logging.ERROR):
        server.stop()

    server.client.publish.assert_not_called()
    server.client.stop.assert_called_once_with()
    assert "MQTT broker is not connected, retained topics cannot be removed" in caplog.text


def test_launch_diag_collect_runs_a_single_collection(server, caplog):
    started = asyncio.Event()

    async def fake_diag():
        started.set()
        await asyncio.sleep(3600)

    async def launch_twice():
        with patch.object(server, "diag", fake_diag):
            assert await server.launch_diag_collect() == "Ok"
            await started.wait()
            assert await server.launch_diag_collect() == "Ok"
        server._diag_collecting_task.cancel()

    with caplog.at_level(logging.WARNING):
        server.asyncio_loop.run_until_complete(launch_twice())

    assert "Diag collecting task is already running" in caplog.text


def test_run_async_replies_to_the_rpc_client(server):
    message = Mock(
        topic="/rpc/v1/diag/main/status/client-1",
        payload=b'{"id": 7, "params": []}',
    )

    server.asyncio_loop.run_until_complete(server.run_async(message))

    (topic, payload, retain), _ = server.client.publish.call_args
    assert topic == "/rpc/v1/diag/main/status/client-1/reply"
    assert json.loads(payload) == {"id": 7, "result": "1", "error": None}
    assert retain is False


@pytest.mark.parametrize(
    "collect_result, expected_payload",
    [
        (
            "/var/www/diag/diag_output_1.zip",
            '{"basename": "diag_output_1.zip", "fullname": "/var/www/diag/diag_output_1.zip"}',
        ),
        (OSError(5, "I/O error", "/var/www/diag"), None),
    ],
    ids=["archive", "os-error"],
)
def test_diag_publishes_the_result(server, collect_result, expected_payload):
    with (
        patch("wb.diag.rpc_server.glob.glob", return_value=[]),
        patch("wb.diag.rpc_server.collector.Collector") as collector_class,
    ):
        collector_class.return_value.collect = AsyncMock(
            side_effect=collect_result if isinstance(collect_result, Exception) else None,
            return_value=collect_result,
        )
        server.asyncio_loop.run_until_complete(server.diag())

    server.client.publish.assert_called_once_with(
        "/wb-diag-collect/artifact", payload=expected_payload, retain=False, qos=1
    )


def test_serve_stops_the_server_and_returns_its_exit_code():
    with (
        patch("wb.diag.rpc_server.MQTTClient") as client_class,
        patch.object(AsyncMQTTRPCServer, "run", return_value=EXIT_INVALIDARGUMENT),
    ):
        assert serve({"broker": "unix:///var/run/mosquitto.sock"}, logger) == EXIT_INVALIDARGUMENT

    client_class.return_value.stop.assert_called_once_with()
