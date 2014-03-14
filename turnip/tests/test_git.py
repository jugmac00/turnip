from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from testtools import TestCase

from turnip import git


class TestEncodePacket(TestCase):
    """Test git pkt-line encoding."""

    def test_data(self):
        # Encoding a string creates a data-pkt, prefixing it with a
        # four-byte length of the entire packet.
        data = b'0123456789abcdef'
        self.assertEqual(b'0014' + data, git.encode_packet(data))

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
