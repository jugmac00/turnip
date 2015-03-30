from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import json

from twisted.internet import (
    defer,
    protocol,
    )
from twisted.protocols import basic


class JSONNetstringProtocol(basic.NetstringReceiver):

    def stringReceived(self, string):
        try:
            val = json.loads(string)
        except ValueError:
            self.invalidValueReceived(string)
        else:
            self.valueReceived(val)

    def valueReceived(self, value):
        raise NotImplementedError()

    def invalidValueReceived(self, string):
        raise NotImplementedError()

    def sendValue(self, value):
        self.sendString(json.dumps(value))


class HookRPCServerProtocol(JSONNetstringProtocol):

    def __init__(self, methods):
        self.result_log = []
        self.methods = dict(methods)

    @defer.inlineCallbacks
    def valueReceived(self, val):
        if not isinstance(val, dict):
            self.sendValue({"error": "Command must be a JSON object"})
            return
        val = dict(val)
        op = val.pop('op', None)
        if not op:
            self.sendValue({"error": "No op specified"})
            return
        if op not in self.methods:
            self.sendValue({"error": "Unknown op: %s" % op})
            return
        res = yield self.methods[op](self, val)
        self.sendValue({"result": res})

    def invalidValueReceived(self, string):
        self.sendValue({"error": "Command must be a JSON object"})


class HookRPCServerFactory(protocol.ServerFactory):

    protocol = HookRPCServerProtocol

    def __init__(self, methods):
        self.methods = dict(methods)

    def buildProtocol(self, addr):
        return self.protocol(self.methods)
