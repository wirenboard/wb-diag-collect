import json
import subprocess


def current_date_and_time_to_json():
    p = subprocess.Popen('date "+%Y %m %d %H %M %S"', shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    cmd_res = p.stdout.readlines()[0].decode()
    current_date_and_time_data = cmd_res.split()
    d = {'year': current_date_and_time_data[0],
         'month': current_date_and_time_data[0],
         'day': current_date_and_time_data[0],
         'hours': current_date_and_time_data[0],
         'minutes': current_date_and_time_data[0],
         'seconds': current_date_and_time_data[0]
         }
    return json.dumps(d, indent=4)


def core_version_to_json():
    p = subprocess.Popen('uname -a', shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    cmd_res = p.stdout.readlines()[0].decode()
    controller_info = cmd_res.split()
    d = {'kernel name': controller_info[0],
         'network node hostname': controller_info[1],
         'kernel release': controller_info[2],
         'kernel version': ' '.join(controller_info[3:11]),
         'machine hardware name': controller_info[11],
         'processor type (non-portable)': controller_info[12],
         'hardware platform (non-portable)': controller_info[13],
         'operating system': controller_info[14]
         }
    return json.dumps(d, indent=4)


def df_to_json():
    p = subprocess.Popen('df -h', shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    cmd_res = p.stdout.readlines()
    df_data = {}
    for line in cmd_res:
        df_line_data = line.decode().split()
        if df_line_data[1] == 'Size':
            continue

        one_fs_data = {
            'Filesystem': df_line_data[0],
            'Size': df_line_data[1],
            'Used': df_line_data[2],
            'Avail': df_line_data[3],
            'Use%': df_line_data[4],
            'Mounted on': df_line_data[5],
        }

        df_data[one_fs_data['Mounted on']] = one_fs_data

    return json.dumps(df_data, indent=4)


def systemctl_to_json():
    p = subprocess.Popen('systemctl list-units --all --output=json', shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.stdout.readlines()[0].decode()
