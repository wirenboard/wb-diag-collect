import logging
import os
import signal
import subprocess
from contextlib import contextmanager
import urllib

from mqttrpc import MQTTRPCResponseManager, dispatcher
from paho.mqtt import client as mqttclient
import paho_socket

from wb.diag import collector


class MQTTRPCServer:
    def __init__(self, options, dispatcher, logger):
        self.options = options
        self.logger = logger
        self.dispatcher = dispatcher
        self.driver_id = "diag"

        self.dispatcher.add_method(self.diag)
        self.dispatcher.add_method(self.status)

        broker = options["broker"]
        url = urllib.parse.urlparse(broker)
        if url.scheme == 'unix':
            logger.debug("Connecting to broker %s", broker)
            self.client = paho_socket.Client("wb-diag-collect")
            self.client.on_message = self.on_message
            self.client.sock_connect(url.netloc + url.path)
        else:
            port = options["port"]
            logger.debug("Connecting to broker %s:%s", broker, port)
            self.client = mqttclient.Client("wb-diag-collect")
            self.client.on_message = self.on_message
            self.client.connect(broker, port)

        self.run = True

        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        self.wb_archive_collector = collector.Collector(logger)

    def setup(self):
        for service, method in self.dispatcher.keys():
            self.client.publish("/rpc/v1/%s/%s/%s" % (self.driver_id, service, method), "1", retain=True)
            self.logger.debug("Subscribe to /rpc/v1/%s/%s/%s/+", self.driver_id, service, method)
            self.client.subscribe("/rpc/v1/%s/%s/%s/+" % (self.driver_id, service, method))

    def on_message(self, mosq, obj, msg):
        parts = msg.topic.split("/")
        service_id = parts[4]
        method_id = parts[5]
        client_id = parts[6]

        response = MQTTRPCResponseManager.handle(msg.payload, service_id, method_id, self.dispatcher)

        self.client.publish(
            "/rpc/v1/%s/%s/%s/%s/reply" % (self.driver_id, service_id, method_id, client_id),
            response.json,
            False,
        )

    def status(self):
        self.logger.debug("Method 'status' was called")
        return "1"

    def diag(self):
        try:
            self.logger.debug("Method 'diag' was called")
            try:
                subprocess.run("rm /var/www/diag/*.zip", shell=True)
            except OSError:
                self.logger.warning("Error deleting a directory %s" % "/var/www/diag/*.zip")

            print("Start data collecting")

            wb_archive_collector = collector.Collector(self.logger)
            path = wb_archive_collector.collect(self.options, "/var/www/diag/", "diag_output")

            print("Data was collected successfully")

            return {"basename": os.path.basename(path), "fullname": path}
        except OSError as e:
            print("OSError: with file %s, errno %d", e.filename, e.errno)

    def loop(self):
        self.client.loop()

    def stop(self, *args):
        self.logger.debug("Asynchronous interrupt, stopping")
        for service, method in self.dispatcher.keys():
            self.client.publish("/rpc/v1/%s/%s/%s" % (self.driver_id, service, method), retain=True)

        # timeout in last loop for publish execution control
        self.client.loop(timeout=1.0)

        self.run = False
        self.logger.debug("Disconnecting with broker")
        self.client.disconnect()


@contextmanager
def rpc_server_context(name, options, dispatcher, logger):
    try:
        rpc_server = MQTTRPCServer(options, dispatcher, logger)
        rpc_server.setup()
        yield rpc_server
    except (TimeoutError, ConnectionRefusedError) as error:
        logger.debug("Cannot connect to broker %s:%s", options["broker"], options["port"])


def serve(options, logger):
    with rpc_server_context("wb-diag-collect", options, dispatcher, logger) as server:
        while server.run:
            server.loop()
