# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

# You can run this .tac file directly with:
#    twistd -ny sshserver.tac

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os

from twisted.application import service
from twisted.scripts.twistd import ServerOptions

from turnip.config import TurnipConfig
from turnip.log import RotatableFileLogObserver
from turnip.pack.ssh import SmartSSHService


def getSmartSSHService():

    config = TurnipConfig()
    log_path = config.get('turnip_log_dir')

    return SmartSSHService(
        b'localhost', config.get('pack_virt_port'),
        config.get('authentication_endpoint'),
        private_key_path=config.get('private_ssh_key_path'),
        public_key_path=config.get('public_ssh_key_path'),
        # XXX cjwatson 2015-04-25: Should we just send access log
        # information to the main log?  Requires lazr.sshserver changes.
        main_log='turnip', access_log=os.path.join(log_path, 'turnip.access'),
        access_log_path=os.path.join(log_path, 'turnip-access.log'),
        strport=b'tcp:{}'.format(config.get('smart_ssh_port')))


options = ServerOptions()
options.parseOptions()

application = service.Application("Turnip SmartSSH Service")
application.addComponent(
    RotatableFileLogObserver(options.get('logfile')), ignoreClass=1)
getSmartSSHService().setServiceParent(application)
