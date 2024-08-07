# Copyright 2015-2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import base64
import os.path
import subprocess
import uuid

import pygit2
import six
from fixtures import MonkeyPatch, TempDir
from testtools import TestCase
from testtools.deferredruntest import AsynchronousDeferredRunTest
from twisted.internet import defer, protocol, reactor

from turnip.pack import hookrpc
from turnip.pack.helpers import ensure_hooks
from turnip.pack.hooks import hook


class HookProcessProtocol(protocol.ProcessProtocol):
    def __init__(self, deferred, stdin):
        self.deferred = deferred
        self.stdin = stdin
        self.stdout = self.stderr = b""

    def connectionMade(self):
        self.transport.write(self.stdin)
        self.transport.closeStdin()

    def outReceived(self, data):
        self.stdout += data

    def errReceived(self, data):
        self.stderr += data

    def processEnded(self, status):
        self.deferred.callback(
            (status.value.exitCode, self.stdout, self.stderr)
        )


class MockHookRPCHandler(hookrpc.HookRPCHandler):
    def __init__(self):
        super().__init__(b"http://localhost", 15)
        self.notifications = []
        self.ref_permissions = {}

    def notifyPush(self, proto, args):
        self.notifications.append(self.ref_paths[args["key"]])

    def checkRefPermissions(self, proto, args):
        return {
            base64.b64encode(ref).decode("UTF-8"): permissions
            for ref, permissions in self.ref_permissions[args["key"]].items()
        }

    def getMergeProposalURL(self, proto, args):
        return "http://mp-url.test"


class MockRef:
    def __init__(self, hex):
        self.hex = hex


class MockRepo:
    def __init__(self, ancestor):
        self.ancestor = ancestor

    def merge_base(self, old, new):
        return MockRef(self.ancestor)


class MockSocket:
    # "sends" up to this amount of bytes on "send()"
    MAX_SEND_DATA = 5

    def __init__(self, blocks=None):
        self.blocks = blocks or []
        self._sent_data = b""

    def send(self, data, flags=None):
        to_send = data[: self.MAX_SEND_DATA]
        self._sent_data += to_send
        return min(self.MAX_SEND_DATA, len(data))

    def sendall(self, data, flags=None):
        ret = self.send(data, flags)
        if ret > 0:
            return self.sendall(data[ret:], flags)
        else:
            return None

    def recv(self, bufsize, flags=None):
        if not self.blocks:
            return b""
        elif bufsize < len(self.blocks[0]):
            block = self.blocks[0][:bufsize]
            self.blocks[0] = self.blocks[0][bufsize:]
            return block
        else:
            return self.blocks.pop(0)


class TestNetstringRecv(TestCase):
    """Tests for netstring_recv."""

    def test_nondigit(self):
        sock = MockSocket([b"zzz:abc,"])
        self.assertRaisesRegex(
            ValueError, "Invalid response: zzz:abc,", hook.netstring_recv, sock
        )

    def test_short(self):
        sock = MockSocket([b"4:abc,"])
        self.assertRaisesRegex(
            ValueError,
            "Length error for message 'abc,': ending=''",
            hook.netstring_recv,
            sock,
        )

    def test_unterminated(self):
        sock = MockSocket([b"4:abcd"])
        self.assertRaisesRegex(
            ValueError,
            "Length error for message 'abcd': ending=''",
            hook.netstring_recv,
            sock,
        )

    def test_split_data(self):
        sock = MockSocket([b"12:abcd", b"efgh", b"ijkl,"])
        self.assertEqual(b"abcdefghijkl", hook.netstring_recv(sock))
        self.assertEqual([], sock.blocks)

    def test_valid(self):
        sock = MockSocket(
            [b"11:\x00\x01\x02\x03\x04,\x05\x06\x07\x08\x09,remaining"]
        )
        self.assertEqual(
            b"\x00\x01\x02\x03\x04,\x05\x06\x07\x08\x09",
            hook.netstring_recv(sock),
        )
        self.assertEqual([b"remaining"], sock.blocks)

    def test_send_uses_sendall(self):
        sock = MockSocket([])

        # Send up to 5 bytes on each "send()" call.
        # This is important to make sure we are using python's higher level
        # socket.sendall() rather than socket.send().
        sock.MAX_SEND_DATA = 5

        hook.netstring_send(sock, b"some-fake-data")
        self.assertEqual(b"14:some-fake-data,", sock._sent_data)


