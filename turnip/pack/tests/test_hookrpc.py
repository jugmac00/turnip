# Copyright 2015-2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import contextlib
import uuid

from testtools import TestCase
from testtools.deferredruntest import AsynchronousDeferredRunTest
from twisted.internet import (
    defer,
    reactor,
    )
from twisted.test import proto_helpers
from twisted.web import server

from turnip.pack import hookrpc
from turnip.pack.tests.fake_servers import FakeVirtInfoService


class DummyJSONNetstringProtocol(hookrpc.JSONNetstringProtocol):

    response_deferred = None

    def __init__(self):
        self.test_value_log = []
        self.test_invalid_log = []

    def valueReceived(self, val):
        self.test_value_log.append(val)

    def invalidValueReceived(self, string):
        self.test_invalid_log.append(string)

    def sendValue(self, value):
        # Hack to allow tests to block until a response is sent, since
        # dataReceived can't return a Deferred without breaking things.
        hookrpc.JSONNetstringProtocol.sendValue(self, value)
        if self.response_deferred is not None:
            d = self.response_deferred
            self.response_deferred = None
            d.callback()


class TestJSONNetStringProtocol(TestCase):
    """Test the JSON netstring protocol."""

    def setUp(self):
        super(TestJSONNetStringProtocol, self).setUp()
        self.proto = DummyJSONNetstringProtocol()
        self.transport = proto_helpers.StringTransportWithDisconnection()
        self.transport.protocol = self.proto
        self.proto.makeConnection(self.transport)

    def test_calls_valueReceived(self):
        # A valid netstring containing valid JSON is given to
        # valueReceived.
        self.proto.dataReceived(b'14:{"foo": "bar"},')
        self.proto.dataReceived(b'19:[{"it": ["works"]}],')
        self.assertEqual(
            [{"foo": "bar"}, [{"it": ["works"]}]],
            self.proto.test_value_log)

    def test_calls_invalidValueReceived(self):
        # A valid nestring containing invalid JSON calls
        # invalidValueReceived. Framing is preserved, so the connection
        # need not be destroyed.
        self.proto.dataReceived(b'12:{"foo": "bar,')
        self.proto.dataReceived(b'3:"ga,')
        self.assertEqual([], self.proto.test_value_log)
        self.assertEqual(
            [b'{"foo": "bar', b'"ga'], self.proto.test_invalid_log)

    def test_sendValue(self):
        # sendValue serialises to JSON and encodes as a netstring.
        self.proto.sendValue({"yay": "it works"})
        self.assertEqual(b'19:{"yay": "it works"},', self.transport.value())


def async_rpc_method(proto, args):
    d = defer.Deferred()
    d.callback(list(args.items()))
    return d


def sync_rpc_method(proto, args):
    return list(args.items())


class TestRPCServerProtocol(TestCase):
    """Test the socket server that handles git hook callbacks."""

    def setUp(self):
        super(TestRPCServerProtocol, self).setUp()
        self.proto = hookrpc.RPCServerProtocol({
            'sync': sync_rpc_method,
            'async': async_rpc_method,
            })
        self.transport = proto_helpers.StringTransportWithDisconnection()
        self.transport.protocol = self.proto
        self.proto.makeConnection(self.transport)

    def test_call_sync(self):
        self.proto.dataReceived(b'28:{"op": "sync", "bar": "baz"},')
        self.assertEqual(
            b'28:{"result": [["bar", "baz"]]},', self.transport.value())

    def test_call_async(self):
        self.proto.dataReceived(b'29:{"op": "async", "bar": "baz"},')
        self.assertEqual(
            b'28:{"result": [["bar", "baz"]]},', self.transport.value())

    def test_bad_op(self):
        self.proto.dataReceived(b'27:{"op": "bar", "bar": "baz"},')
        self.assertEqual(
            b'28:{"error": "Unknown op: bar"},', self.transport.value())

    def test_no_op(self):
        self.proto.dataReceived(b'28:{"nop": "bar", "bar": "baz"},')
        self.assertEqual(
            b'28:{"error": "No op specified"},', self.transport.value())

    def test_bad_value(self):
        self.proto.dataReceived(b'14:["foo", "bar"],')
        self.assertEqual(
            b'42:{"error": "Command must be a JSON object"},',
            self.transport.value())

    def test_bad_json(self):
        self.proto.dataReceived(b'12:["nop", "bar,')
        self.assertEqual(
            b'42:{"error": "Command must be a JSON object"},',
            self.transport.value())


class TestHookRPCHandler(TestCase):
    """Test the hook RPC handler."""

    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=5)

    def setUp(self):
        super(TestHookRPCHandler, self).setUp()
        self.virtinfo = FakeVirtInfoService(allowNone=True)
        self.virtinfo_listener = reactor.listenTCP(
            0, server.Site(self.virtinfo))
        self.virtinfo_port = self.virtinfo_listener.getHost().port
        self.virtinfo_url = b'http://localhost:%d/' % self.virtinfo_port
        self.addCleanup(self.virtinfo_listener.stopListening)
        self.hookrpc_handler = hookrpc.HookRPCHandler(self.virtinfo_url)

    @contextlib.contextmanager
    def registeredKey(self, path, auth_params=None, permissions=None):
        key = str(uuid.uuid4())
        self.hookrpc_handler.registerKey(key, path, auth_params or {})
        if permissions is not None:
            self.hookrpc_handler.ref_permissions[key] = permissions
        try:
            yield key
        finally:
            self.hookrpc_handler.unregisterKey(key)

    def assertCheckedRefPermissions(self, path, ref_paths, auth_params):
        self.assertEqual(
            [(path, ref_paths, auth_params)],
            self.virtinfo.ref_permissions_checks)

    @defer.inlineCallbacks
    def test_checkRefPermissions_fresh(self):
        self.virtinfo.ref_permissions = {
            'refs/heads/master': ['push'],
            'refs/heads/next': ['force_push'],
            }
        with self.registeredKey('/translated', auth_params={'uid': 42}) as key:
            permissions = yield self.hookrpc_handler.checkRefPermissions(
                None,
                {'key': key, 'paths': sorted(self.virtinfo.ref_permissions)})
        self.assertEqual(self.virtinfo.ref_permissions, permissions)
        self.assertCheckedRefPermissions(
            '/translated', [b'refs/heads/master', b'refs/heads/next'],
            {'uid': 42})

    @defer.inlineCallbacks
    def test_checkRefPermissions_cached(self):
        cached_ref_permissions = {
            'refs/heads/master': ['push'],
            'refs/heads/next': ['force_push'],
            }
        with self.registeredKey(
                '/translated', auth_params={'uid': 42},
                permissions=cached_ref_permissions) as key:
            permissions = yield self.hookrpc_handler.checkRefPermissions(
                None, {'key': key, 'paths': ['refs/heads/master']})
        expected_permissions = {'refs/heads/master': ['push']}
        self.assertEqual(expected_permissions, permissions)
        self.assertEqual([], self.virtinfo.ref_permissions_checks)

    @defer.inlineCallbacks
    def test_notifyPush(self):
        with self.registeredKey('/translated') as key:
            yield self.hookrpc_handler.notifyPush(None, {'key': key})
        self.assertEqual(['/translated'], self.virtinfo.push_notifications)
