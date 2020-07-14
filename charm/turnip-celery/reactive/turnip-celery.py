# Copyright 2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import absolute_import, print_function, unicode_literals

from charmhelpers.core import hookenv
from charms.layer import status
from charms.reactive import (
    clear_flag,
    endpoint_from_flag,
    set_flag,
    when,
    when_not,
    )

from charms.turnip.celery import configure_celery
from charms.turnip.base import (
    configure_service,
    deconfigure_service,
    )


@when('turnip.installed', 'turnip.storage.available')
@when_not('turnip.configured')
def configure_turnip():
    configure_service()
    set_flag('turnip.configured')
    clear_flag('turnip.storage.nrpe-external-master.published')
    clear_flag('turnip.nrpe-external-master.published')
    clear_flag('turnip.turnip-celery.published')
    status.active('Ready')


@when('turnip.configured')
@when_not('turnip.storage.available')
def deconfigure_turnip():
    deconfigure_service('turnip-celery')
    clear_flag('turnip.configured')
    status.blocked('Waiting for storage to be available')


@when('amqp.connected')
def rabbitmq_available():
    configure_celery()
    status.active('Ready')


@when('nrpe-external-master.available', 'turnip.configured')
@when_not('turnip.nrpe-external-master.published')
def nrpe_available():
    nagios = endpoint_from_flag('nrpe-external-master.available')
    config = hookenv.config()
    nagios.add_check(
        ['/usr/lib/nagios/plugins/check_http', '-H', 'localhost',
         '-p', str(config['port']), '-j', 'OPTIONS', '-u', '/repo'],
        name='check_api',
        description='Git API check',
        context=config['nagios_context'])
    set_flag('turnip.nrpe-external-master.published')


@when('turnip.nrpe-external-master.published')
@when_not('nrpe-external-master.available')
def nrpe_unavailable():
    clear_flag('turnip.nrpe-external-master.published')
