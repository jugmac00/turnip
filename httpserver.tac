# You can run this .tac file directly with:
#    twistd -ny httpserver.tac
from __future__ import unicode_literals

from twisted.application import (
    service,
    internet,
    )
from twisted.web import server

from turnip.config import TurnipConfig
from turnip.pack.http import SmartHTTPFrontendResource


def getSmartHTTPService():
    """Return a SmartHTTP frontend service."""

    config = TurnipConfig()
    smarthttp_site = server.Site(
        SmartHTTPFrontendResource(b'localhost',
                                  config.get('pack_virt_port'),
                                  config.get('virtinfo_endpoint'),
                                  config.get('repo_store'),
                                  cgit_exec_path=config.get('cgit_exec_path'),
                                  cgit_data_path=config.get('cgit_data_path'),
                                  site_name=config.get('site_name')))
    return internet.TCPServer(config.get('smart_http_port'), smarthttp_site)

application = service.Application("Turnip SmartHTTP Service")
getSmartHTTPService().setServiceParent(application)
