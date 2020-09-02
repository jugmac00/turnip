# Copyright 2020 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from turnip.tests.logging import setupLogger
from turnip.tests.tasks import setupCelery

setupLogger()
setupCelery()
