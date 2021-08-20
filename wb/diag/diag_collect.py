import argparse
import sys
from .json_stats import *
from .lines_stats import *
import subprocess
import shutil
import yaml
from yaml.loader import SafeLoader

DEFAULT_CONF_ROUTE = '/usr/share/wb-diag-collect/wb-diag-collect.conf'


def collect_data(data: dict, commands, prebuild_commands, files):

    if 'current_date_and_time_to_json' in prebuild_commands:
        data['current_date_and_time'] = current_date_and_time_to_json()
    if 'current_date_and_time_to_json' in prebuild_commands:
        data['df'] = df_to_json()
    if 'current_date_and_time_to_json' in prebuild_commands:
        data['core_version'] = core_version_to_json()

    for file in files:
        stdouts = get_stdouts_by_regex(file)
        for stdout in stdouts:
            data[stdout] = stdouts[stdout]

    for command in commands:
        data[command['filename']] = get_stdout(command['command'])


def create_tmp_folder():
    subprocess.Popen('mkdir wb-diag-tmp', shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def delete_tmp_folder():
    subprocess.Popen('rm -rd wb-diag-tmp', shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def main(argv=sys.argv):
    try:
        parser = argparse.ArgumentParser(description='The tool to send metrics')

        parser.add_argument('-c', '--config', action='store', help='get data from config')

        args = parser.parse_args(argv[1:])

        conf_route = DEFAULT_CONF_ROUTE if args.config is None else args.config

        with open(conf_route) as f:
            yaml_data = yaml.load(f, Loader=SafeLoader)
            commands = yaml_data['commands']
            files = yaml_data['files']
            prebuild_commands = set(yaml_data['prebuild_commands'])

        data = {}
        create_tmp_folder()
        collect_data(data, commands, prebuild_commands, files)

        for filename in data:
            with open('wb-diag-tmp/{0}.log'.format(filename), 'w') as file:
                if type(data[filename]) is str:
                    file.write(data[filename])
                else:
                    while True:
                        line = data[filename].readline()
                        if not line:
                            break
                        file.write(line.decode())

        date = datetime.datetime.now().strftime("%Y-%m-%d-%H.%M.%S")
        shutil.make_archive('diag_output_{0}'.format(date), 'zip', 'wb-diag-tmp')
    except FileNotFoundError:
        print('Config not found.')
    finally:
        delete_tmp_folder()


if __name__ == '__main__':
    sys.exit(main())
