# You can run this .tac file directly with:
#    twistd -ny httpserver.tac
from __future__ import unicode_literals

from twisted.application import service, internet
from twisted.web import server

from turnip.config import TurnipConfig
from turnip.pack.http import SmartHTTPFrontendResource


def getSmartHTTPService():
    """Return a SmartHTTP frontend service."""

    config = TurnipConfig()
    smarthttp_site = server.Site(
        SmartHTTPFrontendResource(b'localhost',
                                  config.get('PACK_VIRT_PORT'),
                                  config.get('VIRTINFO_ENDPOINT')))
    return internet.TCPServer(config.get('SMART_HTTP_PORT'), smarthttp_site)

application = service.Application("Turnip SmartHTTP Service")
service = getSmartHTTPService()
service.setServiceParent(application)
