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
    when_not_all,
    )

from charms.turnip.base import (
    configure_service,
    find_git_service,
    publish_website,
    )
from charms.turnip.ssh import write_ssh_keys


@when_not_all('config.set.private_ssh_key', 'config.set.public_ssh_key')
def need_ssh_keys():
    status.blocked('private_ssh_key and public_ssh_key must be configured')


@when('turnip-pack-virt.available')
@when_not('turnip.configured')
def turnip_pack_virt_available():
    turnip_pack_virt = endpoint_from_flag('turnip-pack-virt.available')
    config = hookenv.config()
    address, port = find_git_service(turnip_pack_virt, 'turnip-pack-virt')
    if address is not None and port is not None:
        config['pack_virt_host'] = address
        config['pack_virt_port'] = port
        set_flag('turnip.services.pack-virt')
    else:
        clear_flag('turnip.services.pack-virt')
        status.blocked(
            'turnip-pack-virt must be related to the http interface')


@when('turnip.installed', 'turnip.services.pack-virt',
      'config.set.private_ssh_key', 'config.set.public_ssh_key')
@when_not('turnip.configured')
def configure_turnip():
    write_ssh_keys()
    configure_service('turnip-pack-frontend-ssh')
    set_flag('turnip.configured')
    clear_flag('turnip.nrpe-external-master.published')
    clear_flag('turnip.turnip-pack-frontend-ssh.published')
    status.active('Ready')


@when('nrpe-external-master.available', 'turnip.configured')
@when_not('turnip.nrpe-external-master.published')
def nrpe_available():
    nagios = endpoint_from_flag('nrpe-external-master.available')
    config = hookenv.config()
    nagios.add_check(
        ['/usr/lib/nagios/plugins/check_ssh', '-H', 'localhost',
         str(config['port'])],
        name='check_turnip_pack_frontend_ssh',
        description='Git smart SSH check',
        context=config['nagios_context'])
    set_flag('turnip.nrpe-external-master.published')


@when('turnip.nrpe-external-master.published')
@when_not('nrpe-external-master.available')
def nrpe_unavailable():
    clear_flag('turnip.nrpe-external-master.published')


@when('turnip-pack-frontend-ssh.available', 'turnip.configured')
@when_not('turnip.turnip-pack-frontend-ssh.published')
def turnip_pack_frontend_ssh_available():
    turnip_pack_frontend_ssh = endpoint_from_flag(
        'turnip-pack-frontend-ssh.available')
    publish_website(
        turnip_pack_frontend_ssh, 'turnip-pack-frontend-ssh',
        hookenv.config()['port'], mode='tcp')
    set_flag('turnip.turnip-pack-frontend-ssh.published')


@when('turnip.turnip-pack-frontend-ssh.published')
@when_not('turnip-pack-frontend-ssh.available')
def turnip_pack_frontend_ssh_unavailable():
    clear_flag('turnip.turnip-pack-frontend-ssh.published')
