# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from testtools import TestCase
from twisted.test import proto_helpers

from turnip.pack import (
    git,
    helpers,
    )


class DummyPackServerProtocol(git.PackServerProtocol):

    test_request = None

    def requestReceived(self, command, pathname, host):
        if self.test_request is not None:
            raise AssertionError('Request already received')
        self.test_request = (command, pathname, host)


class TestPackServerProtocol(TestCase):
    """Test the base implementation of the git pack network protocol."""

    def setUp(self):
        super(TestPackServerProtocol, self).setUp()
        self.proto = DummyPackServerProtocol()
        self.transport = proto_helpers.StringTransportWithDisconnection()
        self.transport.protocol = self.proto
        self.proto.makeConnection(self.transport)

    def assertKilledWith(self, message):
        self.assertFalse(self.transport.connected)
        self.assertEqual(
            (b'ERR ' + message + b'\n', b''),
            helpers.decode_packet(self.transport.value()))

    def test_calls_requestReceived(self):
        # dataReceived waits for a complete request packet and calls
        # requestReceived.
        self.proto.dataReceived(
            b'002egit-upload-pack /foo.git\0host=example.com\0')
        self.assertEqual(
            (b'git-upload-pack', b'/foo.git', {b'host': b'example.com'}),
            self.proto.test_request)

    def test_handles_fragmentation(self):
        # dataReceived handles fragmented request packets.
        self.proto.dataReceived(b'002')
        self.proto.dataReceived(b'egit-upload-pack /foo.git\0hos')
        self.proto.dataReceived(b't=example.com\0')
        self.assertEqual(
            (b'git-upload-pack', b'/foo.git', {b'host': b'example.com'}),
            self.proto.test_request)
        self.assertTrue(self.transport.connected)

    def test_buffers_trailing_data(self):
        # Any input after the request packet is buffered until the
        # implementation handles requestReceived() and calls
        # resumeProducing().
        self.proto.dataReceived(
            b'002egit-upload-pack /foo.git\0host=example.com\0lol')
        self.assertEqual(
            (b'git-upload-pack', b'/foo.git', {b'host': b'example.com'}),
            self.proto.test_request)
        self.assertEqual(b'lol', self.proto._PackProtocol__buffer)

    def test_drops_bad_packet(self):
        # An invalid packet causes the connection to be dropped.
        self.proto.dataReceived(b'abcg')
        self.assertKilledWith(b'Invalid pkt-line')

    def test_drops_bad_request(self):
        # An invalid request causes the connection to be dropped.
        self.proto.dataReceived(b'0007lol')
        self.assertKilledWith(b'Invalid git-proto-request')

    def test_drops_flush_request(self):
        # A flush packet is not a valid request, so the connection is
        # dropped.
        self.proto.dataReceived(b'0000')
        self.assertKilledWith(b'Bad request: flush-pkt instead')
