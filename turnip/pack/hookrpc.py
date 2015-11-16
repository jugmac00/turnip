# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""RPC server for Git hooks.

Hooks invoked by PackBackend's git children need to interact with the
outside world (eg. reading ref restrictions or notifying about pushes),
but they can't communicate with the virtinfo service directly.
PackBackend makes these operations available to hooks through this RPC
server.

The RPC server runs on a UNIX socket, with all messages encoded as JSON
netstrings. Hooks authenticate using a secret key from their
environment.
"""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import json
import sys

from twisted.internet import (
    defer,
    protocol,
    )
from twisted.protocols import basic
# twisted.web.xmlrpc doesn't exist for Python 3 yet, but the non-XML-RPC
# bits of this module work.
if sys.version_info.major < 3:
    from twisted.web import xmlrpc


class JSONNetstringProtocol(basic.NetstringReceiver):
    """A protocol that sends and receives JSON as netstrings."""

    def stringReceived(self, string):
        try:
            val = json.loads(string.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            self.invalidValueReceived(string)
        else:
            self.valueReceived(val)

    def valueReceived(self, value):
        raise NotImplementedError()

    def invalidValueReceived(self, string):
        raise NotImplementedError()

    def sendValue(self, value):
        self.sendString(json.dumps(value).encode('utf-8'))


class RPCServerProtocol(JSONNetstringProtocol):
    """An RPC server using JSON netstrings.

    Takes a dict mapping method names to functions.
    """

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


class RPCServerFactory(protocol.ServerFactory):

    protocol = RPCServerProtocol

    def __init__(self, methods):
        self.methods = dict(methods)

    def buildProtocol(self, addr):
        return self.protocol(self.methods)


class HookRPCHandler(object):
    """A collection of methods for use by git hooks.

    Operations that might execute git hooks generate and register a key
    here, letting the RPC server know what the hook is talking about.
    """

    def __init__(self, virtinfo_url):
        self.ref_paths = {}
        self.ref_rules = {}
        self.virtinfo_url = virtinfo_url

    def registerKey(self, key, path, ref_rules):
        """Register a key with the given path and ref_rules.

        Hooks identify themselves using this key.
        """
        self.ref_paths[key] = path
        self.ref_rules[key] = ref_rules

    def unregisterKey(self, key):
        """Unregister a key."""
        del self.ref_rules[key]
        del self.ref_paths[key]

    def listRefRules(self, proto, args):
        """Get forbidden ref path patterns (as Unicode)."""
        return [rule.decode('utf-8') for rule in self.ref_rules[args['key']]]

    @defer.inlineCallbacks
    def notifyPush(self, proto, args):
        """Notify the virtinfo service about a push."""
        path = self.ref_paths[args['key']]
        proxy = xmlrpc.Proxy(self.virtinfo_url, allowNone=True)
        yield proxy.callRemote(b'notify', path)


class HookRPCServerFactory(RPCServerFactory):
    """A JSON netstring RPC interface to a HookRPCHandler."""

    def __init__(self, hookrpc_handler):
        self.hookrpc_handler = hookrpc_handler
        self.methods = {
            'list_ref_rules': self.hookrpc_handler.listRefRules,
            'notify_push': self.hookrpc_handler.notifyPush,
            }
