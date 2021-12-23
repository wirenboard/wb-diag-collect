import datetime
import os
import subprocess
import shutil
from contextlib import redirect_stdout
from tempfile import TemporaryDirectory
from fnmatch import fnmatch

import yaml
from yaml.loader import SafeLoader

DEFAULT_CONF_PATH = '/usr/share/wb-diag-collect/wb-diag-collect.conf'


def write_output_in_file(command, filename, timeout_s = 5.0):
    with open('{0}.log'.format(filename), 'w') as file:
        if type(command) is str:
            commands = [command, ]
        else:
            commands = command

        for comm in commands:
            proc = subprocess.Popen(comm, shell=True, stdout=file, stderr=subprocess.STDOUT)
            try:
                proc.wait(timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                errorMessage = "Command '{0}' didn't finished in {1}s".format(comm, timeout_s)
                print(errorMessage)
                with redirect_stdout(file):
                    print(errorMessage)


def get_stdout(command: str):
    p = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.stdout


def get_filenames_by_wildcard(wildcard: str):
    try:
        p = subprocess.run('find {0} -type "f,l"'.format(wildcard), shell=True, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)

        if p.returncode() != 0:
            raise FileNotFoundError()

        cmd_res = p.stdout.readlines()
        filenames = {}

        for line in cmd_res:
            full_filename = line.decode().strip()
            filename = os.path.basename(full_filename)
            filenames[filename] = full_filename

        return filenames
    except FileNotFoundError:
        print('No files for wildcard "{0}"'.format(wildcard))


def create_dirs(dir, files):
    for file in files:
        os.makedirs('{0}/{1}'.format(dir, os.path.dirname(file)), exist_ok=True)


def collect_data(commands, files, service_names, service_lines_number, dir):
    data = {}
    for file in files:
        filenames = get_filenames_by_wildcard(file) or {}
        for filename in filenames:
            data[filename] = filenames[filename]

    create_dirs(dir, data.values())

    for filename in data:
        shutil.copyfile(data[filename], '{0}{1}'.format(dir, data[filename]))
    for command in commands:
        create_dirs(dir, [command['filename']])
        write_output_in_file(command['command'], '{0}/{1}'.format(dir, command['filename']))

    if service_lines_number > 0:
        collect_all_services_last_logs(dir, service_names, service_lines_number)

    collect_modbus()


def check_wildcard_list(service_name, service_names):
    for serv_name in service_names:
        if fnmatch(service_name, serv_name):
            return True
    return False


def collect_modbus():
    pass


def collect_all_services_last_logs(dir, service_names, n=20):
    p = get_stdout('systemctl list-unit-files --no-pager | grep .service')

    cmd_res = p.readlines()
    services = []
    for line in cmd_res:
        service = line.decode().strip().split()[0]
        if check_wildcard_list(service, service_names):
            services.append(service)

    for serv in services:
        write_output_in_file('journalctl -u {0} --no-pager -n {1}'.format(serv, n),
                             '{0}/service/{1}'.format(dir, serv))


def collect_data_with_conf(conf_path=DEFAULT_CONF_PATH, output_filename='diag_output', server=True):
    try:
        with open(conf_path or DEFAULT_CONF_PATH) as f:
            yaml_data = yaml.load(f, Loader=SafeLoader)
            commands = yaml_data['commands'] or []
            files = yaml_data['files'] or []
            service_lines_number = yaml_data['journald_logs']['lines_number'] or 0
            service_names = yaml_data['journald_logs']['names']

        with TemporaryDirectory() as tmpdir:
            try:
                os.mkdir('{0}/service'.format(tmpdir))
                os.mkdir('{0}/modbus'.format(tmpdir))
            except OSError:
                pass

            collect_data(commands, files, service_names, service_lines_number, tmpdir)

            with open('/var/lib/wirenboard/short_sn.conf', 'r') as f:
                additional_part_of_name = f.readline().strip()

            additional_part_of_name += '_' + datetime.datetime.now().strftime("%Y-%m-%d-%H.%M.%S")

            if server:
                return shutil.make_archive('/var/www/diag/{0}_{1}'.format(output_filename, additional_part_of_name), 'zip', tmpdir)
            else:
                return shutil.make_archive('{0}_{1}'.format(output_filename, additional_part_of_name), 'zip', tmpdir)
    except FileNotFoundError as e:
        print('File "{0}" not found.'.format(e.filename))
        raise
    except OSError as e:
        print('OSError: with file {0}'.format(e.filename))
        raise
