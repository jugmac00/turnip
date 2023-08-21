# Copyright 2015-2020 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

# You can run this .tac file directly with:
#    twistd -ny packbackendserver.tac

import os.path

import statsd
from twisted.application import internet, service
from twisted.scripts.twistd import ServerOptions

from turnip.config import config
from turnip.log import RotatableFileLogObserver
from turnip.pack.git import PackBackendFactory
from turnip.pack.hookrpc import HookRPCHandler, HookRPCServerFactory


def getPackBackendServices():
    """Return PackBackendFactory and HookRPC services."""

    repo_store = config.get("repo_store")
    pack_backend_port = int(config.get("pack_backend_port"))
    hookrpc_handler = HookRPCHandler(
        config.get("virtinfo_endpoint"), int(config.get("virtinfo_timeout"))
    )
    hookrpc_path = config.get("hookrpc_path") or repo_store
    hookrpc_sock_path = os.path.join(
        hookrpc_path, "hookrpc_sock_%d" % pack_backend_port
    )
    statsd_host = config.get("statsd_host")
    statsd_port = config.get("statsd_port")
    statsd_prefix = config.get("statsd_prefix")
    if statsd_host and statsd_port and statsd_prefix:
        statsd_client = statsd.StatsClient(
            statsd_host, statsd_port, statsd_prefix
        )
    else:
        statsd_client = None
    pack_backend_service = internet.TCPServer(
        pack_backend_port,
        PackBackendFactory(
            repo_store, hookrpc_handler, hookrpc_sock_path, statsd_client
        ),
    )
    if os.path.exists(hookrpc_sock_path):
        os.unlink(hookrpc_sock_path)
    hookrpc_service = internet.UNIXServer(
        hookrpc_sock_path, HookRPCServerFactory(hookrpc_handler)
    )
    return pack_backend_service, hookrpc_service


options = ServerOptions()
options.parseOptions()

application = service.Application("Turnip Pack Backend Service")
application.addComponent(
    RotatableFileLogObserver(options.get("logfile")), ignoreClass=1
)
pack_backend_service, hookrpc_service = getPackBackendServices()
pack_backend_service.setServiceParent(application)
hookrpc_service.setServiceParent(application)