class HookTestMixin:
    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=10)

    old_sha1 = b"a" * 40
    new_sha1 = b"b" * 40

    def handlePushNotification(self, path):
        self.notifications.append(path)

    def setUp(self):
        super().setUp()
        self.hookrpc_handler = MockHookRPCHandler()
        self.hookrpc = hookrpc.HookRPCServerFactory(self.hookrpc_handler)
        self.repo_dir = os.path.join(self.useFixture(TempDir()).path, ".git")
        os.mkdir(self.repo_dir)
        subprocess.check_output(["git", "init", self.repo_dir])
        self.hookrpc_sock_path = os.path.join(self.repo_dir, "hookrpc_sock")
        self.hookrpc_port = reactor.listenUNIX(
            self.hookrpc_sock_path, self.hookrpc
        )
        self.addCleanup(self.hookrpc_port.stopListening)
        hooks_dir = os.path.join(self.repo_dir, "hooks")
        os.mkdir(hooks_dir)
        ensure_hooks(self.repo_dir)
        self.hook_path = os.path.join(hooks_dir, self.hook_name)

    def encodeRefs(self, updates):
        return b"\n".join(
            old + b" " + new + b" " + ref for ref, old, new in updates
        )

    @defer.inlineCallbacks
    def invokeHook(self, input, permissions):
        key = str(uuid.uuid4())
        self.hookrpc_handler.registerKey(key, "/translated", {})
        self.hookrpc_handler.ref_permissions[key] = permissions
        try:
            d = defer.Deferred()
            reactor.spawnProcess(
                HookProcessProtocol(d, input),
                self.hook_path,
                [self.hook_path],
                env={
                    b"TURNIP_HOOK_RPC_SOCK": self.hookrpc_sock_path,
                    b"TURNIP_HOOK_RPC_KEY": key,
                },
                path=self.repo_dir,
            )
            code, stdout, stderr = yield d
        finally:
            self.hookrpc_handler.unregisterKey(key)
        return code, stdout, stderr

    @defer.inlineCallbacks
    def assertAccepted(self, updates, permissions):
        code, out, err = yield self.invokeHook(
            self.encodeRefs(updates), permissions
        )
        self.assertEqual((0, b"", b""), (code, out, err))

    @defer.inlineCallbacks
    def assertRejected(self, updates, permissions, message):
        code, out, err = yield self.invokeHook(
            self.encodeRefs(updates), permissions
        )
        self.assertEqual((1, message, b""), (code, out, err))

    @defer.inlineCallbacks
    def assertMergeProposalURLReceived(self, updates, permissions):
        mp_url_message = (
            b"Create a merge proposal for '%s' on Launchpad by"
            b" visiting:\n" % updates[0][0].split(b"/", 2)[2]
        )
        code, out, err = yield self.invokeHook(
            self.encodeRefs(updates), permissions
        )
        self.assertEqual((0, b""), (code, err))
        self.assertIn(mp_url_message, out)


class TestPreReceiveHook(HookTestMixin, TestCase):
    """Tests for the git pre-receive hook."""

    hook_name = "pre-receive"

    @defer.inlineCallbacks
    def test_accepted(self):
        # A single valid ref is accepted.
        yield self.assertAccepted(
            [(b"refs/heads/master", self.old_sha1, self.new_sha1)],
            {b"refs/heads/master": ["push"]},
        )

    @defer.inlineCallbacks
    def test_accepted_non_ascii(self):
        # Valid non-ASCII refs are accepted.
        paths = [b"refs/heads/\x80", "refs/heads/géag".encode()]
        yield self.assertAccepted(
            [(path, self.old_sha1, self.new_sha1) for path in paths],
            {path: ["push"] for path in paths},
        )

    @defer.inlineCallbacks
    def test_rejected(self):
        # An invalid ref is rejected.
        yield self.assertRejected(
            [(b"refs/heads/verboten", self.old_sha1, self.new_sha1)],
            {b"refs/heads/verboten": []},
            b"You do not have permission to push to refs/heads/verboten.\n",
        )

    @defer.inlineCallbacks
    def test_rejected_readonly_namespaces(self):
        # An read-only ref is rejected.
        yield self.assertRejected(
            [(b"refs/merge/123/heads", self.old_sha1, self.new_sha1)],
            {b"refs/heads/master": ["push", "force_push", "create"]},
            b"refs/merge/123/heads is in a read-only namespace.\n",
        )

    @defer.inlineCallbacks
    def test_rejected_multiple(self):
        # A combination of valid and invalid refs is still rejected.
        yield self.assertRejected(
            [
                (b"refs/heads/verboten", self.old_sha1, self.new_sha1),
                (b"refs/heads/master", self.old_sha1, self.new_sha1),
                (b"refs/heads/super-verboten", self.old_sha1, self.new_sha1),
            ],
            {
                b"refs/heads/verboten": [],
                b"refs/heads/super-verboten": [],
                b"refs/heads/master": ["push"],
            },
            b"You do not have permission to push to refs/heads/verboten.\n"
            b"You do not have permission to push "
            b"to refs/heads/super-verboten.\n",
        )

    @defer.inlineCallbacks
    def test_rejected_non_ascii(self):
        # Invalid non-ASCII refs are rejected.
        paths = [b"refs/heads/\x80", "refs/heads/géag".encode()]
        yield self.assertRejected(
            [(path, self.old_sha1, self.new_sha1) for path in paths],
            {path: [] for path in paths},
            b"You do not have permission to push to refs/heads/\x80.\n"
            b"You do not have permission to push to refs/heads/g\xc3\xa9ag.\n",
        )


