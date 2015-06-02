# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import hashlib
import os.path
import stat
from textwrap import dedent
import time

from fixtures import TempDir
from pygit2 import (
    Config,
    init_repository,
    )
from testtools import TestCase

from turnip.pack import helpers
import turnip.pack.hooks


TEST_DATA = b'0123456789abcdef'
TEST_PKT = b'00140123456789abcdef'


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
        data = b'a' * 65520
        self.assertEqual(b'fff4', helpers.encode_packet(data)[:4])
        data += b'a'
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
    """Test turnip-proto-request decoding.

    It's a superset of git-proto-request, supporting multiple named
    parameters rather than just host-parameter.
    """

    def assertInvalid(self, req, message):
        e = self.assertRaises(ValueError, helpers.decode_request, req)
        self.assertEqual(message, str(e).encode('utf-8'))

    def test_without_parameters(self):
        self.assertEqual(
            (b'git-do-stuff', b'/some/path', {}),
            helpers.decode_request(b'git-do-stuff /some/path\0'))

    def test_with_host_parameter(self):
        self.assertEqual(
            (b'git-do-stuff', b'/some/path', {b'host': b'example.com'}),
            helpers.decode_request(
                b'git-do-stuff /some/path\0host=example.com\0'))

    def test_with_host_and_user_parameters(self):
        self.assertEqual(
            (b'git-do-stuff', b'/some/path',
             {b'host': b'example.com', b'user': b'foo=bar'}),
            helpers.decode_request(
                b'git-do-stuff /some/path\0host=example.com\0user=foo=bar\0'))

    def test_rejects_totally_invalid(self):
        # There must be a space preceding the pathname.
        self.assertInvalid(b'git-do-stuff', b'Invalid git-proto-request')

    def test_rejects_no_pathname(self):
        # There must be a NUL-terminated pathname following the command
        # and space.
        self.assertInvalid(b'git-do-stuff ', b'Invalid git-proto-request')

    def test_rejects_parameter_without_value(self):
        # Each named parameter must have a value.
        self.assertInvalid(
            b'git-do-stuff /foo\0host=bar\0lol\0',
            b'Parameters must have values')

    def test_rejects_unterminated_parameters(self):
        # Each parameter must be NUL-terminated.
        self.assertInvalid(
            b'git-do-stuff /foo\0boo=bar',
            b'Invalid git-proto-request')

    def test_rejects_duplicate_parameters(self):
        # Each parameter must be NUL-terminated.
        self.assertInvalid(
            b'git-do-stuff /foo\0host=foo\0host=bar\0',
            b'Parameters must not be repeated')


class TestEncodeRequest(TestCase):
    """Test git-proto-request encoding."""

    def assertInvalid(self, command, pathname, params, message):
        e = self.assertRaises(
            ValueError, helpers.encode_request, command, pathname, params)
        self.assertEqual(message, str(e).encode('utf-8'))

    def test_without_parameters(self):
        self.assertEqual(
            b'git-do-stuff /some/path\0',
            helpers.encode_request(b'git-do-stuff', b'/some/path', {}))

    def test_with_parameters(self):
        self.assertEqual(
            b'git-do-stuff /some/path\0host=example.com\0user=foo=bar\0',
            helpers.encode_request(
                b'git-do-stuff', b'/some/path',
                {b'host': b'example.com', b'user': b'foo=bar'}))

    def test_rejects_meta_in_args(self):
        self.assertInvalid(
            b'git do-stuff', b'/some/path', {b'host': b'example.com'},
            b'Metacharacter in arguments')
        self.assertInvalid(
            b'git-do-stuff', b'/some/\0path', {b'host': b'example.com'},
            b'Metacharacter in arguments')
        self.assertInvalid(
            b'git-do-stuff', b'/some/path', {b'host\0': b'example.com'},
            b'Metacharacter in arguments')
        self.assertInvalid(
            b'git-do-stuff', b'/some/path', {b'host=': b'example.com'},
            b'Metacharacter in arguments')
        self.assertInvalid(
            b'git-do-stuff', b'/some/path', {b'host': b'exam\0le.com'},
            b'Metacharacter in arguments')


