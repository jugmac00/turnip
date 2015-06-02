# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from testtools import TestCase
from twisted.internet import defer
from twisted.test import proto_helpers

from turnip.pack import hookrpc


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
