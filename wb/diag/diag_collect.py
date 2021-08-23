import argparse
import datetime
import sys
from .lines_stats import *
import subprocess
import shutil
import yaml
from yaml.loader import SafeLoader

DEFAULT_CONF_ROUTE = '/usr/share/wb-diag-collect/wb-diag-collect.conf'


def collect_data(data: dict, commands, files):
    for file in files:
        filenames = get_filenames_by_regex(file)
        for filename in filenames:
            data[filename] = filenames[filename]

    for command in commands:
        data[command['filename']] = get_stdout(command['command'])


def create_tmp_folder():
    subprocess.Popen('mkdir wb-diag-tmp', shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def delete_tmp_folder():
    subprocess.Popen('rm -rd wb-diag-tmp', shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def main(argv=sys.argv):
    try:
        parser = argparse.ArgumentParser(description='The tool for collecting diagnostic data')
        parser.add_argument('-c', '--config', action='store', help='get data from config')
        args = parser.parse_args(argv[1:])
        conf_route = DEFAULT_CONF_ROUTE if args.config is None else args.config

        with open(conf_route) as f:
            yaml_data = yaml.load(f, Loader=SafeLoader)
            commands = yaml_data['commands']
            files = yaml_data['files']

        data = {}
        create_tmp_folder()
        collect_data(data, commands, files)
        for filename in data:
            if type(data[filename]) is str:
                p = subprocess.Popen('cp {0} wb-diag-tmp/{1}'.format(data[filename], filename), shell=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            else:
                with open('wb-diag-tmp/{0}.log'.format(filename), 'w') as file:
                    file.write(data[filename].read().decode())

        date = datetime.datetime.now().strftime("%Y-%m-%d-%H.%M.%S")
        shutil.make_archive('diag_output_{0}'.format(date), 'zip', 'wb-diag-tmp')
    except FileNotFoundError:
        print('Config not found.')
    finally:
        delete_tmp_folder()
        pass


if __name__ == '__main__':
    sys.exit(main())
