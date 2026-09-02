import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wb.diag import diag_collect, rpc_server


def make_config(broker="unix:///run/mosquitto.sock"):
    return {
        "commands": [],
        "files": [],
        "filters": [],
        "journald_logs": {"lines_number": 0, "names": []},
        "mqtt": {"broker": broker},
        "timeout": 1,
    }


@pytest.fixture(autouse=True)
def use_source_schema(monkeypatch):
    schema_path = next(
        parent / "wb-diag-collect.schema.json"
        for parent in Path(__file__).resolve().parents
        if (parent / "wb-diag-collect.schema.json").is_file()
    )
    monkeypatch.setattr(
        diag_collect,
        "SCHEMA_PATH",
        str(schema_path),
    )


def test_missing_config_returns_6(tmp_path):
    result = diag_collect.main(["wb-diag-collect", "-s", "-c", str(tmp_path / "missing"), "archive"])
    assert result == diag_collect.ResultCode.NOT_CONFIGURED


def test_malformed_config_returns_6(tmp_path):
    config = tmp_path / "broken.conf"
    config.write_text('{"commands": [', encoding="utf-8")
    result = diag_collect.main(["wb-diag-collect", "-s", "-c", str(config), "archive"])
    assert result == diag_collect.ResultCode.NOT_CONFIGURED


def test_invalid_broker_url_returns_6(tmp_path):
    config = tmp_path / "wb-diag-collect.conf"
    config.write_text(json.dumps(make_config(broker="invalid://broker")), encoding="utf-8")

    result = diag_collect.main(["wb-diag-collect", "-s", "-c", str(config), "archive"])

    assert result == diag_collect.ResultCode.NOT_CONFIGURED


def test_schema_validation_error_returns_6(tmp_path):
    config_data = make_config()
    config_data["commands"] = [{"filename": "missing-command"}]
    config = tmp_path / "wb-diag-collect.conf"
    config.write_text(json.dumps(config_data), encoding="utf-8")

    result = diag_collect.main(["wb-diag-collect", "-s", "-c", str(config), "archive"])

    assert result == diag_collect.ResultCode.NOT_CONFIGURED


def test_no_arguments_starts_server_with_default_config(tmp_path):
    config = tmp_path / "wb-diag-collect.conf"
    config.write_text(json.dumps(make_config()), encoding="utf-8")

    with patch.object(diag_collect, "DEFAULT_CONF_PATH", str(config)), patch.object(
        diag_collect.rpc_server, "serve", return_value=diag_collect.ResultCode.NOT_RUNNING
    ) as serve:
        result = diag_collect.main(["wb-diag-collect"])

    assert result == diag_collect.ResultCode.NOT_RUNNING
    assert serve.call_args.args[0]["broker"] == "unix:///run/mosquitto.sock"


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


def test_other_connection_error_keeps_service_running():
    server = rpc_server.AsyncMQTTRPCServer.__new__(rpc_server.AsyncMQTTRPCServer)
    server.logger = MagicMock()
    server.asyncio_loop = MagicMock()
    server.exit_code = rpc_server.EXIT_NOTRUNNING

    server._on_connect(None, None, None, 3)  # pylint:disable=protected-access

    assert server.exit_code == rpc_server.EXIT_NOTRUNNING
    server.asyncio_loop.call_soon_threadsafe.assert_not_called()


def test_mqtt_client_is_started_once_when_server_runs():
    options = {"broker": "unix:///run/mosquitto.sock"}
    event_loop = MagicMock()

    with patch.object(rpc_server.AsyncMQTTRPCServer, "_setup_event_loop"), patch.object(
        rpc_server, "MQTTClient"
    ) as client_class, patch.object(rpc_server.collector, "Collector"):
        server = rpc_server.AsyncMQTTRPCServer(options, MagicMock(), logging.getLogger())
        server.asyncio_loop = event_loop

        client_class.return_value.start.assert_not_called()
        assert server.run() == rpc_server.EXIT_NOTRUNNING

    client_class.return_value.start.assert_called_once_with()
    event_loop.run_forever.assert_called_once_with()


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
