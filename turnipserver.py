from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os

from twisted.internet import reactor
from twisted.web import server

from turnip.api import TurnipAPIResource
from turnip.packproto import (
    PackBackendFactory,
    PackFrontendFactory,
    PackVirtFactory,
    )
from turnip.smartssh import SmartSSHService
from turnip.smarthttp import SmartHTTPFrontendResource

AUTHENTICATION_ENDPOINT = (
    b'http://xmlrpc-private.launchpad.dev:8087/authserver')
REPO_STORE = '/var/tmp/git.launchpad.dev'
VIRTINFO_ENDPOINT = b'http://localhost:6543/githosting'

data_dir = os.path.join(os.path.dirname(__file__), "turnip", "tests", "data")

# Start a pack storage service on 19418, pointed at by a pack frontend
# on 9418 (the default git:// port), a smart HTTP frontend on 9419, and
# a smart SSH frontend on 9422.  An API service runs on 19417.
reactor.listenTCP(19418, PackBackendFactory(REPO_STORE))
reactor.listenTCP(
    19419, PackVirtFactory('localhost', 19418, VIRTINFO_ENDPOINT))
reactor.listenTCP(9418, PackFrontendFactory('localhost', 19419))
smarthttp_site = server.Site(
    SmartHTTPFrontendResource(b'localhost', 19419, VIRTINFO_ENDPOINT))
reactor.listenTCP(9419, smarthttp_site)
smartssh_service = SmartSSHService(
    b'localhost', 19419, AUTHENTICATION_ENDPOINT,
    private_key_path=os.path.join(data_dir, "ssh-host-key"),
    public_key_path=os.path.join(data_dir, "ssh-host-key.pub"),
    main_log='turnip', access_log='turnip.access',
    access_log_path='turnip-access.log',
    strport=b'tcp:9422')
smartssh_service.startService()

api_site = server.Site(TurnipAPIResource(REPO_STORE))
reactor.listenTCP(19417, api_site)

reactor.run()
