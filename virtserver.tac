# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

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
from turnip.pack.git import PackVirtFactory


def getPackVirtService():
    """Return a PackVirt Service."""

    config = TurnipConfig()
    return internet.TCPServer(
        config.get('pack_virt_port'),
        PackVirtFactory('localhost',
                        config.get('pack_backend_port'),
                        config.get('virtinfo_endpoint')))


options = ServerOptions()
options.parseOptions()

application = service.Application("Turnip Pack Virt Service")
application.addComponent(
    RotatableFileLogObserver(options.get('logfile')), ignoreClass=1)
getPackVirtService().setServiceParent(application)
