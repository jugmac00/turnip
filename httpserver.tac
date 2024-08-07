# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

# You can run this .tac file directly with:
#    twistd -ny httpserver.tac

from twisted.application import internet, service
from twisted.scripts.twistd import ServerOptions
from twisted.web import server

from turnip.config import config
from turnip.log import RotatableFileLogObserver
from turnip.pack.http import SmartHTTPFrontendResource


def getSmartHTTPService():
    """Return a SmartHTTP frontend service."""

    smarthttp_site = server.Site(SmartHTTPFrontendResource(config))
    return internet.TCPServer(
        int(config.get("smart_http_port")), smarthttp_site
    )


options = ServerOptions()
options.parseOptions()

application = service.Application("Turnip SmartHTTP Service")
application.addComponent(
    RotatableFileLogObserver(options.get("logfile")), ignoreClass=1
)
getSmartHTTPService().setServiceParent(application)
