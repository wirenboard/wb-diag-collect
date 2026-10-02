import asyncio
import logging
import shutil
import signal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

import pytest
import yaml
from yaml.loader import SafeLoader

from wb.diag.collector import Collector

logger = logging.getLogger(__name__)


@pytest.fixture
def collect_dir():
    with TemporaryDirectory() as tmpdir:
        shutil.copytree(
            "./tests/data", tmpdir, ignore=shutil.ignore_patterns("*.filtered"), dirs_exist_ok=True
        )
        yield tmpdir


@pytest.fixture
def diag_collect_config():
    return yaml.load(Path("./tests/data/wb-diag-collect.conf").read_text(encoding="utf-8"), Loader=SafeLoader)


def test_filter_files(collect_dir, diag_collect_config):  # pylint:disable=redefined-outer-name
    collector = Collector(logger)
    collector.filter_files(collect_dir, diag_collect_config["filters"])

    filtered = Path(collect_dir + "/etc/mosquitto/conf.d/20bridge.conf").read_text(encoding="utf-8")
    expected = Path("./tests/data/etc/mosquitto/conf.d/20bridge.conf.filtered").read_text(encoding="utf-8")
    assert filtered == expected

    filtered = Path(collect_dir + "/etc/wb-mqtt-serial.conf").read_text(encoding="utf-8")
    expected = Path("./tests/data/etc/wb-mqtt-serial.conf.filtered").read_text(encoding="utf-8")
    assert filtered == expected


@pytest.mark.asyncio
async def test_execute_commands_timeout():
    collector = Collector(logger)

    with TemporaryDirectory() as tmpdir:
        options = {"commands": [{"filename": "slow_cmd", "command": "sleep 5"}], "timeout": 1}
        await collector.execute_commands(tmpdir, options["commands"], options["timeout"])
        assert Path(f"{tmpdir}/slow_cmd.log").exists()


@pytest.mark.asyncio
async def test_execute_commands_mixed_timeout():
    collector = Collector(logger)

    with TemporaryDirectory() as tmpdir:
        options = {
            "commands": [
                {"filename": "cmd1", "command": "echo 'fast command'"},
                {"filename": "cmd_slow", "command": "sleep 5"},
                {"filename": "cmd3", "command": "echo 'another fast'"},
            ],
            "timeout": 1,
        }
        await collector.execute_commands(tmpdir, options["commands"], options["timeout"])

        assert Path(f"{tmpdir}/cmd1.log").exists()
        assert Path(f"{tmpdir}/cmd_slow.log").exists()
        assert Path(f"{tmpdir}/cmd3.log").exists()
        assert Path(f"{tmpdir}/cmd1.log").read_text(encoding="utf-8").strip() == "fast command"
        assert Path(f"{tmpdir}/cmd3.log").read_text(encoding="utf-8").strip() == "another fast"


@pytest.mark.asyncio
async def test_apply_file_wildcard_timeout():
    collector = Collector(logger)

    fake_proc = Mock()
    fake_proc.pid = 12345
    fake_proc.wait = AsyncMock()

    wait_for_calls = 0

    async def wait_for_side_effect(awaitable, timeout):  # pylint: disable=W0613
        nonlocal wait_for_calls
        wait_for_calls += 1
        if wait_for_calls == 1:
            raise asyncio.TimeoutError
        return await awaitable

    with patch("wb.diag.collector.asyncio.create_subprocess_shell", new=AsyncMock(return_value=fake_proc)):
        with patch("wb.diag.collector.asyncio.wait_for", new=wait_for_side_effect):
            with patch("wb.diag.collector.os.killpg") as killpg_mock:
                result = await collector.apply_file_wildcard("/etc/nonexistent/**", timeout=0.1)

    assert result == []
    killpg_mock.assert_called_once_with(12345, signal.SIGTERM)
    fake_proc.wait.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_collect_with_command_timeout():
    collector = Collector(logger)

    with TemporaryDirectory() as tmpdir:
        options = {
            "commands": [{"filename": "ps_aux", "command": "sleep 2"}],
            "files": [],
            "filters": [],
            "service_names": [],
            "service_lines_number": 0,
            "timeout": 1,
        }

        real_open = open

        def open_side_effect(path, mode="r", **kwargs):
            if path == "/var/lib/wirenboard/short_sn.conf" and "r" in mode:
                return StringIO("TEST_SN\n")
            return real_open(path, mode, **kwargs)

        with patch("wb.diag.collector.open", side_effect=open_side_effect):
            result = await collector.collect(options, tmpdir, "test_archive")

        assert Path(result).exists()
        assert result.endswith(".zip")


@pytest.mark.asyncio
async def test_cancelled_collection_terminates_the_child_process():
    """
    A SIGTERM cancels the collection task; the child process must not outlive the service.
    """
    collector = Collector(logger)
    started, terminated = asyncio.Event(), asyncio.Event()

    async def wait_until_terminated():
        started.set()
        await terminated.wait()

    fake_proc = Mock(pid=4242, wait=wait_until_terminated)

    with TemporaryDirectory() as tmpdir:
        with patch(
            "wb.diag.collector.asyncio.create_subprocess_shell", new=AsyncMock(return_value=fake_proc)
        ):
            with patch("wb.diag.collector.os.killpg", side_effect=lambda *_: terminated.set()) as killpg_mock:
                task = asyncio.create_task(
                    collector.execute_commands(tmpdir, [{"filename": "slow", "command": "sleep 30"}], 10)
                )
                await started.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

    killpg_mock.assert_called_once_with(4242, signal.SIGTERM)


@pytest.mark.asyncio
async def test_copy_files_keeps_the_source_layout():
    collector = Collector(logger)

    with TemporaryDirectory() as source, TemporaryDirectory() as target:
        Path(f"{source}/etc").mkdir()
        Path(f"{source}/etc/wb-diag-collect.conf").write_text("timeout: 5\n", encoding="utf-8")

        await collector.copy_files(target, [f"{source}/etc/*.conf", "/nonexistent/*"])

        copied = Path(f"{target}{source}/etc/wb-diag-collect.conf")
        assert copied.read_text(encoding="utf-8") == "timeout: 5\n"
