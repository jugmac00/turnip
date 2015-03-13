from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import json

from twisted.protocols import basic


class JSONNetstringProtocol(basic.NetstringReceiver):

    def stringReceived(self, string):
        self.jsonReceived(json.loads(string))
