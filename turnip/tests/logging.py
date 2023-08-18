# Copyright 2020 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import logging
import sys


def setupLogger():
    """Setup our basic logging for tests."""
    logging.basicConfig(stream=sys.stdout, level=logging.INFO)
