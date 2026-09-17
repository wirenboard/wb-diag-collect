from unittest.mock import patch

import pytest
import yaml

from wb.diag.diag_collect import ResultCode, main

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
