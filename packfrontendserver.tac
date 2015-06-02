# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

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
from twisted.scripts.twistd import ServerOptions

from turnip.config import TurnipConfig
from turnip.log import RotatableFileLogObserver
from turnip.pack.git import PackFrontendFactory


def getPackFrontendService():
    """Return a PackFrontend Service."""

    config = TurnipConfig()
    return internet.TCPServer(
        config.get('pack_frontend_port'),
        PackFrontendFactory('localhost',
                            config.get('pack_virt_port')))


options = ServerOptions()
options.parseOptions()

application = service.Application("Turnip Pack Frontend Service")
application.addComponent(
    RotatableFileLogObserver(options.get('logfile')), ignoreClass=1)
getPackFrontendService().setServiceParent(application)
