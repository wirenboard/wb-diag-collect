import logging
import os
import signal
import subprocess
import sys
import threading
import urllib.parse
from contextlib import contextmanager

from mqttrpc import MQTTRPCResponseManager, dispatcher
from paho.mqtt import client as mqttclient

from wb.diag import collector

EXIT_FAILURE = 1


class MQTTRPCServer:
    def __init__(self, options, dispatcher, logger):
        self.options = options
        self.logger = logger
        self.dispatcher = dispatcher
        self.driver_id = "diag"

        self.dispatcher.add_method(self.diag)
        self.dispatcher.add_method(self.status)

        self.client = mqttclient.Client("wb-diag-collect")
        self.client.on_message = self._on_message
        self.client.on_connect = self._on_connect

        logger.debug("Connecting to broker %s:%s", options["broker"], options["port"])
        self.client.connect(options["broker"], options["port"])
        self._stop_event = threading.Event()

        self.client.loop_start()

        signal.signal(signal.SIGINT, self._signal)
        signal.signal(signal.SIGTERM, self._signal)

        self.wb_archive_collector = collector.Collector(logger)

    def _on_connect(self, client, userdata, flags, rc, *_):
        # TODO: graceful exit here + guideline
        # https://wirenboard.bitrix24.ru/workgroups/group/218/tasks/task/view/55510/
        if rc != 0:
            self.logger.error("MQTT broker connection failed, code %d", rc)
            sys.exit(EXIT_FAILURE)

        self.logger.debug("Settings up RPC endpoints")
        for service, method in self.dispatcher.keys():
            self.client.publish("/rpc/v1/%s/%s/%s" % (self.driver_id, service, method), "1", retain=True)
            self.logger.debug("Subscribe to /rpc/v1/%s/%s/%s/+", self.driver_id, service, method)
            self.client.subscribe("/rpc/v1/%s/%s/%s/+" % (self.driver_id, service, method))

    def _on_message(self, mosq, obj, msg):
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

    def wait_for_stop(self):
        self._stop_event.wait()

    def _signal(self, *_):
        self.logger.debug("Asynchronous interrupt, stopping")
        self._stop_event.set()

    def stop(self):
        try:
            self.logger.debug("Cleaning up retains")

            pubs = []
            for service, method in self.dispatcher.keys():
                pubs.append(
                    self.client.publish("/rpc/v1/%s/%s/%s" % (self.driver_id, service, method), retain=True)
                )
            for pub in pubs:
                pub.wait_for_publish()

            self.logger.debug("Disconnecting from broker")
            self.client.disconnect()
        finally:
            self.client.loop_stop()


@contextmanager
def rpc_server_context(name, options, dispatcher, logger):
    try:
        rpc_server = MQTTRPCServer(options, dispatcher, logger)
        yield rpc_server
    except (TimeoutError, ConnectionRefusedError):
        logger.error("Cannot connect to broker %s:%s", options["broker"], options["port"], exc_info=True)
    finally:
        rpc_server.stop()


def serve(options, logger):
    with rpc_server_context("wb-diag-collect", options, dispatcher, logger) as server:
        server.wait_for_stop()
