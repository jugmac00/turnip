# Copyright 2015-2018 Canonical Ltd.  This software is licensed under the
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

import base64
import json

from six.moves import xmlrpc_client
from twisted.internet import (
    defer,
    protocol,
    reactor as default_reactor,
    )
from twisted.protocols import basic
from twisted.web import xmlrpc

from turnip.pack.git import RequestIDLogger


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
        try:
            res = yield self.methods[op](self, val)
        except defer.TimeoutError:
            self.sendValue({"error": "%s timed out" % op})
        else:
            self.sendValue({"result": res})

    def invalidValueReceived(self, string):
        self.sendValue({"error": "Command must be a JSON object"})


class RPCServerFactory(protocol.ServerFactory):

    protocol = RPCServerProtocol

    def __init__(self, methods):
        self.methods = dict(methods)

    def buildProtocol(self, addr):
        return self.protocol(self.methods)


class HookRPCLogContext(object):
    """A context for logging hook RPC operations."""

    log = RequestIDLogger()

    def __init__(self, auth_params):
        self.request_id = auth_params.get('request-id')


class HookRPCHandler(object):
    """A collection of methods for use by git hooks.

    Operations that might execute git hooks generate and register a key
    here, letting the RPC server know what the hook is talking about.
    """

    def __init__(self, virtinfo_url, virtinfo_timeout, reactor=None):
        self.auth_params = {}
        self.ref_paths = {}
        self.ref_permissions = {}
        self.virtinfo_url = virtinfo_url
        self.virtinfo_timeout = virtinfo_timeout
        self.reactor = reactor or default_reactor

    def registerKey(self, key, path, auth_params):
        """Register a key with the given path and auth permissions.

        Hooks identify themselves using this key.
        """
        self.auth_params[key] = auth_params
        self.ref_paths[key] = path
        self.ref_permissions[key] = {}

    def unregisterKey(self, key):
        """Unregister a key."""
        del self.auth_params[key]
        del self.ref_paths[key]
        del self.ref_permissions[key]

    @defer.inlineCallbacks
    def checkRefPermissions(self, proto, args):
        """Get permissions for a set of refs."""
        log_context = HookRPCLogContext(self.auth_params[args['key']])
        auth_params = self.auth_params[args['key']]
        ref_path = self.ref_paths[args['key']]
        # We don't log all the ref paths being checked, since there can be a
        # lot of them.
        log_context.log.info(
            "checkRefPermissions request received: "
            "auth_params={auth_params}, ref_path={ref_path}",
            auth_params=auth_params, ref_path=ref_path)

        cached_permissions = self.ref_permissions[args['key']]
        paths = [
            base64.b64decode(path.encode('UTF-8')) for path in args['paths']]
        missing = [x for x in paths if x not in cached_permissions]
        if missing:
            proxy = xmlrpc.Proxy(self.virtinfo_url, allowNone=True)
            try:
                result = yield proxy.callRemote(
                    b'checkRefPermissions',
                    ref_path,
                    [xmlrpc_client.Binary(path) for path in missing],
                    auth_params).addTimeout(
                        self.virtinfo_timeout, self.reactor)
            except defer.TimeoutError:
                log_context.log.info(
                    "checkRefPermissions virtinfo timed out: "
                    "auth_params={auth_params}, ref_path={ref_path}",
                    auth_params=auth_params, ref_path=ref_path)
                raise
            log_context.log.info(
                "checkRefPermissions virtinfo response: "
                "auth_params={auth_params}, ref_path={ref_path}",
                auth_params=auth_params, ref_path=ref_path)
            for ref, permission in result:
                cached_permissions[ref.data] = permission
        else:
            log_context.log.info(
                "checkRefPermissions returning cached permissions: "
                "auth_params={auth_params}, ref_path={ref_path}",
                auth_params=auth_params, ref_path=ref_path)
        # cached_permissions is a shallow copy of the key index for
        # self.ref_permissions, so changes will be updated in that.
        defer.returnValue(
            {base64.b64encode(ref).decode('UTF-8'): cached_permissions[ref]
             for ref in paths})

    @defer.inlineCallbacks
    def notify(self, path):
        proxy = xmlrpc.Proxy(self.virtinfo_url, allowNone=True)
        yield proxy.callRemote(b'notify', path).addTimeout(
            self.virtinfo_timeout, self.reactor)

    @defer.inlineCallbacks
    def notifyPush(self, proto, args):
        """Notify the virtinfo service about a push."""
        log_context = HookRPCLogContext(self.auth_params[args['key']])
        path = self.ref_paths[args['key']]
        log_context.log.info(
            "notifyPush request received: ref_path={path}", path=path)
        try:
            yield self.notify(path)
        except defer.TimeoutError:
            log_context.log.info(
                "notifyPush timed out: ref_path={path}", path=path)
            raise
        log_context.log.info("notifyPush done: ref_path={path}", path=path)


class HookRPCServerFactory(RPCServerFactory):
    """A JSON netstring RPC interface to a HookRPCHandler."""

    def __init__(self, hookrpc_handler):
        self.hookrpc_handler = hookrpc_handler
        self.methods = {
            'check_ref_permissions': self.hookrpc_handler.checkRefPermissions,
            'notify_push': self.hookrpc_handler.notifyPush,
            }
