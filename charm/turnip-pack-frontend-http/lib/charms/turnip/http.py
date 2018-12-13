# Copyright 2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import absolute_import, print_function, unicode_literals

import base64
import os.path

from charmhelpers.core import (
    hookenv,
    host,
    templating,
    )

from charms.turnip.base import keys_dir


def configure_cgit():
    config = hookenv.config()
    if config['cgit_secret']:
        cgit_secret_path = os.path.join(keys_dir(), 'cgit-secret')
        hookenv.log('Writing cgit session secret from config to: {}'.format(
            cgit_secret_path))
        host.write_file(
            cgit_secret_path, base64.b64decode(config['cgit_secret']),
            perms=0o644)
        config['cgit_secret_path'] = cgit_secret_path
        config.save()
    templating.render(
        'sudoers-cgit.j2', '/etc/sudoers.d/turnip-cgit', config, perms=0o440)
    templating.render(
        'cgitwrap.j2', '/usr/local/bin/cgitwrap', config, perms=0o755)
