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

from charms.turnip.base import (
    configure_service,
    find_git_service,
    publish_website,
    )


@when('turnip-pack-backend.available')
@when_not('turnip.services.pack-backend')
def turnip_pack_backend_available():
    turnip_pack_backend = endpoint_from_flag('turnip-pack-backend.available')
    config = hookenv.config()
    address, port = find_git_service(
        turnip_pack_backend, 'turnip-pack-backend')
    if address is not None and port is not None:
        config['pack_backend_host'] = address
        config['pack_backend_port'] = port
        set_flag('turnip.services.pack-backend')
    else:
        clear_flag('turnip.services.pack-backend')
        status.blocked(
            'turnip-pack-backend must be related to the http interface')


@when('turnip.installed', 'turnip.services.pack-backend')
@when_not('turnip.configured')
def configure_turnip():
    configure_service('turnip-pack-virt')
    set_flag('turnip.configured')
    clear_flag('turnip.nrpe-external-master.published')
    clear_flag('turnip.turnip-pack-virt.published')
    status.active('Ready')


@when('nrpe-external-master.available', 'turnip.configured')
@when_not('turnip.nrpe-external-master.published')
def nrpe_available():
    nagios = endpoint_from_flag('nrpe-external-master.available')
    config = hookenv.config()
    nagios.add_check(
        ['/usr/lib/nagios/plugins/check_tcp', '-H', 'localhost',
         '-p', str(config['port'])],
        name='check_turnip_pack_virt',
        description='Git pack virt check',
        context=config['nagios_context'])
    set_flag('turnip.nrpe-external-master.published')


@when('turnip.nrpe-external-master.published')
@when_not('nrpe-external-master.available')
def nrpe_unavailable():
    clear_flag('turnip.nrpe-external-master.published')


@when('turnip-pack-virt.available', 'turnip.configured')
@when_not('turnip.turnip-pack-virt.published')
def turnip_pack_virt_available():
    turnip_pack_virt = endpoint_from_flag('turnip-pack-virt.available')
    publish_website(
        turnip_pack_virt, 'turnip-pack-virt', hookenv.config()['port'],
        mode='tcp')
    set_flag('turnip.turnip-pack-virt.published')


@when('turnip.turnip-pack-virt.published')
@when_not('turnip-pack-virt.available')
def turnip_pack_virt_unavailable():
    clear_flag('turnip.turnip-pack-virt.published')
