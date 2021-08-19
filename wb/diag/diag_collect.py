import sys
from .json_stats import *
from .lines_stats import *
import subprocess
import shutil


def collect_data(data: dict):
    #  TODO : список метрик в конфиге, собирать метрики в цикле
    current_date_and_time_json = current_date_and_time_to_json()
    df_json = df_to_json()
    core_version_json = core_version_to_json()
    system_ctl_json = systemctl_to_json()

    data['current_date_and_time'] = current_date_and_time_json
    data['system_ctl'] = system_ctl_json
    data['df'] = df_json
    data['core_version'] = core_version_json

    ps_aux_str = ps_aux_to_stdout()
    dpkg_l_str = dpkg_l_to_stdout()
    interrupts_str = interrupts_to_stdout()
    data['ps_aux'] = ps_aux_str
    data['dpkg_l'] = dpkg_l_str
    data['interrupts_str'] = interrupts_str


def create_tmp_folder():
    subprocess.Popen('mkdir wb-diag-tmp', shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def delete_tmp_folder():
    subprocess.Popen('rm -rd wb-diag-tmp', shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def main():
    try:
        data = {}
        create_tmp_folder()
        collect_data(data)

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

        shutil.make_archive('diag_output_<there_will_be_date>', 'zip', 'wb-diag-tmp')
    finally:
        delete_tmp_folder()


if __name__ == '__main__':
    sys.exit(main())
