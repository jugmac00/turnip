# Copyright 2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import absolute_import, print_function, unicode_literals

from charmhelpers.core import hookenv
from charmhelpers.fetch import apt_install
from charms.reactive import (
    any_flags_set,
    clear_flag,
    data_changed,
    endpoint_from_flag,
    set_flag,
    toggle_flag,
    when,
    when_any,
    when_not,
    )

from charms.turnip.base import data_dir
from charms.turnip.storage import (
    ensure_mounted,
    ensure_repo_store_writable,
    mount_data,
    unmount_data,
    )


def update_storage_available():
    toggle_flag(
        'turnip.storage.available',
        any_flags_set('turnip.storage.internal', 'turnip.storage.nfs'))


@when('turnip.created_user')
@when_not('turnip.storage.initialised')
def initial_request():
    if hookenv.config()['nfs']:
        apt_install('nfs-common', fatal=True)
        clear_flag('turnip.storage.internal')
    else:
        ensure_repo_store_writable()
        set_flag('turnip.storage.internal')
    clear_flag('turnip.storage.nfs')
    set_flag('turnip.storage.initialised')
    update_storage_available()


@when('nfs.joined')
@when_not('turnip.storage.nfs-requested')
def nfs_joined():
    if not hookenv.config()['nfs']:
        return
    nfs = endpoint_from_flag('nfs.joined')
    nfs.set_export_name('turnip')
    set_flag('turnip.storage.nfs-requested')


@when('nfs.available')
def nfs_available():
    if not hookenv.config()['nfs']:
        return
    nfs = endpoint_from_flag('nfs.available')
    mount_info = None
    for mount in nfs.mounts():
        if mount['mount_name'] != 'turnip':
            continue
        if mount['mounts']:
            mount_info = mount['mounts'][0]
            # We only handle one related NFS unit.
            break
    # Unmount and/or mount storage as necessary.  Note that if we previously
    # had valid mount information and now have different valid mount
    # information then we need to do both operations.
    if data_changed('turnip.storage.mount-info', mount_info):
        unmount_data()
        if mount_info is not None:
            mount_data(mount_info)
            ensure_repo_store_writable()
        toggle_flag('turnip.storage.nfs', mount_info is not None)
        update_storage_available()
    # If mount-info didn't changed but is present, let's just make sure that
    # the data storage is mounted.
    elif mount_info is not None:
        ensure_mounted()
        ensure_repo_store_writable()
        set_flag('turnip.storage.nfs')
        update_storage_available()


@when_any('turnip.storage.nfs-requested', 'turnip.storage.nfs')
@when_not('nfs.joined')
def nfs_departed():
    if data_changed('turnip.storage.mount-info', None):
        unmount_data()
    clear_flag('turnip.storage.nfs-requested')
    clear_flag('turnip.storage.nfs')
    update_storage_available()


@when('config.changed.nfs')
def nfs_config_changed():
    clear_flag('turnip.storage.internal')
    clear_flag('turnip.storage.nfs')
    clear_flag('turnip.storage.initialised')
    clear_flag('turnip.storage.nfs-requested')
    update_storage_available()


@when('nrpe-external-master.available', 'turnip.configured')
@when_not('turnip.storage.nrpe-external-master.published')
def nrpe_available():
    nagios = endpoint_from_flag('nrpe-external-master.available')
    config = hookenv.config()
    # XXX cjwatson 2018-12-11: We perhaps don't need this check on every
    # unit that consumes the same storage; perhaps it could live on the
    # storage backend instead.  However, this is easier to arrange for now.
    nagios.add_check(
        ['/usr/lib/nagios/plugins/check_disk', '-u', 'GB',
         '-w', '10%', '-c', '5%', '-K', '5%', '-p', data_dir()],
        name='check_disk_data',
        description='Disk space on {}'.format(data_dir()),
        context=config['nagios_context'])
    set_flag('turnip.storage.nrpe-external-master.published')


@when('turnip.storage.nrpe-external-master.published')
@when_not('nrpe-external-master.available')
def nrpe_unavailable():
    clear_flag('turnip.storage.nrpe-external-master.published')
