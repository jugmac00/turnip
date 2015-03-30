from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os

from twisted.internet import reactor
from twisted.web import server

from turnip.config import TurnipConfig
from turnip.pack.git import (
    PackBackendFactory,
    PackFrontendFactory,
    PackVirtFactory,
    )
from turnip.pack.http import SmartHTTPFrontendResource
from turnip.pack.ssh import SmartSSHService

data_dir = os.path.join(
    os.path.dirname(__file__), "turnip", "pack", "tests", "data")
config = TurnipConfig()

LOG_PATH = config.get('turnip_log_dir')
PACK_VIRT_PORT = config.get('pack_virt_port')
PACK_BACKEND_PORT = config.get('pack_backend_port')
REPO_STORE = config.get('repo_store')
VIRTINFO_ENDPOINT = config.get('virtinfo_endpoint')
CGIT_EXEC_PATH = config.get('cgit_exec_path')
CGIT_DATA_PATH = config.get('cgit_data_path')

# turnipserver.py is preserved for convenience in development, services
# in production are run in separate processes.
#
# Start a pack storage service on 19418, pointed at by a pack frontend
# on 9418 (the default git:// port), a smart HTTP frontend on 9419, and
# a smart SSH frontend on 9422.
reactor.listenTCP(PACK_BACKEND_PORT,
                  PackBackendFactory(REPO_STORE))
reactor.listenTCP(PACK_VIRT_PORT,
                  PackVirtFactory('localhost',
                                  PACK_BACKEND_PORT,
                                  VIRTINFO_ENDPOINT))
reactor.listenTCP(config.get('pack_frontend_port'),
                  PackFrontendFactory('localhost',
                                      PACK_VIRT_PORT))
smarthttp_site = server.Site(
    SmartHTTPFrontendResource(
        b'localhost', PACK_VIRT_PORT, VIRTINFO_ENDPOINT, REPO_STORE,
        cgit_exec_path=CGIT_EXEC_PATH, cgit_data_path=CGIT_DATA_PATH))
reactor.listenTCP(config.get('smart_http_port'), smarthttp_site)
smartssh_service = SmartSSHService(
    b'localhost', PACK_VIRT_PORT, config.get('authentication_endpoint'),
    private_key_path=os.path.join(data_dir, "ssh-host-key"),
    public_key_path=os.path.join(data_dir, "ssh-host-key.pub"),
    main_log='turnip', access_log=os.path.join(LOG_PATH, 'turnip.access'),
    access_log_path=os.path.join(LOG_PATH, 'turnip-access.log'),
    strport=b'tcp:{}'.format(config.get('smart_ssh_port')))
smartssh_service.startService()

reactor.run()
