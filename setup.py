#! /usr/bin/env python

# Copyright 2015 Canonical Ltd.  All rights reserved.

import os

from setuptools import (
    find_packages,
    setup,
)

here = os.path.abspath(os.path.dirname(__file__))

with open(os.path.join(here, 'README')) as f:
    README = f.read()

requires = ['cornice', 'lazr.sshserver', 'pygit2', 'PyYAML', 'Twisted',
            'waitress', 'zope.interface']
test_requires = ['fixtures', 'testtools']

setup(
    name='turnip',
    version='0.1',
    packages = ['turnip.%s' % package for package in find_packages(
        'turnip', exclude=['*.tests', 'tests'])],
    include_package_data=True,
    zip_safe=False,
    maintainer='LAZR Developers',
    maintainer_email='lazr-developers@lists.launchpad.net',
    description='turnip',
    long_description=README,
    url='https://launchpad.net/turnip',
    download_url='https://launchpad.net/turnip/+download',
    setup_requires=['PasteScript'],
    install_requires=requires,
    extras_require=dict(
        test=test_requires),
    test_suite='turnip',
    entry_points = """\
    [paste.app_factory]
    main = turnip.api:main
    """,
    paster_plugins=['pyramid'],
    )
