from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from twisted.application import (
    service,
    internet,
    )

from turnip.config import TurnipConfig
from turnip.pack.git import PackVirtFactory


def getPackVirtService():
    """Return a PackVirt Service."""

    config = TurnipConfig()
    return internet.TCPServer(
        config.get('pack_virt_port'),
        PackVirtFactory('localhost',
                        config.get('pack_backend_port'),
                        config.get('virtinfo_endpoint')))

application = service.Application("Turnip Pack Virt Service")
getPackVirtService().setServiceParent(application)
