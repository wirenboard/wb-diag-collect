import logging
import shutil

LOG_FILENAME = "wb-diag-collect.log"


def add_file_handler(logger, level, path):
    file_handler = logging.FileHandler(path + LOG_FILENAME, mode="w")
    file_handler.setLevel(level)
    logger.addHandler(file_handler)
    return file_handler


def move_log_to_directory(handler, directory):
    shutil.move(handler.baseFilename, directory)


def remove_file_handler(logger, handler):
    logger.removeHandler(handler)
