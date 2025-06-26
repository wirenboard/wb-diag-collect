import logging
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

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

    assert collector.branch_test(True) == 4


