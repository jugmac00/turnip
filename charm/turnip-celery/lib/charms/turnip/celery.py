# Copyright 2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import absolute_import, print_function, unicode_literals


from charmhelpers.core import (
    hookenv,
    host,
    templating,
    )

from charms.turnip.base import (
    code_dir,
    data_dir,
    data_mount_unit,
    get_rabbitmq_url,
    logs_dir,
    reload_systemd,
    venv_dir,
    )


def configure_celery():
    """Configure celery service, connecting it to rabbitmq.

    :return: True if service is running, False otherwise."""
    celery_broker = get_rabbitmq_url()
    if celery_broker is None:
        return host.service_running('turnip-celery')
    config = hookenv.config()
    context = dict(config)
    context.update({
        'code_dir': code_dir(),
        'data_dir': data_dir(),
        'data_mount_unit': data_mount_unit(),
        'logs_dir': logs_dir(),
        'venv_dir': venv_dir(),
        'celery_broker': celery_broker,
        })
    templating.render(
        'turnip-celery.service.j2',
        '/lib/systemd/system/turnip-celery.service',
        context, perms=0o644)
    if host.service_running('turnip-celery'):
        host.service_stop('turnip-celery')
    reload_systemd()
    if not host.service_resume('turnip-celery'):
        raise RuntimeError('Failed to start turnip-celery')
    return True