# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

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
from turnip.pack.hookrpc import (
    HookRPCHandler,
    HookRPCServerFactory,
    )
from turnip.pack.http import SmartHTTPFrontendResource
from turnip.pack.ssh import SmartSSHService

data_dir = os.path.join(
    os.path.dirname(__file__), "turnip", "pack", "tests", "data")
config = TurnipConfig()

LOG_PATH = config.get('turnip_log_dir')
PACK_VIRT_HOST = config.get('pack_virt_host')
PACK_VIRT_PORT = config.get('pack_virt_port')
PACK_BACKEND_HOST = config.get('pack_backend_host')
PACK_BACKEND_PORT = config.get('pack_backend_port')
REPO_STORE = config.get('repo_store')
VIRTINFO_ENDPOINT = config.get('virtinfo_endpoint')

# turnipserver.py is preserved for convenience in development, services
# in production are run in separate processes.
#
# Start a pack storage service on 19418, pointed at by a pack frontend
# on 9418 (the default git:// port), a smart HTTP frontend on 9419, and
# a smart SSH frontend on 9422.

hookrpc_handler = HookRPCHandler(VIRTINFO_ENDPOINT)
hookrpc_path = os.path.join(REPO_STORE, 'hookrpc_sock_%d' % PACK_BACKEND_PORT)
reactor.listenTCP(
    PACK_BACKEND_PORT,
    PackBackendFactory(REPO_STORE,
                       hookrpc_handler,
                       hookrpc_path,
                       VIRTINFO_ENDPOINT))
if os.path.exists(hookrpc_path):
    os.unlink(hookrpc_path)
reactor.listenUNIX(hookrpc_path, HookRPCServerFactory(hookrpc_handler))

reactor.listenTCP(PACK_VIRT_PORT,
                  PackVirtFactory(PACK_BACKEND_HOST,
                                  PACK_BACKEND_PORT,
                                  VIRTINFO_ENDPOINT))
reactor.listenTCP(config.get('pack_frontend_port'),
                  PackFrontendFactory(PACK_VIRT_HOST,
                                      PACK_VIRT_PORT))
smarthttp_site = server.Site(SmartHTTPFrontendResource(config))
reactor.listenTCP(config.get('smart_http_port'), smarthttp_site)
smartssh_service = SmartSSHService(
    PACK_VIRT_HOST, PACK_VIRT_PORT, config.get('authentication_endpoint'),
    private_key_path=config.get('private_ssh_key_path'),
    public_key_path=config.get('public_ssh_key_path'),
    main_log='turnip', access_log=os.path.join(LOG_PATH, 'turnip.access'),
    access_log_path=os.path.join(LOG_PATH, 'turnip-access.log'),
    strport=b'tcp:{}'.format(config.get('smart_ssh_port')),
    moduli_path=config.get('moduli_path'))
smartssh_service.startService()

reactor.run()
