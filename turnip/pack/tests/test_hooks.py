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
        self.test_json_log = []

    def jsonReceived(self, obj):
        self.test_json_log.append(obj)


class TestJSONNetStringProtocol(TestCase):
    """Test the JSON netstring protocol."""

    def setUp(self):
        super(TestJSONNetStringProtocol, self).setUp()
        self.proto = DummyJSONNetstringProtocol()
        self.transport = proto_helpers.StringTransportWithDisconnection()
        self.transport.protocol = self.proto
        self.proto.makeConnection(self.transport)

    def test_calls_jsonReceived(self):
        self.proto.dataReceived(b'14:{"foo": "bar"},')
        self.assertEqual([{"foo": "bar"}], self.proto.test_json_log)
