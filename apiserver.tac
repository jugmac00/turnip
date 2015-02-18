# You can run this .tac file directly with:
#    twistd -ny apiserver.tac
from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from twisted.application import (
    service,
    internet,
    )
from twisted.web import server

from turnip.api import TurnipAPIResource
from turnip.config import TurnipConfig


def getAPIService():
    """Return an API service."""

    config = TurnipConfig()
    api_site = server.Site(TurnipAPIResource(config.get('repo_store')))
    return internet.TCPServer(config.get('repo_api_port'), api_site)

application = service.Application("Turnip API Service")
service = getAPIService()
service.setServiceParent(application)
