# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import unicode_literals

import os

import yaml


class TurnipConfig(object):
    """Return configuration from environment or defaults."""

    def __init__(self):
        """Load default configuration from config.yaml"""
        config_file = open(os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 'config.yaml'))
        self.defaults = yaml.load(config_file)

    def get(self, key):
        return os.getenv(key.upper()) or self.defaults.get(key.lower()) or ''
