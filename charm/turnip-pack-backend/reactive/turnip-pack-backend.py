# Copyright 2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from charmhelpers.core import hookenv
from charms.layer import status
from charms.reactive import (
    clear_flag,
    endpoint_from_flag,
    set_flag,
    when,
    when_not,
    )

from charms.turnip.base import (
    configure_service,
    deconfigure_service,
    publish_website,
    )


@when('turnip.installed', 'turnip.storage.available')
@when_not('turnip.configured')
def configure_turnip():
    configure_service('turnip-pack-backend')
    set_flag('turnip.configured')
    clear_flag('turnip.storage.nrpe-external-master.published')
    clear_flag('turnip.nrpe-external-master.published')
    clear_flag('turnip.turnip-pack-backend.published')
    status.active('Ready')


@when('turnip.configured')
@when_not('turnip.storage.available')
def deconfigure_turnip():
    deconfigure_service('turnip-pack-backend')
    clear_flag('turnip.configured')
    status.blocked('Waiting for storage to be available')


@when('nrpe-external-master.available', 'turnip.configured')
@when_not('turnip.nrpe-external-master.published')
def nrpe_available():
    nagios = endpoint_from_flag('nrpe-external-master.available')
    config = hookenv.config()
    nagios.add_check(
        ['/usr/lib/nagios/plugins/check_tcp', '-H', 'localhost',
         '-p', str(config['port'])],
        name='check_turnip_pack_backend',
        description='Git pack backend check',
        context=config['nagios_context'])
    set_flag('turnip.nrpe-external-master.published')


@when('turnip.nrpe-external-master.published')
@when_not('nrpe-external-master.available')
def nrpe_unavailable():
    clear_flag('turnip.nrpe-external-master.published')


@when('turnip-pack-backend.available', 'turnip.configured')
@when_not('turnip.turnip-pack-backend.published')
def turnip_pack_backend_available():
    turnip_pack_backend = endpoint_from_flag('turnip-pack-backend.available')
    publish_website(
        turnip_pack_backend, 'turnip-pack-backend', hookenv.config()['port'])
    set_flag('turnip.turnip-pack-backend.published')


@when('turnip.turnip-pack-backend.published')
@when_not('turnip-pack-backend.available')
def turnip_pack_backend_unavailable():
    clear_flag('turnip.turnip-pack-backend.published')
