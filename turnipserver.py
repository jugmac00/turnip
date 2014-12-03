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
    PackVirtFactory,
    )
from turnip.smarthttp import (
    SmartHTTPFrontendResource,
    )

REPO_STORE = '/var/tmp/git.launchpad.dev'
VIRTINFO_ENDPOINT = b'http://localhost:6543/githosting'

# Start a pack storage service on 19418, pointed at by a pack frontend
# on 9418 (the default git:// port) and a smart HTTP frontend on 9419.
# An API service runs on 19417.
reactor.listenTCP(19418, PackBackendFactory(REPO_STORE))
reactor.listenTCP(
    19419, PackVirtFactory('localhost', 19418, VIRTINFO_ENDPOINT))
reactor.listenTCP(9418, PackFrontendFactory('localhost', 19419))
smarthttp_site = server.Site(SmartHTTPFrontendResource(b'localhost', 19419))
reactor.listenTCP(9419, smarthttp_site)

api_site = server.Site(TurnipAPIResource(REPO_STORE))
reactor.listenTCP(19417, api_site)

reactor.run()
