import datetime
import glob
import logging
import os
import re
import shutil
import subprocess
from fnmatch import fnmatch
from io import StringIO
from tempfile import TemporaryDirectory


class Collector:
    def __init__(self, logger):
        self.logger = logger

    def collect(self, options, output_directory, output_filename):
        with TemporaryDirectory() as tmpdir:
            try:
                self.log_stream = StringIO()
                self.log_stream_handler = logging.StreamHandler(self.log_stream)
                self.log_stream_handler.setFormatter(
                    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
                )
                self.logger.setLevel(self.logger.getEffectiveLevel())
                self.logger.addHandler(self.log_stream_handler)

                self.copy_files(tmpdir, options["files"])
                self.filter_files(tmpdir, options["filters"])
                self.execute_commands(tmpdir, options["commands"], options["timeout"])
                self.copy_journalctl(
                    tmpdir, options["service_names"], options["service_lines_number"], options["timeout"]
                )

            except FileNotFoundError as e:
                self.logger.error(
                    "File %s not found.", e.filename, exc_info=(self.logger.level <= logging.DEBUG)
                )

            except OSError as e:
                self.logger.error("OSError: with file %s, errno %d", e.filename, e.errno)
                self.logger.error(e.strerror, exc_info=(self.logger.level <= logging.DEBUG))

            finally:
                with open("{0}/{1}".format(tmpdir, "wb-diag-collect.log"), "w") as logfile:
                    self.log_stream.seek(0)
                    shutil.copyfileobj(self.log_stream, logfile)

                self.logger.removeHandler(self.log_stream_handler)

                with open("/var/lib/wirenboard/short_sn.conf", "r") as f:
                    serial_number = f.readline().strip()

                date_time_now = datetime.datetime.now().strftime("%Y-%m-%d-%H.%M.%S")

                return shutil.make_archive(
                    base_name=output_directory
                    + "{0}_{1}_{2}".format(output_filename, serial_number, date_time_now),
                    format="zip",
                    root_dir=tmpdir,
                )

    def apply_file_wildcard(self, wildcard: str, timeout):
        proc = subprocess.Popen(
            "find {0} -type f,l".format(wildcard),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        try:
            if proc.wait(timeout) != 0:
                self.logger.debug("No files for wildcard %s", wildcard)
                return []

            file_paths = []
            cmd_res = proc.stdout.readlines()

            for line in cmd_res:
                path = line.decode().strip()
                file_paths.append(path)

            return file_paths
        except subprocess.TimeoutExpired:
            self.logger.warning("Timeout was expired for wildcard %s", wildcard)
            return []

    def copy_files(self, directory, wildcards):
        for wildcard in wildcards:
            file_paths = self.apply_file_wildcard(wildcard, 1.0) or []
            for path in file_paths:
                os.makedirs("{0}/{1}".format(directory, os.path.dirname(path)), exist_ok=True)
                shutil.copyfile(path, "{0}/{1}".format(directory, path))

    def filter_files(self, directory, filters):
        for filter_data in filters:
            for path in glob.glob(os.path.join(directory, filter_data["glob"])):
                with open(path, "r+") as f:
                    content = re.sub(filter_data["pattern"], filter_data["repl"], f.read())
                    f.seek(0)
                    f.write(content)
                    f.truncate()

    def execute_commands(self, directory, commands, timeout):
        for command_data in commands:
            command = command_data["command"]
            file_name = command_data["filename"]

            os.makedirs("{0}/{1}".format(directory, os.path.dirname(file_name)), exist_ok=True)

            with open("{0}/{1}.log".format(directory, file_name), "w") as file:

                proc = subprocess.Popen(command, shell=True, stdout=file, stderr=subprocess.STDOUT)
                try:
                    proc.wait(timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    self.logger.warning(
                        "Command %s didn't finish in %ds",
                        command,
                        timeout,
                        exc_info=(self.logger.level <= logging.DEBUG),
                    )

    def copy_journalctl(self, directory, service_wildcards, lines_count, timeout):
        proc = subprocess.Popen(
            "systemctl list-unit-files --no-pager | grep .service",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        cmd_res = proc.stdout.readlines()

        services = []
        for line in cmd_res:
            systemctl_service = line.decode().strip().split()[0]

            for wildcard in service_wildcards:
                if fnmatch(systemctl_service, wildcard):
                    services.append(systemctl_service)
                    break

        os.makedirs("{0}/service".format(directory), exist_ok=True)

        for service in services:
            with open("{0}/service/{1}.log".format(directory, service), "w") as file:
                command = "journalctl -u {0} --no-pager -n {1}".format(service, lines_count)
                proc = subprocess.Popen(command, shell=True, stdout=file, stderr=subprocess.STDOUT)

                try:
                    proc.wait(timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    self.logger.warning(
                        "Journalctl reading %s didn't finish in %ds",
                        command,
                        timeout,
                        exc_info=(self.logger.level <= logging.DEBUG),
                    )
