# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from twisted.application import internet, service
from twisted.scripts.twistd import ServerOptions

from turnip.config import config
from turnip.log import RotatableFileLogObserver
from turnip.pack.git import PackVirtFactory


def getPackVirtService():
    """Return a PackVirt Service."""

    return internet.TCPServer(
        int(config.get("pack_virt_port")),
        PackVirtFactory(
            config.get("pack_backend_host"),
            int(config.get("pack_backend_port")),
            config.get("virtinfo_endpoint"),
            int(config.get("virtinfo_timeout")),
        ),
    )


options = ServerOptions()
options.parseOptions()

application = service.Application("Turnip Pack Virt Service")
application.addComponent(
    RotatableFileLogObserver(options.get("logfile")), ignoreClass=1
)
getPackVirtService().setServiceParent(application)
