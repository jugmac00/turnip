# You can run this .tac file directly with:
#    twistd -ny sshserver.tac

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os

from twisted.application import service

from turnip.config import TurnipConfig
from turnip.pack.ssh import SmartSSHService


def getSmartSSHService():

    config = TurnipConfig()
    data_dir = os.path.join(
        os.path.dirname(__file__), "turnip", "pack", "tests", "data")
    log_path = config.get('TURNIP_LOG_DIR')

    return SmartSSHService(
        b'localhost', config.get('PACK_VIRT_PORT'),
        config.get('AUTHENTICATION_ENDPOINT'),
        private_key_path=os.path.join(data_dir, "ssh-host-key"),
        public_key_path=os.path.join(data_dir, "ssh-host-key.pub"),
        main_log='turnip', access_log=os.path.join(log_path, 'turnip.access'),
        access_log_path=os.path.join(log_path, 'turnip-access.log'),
        strport=b'tcp:{}'.format(config.get('SMART_SSH_PORT')))


application = service.Application("Turnip SmartSSH Service")
service = getSmartSSHService()
service.setServiceParent(application)
