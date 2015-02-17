# You can run this .tac file directly with:
#    twistd -ny packfrontendserver.tac

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from twisted.application import service, internet

from turnip.config import TurnipConfig
from turnip.pack.git import PackFrontendFactory


def getPackFrontendService():
    """Return a PackFrontend Service."""

    config = TurnipConfig()
    return internet.TCPServer(
        config.get('PACK_FRONTEND_PORT'),
        PackFrontendFactory('localhost',
                            config.get('PACK_VIRT_PORT')))

application = service.Application("Turnip Pack Frontend Service")
service = getPackFrontendService()
service.setServiceParent(application)
