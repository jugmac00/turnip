# You can run this .tac file directly with:
#    twistd -ny packbackendserver.tac

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from twisted.application import (
    service,
    internet,
    )

from turnip.config import TurnipConfig
from turnip.pack.git import PackBackendFactory


def getPackBackendService():
    """Return a PackBackendFactory service."""

    config = TurnipConfig()
    return internet.TCPServer(config.get('pack_backend_port'),
                              PackBackendFactory(config.get('repo_store')))

application = service.Application("Turnip Pack Backend Service")
service = getPackBackendService()
service.setServiceParent(application)
