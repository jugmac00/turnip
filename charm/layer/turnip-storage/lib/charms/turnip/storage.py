# Copyright 2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import absolute_import, print_function, unicode_literals

import os
import subprocess

from charmhelpers.core import (
    host,
    templating,
    )

from charms.turnip.base import (
    data_dir,
    data_mount_unit,
    group_id,
    reload_systemd,
    user_id,
    )


def mount_data(mount_info):
    # We use a systemd.mount(5) unit rather than a line in /etc/fstab partly
    # because it's easier to deal with a file we can completely overwrite,
    # and partly because this lets us automatically stop and start services
    # that require the mount.
    data_mount = data_mount_unit()
    data_mount_conf = '/lib/systemd/system/{}'.format(data_mount)
    context = dict(mount_info)
    context['data_dir'] = data_dir()
    templating.render('data.mount.j2', data_mount_conf, context, perms=0o644)
    reload_systemd()
    host.service('unmask', data_mount)
    host.service_restart(data_mount)
    # systemctl shouldn't return successfully unless the mount completed,
    # but let's make sure.
    subprocess.check_call(['mountpoint', '-q', data_dir()])


def unmount_data():
    data_mount = data_mount_unit()
    if host.service_running(data_mount):
        host.service_stop(data_mount)
    host.service('mask', data_mount)
    reload_systemd()


def ensure_repo_store_writable():
    os.chown(data_dir(), user_id(), group_id())
    os.chmod(data_dir(), 0o755)
    repo_store = os.path.join(data_dir(), 'repos')
    if not os.path.exists(repo_store):
        os.makedirs(repo_store)
    os.chown(repo_store, user_id(), group_id())
    os.chmod(repo_store, 0o755)
