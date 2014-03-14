from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from twisted.internet import reactor
from twisted.web import server

from turnip.http import TurnipAPIResource
from turnip.protocols import (
    PackBackendFactory,
    PackFrontendFactory,
    )

REPO_STORE = '/var/tmp/git.launchpad.dev'
GITHOSTING_ENDPOINT = b'http://xmlrpc-private.launchpad.dev:8087/githosting'

# Start a backend on 9419, pointed at by a frontend on 9418 (the
# default git:// port).
reactor.listenTCP(9419, PackBackendFactory(REPO_STORE))
reactor.listenTCP(
    9418, PackFrontendFactory('localhost', 9419, GITHOSTING_ENDPOINT))

api_site = server.Site(TurnipAPIResource(REPO_STORE))
reactor.listenTCP(9420, api_site)
reactor.run()
