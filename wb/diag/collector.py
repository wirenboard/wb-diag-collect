import asyncio
import datetime
import glob
import json
import logging
import os
import re
import shutil
import signal
from fnmatch import fnmatch
from io import StringIO
from tempfile import TemporaryDirectory

from mqttrpc.client import MQTTRPCError
from mqttrpc.client import TimeoutError as RPCTimeoutError
from mqttrpc.client import TMQTTRPCClient
from wb_common.mqtt_client import MQTTClient

SERIAL_DRIVER = "wb-mqtt-serial"
RPC_TIMEOUT_S = 10.0


class Collector:
    def __init__(self, logger):
        self.logger = logger
        self.log_stream = None
        self.log_stream_handler = None

    async def collect(self, options, output_directory, output_filename):
        with TemporaryDirectory() as tmpdir:
            try:
                self.log_stream = StringIO()
                self.log_stream_handler = logging.StreamHandler(self.log_stream)
                self.log_stream_handler.setLevel(logging.DEBUG)
                self.log_stream_handler.setFormatter(
                    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
                )
                self.logger.addHandler(self.log_stream_handler)

                await self.copy_files(tmpdir, options["files"])
                self.filter_files(tmpdir, options["filters"])
                await self.execute_commands(tmpdir, options["commands"], options["timeout"])
                if "broker" in options:
                    await self.collect_modbus_devices(tmpdir, options["broker"])
                await self.copy_journalctl(
                    tmpdir, options["service_names"], options["service_lines_number"], options["timeout"]
                )

            except FileNotFoundError as e:
                self.logger.error(
                    "File %s not found.", e.filename, exc_info=(self.logger.level <= logging.DEBUG)
                )

            except OSError as e:
                self.logger.error("OSError: with file %s, errno %d", e.filename, e.errno)
                self.logger.error(e.strerror, exc_info=self.logger.level <= logging.DEBUG)

            finally:
                with open(f"{tmpdir}/wb-diag-collect.log", "w", encoding="utf-8") as logfile:
                    self.log_stream.seek(0)
                    shutil.copyfileobj(self.log_stream, logfile)

                self.logger.removeHandler(self.log_stream_handler)

                with open("/var/lib/wirenboard/short_sn.conf", "r", encoding="utf-8") as f:
                    serial_number = f.readline().strip()

                date_time_now = datetime.datetime.now().strftime("%Y-%m-%d-%H.%M.%S")

            return shutil.make_archive(
                base_name=f"{output_directory}{output_filename}_{serial_number}_{date_time_now}",
                format="zip",
                root_dir=tmpdir,
            )

    @staticmethod
    async def terminate_process_group(proc):
        if proc is None:
            return

        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

        try:
            await asyncio.wait_for(proc.wait(), timeout=1)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()

    async def wait_for_process(self, proc, timeout):
        """
        Wait for the process; kill its group when the timeout expires or the collection is cancelled.
        """
        try:
            return await asyncio.wait_for(proc.wait(), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            await self.terminate_process_group(proc)
            raise

    async def apply_file_wildcard(self, wildcard: str, timeout):
        cmd = f"find {wildcard} -type f,l"
        proc = None
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd=cmd,
                shell=True,
                start_new_session=True,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            rc = await self.wait_for_process(proc, timeout)
            if rc != 0:
                self.logger.debug("No files for wildcard %s", wildcard)
                return []

            file_paths = []
            cmd_res = await proc.stdout.read()

            for line in cmd_res.splitlines():
                path = line.decode().strip()
                file_paths.append(path)

            return file_paths
        except asyncio.TimeoutError:
            self.logger.warning("Timeout expired while processing wildcard %s", wildcard)
            return []

    async def copy_files(self, directory, wildcards):
        for wildcard in wildcards:
            file_paths = await self.apply_file_wildcard(wildcard, 1.0) or []
            for path in file_paths:
                os.makedirs(f"{directory}/{os.path.dirname(path)}", exist_ok=True)
                shutil.copyfile(path, f"{directory}/{path}")

    def filter_files(self, directory, filters):
        for filter_data in filters:
            for path in glob.glob(os.path.join(directory, filter_data["glob"])):
                with open(path, "r+", encoding="utf-8") as f:
                    content = re.sub(filter_data["pattern"], filter_data["repl"], f.read())
                    f.seek(0)
                    f.write(content)
                    f.truncate()

    async def execute_commands(self, directory, commands, timeout):
        env = os.environ.copy()
        env["LC_ALL"] = "C"

        for command_data in commands:
            command = command_data["command"]
            file_name = command_data["filename"]

            os.makedirs(f"{directory}/{os.path.dirname(file_name)}", exist_ok=True)

            with open(f"{directory}/{file_name}.log", "w", encoding="utf-8") as file:
                proc = None
                try:
                    proc = await asyncio.create_subprocess_shell(
                        cmd=command,
                        shell=True,
                        start_new_session=True,
                        env=env,
                        stdout=file,
                        stderr=asyncio.subprocess.STDOUT,
                    )  # nosec B602
                    await self.wait_for_process(proc, timeout)
                except asyncio.TimeoutError:
                    self.logger.warning(
                        "Command `%s` exceeded timeout of %ds and was terminated",
                        command,
                        timeout,
                        exc_info=(self.logger.level <= logging.DEBUG),
                    )

    @staticmethod
    async def call_serial_rpc(rpc_client, service, method, params):
        future = rpc_client.call_async(SERIAL_DRIVER, service, method, params)
        return await asyncio.to_thread(future.result, RPC_TIMEOUT_S)

    async def read_port_devices(self, rpc_client, port):
        if "path" in port:
            port_settings = {
                key: port[key] for key in ("path", "baud_rate", "parity", "data_bits", "stop_bits")
            }
        else:
            port_settings = {"address": port["ip"], "port": port["port"]}
        protocol = "modbus-tcp" if port.get("mode") == "modbus-tcp" else "modbus"

        devices = []
        for device in port["devices"]:
            if device.get("protocol") != "modbus" or "slave_id" not in device:
                continue
            info = {
                "device_id": device["device_id"],
                "device_type": device.get("device_type"),
                "slave_id": device["slave_id"],
                "port": port_settings,
            }
            devices.append(info)
            try:
                info["firmware"] = await self.call_serial_rpc(
                    rpc_client,
                    "fw-update",
                    "GetFirmwareInfo",
                    {"slave_id": int(device["slave_id"], 0), "port": port_settings, "protocol": protocol},
                )
            except ValueError:
                info["error"] = "invalid slave_id"
            except MQTTRPCError as e:
                info["error"] = e.data or e.rpc_message
            except RPCTimeoutError:
                # the driver does not answer at all, the rest of the port would wait for nothing too
                info["error"] = "no reply from " + SERIAL_DRIVER
                break
        return devices

    async def collect_modbus_devices(self, directory, broker):
        client = MQTTClient("wb-diag-collect", broker)
        rpc_client = TMQTTRPCClient(client)
        client.on_message = rpc_client.on_mqtt_message
        try:
            client.start()
            ports = (await self.call_serial_rpc(rpc_client, "ports", "List", {}))["ports"]
            devices = await asyncio.gather(*(self.read_port_devices(rpc_client, port) for port in ports))
        except (OSError, MQTTRPCError, RPCTimeoutError) as e:
            self.logger.warning("Cannot read Modbus devices firmware info: %s", repr(e))
            return
        finally:
            client.stop()

        with open(f"{directory}/modbus_devices.json", "w", encoding="utf-8") as file:
            json.dump([info for port_devices in devices for info in port_devices], file, indent=4)

    async def copy_journalctl(
        self, directory, service_wildcards, lines_count, timeout
    ):  # pylint:disable=too-many-locals
        env = os.environ.copy()
        env["LC_ALL"] = "C"

        proc = await asyncio.create_subprocess_shell(
            cmd="systemctl list-units --type=service --state=loaded --no-pager --plain | grep .service",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        cmd_res = stdout.splitlines()

        services = []
        for line in cmd_res:
            systemctl_service = line.decode().strip().split()[0]

            for wildcard in service_wildcards:
                if fnmatch(systemctl_service, wildcard):
                    services.append(systemctl_service)
                    break

        os.makedirs(f"{directory}/service", exist_ok=True)

        for service in services:
            with open(f"{directory}/service/{service}.log", "w", encoding="utf-8") as file:
                command = f"journalctl -u {service} --no-pager -n {lines_count}"
                try:
                    proc = await asyncio.create_subprocess_shell(
                        command,
                        env=env,
                        shell=True,
                        start_new_session=True,
                        stdout=file,
                        stderr=asyncio.subprocess.STDOUT,  # nosec B602
                    )
                    await self.wait_for_process(proc, timeout)
                except asyncio.TimeoutError:
                    self.logger.warning(
                        "Journalctl reading %s exceeded timeout of %ds and was terminated",
                        command,
                        timeout,
                        exc_info=(self.logger.level <= logging.DEBUG),
                    )
