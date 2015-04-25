# You can run this .tac file directly with:
#    twistd -ny packfrontendserver.tac

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
from turnip.pack.git import PackFrontendFactory


def getPackFrontendService():
    """Return a PackFrontend Service."""

    config = TurnipConfig()
    return internet.TCPServer(
        config.get('pack_frontend_port'),
        PackFrontendFactory('localhost',
                            config.get('pack_virt_port')))

application = service.Application("Turnip Pack Frontend Service")
getPackFrontendService().setServiceParent(application)
