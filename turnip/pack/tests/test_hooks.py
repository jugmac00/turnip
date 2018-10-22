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
import pygit2
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
        self.ref_permissions = {}

    def notifyPush(self, proto, args):
        self.notifications.append(self.ref_paths[args['key']])

    def checkRefPermissions(self, proto, args):
        return self.ref_permissions[args['key']]


class MockRef(object):

    def __init__(self, hex):
        self.hex = hex


class MockRepo(object):

    def __init__(self, ancestor):
        self.ancestor = ancestor

    def merge_base(self, old, new):
        return MockRef(self.ancestor)


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
    def invokeHook(self, input, permissions):
        key = str(uuid.uuid4())
        self.hookrpc_handler.registerKey(key, '/translated', {})
        self.hookrpc_handler.ref_permissions[key] = permissions
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
    def assertAccepted(self, updates, permissions):
        code, out, err = yield self.invokeHook(
            self.encodeRefs(updates), permissions)
        self.assertEqual((0, b'', b''), (code, out, err))

    @defer.inlineCallbacks
    def assertRejected(self, updates, permissions, message):
        code, out, err = yield self.invokeHook(
            self.encodeRefs(updates), permissions)
        self.assertEqual((1, message, b''), (code, out, err))


class TestPreReceiveHook(HookTestMixin, TestCase):
    """Tests for the git pre-receive hook."""

    hook_name = 'pre-receive'

    @defer.inlineCallbacks
    def test_accepted(self):
        # A single valid ref is accepted.
        yield self.assertAccepted(
            [(b'refs/heads/master', self.old_sha1, self.new_sha1)],
            {'refs/heads/master': ['push']})

    @defer.inlineCallbacks
    def test_rejected(self):
        # An invalid ref is rejected.
        yield self.assertRejected(
            [(b'refs/heads/verboten', self.old_sha1, self.new_sha1)],
            {'refs/heads/verboten': []},
            b"You do not have permission to push to refs/heads/verboten.\n")

    @defer.inlineCallbacks
    def test_rejected_multiple(self):
        # A combination of valid and invalid refs is still rejected.
        yield self.assertRejected(
            [(b'refs/heads/verboten', self.old_sha1, self.new_sha1),
             (b'refs/heads/master', self.old_sha1, self.new_sha1),
             (b'refs/heads/super-verboten', self.old_sha1, self.new_sha1)],
            {'refs/heads/verboten': [],
             'refs/heads/super-verboten': [],
             'refs/heads/master': ['push']},
            b"You do not have permission to push to refs/heads/verboten.\n"
            b"You do not have permission to push "
            b"to refs/heads/super-verboten.\n")


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


class TestUpdateHook(TestCase):
    """Tests for the git update hook"""

    def patch_repo(self, ancestor):
        hook.get_repo = lambda: MockRepo(ancestor)

    def test_create(self):
        # Creation is determined by an all 0 base sha
        self.patch_repo('')
        self.assertEqual(
            [], hook.match_update_rules(
                [], ['ref', pygit2.GIT_OID_HEX_ZERO, 'new']))

    def test_fast_forward(self):
        # If the old sha is a merge ancestor of the new
        self.patch_repo('somehex')
        self.assertEqual(
            [], hook.match_update_rules([], ['ref', 'somehex', 'new']))

    def test_rules_fall_through(self):
        # The default is to deny
        self.patch_repo('somehex')
        output = hook.match_update_rules({}, ['ref', 'old', 'new'])
        self.assertEqual(
            [b'You do not have permission to force-push to ref.'], output)

    def test_no_matching_ref(self):
        # No matches means deny by default
        self.patch_repo('somehex')
        output = hook.match_update_rules(
            {'notamatch': []},
            ['ref', 'old', 'new'])
        self.assertEqual(
            [b'You do not have permission to force-push to ref.'], output)

    def test_matching_ref(self):
        # Permission given to force-push
        self.patch_repo('somehex')
        output = hook.match_update_rules(
            {'ref': ['force_push']},
            ['ref', 'old', 'new'])
        self.assertEqual([], output)

    def test_no_permission(self):
        # User does not have permission to force-push
        self.patch_repo('somehex')
        output = hook.match_update_rules(
            {'ref': ['create']},
            ['ref', 'old', 'new'])
        self.assertEqual(
            [b'You do not have permission to force-push to ref.'], output)


class TestDeterminePermissions(TestCase):

    def test_no_match_fallthrough(self):
        # No matching rule is deny by default
        output = hook.determine_permissions_outcome(
            'old', 'ref', {})
        self.assertEqual(b"You do not have permission to push to ref.", output)

    def test_match_no_permissions(self):
        output = hook.determine_permissions_outcome(
            'old', 'ref', {'ref': []})
        self.assertEqual(b"You do not have permission to push to ref.", output)

    def test_match_with_create(self):
        output = hook.determine_permissions_outcome(
            pygit2.GIT_OID_HEX_ZERO, 'ref', {'ref': ['create']})
        self.assertIsNone(output)

    def test_match_no_create_perms(self):
        output = hook.determine_permissions_outcome(
            pygit2.GIT_OID_HEX_ZERO, 'ref', {'ref': []})
        self.assertEqual(b"You do not have permission to create ref.", output)

    def test_push(self):
        output = hook.determine_permissions_outcome(
            'old', 'ref', {'ref': ['push']})
        self.assertIsNone(output)

    def test_force_push(self):
        output = hook.determine_permissions_outcome(
            'old', 'ref', {'ref': ['force_push']})
        self.assertIsNone(output)

    def test_force_push_always_allows(self):
        # If user has force-push, they can do anything
        output = hook.determine_permissions_outcome(
            pygit2.GIT_OID_HEX_ZERO, 'ref', {'ref': ['force_push']})
        self.assertIsNone(output)
