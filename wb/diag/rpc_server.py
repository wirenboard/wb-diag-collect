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

EXIT_FAILURE = 1
EXIT_INVALIDARGUMENT = 2
EXIT_NOTRUNNING = 7
MQTT_PUBLISH_TIMEOUT_S = 1


def wait_for_publish(message_info, topic):
    message_info.wait_for_publish(timeout=MQTT_PUBLISH_TIMEOUT_S)
    if not message_info.is_published():
        raise TimeoutError(f"Timed out while publishing '{topic}'")


class AsyncMQTTRPCServer:
    # pylint: disable=too-many-instance-attributes
    DIAG_ARTIFACT_TOPIC = "/wb-diag-collect/artifact"

    def __init__(self, options, dispatcher, logger):  # pylint:disable=redefined-outer-name
        self.options = options
        self.logger = logger
        self.driver_id = "diag"
        self.exit_code = EXIT_NOTRUNNING
        self._mqtt_started = False
        self._closed = False

        self._setup_event_loop()

        self.dispatcher = dispatcher
        self.dispatcher.add_method(self.launch_diag_collect, name="diag")
        self.dispatcher.add_method(self.status)

        broker = options["broker"]
        self.client = MQTTClient("wb-diag-collect", broker)
        logger.debug("Connecting to broker %s", broker)

        self.wb_archive_collector = collector.Collector(logger)

        self._diag_collecting_task = None

    def _setup_event_loop(self):
        self.asyncio_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.asyncio_loop)
        signals = [signal.SIGINT, signal.SIGTERM]
        for sig in signals:
            self.asyncio_loop.add_signal_handler(sig, self._on_termination_signal)
        self.logger.debug("Add handler for: %s; event loop: %s", str(signals), str(self.asyncio_loop))

    def _on_termination_signal(self):
        self.exit_code = EXIT_NOTRUNNING
        self.asyncio_loop.stop()

    def _setup_mqtt_connection(self):
        self.client.on_message = self._on_message
        self.client.on_connect = self._on_connect
        self.client.start()
        self._mqtt_started = True

    def _on_connect(self, _client, _userdata, _flags, rc, *_):
        if rc in (4, 5):
            self.logger.error("MQTT broker authentication failed, code %d", rc)
            self.exit_code = EXIT_INVALIDARGUMENT
            self.asyncio_loop.call_soon_threadsafe(self.asyncio_loop.stop)
            return
        if rc != 0:
            self.logger.warning("MQTT broker connection failed, code %d, retrying", rc)
            return

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
        return self.client.publish(self.DIAG_ARTIFACT_TOPIC, payload=payload, retain=False, qos=1)

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
        self._setup_mqtt_connection()
        self.asyncio_loop.run_forever()
        return self.exit_code

    def _clear_mqtt_topics(self):
        publications = [(self.DIAG_ARTIFACT_TOPIC, self.publish_result(payload=None))]
        for service, method in self.dispatcher.keys():
            topic = f"/rpc/v1/{self.driver_id}/{service}/{method}"
            publications.append((topic, self.client.publish(topic, retain=True)))
        for topic, publication in publications:
            wait_for_publish(publication, topic)

    def _cancel_asyncio_tasks(self):
        tasks = asyncio.all_tasks(self.asyncio_loop)
        for task in tasks:
            task.cancel()
        if tasks:
            self.asyncio_loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))

    def stop(self):
        if self._closed:
            return
        try:
            if self._mqtt_started:
                if self.client.is_connected():
                    try:
                        self.logger.debug("Cleaning up MQTT topics")
                        self._clear_mqtt_topics()
                    except Exception as error:  # pylint:disable=broad-exception-caught
                        self.logger.error("Unable to clear retained MQTT topics: %s", error)
                elif self.exit_code == EXIT_NOTRUNNING:
                    self.logger.error("Unable to clear retained MQTT topics: broker is unavailable")

                try:
                    self.client.stop()
                except Exception as error:  # pylint:disable=broad-exception-caught
                    self.logger.error("Unable to close MQTT connection: %s", error)
        finally:
            self._cancel_asyncio_tasks()
            self.asyncio_loop.close()
            self._closed = True


def serve(options, logger):
    server = None
    try:
        server = AsyncMQTTRPCServer(options, dispatcher, logger)
        return server.run()
    except Exception as error:  # pylint:disable=broad-exception-caught
        logger.error("Unable to run service: %s", error)
        return EXIT_FAILURE
    finally:
        if server is not None:
            server.stop()
