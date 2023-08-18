# Copyright 2020 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from turnip.tests.logging import setupLogger
from turnip.tests.tasks import setupCelery

setupLogger()
setupCelery()
