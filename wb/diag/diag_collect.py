import argparse
import asyncio
import logging
import sys
import time
from enum import IntEnum

import yaml
from systemd.journal import JournalHandler
from yaml.loader import SafeLoader

from wb.diag import collector, rpc_server

DEFAULT_CONF_PATH = "/usr/share/wb-diag-collect/wb-diag-collect.conf"


class ResultCode(IntEnum):
    OK = 0
    OPERATION_ERROR = 1
    USER_INPUT_ERROR = 2
    NOT_CONFIGURED = 6


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def read_options(conf_path, args):
    """
    Raises OSError, yaml.YAMLError, KeyError or TypeError on a missing or invalid config.
    """
    with open(conf_path, encoding="utf-8") as f:
        yaml_data = yaml.load(f, Loader=SafeLoader)

    options = {
        "commands": yaml_data["commands"] or [],
        "files": yaml_data["files"] or [],
        "filters": yaml_data["filters"] or [],
        "service_lines_number": yaml_data["journald_logs"]["lines_number"] or 0,
        "service_names": yaml_data["journald_logs"]["names"],
        "timeout": args.timeout or yaml_data["timeout"],
    }
    if args.server:
        options["broker"] = yaml_data["mqtt"]["broker"]
    return options


def main(argv=sys.argv):
    parser = argparse.ArgumentParser(
        description="one-click diagnostic data collector for Wiren Board, generating archive with data"
    )
    parser.add_argument("-c", "--config", action="store", help="get data from config")
    parser.add_argument("-s", "--server", action="store_true", help="run server")
    parser.add_argument("-d", "--debug", action="store_true", help="set debug logging level")
    parser.add_argument(
        "-t", "--timeout", action="store", type=int, help="set timeout for commands execution"
    )
    parser.add_argument(
        "output_filename", metavar="output_filename", type=str, nargs=1, help="output filename"
    )

    args = parser.parse_args(argv[1:])
    conf_path = args.config or DEFAULT_CONF_PATH

    if args.server:
        handler = JournalHandler(SYSLOG_IDENTIFIER="wb-diag-collect")
        handler.setFormatter(logging.Formatter("%(message)s"))
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    log_level = logging.DEBUG if args.debug else logging.INFO
    logger.setLevel(log_level)
    handler.setLevel(log_level)
    logger.addHandler(handler)

    try:
        options = read_options(conf_path, args)
    except (OSError, yaml.YAMLError, KeyError, TypeError) as e:
        logger.error("Cannot read config %s: %s", conf_path, e)
        return ResultCode.NOT_CONFIGURED

    if args.server:
        return rpc_server.serve(options, logger)

    try:
        print("Start data collecting")

        wb_archive_collector = collector.Collector(logger)
        started_at = time.monotonic()
        asyncio.run(wb_archive_collector.collect(options, "", args.output_filename[0]))
        elapsed = time.monotonic() - started_at

        print(f"Data was collected successfully in {elapsed:.2f}s")
        return ResultCode.OK
    except OSError as e:
        print(f"OSError: with file {e.filename}, errno {e.errno}")
        return ResultCode.OPERATION_ERROR


if __name__ == "__main__":
    sys.exit(main())
