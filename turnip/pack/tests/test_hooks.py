from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os.path
import tempfile

from fixtures import TempDir
from testtools import TestCase
from testtools.deferredruntest import AsynchronousDeferredRunTest
from twisted.internet import (
    defer,
    protocol,
    reactor,
    )

from turnip.pack import hookrpc
import turnip.pack.hooks.pre_receive


class HookProcessProtocol(protocol.ProcessProtocol):

    def __init__(self, deferred, stdin):
        self.deferred = deferred
        self.stdin = stdin
        self.stdout = self.stderr = ''

    def connectionMade(self):
        self.transport.write(self.stdin)
        self.transport.closeStdin()

    def outReceived(self, data):
        self.stdout += data

    def errReceived(self, data):
        self.stderr += data

    def processEnded(self, status):
        self.deferred.callback(
            (status.value.exitCode, self.stdout, self.stderr))


class TestPreReceiveHook(TestCase):
    """Tests for the git pre-receive hook."""

    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=1)

    hook_path = os.path.join(
        os.path.dirname(turnip.pack.hooks.__file__), 'pre_receive.py')
    old_sha1 = b'a' * 40
    new_sha1 = b'b' * 40

    def setUp(self):
        super(TestPreReceiveHook, self).setUp()
        self.hookrpc = hookrpc.HookRPCServerFactory({})
        dir = self.useFixture(TempDir()).path
        self.hookrpc_path = os.path.join(dir, 'hookrpc_sock')
        self.hookrpc_port = reactor.listenUNIX(
            self.hookrpc_path, self.hookrpc)
        self.addCleanup(self.hookrpc_port.stopListening)

    def encodeRefs(self, updates):
        return b'\n'.join(
            b'%s %s %s' % (old, new, ref) for ref, old, new in updates)

    @defer.inlineCallbacks
    def invokeHook(self, input, rules):
        with tempfile.NamedTemporaryFile(mode='wb') as rulefile:
            rulefile.writelines(rule + b'\n' for rule in rules)
            rulefile.flush()
            d = defer.Deferred()
            reactor.spawnProcess(
                HookProcessProtocol(d, input),
                self.hook_path, [self.hook_path],
                env={
                    b'TURNIP_HOOK_REF_RULES': rulefile.name,
                    b'TURNIP_HOOK_RPC_SOCK': self.hookrpc_path})
            code, stdout, stderr = yield d
        defer.returnValue((code, stdout, stderr))

    @defer.inlineCallbacks
    def assertAccepted(self, updates, rules):
        code, out, err = yield self.invokeHook(self.encodeRefs(updates), rules)
        self.assertEqual((0, b'', b''), (code, out, err))

    @defer.inlineCallbacks
    def assertRejected(self, updates, rules, message):
        code, out, err = yield self.invokeHook(self.encodeRefs(updates), rules)
        self.assertEqual((1, message, b''), (code, out, err))

    @defer.inlineCallbacks
    def test_accepted(self):
        # A single valid ref is accepted.
        yield self.assertAccepted(
            [(b'refs/heads/master', self.old_sha1, self.new_sha1)],
            [])

    @defer.inlineCallbacks
    def test_rejected(self):
        # An invalid ref is rejected.
        yield self.assertRejected(
            [(b'refs/heads/verboten', self.old_sha1, self.new_sha1)],
            [b'refs/heads/verboten'],
            b"You can't push to refs/heads/verboten.\n")

    @defer.inlineCallbacks
    def test_wildcard(self):
        # "*" in a rule matches any path segment.
        yield self.assertRejected(
            [(b'refs/heads/foo', self.old_sha1, self.new_sha1),
             (b'refs/tags/bar', self.old_sha1, self.new_sha1),
             (b'refs/tags/foo', self.old_sha1, self.new_sha1),
             (b'refs/baz/quux', self.old_sha1, self.new_sha1)],
            [b'refs/*/foo', b'refs/baz/*'],
            b"You can't push to refs/heads/foo.\n"
            b"You can't push to refs/tags/foo.\n"
            b"You can't push to refs/baz/quux.\n")

    @defer.inlineCallbacks
    def test_rejected_multiple(self):
        # A combination of valid and invalid refs is still rejected.
        yield self.assertRejected(
            [(b'refs/heads/verboten', self.old_sha1, self.new_sha1),
             (b'refs/heads/master', self.old_sha1, self.new_sha1),
             (b'refs/heads/super-verboten', self.old_sha1, self.new_sha1)],
            [b'refs/heads/verboten', b'refs/heads/super-verboten'],
            b"You can't push to refs/heads/verboten.\n"
            b"You can't push to refs/heads/super-verboten.\n")
