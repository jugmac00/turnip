from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from twisted.internet import reactor
from twisted.web import server

from turnip.api import TurnipAPIResource
from turnip.packproto import (
    PackBackendFactory,
    PackFrontendFactory,
    )
from turnip.smarthttp import (
    SmartHTTPFrontendResource,
    )

REPO_STORE = '/var/tmp/git.launchpad.dev'
VIRTINFO_ENDPOINT = b'http://xmlrpc-private.launchpad.dev:8087/githosting'

# Start a backend on 9419, pointed at by a pack frontend on 9418 (the
# default git:// port) and a smart HTTP frontend on 9421.
reactor.listenTCP(9419, PackBackendFactory(REPO_STORE))
reactor.listenTCP(
    9418, PackFrontendFactory('localhost', 9419, VIRTINFO_ENDPOINT))
smarthttp_site = server.Site(
    SmartHTTPFrontendResource(b'localhost', 9419, VIRTINFO_ENDPOINT))
reactor.listenTCP(9421, smarthttp_site)

api_site = server.Site(TurnipAPIResource(REPO_STORE))
reactor.listenTCP(9420, api_site)

reactor.run()
