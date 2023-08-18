# Copyright 2020 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import absolute_import, print_function, unicode_literals

__all__ = ["app", "logger"]

from celery import Celery
from celery.utils.log import get_task_logger

from turnip.config import config

app = Celery("tasks", broker=config.get("celery_broker"))
app.conf.update(imports=("turnip.api.store",))

logger = get_task_logger(__name__)
