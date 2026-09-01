import logging
from unittest.mock import MagicMock, patch

from wb.diag import diag_collect, rpc_server


def test_missing_config_returns_6(tmp_path):
    result = diag_collect.main(["wb-diag-collect", "-s", "-c", str(tmp_path / "missing"), "archive"])
    assert result == diag_collect.ResultCode.NOT_CONFIGURED


def test_malformed_config_returns_6(tmp_path):
    config = tmp_path / "broken.conf"
    config.write_text("commands: [", encoding="utf-8")
    result = diag_collect.main(["wb-diag-collect", "-s", "-c", str(config), "archive"])
    assert result == diag_collect.ResultCode.NOT_CONFIGURED


def test_serve_returns_7_and_stops():
    with patch.object(rpc_server, "AsyncMQTTRPCServer") as server_class:
        server = server_class.return_value
        server.run.return_value = rpc_server.EXIT_NOTRUNNING

        assert rpc_server.serve({"broker": "unix:///run/mosquitto.sock"}, logging.getLogger()) == 7
    server.stop.assert_called_once_with()


def test_authentication_error_stops_loop_threadsafe():
    server = rpc_server.AsyncMQTTRPCServer.__new__(rpc_server.AsyncMQTTRPCServer)
    server.logger = MagicMock()
    server.asyncio_loop = MagicMock()
    server.exit_code = rpc_server.EXIT_NOTRUNNING

    server._on_connect(None, None, None, 5)  # pylint:disable=protected-access

    assert server.exit_code == rpc_server.EXIT_INVALIDARGUMENT
    server.asyncio_loop.call_soon_threadsafe.assert_called_once_with(server.asyncio_loop.stop)


def test_other_connection_error_uses_failure_code():
    server = rpc_server.AsyncMQTTRPCServer.__new__(rpc_server.AsyncMQTTRPCServer)
    server.logger = MagicMock()
    server.asyncio_loop = MagicMock()
    server.exit_code = rpc_server.EXIT_NOTRUNNING

    server._on_connect(None, None, None, 3)  # pylint:disable=protected-access

    assert server.exit_code == rpc_server.EXIT_FAILURE


def test_wait_for_publish_reports_timeout():
    publication = MagicMock()
    publication.is_published.return_value = False

    with patch.object(rpc_server, "MQTT_PUBLISH_TIMEOUT_S", 0.1):
        try:
            rpc_server.wait_for_publish(publication, "/test/topic")
        except TimeoutError as error:
            assert "/test/topic" in str(error)
        else:
            raise AssertionError("TimeoutError was not raised")

    publication.wait_for_publish.assert_called_once_with(timeout=0.1)