class TestEnsureConfig(TestCase):
    """Test repository configuration maintenance."""

    def setUp(self):
        super(TestEnsureConfig, self).setUp()
        self.repo_dir = self.useFixture(TempDir()).path
        init_repository(self.repo_dir, bare=True)
        self.config_path = os.path.join(self.repo_dir, 'config')

    def assertWritesCorrectConfig(self):
        helpers.ensure_config(self.repo_dir)
        config = Config(path=self.config_path)
        self.assertTrue(config['core.logallrefupdates'])
        self.assertTrue(config['repack.writeBitmaps'])

    def test_writes_new(self):
        self.assertWritesCorrectConfig()

    def test_preserves_existing(self):
        # If the configuration file is already in the correct state, then
        # the file is left unchanged; for efficiency we do not even write
        # out a new file.  (Currently, pygit2/libgit2 take care of this; if
        # they ever stop doing so then we should take extra care ourselves.)
        helpers.ensure_config(self.repo_dir)
        now = time.time()
        os.utime(self.config_path, (now - 60, now - 60))
        old_mtime = os.stat(self.config_path).st_mtime
        self.assertWritesCorrectConfig()
        self.assertEqual(old_mtime, os.stat(self.config_path).st_mtime)

    def test_fixes_incorrect(self):
        with open(self.config_path, 'w') as f:
            f.write(dedent("""\
                [core]
                \tlogallrefupdates = false
                [repack]
                \twriteBitmaps = false
                """))
        self.assertWritesCorrectConfig()


class TestEnsureHooks(TestCase):
    """Test repository hook maintenance."""

    def setUp(self):
        super(TestEnsureHooks, self).setUp()
        self.repo_dir = self.useFixture(TempDir()).path
        self.hooks_dir = os.path.join(self.repo_dir, 'hooks')
        os.mkdir(self.hooks_dir)

    def hook(self, hook):
        return os.path.join(self.hooks_dir, hook)

    def test_deletes_random(self):
        # Unknown files are deleted.
        os.symlink('foo', self.hook('bar'))
        self.assertIn('bar', os.listdir(self.hooks_dir))
        helpers.ensure_hooks(self.repo_dir)
        self.assertNotIn('bar', os.listdir(self.hooks_dir))

    def test_fixes_symlink(self):
        # A symlink with a bad path is fixed.
        os.symlink('foo', self.hook('pre-receive'))
        self.assertEqual('foo', os.readlink(self.hook('pre-receive')))
        helpers.ensure_hooks(self.repo_dir)
        self.assertEqual('hook.py', os.readlink(self.hook('pre-receive')))

    def test_replaces_regular_file(self):
        # A regular file is replaced with a symlink.
        with open(self.hook('pre-receive'), 'w') as f:
            f.write('garbage')
        self.assertRaises(OSError, os.readlink, self.hook('pre-receive'))
        helpers.ensure_hooks(self.repo_dir)
        self.assertEqual('hook.py', os.readlink(self.hook('pre-receive')))

    def test_replaces_hook_py(self):
        # The hooks themselves are symlinks to hook.py, which is always
        # kept up to date.
        with open(self.hook('hook.py'), 'w') as f:
            f.write('nothing to see here')
        helpers.ensure_hooks(self.repo_dir)
        with open(self.hook('hook.py'), 'rb') as actual:
            expected_path = os.path.join(
                os.path.dirname(turnip.pack.hooks.__file__), 'hook.py')
            with open(expected_path, 'rb') as expected:
                self.assertEqual(
                    hashlib.sha256(expected.read()).hexdigest(),
                    hashlib.sha256(actual.read()).hexdigest())
        # The hook is executable.
        self.assertTrue(os.stat(self.hook('hook.py')).st_mode & stat.S_IXUSR)
