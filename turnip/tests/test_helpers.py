from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from testtools import TestCase

from turnip import helpers


TEST_DATA = '0123456789abcdef'
TEST_PKT = '00140123456789abcdef'


class TestComposePath(TestCase):
    """Tests for path composition."""

    def test_basic(self):
        # The path is resolved within the given root tree.
        self.assertEqual(
            b'/foo/bar/baz/quux',
            helpers.compose_path(b'/foo/bar', b'baz/quux'))

    def test_absolute(self):
        # Even absolute paths are contained.
        self.assertEqual(
            b'/foo/bar/baz/quux',
            helpers.compose_path(b'/foo/bar', b'/baz/quux'))

    def test_normalises(self):
        # Paths are normalised.
        self.assertEqual(
            b'/foo/bar/baz/quux',
            helpers.compose_path(b'///foo/.//bar', b'//baz/..//baz/./quux'))

    def test_no_escape(self):
        # You can't get out.
        self.assertRaises(
            ValueError, helpers.compose_path, '/foo', '../bar')
        self.assertRaises(
            ValueError, helpers.compose_path, '/foo', '/foo/../../bar')


class TestEncodePacket(TestCase):
    """Test git pkt-line encoding."""

    def test_data(self):
        # Encoding a string creates a data-pkt, prefixing it with a
        # four-byte length of the entire packet.
        self.assertEqual(TEST_PKT, helpers.encode_packet(TEST_DATA))

    def test_flush(self):
        # None represents the special flush-pkt, a zero-length packet.
        self.assertEqual(b'0000', helpers.encode_packet(None))

    def test_rejects_oversized_payload(self):
        # pkt-lines are limited to 65524 bytes, so the data must not
        # exceed 65520 bytes.
        data = 'a' * 65520
        self.assertEqual(b'fff4', helpers.encode_packet(data)[:4])
        data += 'a'
        self.assertRaises(ValueError, helpers.encode_packet, data)


class TestDecodePacket(TestCase):
    """Test git pkt-line decoding."""

    def test_data(self):
        self.assertEqual((TEST_DATA, b''), helpers.decode_packet(TEST_PKT))

    def test_flush(self):
        self.assertEqual((None, b''), helpers.decode_packet(b'0000'))

    def test_data_with_tail(self):
        self.assertEqual(
            (TEST_DATA, b'foo'), helpers.decode_packet(TEST_PKT + b'foo'))

    def test_flush_with_tail(self):
        self.assertEqual((None, b'foo'), helpers.decode_packet(b'0000foo'))

    def test_incomplete_len(self):
        self.assertEqual(
            (helpers.INCOMPLETE_PKT, b'001'), helpers.decode_packet(b'001'))

    def test_incomplete_data(self):
        self.assertEqual(
            (helpers.INCOMPLETE_PKT, TEST_PKT[:-1]),
            helpers.decode_packet(TEST_PKT[:-1]))


class TestDecodeRequest(TestCase):
    """Test git-proto-request decoding."""

    def assertInvalid(self, req, message):
        e = self.assertRaises(ValueError, helpers.decode_request, req)
        self.assertEqual(message, e.message)

    def test_without_host(self):
        self.assertEqual(
            (b'git-do-stuff', b'/some/path', None),
            helpers.decode_request(b'git-do-stuff /some/path\0'))

    def test_with_host(self):
        self.assertEqual(
            (b'git-do-stuff', b'/some/path', b'example.com'),
            helpers.decode_request(
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
            ValueError, helpers.encode_request, command, pathname, host)
        self.assertEqual(message, e.message)

    def test_without_host(self):
        self.assertEqual(
            b'git-do-stuff /some/path\0',
            helpers.encode_request(b'git-do-stuff', b'/some/path'))

    def test_with_host(self):
        self.assertEqual(
            b'git-do-stuff /some/path\0host=example.com\0',
            helpers.encode_request(
                b'git-do-stuff', b'/some/path', b'example.com'))

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
