import argparse
import logging
import signal
import sys
from enum import IntEnum

import yaml
from paho.mqtt import client as mqtt_client
from yaml.loader import SafeLoader

from .collecting import DEFAULT_CONF_PATH, collect_data_with_conf
from .server import *


class ResultCode(IntEnum):
    OK = 0
    OPERATION_ERROR = 1
    USER_INPUT_ERROR = 2


class GracefulKiller:
    def __init__(self):
        self.kill_now = False
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, *args):
        self.kill_now = True


def main(argv=sys.argv):
    parser = argparse.ArgumentParser(
        description="one-click diagnostic data collector for Wiren Board, " "generating archive with data"
    )
    parser.add_argument("-c", "--config", action="store", help="get data from config")
    parser.add_argument("-s", "--server", action="store_true", help="run server")
    parser.add_argument(
        "output_filename", metavar="output_filename", type=str, nargs=1, help="output filename"
    )

    args = parser.parse_args(argv[1:])
    conf_path = args.config
    try:
        if args.server:
            with open(conf_path or DEFAULT_CONF_PATH) as f:
                yaml_data = yaml.load(f, Loader=SafeLoader)
                broker = yaml_data["mqtt"]["broker"]
                port = yaml_data["mqtt"]["port"]
            client = mqtt_client.Client("wb-diag-collect")

            with TMQTTRPCServerContextManager(client, "diag") as rpc_server:
                client.connect(broker, port)
                client.on_message = rpc_server.on_mqtt_message
                rpc_server.setup()

                killer = GracefulKiller()
                while not killer.kill_now:
                    rc = client.loop()
                    if rc != 0:
                        break
        else:
            collect_data_with_conf(conf_path, args.output_filename[0], server=False)

        return ResultCode.OK
    except OSError as e:
        return ResultCode.OPERATION_ERROR


if __name__ == "__main__":
    sys.exit(main())
