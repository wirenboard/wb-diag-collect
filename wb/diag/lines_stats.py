import subprocess


def get_stdout(command: str):
    p = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.stdout


def get_stdouts_by_regex(regex: str):
    p = subprocess.Popen('find {0} -type f'.format(regex), shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    cmd_res = p.stdout.readlines()
    stdouts = {}

    for line in cmd_res:
        filename = line.decode()
        p2 = subprocess.Popen('cat {0}'.format(filename), shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        filename = filename.replace('/', ' ').split()[-1].strip()
        stdouts[filename] = p2.stdout

    return stdouts
