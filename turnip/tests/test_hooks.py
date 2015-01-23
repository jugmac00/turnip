from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os.path
import subprocess
import tempfile

from testtools import TestCase

import turnip


class TestPreReceiveHook(TestCase):
    """Tests for the git pre-receive hook."""

    hook_path = os.path.join(
        os.path.dirname(turnip.__file__), b'data', b'hooks', b'pre-receive')
    old_sha1 = b'a' * 40
    new_sha1 = b'b' * 40

    def encodeRefs(self, updates):
        return '\n'.join(
            b'%s %s %s' % (old, new, ref) for ref, old, new in updates)

    def invokeHook(self, input, rules):
        with tempfile.NamedTemporaryFile(mode='wb') as rulefile:
            rulefile.writelines(rule + b'\n' for rule in rules)
            rulefile.flush()

            hook = subprocess.Popen(
                [self.hook_path], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={b'TURNIP_HOOK_REF_RULES': rulefile.name})
            stdout, stderr = hook.communicate(input)
        return hook.returncode, stdout, stderr

    def assertAccepted(self, updates, rules):
        self.assertEqual(
            (0, b'', b''), self.invokeHook(self.encodeRefs(updates), rules))

    def assertRejected(self, updates, rules, message):
        self.assertEqual(
            (1, message, b''),
            self.invokeHook(self.encodeRefs(updates), rules))

    def test_accepted(self):
        # A single valid ref is accepted.
        self.assertAccepted(
            [(b'refs/heads/master', self.old_sha1, self.new_sha1)],
            [])

    def test_rejected(self):
        # An invalid ref is rejected.
        self.assertRejected(
            [(b'refs/heads/verboten', self.old_sha1, self.new_sha1)],
            [b'refs/heads/verboten'],
            b"You can't push to refs/heads/verboten.\n")

    def test_rejected_multiple(self):
        # A combination of valid and invalid refs is still rejected.
        self.assertRejected(
            [(b'refs/heads/verboten', self.old_sha1, self.new_sha1),
             (b'refs/heads/master', self.old_sha1, self.new_sha1),
             (b'refs/heads/super-verboten', self.old_sha1, self.new_sha1)],
            [b'refs/heads/verboten', b'refs/heads/super-verboten'],
            b"You can't push to refs/heads/verboten.\n"
            b"You can't push to refs/heads/super-verboten.\n")
