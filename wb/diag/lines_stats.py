import subprocess


def get_stdout(command: str):
    p = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.stdout


def get_filenames_by_regex(regex: str):
    p = subprocess.Popen('find {0} -type f'.format(regex), shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    cmd_res = p.stdout.readlines()
    filenames = {}

    for line in cmd_res:
        full_filename = line.decode().strip()
        if full_filename[0:5] == 'find:':
            break
        filename = full_filename.replace('/', ' ').split()[-1].strip()
        filenames[filename] = full_filename

    return filenames



