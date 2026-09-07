#!/usr/bin/env python3

import os

from setuptools import setup


def get_version():
    return os.environ.get("DEB_VERSION", "0.0.0").split("~")[0].replace("-", "+")


setup(
    name="wb-diag-collect",
    version=get_version(),
    description="Diagnostic collector",
    license="MIT",
    author="Sokolov Semen",
    author_email="s.sokolov@wirenboard.ru",
    maintainer="Wiren Board Team",
    maintainer_email="info@wirenboard.com",
    url="https://github.com/wirenboard/wb-diag-collect",
    packages=["wb.diag"],
    scripts=["wb-diag-collect"],
)