class TestPostReceiveHook(HookTestMixin, TestCase):
    """Tests for the git post-receive hook."""

    hook_name = "post-receive"

    @defer.inlineCallbacks
    def test_notifies(self):
        # The notification callback is invoked with the storage path.
        yield self.assertMergeProposalURLReceived(
            [(b"refs/heads/foo", self.old_sha1, self.new_sha1)], []
        )
        self.assertEqual(["/translated"], self.hookrpc_handler.notifications)

    @defer.inlineCallbacks
    def test_does_not_notify_on_empty_push(self):
        # No notification is sent for an empty push.
        yield self.assertAccepted([], [])
        self.assertEqual([], self.hookrpc_handler.notifications)

    @defer.inlineCallbacks
    def test_merge_proposal_URL(self):
        # MP URL received for push of non-default branch
        curdir = os.getcwd()
        try:
            os.chdir(self.repo_dir)
            default_branch = subprocess.check_output(
                ["git", "symbolic-ref", "HEAD"]
            ).rstrip(b"\n")
            pushed_branch = default_branch + b"notdefault"
            yield self.assertMergeProposalURLReceived(
                [(pushed_branch, self.old_sha1, self.new_sha1)],
                {pushed_branch: ["push"]},
            )
        finally:
            os.chdir(curdir)

    @defer.inlineCallbacks
    def test_no_merge_proposal_URL(self):
        # No MP URL received for push of default branch
        curdir = os.getcwd()
        try:
            os.chdir(self.repo_dir)
            default_branch = subprocess.check_output(
                ["git", "symbolic-ref", "HEAD"]
            ).rstrip(b"\n")
            yield self.assertAccepted(
                [(b"%s" % default_branch, self.old_sha1, self.new_sha1)],
                {b"%s" % default_branch: ["push"]},
            )
        finally:
            os.chdir(curdir)

    @defer.inlineCallbacks
    def test_no_merge_proposal_URL_delete_branch(self):
        # No MP URL received for delete of non default branch
        curdir = os.getcwd()
        try:
            os.chdir(self.repo_dir)
            yield self.assertAccepted(
                [
                    (
                        b"pushed_branch",
                        self.old_sha1,
                        six.ensure_binary(pygit2.GIT_OID_HEX_ZERO),
                    )
                ],
                {b"pushed_branch": ["force_push"]},
            )
        finally:
            os.chdir(curdir)


