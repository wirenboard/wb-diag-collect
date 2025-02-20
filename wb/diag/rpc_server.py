import asyncio
import atexit
import json
import os
import signal
import subprocess
import sys
from contextlib import contextmanager

from mqttrpc import dispatcher
from mqttrpc.manager import AMQTTRPCResponseManager
from wb_common.mqtt_client import MQTTClient

from wb.diag import collector

EXIT_FAILURE = 1


class AsyncMQTTRPCServer:
    # pylint: disable=too-many-instance-attributes
    DIAG_ARTIFACT_TOPIC = "/wb-diag-collect/artifact"

    def __init__(self, options, dispatcher, logger):  # pylint:disable=redefined-outer-name
        self.options = options
        self.logger = logger
        self.driver_id = "diag"

        self._setup_event_loop()

        self.dispatcher = dispatcher
        self.dispatcher.add_method(self.launch_diag_collect, name="diag")
        self.dispatcher.add_method(self.status)

        broker = options["broker"]
        self.client = MQTTClient("wb-diag-collect", broker)
        logger.debug("Connecting to broker %s", broker)
        self._setup_mqtt_connection()

        self.wb_archive_collector = collector.Collector(logger)

        self._diag_collecting_task = None

    def _setup_event_loop(self):
        self.asyncio_loop = asyncio.get_event_loop()
        signals = [signal.SIGINT, signal.SIGTERM]
        for sig in signals:
            self.asyncio_loop.add_signal_handler(sig, self.asyncio_loop.stop)
        self.logger.debug("Add handler for: %s; event loop: %s", str(signals), str(self.asyncio_loop))

    def _setup_mqtt_connection(self):
        self.client.on_message = self._on_message
        self.client.on_connect = self._on_connect
        try:
            self.client.start()
        finally:
            atexit.register(self.client.stop)

    def _on_connect(self, _client, _userdata, _flags, rc, *_):
        # write graceful exit here + guideline
        # https://wirenboard.bitrix24.ru/workgroups/group/218/tasks/task/view/55510/
        if rc != 0:
            self.logger.error("MQTT broker connection failed, code %d", rc)
            self.asyncio_loop.stop()
            sys.exit(EXIT_FAILURE)

        self.logger.debug("Settings up RPC endpoints")
        for service, method in self.dispatcher.keys():
            self.client.publish(f"/rpc/v1/{self.driver_id}/{service}/{method}", "1", retain=True)
            self.logger.debug(f"Subscribe to /rpc/v1/{self.driver_id}/{service}/{method}/+")
            self.client.subscribe(f"/rpc/v1/{self.driver_id}/{service}/{method}/+")

    def _on_message(self, _mosq, _obj, msg):
        asyncio.run_coroutine_threadsafe(self.run_async(msg), self.asyncio_loop)

    async def run_async(self, message):
        parts = message.topic.split("/")
        service_id, method_id, client_id = parts[4], parts[5], parts[6]

        ret = await AMQTTRPCResponseManager.handle(  # wraps any exception into json-rpc
            message.payload, service_id, method_id, self.dispatcher
        )

        self.client.publish(
            f"/rpc/v1/{self.driver_id}/{service_id}/{method_id}/{client_id}/reply",
            ret.json,
            False,
        )

    def publish_result(self, payload=None):
        payload = json.dumps(payload) if payload else None
        self.client.publish(self.DIAG_ARTIFACT_TOPIC, payload=payload, retain=False, qos=1)

    async def launch_diag_collect(self):
        if self._diag_collecting_task and not self._diag_collecting_task.done():
            self.logger.warning("Diag collecting task is already running")
        else:
            self._diag_collecting_task = self.asyncio_loop.create_task(
                self.diag(), name="Collect diagnostics (may be long running)"
            )
        return "Ok"

    async def status(self):
        self.logger.debug("Method 'status' was called")
        return "1"

    async def diag(self):
        try:
            self.logger.debug("Method 'diag' was called")
            try:
                subprocess.run("rm /var/www/diag/*.zip", check=False, shell=True)
            except OSError:
                self.logger.warning('Error deleting a directory "/var/www/diag/*.zip"')

            print("Start data collecting")

            wb_archive_collector = collector.Collector(self.logger)
            path = await wb_archive_collector.collect(self.options, "/var/www/diag/", "diag_output")

            print("Data was collected successfully")

            self.publish_result(payload={"basename": os.path.basename(path), "fullname": path})
        except OSError as e:
            print("OSError: with file %s, errno %d", e.filename, e.errno)
            self.publish_result(payload=None)

    def run(self):
        self.asyncio_loop.run_forever()

    def stop(self):
        try:
            self.logger.debug("Cleaning up retains")

            self.publish_result(payload=None)

            pubs = []
            for service, method in self.dispatcher.keys():
                pubs.append(self.client.publish(f"/rpc/v1/{self.driver_id}/{service}/{method}", retain=True))
            for pub in pubs:
                pub.wait_for_publish()
        finally:
            self.client.stop()
            self.asyncio_loop.stop()


@contextmanager
def rpc_server_context(options, dispatcher, logger):  # pylint:disable=redefined-outer-name
    try:
        rpc_server = AsyncMQTTRPCServer(options, dispatcher, logger)
        yield rpc_server
    except (TimeoutError, ConnectionRefusedError):
        logger.error("Cannot connect to broker %s", options["broker"], exc_info=True)
    finally:
        rpc_server.stop()


def serve(options, logger):
    with rpc_server_context(options, dispatcher, logger) as server:
        server.run()
