from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os

from twisted.internet import reactor
from twisted.web import server

from turnip.api import TurnipAPIResource
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

LOG_PATH = config.get('TURNIP_LOG_DIR')
PACK_VIRT_PORT = config.get('PACK_VIRT_PORT')
PACK_BACKEND_PORT = config.get('PACK_BACKEND_PORT')
REPO_STORE = config.get('REPO_STORE')
VIRTINFO_ENDPOINT = config.get('VIRTINFO_ENDPOINT')

# turnipserver.py is preserved for convenience in development, services
# in production are run in separate processes.
#
# Start a pack storage service on 19418, pointed at by a pack frontend
# on 9418 (the default git:// port), a smart HTTP frontend on 9419, and
# a smart SSH frontend on 9422.  An API service runs on 19417.
reactor.listenTCP(PACK_BACKEND_PORT,
                  PackBackendFactory(REPO_STORE))
reactor.listenTCP(PACK_VIRT_PORT,
                  PackVirtFactory('localhost',
                                  PACK_BACKEND_PORT,
                                  VIRTINFO_ENDPOINT))
reactor.listenTCP(config.get('PACK_FRONTEND_PORT'),
                  PackFrontendFactory('localhost',
                                      PACK_VIRT_PORT))
smarthttp_site = server.Site(
    SmartHTTPFrontendResource(b'localhost', PACK_VIRT_PORT, VIRTINFO_ENDPOINT))
reactor.listenTCP(config.get('SMART_HTTP_PORT'), smarthttp_site)
smartssh_service = SmartSSHService(
    b'localhost', 19419, config.get('AUTHENTICATION_ENDPOINT'),
    private_key_path=os.path.join(data_dir, "ssh-host-key"),
    public_key_path=os.path.join(data_dir, "ssh-host-key.pub"),
    main_log='turnip', access_log=os.path.join(LOG_PATH, 'turnip.access'),
    access_log_path=os.path.join(LOG_PATH, 'turnip-access.log'),
    strport=b'tcp:{}'.format(config.get('SMART_SSH_PORT')))
smartssh_service.startService()

api_site = server.Site(TurnipAPIResource(REPO_STORE))
reactor.listenTCP(config.get('REPO_API_PORT'), api_site)

reactor.run()
