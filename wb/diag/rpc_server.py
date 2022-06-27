import logging
import os
import signal
import subprocess
from contextlib import contextmanager

from mqttrpc import MQTTRPCResponseManager, dispatcher
from paho.mqtt import client as mqttclient

from wb.diag import collector

global_options = {}
global_logger = logging.getLogger(__name__)


@dispatcher.add_method
def diag():
    try:
        global_logger.debug("Method 'diag' was called")
        try:
            subprocess.run("rm /var/www/diag/*.zip", shell=True)
        except OSError:
            global_logger.warning("Error deleting a directory %s" % "/var/www/diag/*.zip")

        print("Start data collecting")

        wb_archive_collector = collector.Collector(global_logger)
        path = wb_archive_collector.collect(global_options, "/var/www/diag/", "diag_output")

        print("Data was collected successfully")

        return {"basename": os.path.basename(path), "fullname": path}
    except OSError as e:
        print("OSError: with file %s, errno %d", e.filename, e.errno)


@dispatcher.add_method
def status():
    global_logger.debug("Method 'status' was called")
    return "1"


class MQTTRPCServer:
    def __init__(self, options, logger):
        self.options = options
        self.logger = logger

        global global_options
        global_options = options

        global global_logger
        global_logger = logger

        self.driver_id = "diag"

        self.client = mqttclient.Client("wb-diag-collect")
        self.client.on_message = self.on_message

        logger.debug("Connecting to broker %s:%s", options["broker"], options["port"])
        self.client.connect(options["broker"], options["port"])
        self.client.loop_start()

        self.run = True

        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        self.wb_archive_collector = collector.Collector(logger)

    def setup(self):
        for service, method in dispatcher.keys():
            self.client.publish("/rpc/v1/%s/%s/%s" % (self.driver_id, service, method), "1", retain=True)
            self.logger.debug("Subscribe to /rpc/v1/%s/%s/%s/+", self.driver_id, service, method)
            self.client.subscribe("/rpc/v1/%s/%s/%s/+" % (self.driver_id, service, method))

    def on_message(self, mosq, obj, msg):

        parts = msg.topic.split("/")
        service_id = parts[4]
        method_id = parts[5]
        client_id = parts[6]

        response = MQTTRPCResponseManager.handle(msg.payload, service_id, method_id, dispatcher)

        self.client.publish(
            "/rpc/v1/%s/%s/%s/%s/reply" % (self.driver_id, service_id, method_id, client_id),
            response.json,
            False,
        )

    def stop(self, *args):
        self.logger.debug("Asynchronous interrupt, stopping")
        self.run = False

    def clean(self):
        for service, method in dispatcher.keys():
            self.client.publish("/rpc/v1/%s/%s/%s" % (self.driver_id, service, method), retain=True)

        self.client.loop_stop()
        self.client.disconnect()


@contextmanager
def rpc_server_context(name, options, logger):
    try:
        rpc_server = MQTTRPCServer(options, logger)
        rpc_server.setup()
        yield rpc_server
    except (TimeoutError, ConnectionRefusedError) as error:
        logger.debug("Cannot connect with broker %s:%s", options["broker"], options["port"])
        pass
    finally:
        logger.debug("Clear topics, disconnecting with broker")
        rpc_server.clean()


def serve(options, logger):

    with rpc_server_context("wb-diag-collect", options, logger) as server:
        while server.run:
            pass
