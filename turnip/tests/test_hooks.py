from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os.path
import subprocess

from testtools import TestCase

import turnip


class TestUpdateHook(TestCase):
    """Tests for the git update hook."""

    hook_path = os.path.join(
        os.path.dirname(turnip.__file__), b'data', b'hooks', b'update')
    old_sha1 = b'a' * 40
    new_sha1 = b'b' * 40

    def assertAccepted(self, ref, old, new):
        hook = subprocess.Popen(
            [self.hook_path, ref, old, new],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = hook.communicate()
        self.assertEqual(0, hook.returncode)
        self.assertEqual(b'', stdout)
        self.assertEqual(b'', stderr)

    def assertRejected(self, ref, old, new, message):
        hook = subprocess.Popen(
            [self.hook_path, ref, old, new],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = hook.communicate()
        self.assertEqual(1, hook.returncode)
        self.assertEqual(message, stdout)
        self.assertEqual(b'', stderr)

    def test_accepted(self):
        self.assertAccepted(
            b'refs/heads/master', self.old_sha1, self.new_sha1)

    def test_rejected(self):
        self.assertRejected(
            b'refs/heads/verboten', self.old_sha1, self.new_sha1,
            b"You can't push to refs/heads/verboten.\n")
