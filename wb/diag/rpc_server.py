import os
import signal
import subprocess
import sys
import threading
from contextlib import contextmanager

from mqttrpc import MQTTRPCResponseManager, dispatcher
from wb_common.mqtt_client import MQTTClient

from wb.diag import collector

EXIT_FAILURE = 1


class MQTTRPCServer:
    def __init__(self, options, dispatcher, logger):  # pylint:disable=redefined-outer-name
        self.options = options
        self.logger = logger
        self.dispatcher = dispatcher
        self.driver_id = "diag"

        self.dispatcher.add_method(self.diag)
        self.dispatcher.add_method(self.status)

        self._stop_event = threading.Event()

        broker = options["broker"]
        self.client = MQTTClient("wb-diag-collect", broker)
        logger.debug("Connecting to broker %s", broker)
        self.client.on_message = self._on_message
        self.client.on_connect = self._on_connect
        self.client.start()

        signal.signal(signal.SIGINT, self._signal)
        signal.signal(signal.SIGTERM, self._signal)

        self.wb_archive_collector = collector.Collector(logger)

    def _on_connect(self, _client, _userdata, _flags, rc, *_):
        # write graceful exit here + guideline
        # https://wirenboard.bitrix24.ru/workgroups/group/218/tasks/task/view/55510/
        if rc != 0:
            self.logger.error("MQTT broker connection failed, code %d", rc)
            sys.exit(EXIT_FAILURE)

        self.logger.debug("Settings up RPC endpoints")
        for service, method in self.dispatcher.keys():
            self.client.publish(f"/rpc/v1/{self.driver_id}/{service}/{method}", "1", retain=True)
            self.logger.debug(f"Subscribe to /rpc/v1/{self.driver_id}/{service}/{method}/+")
            self.client.subscribe(f"/rpc/v1/{self.driver_id}/{service}/{method}/+")

    def _on_message(self, _mosq, _obj, msg):
        parts = msg.topic.split("/")
        service_id = parts[4]
        method_id = parts[5]
        client_id = parts[6]

        response = MQTTRPCResponseManager.handle(msg.payload, service_id, method_id, self.dispatcher)

        self.client.publish(
            f"/rpc/v1/{self.driver_id}/{service_id}/{method_id}/{client_id}/reply",
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
                subprocess.run("rm /var/www/diag/*.zip", check=False, shell=True)
            except OSError:
                self.logger.warning('Error deleting a directory "/var/www/diag/*.zip"')

            print("Start data collecting")

            wb_archive_collector = collector.Collector(self.logger)
            path = wb_archive_collector.collect(self.options, "/var/www/diag/", "diag_output")

            print("Data was collected successfully")

            return {"basename": os.path.basename(path), "fullname": path}
        except OSError as e:
            print("OSError: with file %s, errno %d", e.filename, e.errno)
            return None

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
                pubs.append(self.client.publish(f"/rpc/v1/{self.driver_id}/{service}/{method}", retain=True))
            for pub in pubs:
                pub.wait_for_publish()
        finally:
            self.logger.debug("Disconnecting from broker")
            self.client.stop()


@contextmanager
def rpc_server_context(options, dispatcher, logger):  # pylint:disable=redefined-outer-name
    try:
        rpc_server = MQTTRPCServer(options, dispatcher, logger)
        yield rpc_server
    except (TimeoutError, ConnectionRefusedError):
        logger.error("Cannot connect to broker %s", options["broker"], exc_info=True)
    finally:
        rpc_server.stop()


def serve(options, logger):
    with rpc_server_context(options, dispatcher, logger) as server:
        server.wait_for_stop()
