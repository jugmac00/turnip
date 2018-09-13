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
from turnip.pack.hooks import hook


class HookProcessProtocol(protocol.ProcessProtocol):

    def __init__(self, deferred, stdin):
        self.deferred = deferred
        self.stdin = stdin
        self.stdout = self.stderr = b''

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
            old + b' ' + new + b' ' + ref for ref, old, new in updates)

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
            [{'pattern': 'refs/heads/master', 'permissions': ['push']}])

    @defer.inlineCallbacks
    def test_rejected(self):
        # An invalid ref is rejected.
        yield self.assertRejected(
            [(b'refs/heads/verboten', self.old_sha1, self.new_sha1)],
            [{'pattern': 'refs/heads/verboten', 'permissions': []}],
            b"You can't push to refs/heads/verboten.\n")

    @defer.inlineCallbacks
    def test_wildcard(self):
        # "*" in a rule matches any path segment.
        yield self.assertRejected(
            [(b'refs/heads/foo', self.old_sha1, self.new_sha1),
             (b'refs/tags/bar', self.old_sha1, self.new_sha1),
             (b'refs/tags/foo', self.old_sha1, self.new_sha1),
             (b'refs/baz/quux', self.old_sha1, self.new_sha1)],
            [{'pattern': 'refs/*/foo', 'permissions': []},
             {'pattern': 'refs/baz/*', 'permissions': []}],
            b"You can't push to refs/heads/foo.\n"
            b"You can't push to refs/tags/bar.\n"
            b"You can't push to refs/tags/foo.\n"
            b"You can't push to refs/baz/quux.\n")

    @defer.inlineCallbacks
    def test_rejected_multiple(self):
        # A combination of valid and invalid refs is still rejected.
        yield self.assertRejected(
            [(b'refs/heads/verboten', self.old_sha1, self.new_sha1),
             (b'refs/heads/master', self.old_sha1, self.new_sha1),
             (b'refs/heads/super-verboten', self.old_sha1, self.new_sha1)],
            [{'pattern': 'refs/heads/verboten', 'permissions': []},
             {'pattern': 'refs/heads/super-verboten', 'permissions': []},
             {'pattern': 'refs/heads/master', 'permissions': ['push']}],
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


class MockRef(object):

    def __init__(self, hex):
        self.hex = hex

class MockRepo(object):

    def __init__(self, hex):
        self.hex = hex

    def merge_base(self, old, new):
        return MockRef(self.hex)


class TestUpdateHook(TestCase):
    """Tests for the git update hook"""

    def patch_repo(self, hex):
        hook.get_repo = lambda: MockRepo(hex)

    def setUp(self):
        super(TestUpdateHook, self).setUp()

    def test_create(self):
        """Creation is determined by an all 0 base sha"""
        self.patch_repo('')
        self.assertEqual(
            [], hook.match_update_rules([], ['ref', '0'*40, 'new']))

    def test_fast_forward(self):
        """If the old sha is a merge ancestor of the new"""
        self.patch_repo('somehex')
        self.assertEqual(
            [], hook.match_update_rules([], ['ref', 'somehex', 'new']))

    def test_rules_fall_through(self):
        """The default is to deny"""
        self.patch_repo('somehex')
        output = hook.match_update_rules([], ['ref', 'old', 'new'])
        self.assertEqual(
            [b'You are not allowed to force push to ref'], output)

    def test_no_matching_ref(self):
        """No matches means deny by default"""
        self.patch_repo('somehex')
        output = hook.match_update_rules(
            [{'pattern': 'notamatch', 'permissions': []}],
            ['ref', 'old', 'new'])
        self.assertEqual(
            [b'You are not allowed to force push to ref'], output)

    def test_matching_ref(self):
        self.patch_repo('somehex')
        output = hook.match_update_rules(
            [{'pattern': 'ref', 'permissions': ['force_push']}],
            ['ref', 'old', 'new'])
        self.assertEqual([], output)

    def test_wildcard_ref(self):
        self.patch_repo('somehex')
        output = hook.match_update_rules(
            [{'pattern': 'refs/heads/*/test', 'permissions': ['force_push']}],
            ['refs/heads/wildcard/test', 'old', 'new'])
        self.assertEqual([], output)

    def test_no_permission(self):
        """User does not have permission to force push"""
        self.patch_repo('somehex')
        output = hook.match_update_rules(
            [{'pattern': 'ref', 'permissions': ['create']}],
            ['ref', 'old', 'new'])
        self.assertEqual([b'You are not allowed to force push to ref'], output)
