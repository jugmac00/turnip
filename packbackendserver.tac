# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

# You can run this .tac file directly with:
#    twistd -ny packbackendserver.tac

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os.path

from twisted.application import (
    service,
    internet,
    )
from twisted.scripts.twistd import ServerOptions

from turnip.config import TurnipConfig
from turnip.log import RotatableFileLogObserver
from turnip.pack.git import PackBackendFactory
from turnip.pack.hookrpc import (
    HookRPCHandler,
    HookRPCServerFactory,
    )


def getPackBackendServices():
    """Return PackBackendFactory and HookRPC services."""

    config = TurnipConfig()
    repo_store = config.get('repo_store')
    pack_backend_port = config.get('pack_backend_port')
    hookrpc_handler = HookRPCHandler(config.get('virtinfo_endpoint'))
    hookrpc_path = os.path.join(
        repo_store, 'hookrpc_sock_%d' % pack_backend_port)
    pack_backend_service = internet.TCPServer(
        pack_backend_port,
        PackBackendFactory(repo_store, hookrpc_handler, hookrpc_path))
    if os.path.exists(hookrpc_path):
        os.unlink(hookrpc_path)
    hookrpc_service = internet.UNIXServer(
        hookrpc_path, HookRPCServerFactory(hookrpc_handler))
    return pack_backend_service, hookrpc_service


options = ServerOptions()
options.parseOptions()

application = service.Application("Turnip Pack Backend Service")
application.addComponent(
    RotatableFileLogObserver(options.get('logfile')), ignoreClass=1)
pack_backend_service, hookrpc_service = getPackBackendServices()
pack_backend_service.setServiceParent(application)
hookrpc_service.setServiceParent(application)
