# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import os

import yaml

__all__ = [
    "config",
]


class TurnipConfig:
    """Return configuration from environment or defaults."""

    def __init__(self):
        """Load default configuration from config.yaml"""
        config_file_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "config.yaml"
        )
        with open(config_file_path) as config_file:
            self.defaults = yaml.safe_load(config_file)

    def get(self, key):
        return os.getenv(key.upper()) or self.defaults.get(key.lower()) or ""


config = TurnipConfig()