class TestUpdateHook(TestCase):
    """Tests for the git update hook"""

    def setUp(self):
        super().setUp()
        self.useFixture(
            MonkeyPatch(
                "turnip.pack.hooks.hook.check_ancestor",
                self.patched_ancestor_check,
            )
        )

    def patched_ancestor_check(self, old, new):
        # Avoid a subprocess call to execute on a git repository
        # that we haven't created.
        if old == b"old":
            return False
        return True

    def test_create(self):
        # Creation is determined by an all 0 base sha
        self.assertEqual(
            [],
            hook.match_update_rules(
                {}, [b"ref", pygit2.GIT_OID_HEX_ZERO, "new"]
            ),
        )

    def test_fast_forward(self):
        # If the old sha is a merge ancestor of the new
        self.assertEqual(
            [], hook.match_update_rules({}, [b"ref", b"somehex", b"new"])
        )

    def test_rules_fall_through(self):
        # The default is to deny
        output = hook.match_update_rules({}, [b"ref", b"old", b"new"])
        self.assertEqual(
            [b"You do not have permission to force-push to ref."], output
        )

    def test_no_matching_ref(self):
        # No matches means deny by default
        output = hook.match_update_rules(
            {"notamatch": []}, [b"ref", b"old", b"new"]
        )
        self.assertEqual(
            [b"You do not have permission to force-push to ref."], output
        )

    def test_no_matching_non_utf8_ref(self):
        # An unmatched non-UTF-8 ref is denied.
        output = hook.match_update_rules(
            {}, [b"refs/heads/\x80", b"old", b"new"]
        )
        self.assertEqual(
            [b"You do not have permission to force-push to refs/heads/\x80."],
            output,
        )

    def test_no_matching_utf8_ref(self):
        # An unmatched UTF-8 ref is denied.
        output = hook.match_update_rules(
            {}, ["refs/heads/géag".encode(), b"old", b"new"]
        )
        self.assertEqual(
            [
                b"You do not have permission to force-push to "
                b"refs/heads/g\xc3\xa9ag."
            ],
            output,
        )

    def test_matching_ref(self):
        # Permission given to force-push
        output = hook.match_update_rules(
            {b"ref": ["force_push"]}, [b"ref", b"old", b"new"]
        )
        self.assertEqual([], output)

    def test_matching_non_utf8_ref(self):
        # A non-UTF-8 ref with force-push permission is accepted.
        output = hook.match_update_rules(
            {b"refs/heads/\x80": ["force_push"]},
            [b"refs/heads/\x80", "old", "new"],
        )
        self.assertEqual([], output)

    def test_matching_utf8_ref(self):
        # A UTF-8 ref with force-push permission is accepted.
        output = hook.match_update_rules(
            {"refs/heads/géag".encode(): ["force_push"]},
            ["refs/heads/géag".encode(), "old", "new"],
        )
        self.assertEqual([], output)

    def test_no_permission(self):
        # User does not have permission to force-push
        output = hook.match_update_rules(
            {b"ref": ["create"]}, [b"ref", b"old", b"new"]
        )
        self.assertEqual(
            [b"You do not have permission to force-push to ref."], output
        )

    def test_read_only_ref(self):
        # User does not have permission to force-push to a read-only namespace
        output = hook.match_update_rules(
            {"ref": ["create"]}, [b"refs/merge/123/head", "old", "new"]
        )
        self.assertEqual(
            [b"refs/merge/123/head is in a read-only namespace."], output
        )


class TestDeterminePermissions(TestCase):
    def test_no_match_fallthrough(self):
        # No matching rule is deny by default
        output = hook.determine_permissions_outcome(b"old", b"ref", {})
        self.assertEqual(b"You do not have permission to push to ref.", output)

    def test_match_no_permissions(self):
        output = hook.determine_permissions_outcome(
            b"old", b"ref", {"ref": []}
        )
        self.assertEqual(b"You do not have permission to push to ref.", output)

    def test_match_with_create(self):
        output = hook.determine_permissions_outcome(
            six.ensure_binary(pygit2.GIT_OID_HEX_ZERO),
            b"ref",
            {b"ref": ["create"]},
        )
        self.assertIsNone(output)

    def test_match_no_create_perms(self):
        output = hook.determine_permissions_outcome(
            six.ensure_binary(pygit2.GIT_OID_HEX_ZERO), b"ref", {b"ref": []}
        )
        self.assertEqual(b"You do not have permission to create ref.", output)

    def test_push(self):
        output = hook.determine_permissions_outcome(
            b"old", b"ref", {b"ref": ["push"]}
        )
        self.assertIsNone(output)

    def test_force_push(self):
        output = hook.determine_permissions_outcome(
            b"old", b"ref", {b"ref": ["force_push"]}
        )
        self.assertIsNone(output)

    def test_force_push_does_not_imply_create(self):
        output = hook.determine_permissions_outcome(
            six.ensure_binary(pygit2.GIT_OID_HEX_ZERO),
            b"ref",
            {b"ref": ["force_push"]},
        )
        self.assertEqual(b"You do not have permission to create ref.", output)
