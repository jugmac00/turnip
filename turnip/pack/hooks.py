from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import json

from twisted.protocols import basic


class JSONNetstringProtocol(basic.NetstringReceiver):

    def stringReceived(self, string):
        try:
            val = json.loads(string)
        except ValueError:
            return self.invalidValueReceived(string)
        else:
            return self.valueReceived(val)

    def valueReceived(self, value):
        raise NotImplementedError()

    def invalidValueReceived(self, string):
        raise NotImplementedError()
