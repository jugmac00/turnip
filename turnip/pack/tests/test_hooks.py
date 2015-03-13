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
