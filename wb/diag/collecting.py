import subprocess
import shutil
from tempfile import TemporaryDirectory

import yaml
from yaml.loader import SafeLoader

DEFAULT_CONF_PATH = '/usr/share/wb-diag-collect/wb-diag-collect.conf'


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


def collect_data(commands, files):
    data = {}
    for file in files:
        filenames = get_filenames_by_regex(file) or {}
        for filename in filenames:
            data[filename] = filenames[filename]

    for command in commands:
        data[command['filename']] = get_stdout(command['command'])

    return data


def collect_data_with_conf(conf_path=DEFAULT_CONF_PATH, output_filename='diag_output'):
    try:
        with open(conf_path or DEFAULT_CONF_PATH) as f:
            yaml_data = yaml.load(f, Loader=SafeLoader)
            commands = yaml_data['commands']
            files = yaml_data['files']

        with TemporaryDirectory() as tmpdir:
            data = collect_data(commands, files)
            for filename in data:
                if type(data[filename]) is str:
                    shutil.copyfile(data[filename], '{0}/{1}'.format(tmpdir, filename))
                else:
                    with open('{1}/{0}.log'.format(filename, tmpdir), 'wb') as file:
                        file.write(data[filename].read())

            shutil.make_archive(output_filename, 'zip', tmpdir)

        return '{0}.zip'.format(output_filename)
    except FileNotFoundError:
        print('Config not found.')
        raise FileNotFoundError()
