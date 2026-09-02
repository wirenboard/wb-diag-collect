import argparse
import asyncio
import json
import logging
import sys
import time
from enum import IntEnum
from urllib.parse import urlparse

import jsonschema
from systemd.journal import JournalHandler

from wb.diag import collector, rpc_server

DEFAULT_CONF_PATH = "/usr/share/wb-diag-collect/wb-diag-collect.conf"
SCHEMA_PATH = "/usr/share/wb-mqtt-confed/schemas/wb-diag-collect.schema.json"


class ResultCode(IntEnum):
    OK = 0
    OPERATION_ERROR = 1
    USER_INPUT_ERROR = 2
    NOT_CONFIGURED = 6
    NOT_RUNNING = 7


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def validate_broker_url(broker_url: str) -> None:
    parsed_url = urlparse(broker_url)
    if parsed_url.scheme == "unix":
        if not parsed_url.path:
            raise TypeError("mqtt.broker unix URL must contain a socket path")
        return
    if parsed_url.scheme not in ("mqtt-tcp", "tcp", "ws"):
        raise TypeError(f"mqtt.broker has unsupported URL scheme: {parsed_url.scheme}")
    try:
        port = parsed_url.port
    except ValueError as error:
        raise TypeError(f"mqtt.broker has invalid port: {error}") from error
    if not parsed_url.hostname or port is None:
        raise TypeError("mqtt.broker TCP URL must contain a host and port")


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
        "output_filename", metavar="output_filename", type=str, nargs="?", help="output filename"
    )

    args = parser.parse_args(argv[1:])
    conf_path = args.config
    server_mode = args.server or args.output_filename is None

    if server_mode:
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
        with open(conf_path or DEFAULT_CONF_PATH, encoding="utf-8") as config_file:
            config = json.load(config_file)
        with open(SCHEMA_PATH, encoding="utf-8") as schema_file:
            schema = json.load(schema_file)
        jsonschema.validate(config, schema)

        options = {
            "commands": config["commands"],
            "files": config["files"],
            "filters": config["filters"],
            "service_lines_number": config["journald_logs"]["lines_number"],
            "service_names": config["journald_logs"]["names"],
            "timeout": args.timeout or config["timeout"],
        }
        if server_mode:
            options["broker"] = config["mqtt"]["broker"]
            validate_broker_url(options["broker"])
    except (
        OSError,
        TypeError,
        json.JSONDecodeError,
        jsonschema.SchemaError,
        jsonschema.ValidationError,
    ) as error:
        logger.error("Cannot read config %s: %s", conf_path or DEFAULT_CONF_PATH, error)
        return ResultCode.NOT_CONFIGURED

    if server_mode:
        return rpc_server.serve(options, logger)

    try:
        print("Start data collecting")

        wb_archive_collector = collector.Collector(logger)
        started_at = time.monotonic()
        asyncio.get_event_loop().run_until_complete(
            wb_archive_collector.collect(options, "", args.output_filename)
        )
        elapsed = time.monotonic() - started_at

        print(f"Data was collected successfully in {elapsed:.2f}s")
        return ResultCode.OK
    except OSError as error:
        logger.error("OSError: with file %s, errno %s", error.filename, error.errno)
        return ResultCode.OPERATION_ERROR


if __name__ == "__main__":
    sys.exit(main())
