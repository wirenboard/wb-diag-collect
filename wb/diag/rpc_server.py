import asyncio
import glob
import json
import os
import signal
import time

from mqttrpc import dispatcher
from mqttrpc.manager import AMQTTRPCResponseManager
from wb_common.mqtt_client import MQTTClient

from wb.diag import collector

EXIT_SUCCESS = 0
EXIT_INVALIDARGUMENT = 2
# one deadline for confirming all the retained clears at stop; well below systemd's TimeoutStopSec
CLEAR_RETAINED_TIMEOUT_S = 5.0
# CONNACK codes for a rejected login: bad user name or password, not authorized
MQTT_AUTH_ERRORS = (4, 5)


class AsyncMQTTRPCServer:
    # pylint: disable=too-many-instance-attributes
    DIAG_ARTIFACT_TOPIC = "/wb-diag-collect/artifact"

    def __init__(self, options, dispatcher, logger):  # pylint:disable=redefined-outer-name
        self.options = options
        self.logger = logger
        self.driver_id = "diag"
        self.exit_code = EXIT_SUCCESS

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
        self.asyncio_loop = asyncio.new_event_loop()
        signals = [signal.SIGINT, signal.SIGTERM]
        for sig in signals:
            self.asyncio_loop.add_signal_handler(sig, self.asyncio_loop.stop)
        self.logger.debug("Add handler for: %s; event loop: %s", str(signals), str(self.asyncio_loop))

    def _setup_mqtt_connection(self):
        self.client.on_message = self._on_message
        self.client.on_connect = self._on_connect
        # an unavailable broker is retried by paho's network thread until stop()
        self.client.start(retry_first_connection=True)

    def _on_connect(self, _client, _userdata, _flags, rc, *_):
        if rc != 0:
            self.logger.error("MQTT broker connection failed, code %d", rc)
            if rc in MQTT_AUTH_ERRORS:
                # a rejected login is a configuration problem, paho would retry it forever: exit with 2
                self.exit_code = EXIT_INVALIDARGUMENT
                self.asyncio_loop.call_soon_threadsafe(self.asyncio_loop.stop)
            return

        # the first connection and every reconnect: the broker holds none of our retained state
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
            for f in glob.glob("/var/www/diag/*.zip"):
                try:
                    os.remove(f)
                except OSError:
                    self.logger.warning("Error deleting file %s", f)

            self.logger.info("Start data collecting")

            wb_archive_collector = collector.Collector(self.logger)
            started_at = time.monotonic()
            path = await wb_archive_collector.collect(self.options, "/var/www/diag/", "diag_output")
            elapsed = time.monotonic() - started_at

            self.logger.info("Data was collected successfully in %.2fs", elapsed)

            self.publish_result(payload={"basename": os.path.basename(path), "fullname": path})
        except OSError as e:
            self.logger.error("OSError: with file %s, errno %s", e.filename, e.errno, exc_info=True)
            self.publish_result(payload=None)

    def run(self):
        """
        Serve until SIGINT/SIGTERM or a rejected MQTT login; returns the exit code.
        """
        self.asyncio_loop.run_forever()

        if self._diag_collecting_task and not self._diag_collecting_task.done():
            self.logger.info("Cancelling the running diagnostics collection")
            self._diag_collecting_task.cancel()
            try:
                self.asyncio_loop.run_until_complete(self._diag_collecting_task)
            except asyncio.CancelledError:
                pass
        return self.exit_code

    def stop(self):
        if self.client.is_connected():
            self.logger.debug("Cleaning up retains")
            self.publish_result(payload=None)
            pubs = [
                self.client.publish(f"/rpc/v1/{self.driver_id}/{service}/{method}", retain=True)
                for service, method in self.dispatcher.keys()
            ]
            self._wait_published(pubs)
        else:
            self.logger.error("MQTT broker is not connected, retained topics cannot be removed")
        self.client.stop()

    def _wait_published(self, pubs):
        """
        Wait for the retained clears within one shared deadline; a failure is logged, never raised.
        """
        deadline = time.monotonic() + CLEAR_RETAINED_TIMEOUT_S
        try:
            for pub in pubs:
                pub.wait_for_publish(max(0.0, deadline - time.monotonic()))
        except (RuntimeError, ValueError) as exc:  # paho: the client is not connected / queue full
            self.logger.error("Removal of the retained topics is not confirmed: %s", exc)
            return
        if not all(pub.is_published() for pub in pubs):
            self.logger.error(
                "Removal of the retained topics is not confirmed within %.0f s", CLEAR_RETAINED_TIMEOUT_S
            )


def serve(options, logger):
    """
    Run the RPC server until it is stopped; returns the exit code.
    """
    server = AsyncMQTTRPCServer(options, dispatcher, logger)
    try:
        return server.run()
    finally:
        try:
            server.stop()
        finally:
            server.asyncio_loop.close()
