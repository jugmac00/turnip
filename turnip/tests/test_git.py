from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from testtools import TestCase
from twisted.test import proto_helpers

from turnip import git

TEST_DATA = '0123456789abcdef'
TEST_PKT = '00140123456789abcdef'


class TestEncodePacket(TestCase):
    """Test git pkt-line encoding."""

    def test_data(self):
        # Encoding a string creates a data-pkt, prefixing it with a
        # four-byte length of the entire packet.
        self.assertEqual(TEST_PKT, git.encode_packet(TEST_DATA))

    def test_flush(self):
        # None represents the special flush-pkt, a zero-length packet.
        self.assertEqual(b'0000', git.encode_packet(None))

    def test_rejects_oversized_payload(self):
        # pkt-lines are limited to 65524 bytes, so the data must not
        # exceed 65520 bytes.
        data = 'a' * 65520
        self.assertEqual(b'fff4', git.encode_packet(data)[:4])
        data += 'a'
        self.assertRaises(ValueError, git.encode_packet, data)


class TestDecodePacket(TestCase):
    """Test git pkt-line decoding."""

    def test_data(self):
        self.assertEqual((TEST_DATA, b''), git.decode_packet(TEST_PKT))

    def test_flush(self):
        self.assertEqual((None, b''), git.decode_packet(b'0000'))

    def test_data_with_tail(self):
        self.assertEqual(
            (TEST_DATA, b'foo'), git.decode_packet(TEST_PKT + b'foo'))

    def test_flush_with_tail(self):
        self.assertEqual((None, b'foo'), git.decode_packet(b'0000foo'))

    def test_incomplete_len(self):
        self.assertEqual(
            (git.INCOMPLETE_PKT, b'001'), git.decode_packet(b'001'))

    def test_incomplete_data(self):
        self.assertEqual(
            (git.INCOMPLETE_PKT, TEST_PKT[:-1]),
            git.decode_packet(TEST_PKT[:-1]))


class TestDecodeRequest(TestCase):
    """Test git-proto-request decoding."""

    def assertInvalid(self, req, message):
        e = self.assertRaises(ValueError, git.decode_request, req)
        self.assertEqual(message, e.message)

    def test_without_host(self):
        self.assertEqual(
            (b'git-do-stuff', b'/some/path', None),
            git.decode_request(b'git-do-stuff /some/path\0'))

    def test_with_host(self):
        self.assertEqual(
            (b'git-do-stuff', b'/some/path', b'example.com'),
            git.decode_request(
                b'git-do-stuff /some/path\0host=example.com\0'))

    def test_rejects_totally_invalid(self):
        # There must be a space preceding an argument list.
        self.assertInvalid(b'git-do-stuff', b'Invalid git-proto-request')

    def test_rejects_no_args(self):
        # There must be at least one NUL-terminated argument, the path.
        self.assertInvalid(b'git-do-stuff ', b'Invalid git-proto-request')

    def test_rejects_too_many_args(self):
        # A maximum of two arguments are supported.
        self.assertInvalid(
            b'git-do-stuff /foo\0host=bar\0lol\0',
            b'Invalid git-proto-request')

    def test_rejects_bad_host(self):
        # The host-parameter must start with host=.
        self.assertInvalid(
            b'git-do-stuff /foo\0ghost\0', b'Invalid host-parameter')


class TestEncodeRequest(TestCase):
    """Test git-proto-request encoding."""

    def assertInvalid(self, command, pathname, host, message):
        e = self.assertRaises(
            ValueError, git.encode_request, command, pathname, host)
        self.assertEqual(message, e.message)

    def test_without_host(self):
        self.assertEqual(
            b'git-do-stuff /some/path\0',
            git.encode_request(b'git-do-stuff', b'/some/path'))

    def test_with_host(self):
        self.assertEqual(
            b'git-do-stuff /some/path\0host=example.com\0',
            git.encode_request(b'git-do-stuff', b'/some/path', b'example.com'))

    def test_rejects_meta_in_args(self):
        self.assertInvalid(
            b'git do-stuff', b'/some/path', b'example.com',
            b'Metacharacter in arguments')
        self.assertInvalid(
            b'git-do-stuff', b'/some/\0path', b'example.com',
            b'Metacharacter in arguments')
        self.assertInvalid(
            b'git-do-stuff', b'/some/path', b'exam\0le.com',
            b'Metacharacter in arguments')


class DummyGitServerProtocol(git.GitServerProtocol):

    test_request = None

    def requestReceived(self, command, pathname, host):
        if self.test_request is not None:
            raise AssertionError('Request already received')
        self.test_request = (command, pathname, host)


class TestGitServerProtocol(TestCase):
    """Test the base implementation of the git pack network protocol."""

    def setUp(self):
        super(TestGitServerProtocol, self).setUp()
        self.proto = DummyGitServerProtocol()
        self.transport = proto_helpers.StringTransportWithDisconnection()
        self.transport.protocol = self.proto
        self.proto.makeConnection(self.transport)

    def assertKilledWith(self, message):
        self.assertFalse(self.transport.connected)
        self.assertEqual(
            (b'ERR ' + message, b''),
            git.decode_packet(self.transport.value()))

    def test_calls_requestReceived(self):
        # dataReceived waits for a complete request packet and calls
        # requestReceived.
        self.proto.dataReceived(
            b'002egit-upload-pack /foo.git\0host=example.com\0')
        self.assertEqual(
            (b'git-upload-pack', b'/foo.git', b'example.com'),
            self.proto.test_request)

    def test_handles_fragmentation(self):
        # dataReceived handles fragmented request packets.
        self.proto.dataReceived(b'002')
        self.proto.dataReceived(b'egit-upload-pack /foo.git\0hos')
        self.proto.dataReceived(b't=example.com\0')
        self.assertEqual(
            (b'git-upload-pack', b'/foo.git', b'example.com'),
            self.proto.test_request)
        self.assertTrue(self.transport.connected)

    def test_rejects_trailing_garbage(self):
        # Any input after the request packet but before the server's
        # greeting is invalid. (This will change once we're handling
        # HTTP too, but by then we'll be able to forward the trailing
        # bits through).
        self.proto.dataReceived(
            b'002egit-upload-pack /foo.git\0host=example.com\0lol')
        self.assertEqual(
            (b'git-upload-pack', b'/foo.git', b'example.com'),
            self.proto.test_request)
        self.assertKilledWith(b'Garbage after request packet')

    def test_drops_bad_packet(self):
        # An invalid packet causes the connection to be dropped.
        self.proto.dataReceived('abcg')
        self.assertKilledWith(b'Bad request: Invalid pkt-len')

    def test_drops_bad_request(self):
        # An invalid request causes the connection to be dropped.
        self.proto.dataReceived('0007lol')
        self.assertKilledWith(b'Invalid git-proto-request')

    def test_drops_flush_request(self):
        # A flush packet is not a valid request, so the connection is
        # dropped.
        self.proto.dataReceived('0000')
        self.assertKilledWith(b'Bad request: flush-pkt instead')
