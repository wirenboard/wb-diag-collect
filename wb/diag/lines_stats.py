import subprocess


def get_stdout(command: str):
    p = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.stdout


def ps_aux_to_stdout():
    return get_stdout('ps aux')


def dpkg_l_to_stdout():
    return get_stdout('dpkg -l')


def interrupts_to_stdout():
    return get_stdout('cat /proc/interrupts')

