import argparse
import sys
from .server import *
from paho.mqtt import client as mqtt_client


def main(argv=sys.argv):
    parser = argparse.ArgumentParser(description='The tool for collecting diagnostic data')
    parser.add_argument('-c', '--config', action='store', help='get data from config')
    parser.add_argument('-s', '--server', action='store_true', help='run server')
    parser.add_argument('output_filename', metavar='output_filename', type=str, nargs=1, help='output filename')

    args = parser.parse_args(argv[1:])
    conf_path = args.config
    try:
        if args.server:
            client = mqtt_client.Client('python-mqtt-wb-diag')
            rpc_server = TMQTTRPCServer(client, 'diag')

            client.connect('127.0.0.1', 1883)
            client.on_message = rpc_server.on_mqtt_message
            rpc_server.setup()

            while 1:
                rc = client.loop()
                if rc != 0:
                    break
        else:
            collect_data_with_conf(conf_path, args.output_filename[0])
    except FileNotFoundError:
        return 2


if __name__ == '__main__':
    sys.exit(main())
