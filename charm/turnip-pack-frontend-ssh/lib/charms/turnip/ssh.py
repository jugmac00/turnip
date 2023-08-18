# Copyright 2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import absolute_import, print_function, unicode_literals

import base64
import os.path

from charmhelpers.core import hookenv, host

from charms.turnip.base import keys_dir


def write_ssh_keys():
    config = hookenv.config()

    private_ssh_key_path = os.path.join(keys_dir(), "ssh-host-key")
    hookenv.log(
        "Writing private ssh key from config to: {}".format(
            private_ssh_key_path
        )
    )
    host.write_file(
        private_ssh_key_path,
        base64.b64decode(config["private_ssh_key"]),
        perms=0o644,
    )
    config["private_ssh_key_path"] = private_ssh_key_path

    public_ssh_key_path = os.path.join(keys_dir(), "ssh-host-key.pub")
    hookenv.log(
        "Writing public ssh key from config to: {}".format(public_ssh_key_path)
    )
    host.write_file(
        public_ssh_key_path,
        base64.b64decode(config["public_ssh_key"]),
        perms=0o644,
    )
    config["public_ssh_key_path"] = public_ssh_key_path

    config.save()
