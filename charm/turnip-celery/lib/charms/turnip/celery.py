# Copyright 2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import absolute_import, print_function, unicode_literals

from charmhelpers.core import hookenv, host, templating, unitdata

from charms.turnip.base import (
    code_dir,
    data_dir,
    data_mount_unit,
    logs_dir,
    reload_systemd,
    venv_dir,
)


def configure_celery():
    """Configure celery service, connecting it to rabbitmq."""
    config = hookenv.config()
    context = dict(config)
    context.update(
        {
            "code_dir": code_dir(),
            "data_dir": data_dir(),
            "data_mount_unit": data_mount_unit(),
            "logs_dir": logs_dir(),
            "venv_dir": venv_dir(),
            "celery_broker": unitdata.kv().get("turnip.amqp.url"),
        }
    )
    templating.render(
        "turnip-celery.service.j2",
        "/lib/systemd/system/turnip-celery.service",
        context,
        perms=0o644,
    )
    if host.service_running("turnip-celery"):
        host.service_stop("turnip-celery")
    templating.render(
        "turnip-celery-repack.service.j2",
        "/lib/systemd/system/turnip-celery-repack.service",
        context,
        perms=0o644,
    )
    if host.service_running("turnip-celery-repack"):
        host.service_stop("turnip-celery-repack")
    reload_systemd()
    if not host.service_resume("turnip-celery"):
        raise RuntimeError("Failed to start turnip-celery")
    if not host.service_resume("turnip-celery-repack"):
        raise RuntimeError("Failed to start turnip-celery-repack")
