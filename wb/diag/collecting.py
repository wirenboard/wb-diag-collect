import datetime
import subprocess
import shutil
from tempfile import TemporaryDirectory
from fnmatch import fnmatch

import yaml
from yaml.loader import SafeLoader

DEFAULT_CONF_PATH = '/usr/share/wb-diag-collect/wb-diag-collect.conf'


def write_output_in_file(command, filename):
    with open('{0}.log'.format(filename), 'w') as file:
        if type(command) is str:
            subprocess.Popen(command, shell=True, stdout=file, stderr=subprocess.STDOUT)
        else:
            for comm in command:
                subprocess.Popen(comm, shell=True, stdout=file, stderr=subprocess.STDOUT)


def get_stdout(command: str):
    p = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.stdout


def get_filenames_by_regex(regex: str):
    try:
        p = subprocess.Popen('find {0} -type f'.format(regex), shell=True, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
        cmd_res = p.stdout.readlines()
        filenames = {}

        if p.poll() == 1:
            raise FileNotFoundError()

        for line in cmd_res:
            full_filename = line.decode().strip()
            filename = full_filename.replace('/', ' ').split()[-1].strip()
            filenames[filename] = full_filename

        return filenames
    except FileNotFoundError:
        print('No files for regex "{0}"'.format(regex))


def collect_data(commands, files, service_names, service_lines_number, dir):
    data = {}
    for file in files:
        filenames = get_filenames_by_regex(file) or {}
        for filename in filenames:
            data[filename] = filenames[filename]
    for filename in data:
        shutil.copyfile(data[filename], '{0}/{1}'.format(dir, filename))

    for command in commands:
        write_output_in_file(command['command'], '{0}/{1}'.format(dir, command['filename']))

    if service_lines_number > 0:
        collect_all_services_last_logs(dir, service_names, service_lines_number)


def check_regex_list(service_name, service_names):
    for serv_name in service_names:
        if fnmatch(service_name, serv_name):
            return True
    return False


def collect_all_services_last_logs(dir, service_names, n=20):
    p = get_stdout('systemctl list-unit-files --no-pager | grep .service')

    cmd_res = p.readlines()
    services = []
    for line in cmd_res:
        service = line.decode().strip().split()[0]
        if check_regex_list(service, service_names):
            services.append(service)

    commands = []
    for serv in services:
        commands.append('journalctl -u {0} --no-pager -n {1}'.format(serv, n))

    write_output_in_file(commands, '{0}/services_log'.format(dir))


def collect_data_with_conf(conf_path=DEFAULT_CONF_PATH, output_filename='diag_output', server=True):
    try:
        with open(conf_path or DEFAULT_CONF_PATH) as f:
            yaml_data = yaml.load(f, Loader=SafeLoader)
            commands = yaml_data['commands'] or []
            files = yaml_data['files'] or []
            service_lines_number = yaml_data['journald_logs']['number'] or 0
            service_names = yaml_data['journald_logs']['names']

        with TemporaryDirectory() as tmpdir:
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
