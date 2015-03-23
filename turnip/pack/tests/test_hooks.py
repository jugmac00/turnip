from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from testtools import TestCase
from twisted.test import proto_helpers

from turnip.pack import hooks


class DummyJSONNetstringProtocol(hooks.JSONNetstringProtocol):

    def __init__(self):
        self.test_value_log = []
        self.test_invalid_log = []

    def valueReceived(self, val):
        self.test_value_log.append(val)

    def invalidValueReceived(self, string):
        self.test_invalid_log.append(string)


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
            ['{"foo": "bar', '"ga'], self.proto.test_invalid_log)

    def test_sendValue(self):
        # sendValue serialises to JSON and encodes as a netstring.
        self.proto.sendValue({"yay": "it works"})
        self.assertEqual('19:{"yay": "it works"},', self.transport.value())


class TestHookRPCServerProtocol(TestCase):
    """Test the socket server that handles git hook callbacks."""

    def setUp(self):
        super(TestHookRPCServerProtocol, self).setUp()
        self.proto = hooks.HookRPCServerProtocol(
            {'foo': lambda proto, args: args.items()})
        self.transport = proto_helpers.StringTransportWithDisconnection()
        self.transport.protocol = self.proto
        self.proto.makeConnection(self.transport)

    def test_call(self):
        self.proto.dataReceived(b'27:{"op": "foo", "bar": "baz"},')
        self.assertEqual(
            '28:{"result": [["bar", "baz"]]},', self.transport.value())

    def test_bad_op(self):
        self.proto.dataReceived(b'27:{"op": "bar", "bar": "baz"},')
        self.assertEqual(
            '28:{"error": "Unknown op: bar"},', self.transport.value())

    def test_no_op(self):
        self.proto.dataReceived(b'28:{"nop": "bar", "bar": "baz"},')
        self.assertEqual(
            '28:{"error": "No op specified"},', self.transport.value())

    def test_bad_value(self):
        self.proto.dataReceived(b'14:["foo", "bar"],')
        self.assertEqual(
            '42:{"error": "Command must be a JSON object"},',
            self.transport.value())

    def test_bad_json(self):
        self.proto.dataReceived(b'12:["nop", "bar,')
        self.assertEqual(
            '42:{"error": "Command must be a JSON object"},',
            self.transport.value())
