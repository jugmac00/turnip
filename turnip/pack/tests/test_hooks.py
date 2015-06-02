# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os.path
import uuid

from fixtures import TempDir
from testtools import TestCase
from testtools.deferredruntest import AsynchronousDeferredRunTest
from twisted.internet import (
    defer,
    protocol,
    reactor,
    )

from turnip.pack import hookrpc
import turnip.pack.hooks


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


class MockHookRPCHandler(hookrpc.HookRPCHandler):

    def __init__(self):
        super(MockHookRPCHandler, self).__init__(None)
        self.notifications = []

    def notifyPush(self, proto, args):
        self.notifications.append(self.ref_paths[args['key']])


class HookTestMixin(object):
    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=1)

    old_sha1 = b'a' * 40
    new_sha1 = b'b' * 40

    @property
    def hook_path(self):
        return os.path.join(
            os.path.dirname(turnip.pack.hooks.__file__), self.hook_name)

    def handlePushNotification(self, path):
        self.notifications.append(path)

    def setUp(self):
        super(HookTestMixin, self).setUp()
        self.hookrpc_handler = MockHookRPCHandler()
        self.hookrpc = hookrpc.HookRPCServerFactory(self.hookrpc_handler)
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
        key = str(uuid.uuid4())
        self.hookrpc_handler.registerKey(key, '/translated', list(rules))
        try:
            d = defer.Deferred()
            reactor.spawnProcess(
                HookProcessProtocol(d, input),
                self.hook_path, [self.hook_path],
                env={
                    b'TURNIP_HOOK_RPC_SOCK': self.hookrpc_path,
                    b'TURNIP_HOOK_RPC_KEY': key})
            code, stdout, stderr = yield d
        finally:
            self.hookrpc_handler.unregisterKey(key)
        defer.returnValue((code, stdout, stderr))

    @defer.inlineCallbacks
    def assertAccepted(self, updates, rules):
        code, out, err = yield self.invokeHook(self.encodeRefs(updates), rules)
        self.assertEqual((0, b'', b''), (code, out, err))

    @defer.inlineCallbacks
    def assertRejected(self, updates, rules, message):
        code, out, err = yield self.invokeHook(self.encodeRefs(updates), rules)
        self.assertEqual((1, message, b''), (code, out, err))


class TestPreReceiveHook(HookTestMixin, TestCase):
    """Tests for the git pre-receive hook."""

    hook_name = 'pre-receive'

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


class TestPostReceiveHook(HookTestMixin, TestCase):
    """Tests for the git post-receive hook."""

    hook_name = 'post-receive'

    @defer.inlineCallbacks
    def test_notifies(self):
        # The notification callback is invoked with the storage path.
        yield self.assertAccepted(
            [(b'refs/heads/foo', self.old_sha1, self.new_sha1)], [])
        self.assertEqual(['/translated'], self.hookrpc_handler.notifications)

    @defer.inlineCallbacks
    def test_does_not_notify_on_empty_push(self):
        # No notification is sent for an empty push.
        yield self.assertAccepted([], [])
        self.assertEqual([], self.hookrpc_handler.notifications)
