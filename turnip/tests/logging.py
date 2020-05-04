# Copyright 2020 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import sys
import logging


def setupLogger():
    """Setup our basic logging for tests."""
    logging.basicConfig(stream=sys.stdout, level=logging.INFO)
