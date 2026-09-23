import logging
from unittest.mock import patch

import pytest
import yaml

from wb.diag.diag_collect import ResultCode, check_broker_url, main

VALID_CONFIG = {
    "commands": [{"command": "true", "filename": "true"}],
    "files": [],
    "filters": [],
    "journald_logs": {"lines_number": 10, "names": []},
    "timeout": 5,
    "mqtt": {"broker": "unix:///var/run/mosquitto.sock"},
}


def write_config(tmp_path, content):
    path = tmp_path / "wb-diag-collect.conf"
    if content is not None:
        path.write_text(content if isinstance(content, str) else yaml.safe_dump(content), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize(
    "content",
    [None, "commands: [broken", "", {"commands": []}],
    ids=["missing-file", "broken-yaml", "empty-file", "missing-keys"],
)
def test_invalid_config_exits_not_configured(tmp_path, content):
    assert (
        main(["wb-diag-collect", "-c", write_config(tmp_path, content), "out"]) == ResultCode.NOT_CONFIGURED
    )


def test_server_without_broker_in_config_exits_not_configured(tmp_path):
    config = {key: value for key, value in VALID_CONFIG.items() if key != "mqtt"}

    assert (
        main(["wb-diag-collect", "-s", "-c", write_config(tmp_path, config), "out"])
        == ResultCode.NOT_CONFIGURED
    )


@pytest.mark.parametrize(
    "broker",
    ["tcp://user:secret@localhost", "foo://x", "unix://", "tcp://:1883", "tcp://host:abc", 1883],
    ids=["no-port", "unknown-scheme", "no-socket-path", "no-host", "bad-port", "not-a-string"],
)
def test_server_with_unusable_broker_url_exits_not_configured(tmp_path, caplog, broker):
    """
    wb-common's MQTTClient.start() would raise on each of these: the config check rejects them
    before the server exists, and the log line never repeats the URL, which may carry a password.
    """
    config = {**VALID_CONFIG, "mqtt": {"broker": broker}}

    with patch("wb.diag.diag_collect.rpc_server.serve") as serve, caplog.at_level(logging.ERROR):
        assert (
            main(["wb-diag-collect", "-s", "-c", write_config(tmp_path, config), "out"])
            == ResultCode.NOT_CONFIGURED
        )

    serve.assert_not_called()
    assert "Cannot read config" in caplog.text
    assert "secret" not in caplog.text


@pytest.mark.parametrize(
    "broker",
    [
        "unix:///var/run/mosquitto.sock",
        "tcp://user:pw@localhost:1883",
        "mqtt-tcp://host:1883",
        "ws://host:9001/mqtt",
    ],
)
def test_check_broker_url_accepts_the_mqtt_client_forms(broker):
    assert check_broker_url(broker) == broker


def test_server_exit_code_comes_from_serve(tmp_path):
    with patch("wb.diag.diag_collect.rpc_server.serve", return_value=2) as serve:
        assert main(["wb-diag-collect", "-s", "-c", write_config(tmp_path, VALID_CONFIG), "out"]) == 2

    options = serve.call_args.args[0]
    assert options["broker"] == VALID_CONFIG["mqtt"]["broker"]
    assert options["timeout"] == 5


def test_collect_from_console_returns_ok(tmp_path):
    with patch("wb.diag.diag_collect.collector.Collector") as collector_class:
        collector_class.return_value.collect.return_value = asyncio_result("archive.zip")
        assert (
            main(["wb-diag-collect", "-t", "1", "-c", write_config(tmp_path, VALID_CONFIG), "out"])
            == ResultCode.OK
        )

    options, _, output_filename = collector_class.return_value.collect.call_args.args
    assert options["timeout"] == 1
    assert output_filename == "out"


async def asyncio_result(value):
    return value
