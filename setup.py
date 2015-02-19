#! /usr/bin/env python

# Copyright 2015 Canonical Ltd.  All rights reserved.

import os

from setuptools import setup

here = os.path.abspath(os.path.dirname(__file__))

with open(os.path.join(here, 'README')) as f:
    README = f.read()


setup(
    name='turnip',
    version='0.1',
    packages=['turnip'],
    include_package_data=True,
    zip_safe=False,
    maintainer='LAZR Developers',
    maintainer_email='lazr-developers@lists.launchpad.net',
    description='turnip',
    long_description=README,
    url='https://launchpad.net/turnip',
    download_url='https://launchpad.net/turnip/+download',
    install_requires=[
        'lazr.sshserver',
        'PyYAML',
        'Twisted',
        'zope.interface',
        ],
    extras_require=dict(
        test=[
            'fixtures',
            'testtools',
            ]),
    test_suite='turnip.tests',
    )
