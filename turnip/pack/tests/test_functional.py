# -*- coding: utf-8 -*-
# Copyright 2015-2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import base64
import hashlib
import io
import os
import random
import shutil
import stat
import tempfile

from turnip.config import config

try:
    from urllib.parse import (
        urlsplit,
        urlunsplit,
        )
except ImportError:
    from urlparse import (
        urlsplit,
        urlunsplit,
        )

from fixtures import (
    EnvironmentVariable,
    TempDir,
    )
from pygit2 import GIT_OID_HEX_ZERO
import six
from testscenarios.testcase import WithScenarios
from testtools import TestCase
from testtools.content import text_content
from testtools.deferredruntest import AsynchronousDeferredRunTest
from testtools.matchers import (
    Equals,
    Is,
    MatchesDict,
    MatchesListwise,
    Not,
    StartsWith,
    )
from twisted.internet import (
    defer,
    reactor,
    utils,
    )
from twisted.web import (
    client,
    http_headers,
    server,
    xmlrpc,
    )

from turnip.pack import helpers
from turnip.pack.git import (
    PackBackendFactory,
    PackFrontendFactory,
    PackVirtFactory,
    )
from turnip.pack.hookrpc import (
    HookRPCHandler,
    HookRPCServerFactory,
    )
from turnip.pack.http import SmartHTTPFrontendResource
from turnip.pack.ssh import SmartSSHService
from turnip.pack.tests.fake_servers import (
    FakeAuthServerService,
    FakeVirtInfoService,
    )
from turnip.version_info import version_info


class FunctionalTestMixin(WithScenarios):

    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=30)

    scenarios = [
        ('v0 protocol', {"protocol_version": b"0"}),
        ('v1 protocol', {"protocol_version": b"1"}),
        ('v2 protocol', {"protocol_version": b"2"}),
        ]

    def startVirtInfo(self):
        # Set up a fake virt information XML-RPC server which just
        # maps paths to their SHA-256 hash.
        self.virtinfo = FakeVirtInfoService(allowNone=True)
        self.virtinfo_listener = reactor.listenTCP(
            0, server.Site(self.virtinfo))
        self.virtinfo_port = self.virtinfo_listener.getHost().port
        self.virtinfo_url = b'http://localhost:%d/' % self.virtinfo_port
        self.addCleanup(self.virtinfo_listener.stopListening)
        self.virtinfo.ref_permissions = {
            b'refs/heads/master': ['create', 'push']}
        config.defaults['virtinfo_endpoint'] = self.virtinfo_url

    def startHookRPC(self):
        self.hookrpc_handler = HookRPCHandler(self.virtinfo_url, 15)
        # XXX cjwatson 2018-11-20: Use bytes so that shutil.rmtree doesn't
        # get confused on Python 2.
        dir = tempfile.mkdtemp(prefix=b'turnip-test-hook-')
        self.addCleanup(shutil.rmtree, dir, ignore_errors=True)

        self.hookrpc_sock_path = os.path.join(dir, b'hookrpc_sock')
        self.hookrpc_listener = reactor.listenUNIX(
            self.hookrpc_sock_path, HookRPCServerFactory(self.hookrpc_handler))
        self.addCleanup(self.hookrpc_listener.stopListening)

    def startPackBackend(self):
        # XXX cjwatson 2018-11-20: Use bytes so that shutil.rmtree doesn't
        # get confused on Python 2.
        self.root = tempfile.mkdtemp(prefix=b'turnip-test-root-')
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.backend_listener = reactor.listenTCP(
            0,
            PackBackendFactory(
                self.root, self.hookrpc_handler, self.hookrpc_sock_path))
        self.backend_port = self.backend_listener.getHost().port
        self.addCleanup(self.backend_listener.stopListening)

    def getProcessOutput(self, executable, args=(), env=None, path=None,
                         reactor=None, errortoo=0):
        if executable == b'git':
            protocol_args = [
                b'-c', b'protocol.version=%s' % self.protocol_version]
            args = protocol_args + list(args)
            args = tuple(args)
        return utils.getProcessOutput(
            executable, args, env=env or {}, path=path, reactor=reactor,
            errortoo=errortoo)

    def getProcessOutputAndValue(self, executable, args=(), env=None,
                                 path=None, reactor=None):
        if executable == b'git':
            protocol_args = [
                b'-c', b'protocol.version=%s' % self.protocol_version]
            args = protocol_args + list(args)
            args = tuple(args)
        return utils.getProcessOutputAndValue(
            executable, args, env=env or {}, path=path, reactor=reactor)

    @defer.inlineCallbacks
    def assertCommandSuccess(self, command, path='.'):
        if command[0] == b'git' and getattr(self, 'protocol_version', None):
            args = list(command[1:])
            command = [b'git']
            command.extend(
                [b'-c', b'protocol.version=%s' % self.protocol_version])
            command.extend(args)
        out, err, code = yield utils.getProcessOutputAndValue(
            command[0], command[1:], env=os.environ, path=path)
        if code != 0:
            self.addDetail('stdout', text_content(six.ensure_text(out)))
            self.addDetail('stderr', text_content(six.ensure_text(err)))
            self.assertEqual(0, code)
        defer.returnValue(out)

    @defer.inlineCallbacks
    def assertCommandFailure(self, command, path='.'):
        if command[0] == b'git' and getattr(self, 'protocol_version', None):
            args = list(command[1:])
            command = [
                b'git', b'-c', b'protocol.version=%s' % self.protocol_version
            ] + args
        out, err, code = yield utils.getProcessOutputAndValue(
            command[0], command[1:], env=os.environ, path=path)
        if code == 0:
            self.addDetail('stdout', text_content(six.ensure_text(out)))
            self.addDetail('stderr', text_content(six.ensure_text(err)))
            self.assertNotEqual(0, code)
        defer.returnValue((out, err))

    @defer.inlineCallbacks
    def test_clone_and_push(self):
        # Test a full clone, commit, push, clone, commit, push, pull
        # cycle using the backend server.
        test_root = self.useFixture(TempDir()).path
        clone1 = os.path.join(test_root, 'clone1')
        clone2 = os.path.join(test_root, 'clone2')

        # Clone the empty repo from the backend and commit to it.
        yield self.assertCommandSuccess((b'git', b'clone', self.url, clone1))
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.name', b'Test User'), path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.email', b'test@example.com'),
            path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Committed test'),
            path=clone1)

        # There are no "matching" branches yet, so an attempt to push all
        # matching branches will exit early on the client side and not push
        # anything.  Make sure that the frontend disconnects appropriately.
        out, err, code = yield self.getProcessOutputAndValue(
            b'git', (b'push', b'origin', b':'), env=os.environ, path=clone1)
        self.assertEqual(b'', out)
        self.assertIn(b'No refs in common and none specified', err)
        self.assertEqual(0, code)

        # Push it back up to the backend.
        yield self.assertCommandSuccess(
            (b'git', b'push', b'origin', b'master'), path=clone1)

        # Re-clone and check that we got the fresh commit.
        yield self.assertCommandSuccess((b'git', b'clone', self.url, clone2))
        out = yield self.assertCommandSuccess(
            (b'git', b'log', b'--oneline', b'-n', b'1'), path=clone2)
        self.assertIn(b'Committed test', out)

        # Commit and push from the second clone.
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.name', b'Test User'), path=clone2)
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.email', b'test@example.com'),
            path=clone2)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Another test'),
            path=clone2)
        yield self.assertCommandSuccess((b'git', b'push'), path=clone2)

        # Pull into the first clone and check for the second commit.
        yield self.assertCommandSuccess((b'git', b'pull'), path=clone1)
        out = yield self.assertCommandSuccess(
            (b'git', b'log', b'--oneline', b'-n', b'1'), path=clone1)
        self.assertIn(b'Another test', out)

    @defer.inlineCallbacks
    def test_push_forked_repository(self):
        # Test that repository creation in the backend is working.
        test_root = self.useFixture(TempDir()).path
        clone = os.path.join(test_root, 'clone1')

        # Clone the empty repo from the backend.
        yield self.assertCommandSuccess((b'git', b'clone', self.url, clone))
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.name', b'Test User'), path=clone)
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.email', b'test@example.com'),
            path=clone)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Committed test'),
            path=clone)

        # Add a new remote to the backend, indicating to XML-RPC fake server
        # that it should request a new repository creation, cloning from the
        # existing repository.
        url = list(urlsplit(self.url))
        new_path = b'/+rw/example-new/clone-from:%s' % url[2]
        url[2] = new_path
        url = urlunsplit(url)
        yield self.assertCommandSuccess(
            (b'git', b'remote', b'add', b'myorigin', url), path=clone)
        yield self.assertCommandSuccess(
            (b'git', b'push', b'-u', b'myorigin', b'master'), path=clone)

        self.assertEqual(
            [(self.virtinfo.getInternalPath(new_path), )],
            self.virtinfo.confirm_repo_creation_call_args)

    @defer.inlineCallbacks
    def test_push_new_repository(self):
        # Test that repository creation in the backend is working.
        test_root = self.useFixture(TempDir()).path
        clone = os.path.join(test_root, 'clone1')

        # Clone the empty repo from the backend.
        yield self.assertCommandSuccess((b'git', b'init', clone))
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.name', b'Test User'), path=clone)
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.email', b'test@example.com'),
            path=clone)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Committed test'),
            path=clone)

        # Add a new remote to the backend, indicating to XML-RPC fake server
        # that it should request a new repository creation.
        url = list(urlsplit(self.url))
        url[2] = b'/+rw/example-new'
        url = urlunsplit(url)
        yield self.assertCommandSuccess(
            (b'git', b'remote', b'add', b'myorigin', url), path=clone)
        yield self.assertCommandSuccess(
            (b'git', b'push', b'-u', b'myorigin', b'master'), path=clone)

    @defer.inlineCallbacks
    def test_clone_shallow(self):
        # Test a shallow clone. This makes the negotation a little more
        # complicated, and tests some weird edge cases in the HTTP protocol.
        test_root = self.useFixture(TempDir()).path
        clone1 = os.path.join(test_root, 'clone1')
        clone2 = os.path.join(test_root, 'clone2')

        # Push a commit that we can try to clone.
        yield self.assertCommandSuccess((b'git', b'clone', self.url, clone1))
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.name', b'Test User'), path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.email', b'test@example.com'),
            path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Committed test'),
            path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'push', b'origin', b'master'), path=clone1)

        # Try to shallow clone.
        yield self.assertCommandSuccess(
            (b'git', b'clone', '--depth=1', self.url, clone2))
        out = yield self.assertCommandSuccess(
            (b'git', b'log', b'--oneline', b'-n', b'1'), path=clone2)
        self.assertIn(b'Committed test', out)

    @defer.inlineCallbacks
    def test_no_repo(self):
        test_root = self.useFixture(TempDir()).path
        parsed_url = list(urlsplit(self.url))
        parsed_url[2] = b'/fail'
        fail_url = urlunsplit(parsed_url)
        output = yield self.getProcessOutput(
            b'git', (b'clone', fail_url),
            env=os.environ, path=test_root, errortoo=True)
        self.assertIn(
            b"Cloning into 'fail'...\n" + self.early_error + b'fatal: ',
            output)
        self.assertIn(b'does not appear to be a git repository', output)

    @defer.inlineCallbacks
    def test_no_permissions(self):
        # Update the test ref_permissions
        self.virtinfo.ref_permissions = {b'refs/heads/master': ['push']}
        # Test a push fails if the user has no permissions to that ref
        test_root = self.useFixture(TempDir()).path
        clone1 = os.path.join(test_root, 'clone1')

        # Clone the empty repo from the backend and commit to it.
        yield self.assertCommandSuccess((b'git', b'clone', self.url, clone1))
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.name', b'Test User'), path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.email', b'test@example.com'),
            path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Committed test'),
            path=clone1)

        # This should fail to push.
        output, error = yield self.assertCommandFailure(
            (b'git', b'push', b'origin', b'master'), path=clone1)
        self.assertIn(
            b'You do not have permission to create refs/heads/master.',
            error)

        # add create, disable push
        self.virtinfo.ref_permissions = {b'refs/heads/master': ['create']}
        # Can now create the ref
        yield self.assertCommandSuccess(
            (b'git', b'push', b'origin', b'master'), path=clone1)

        # But can't push a new commit.
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Second test'),
            path=clone1)
        output, error = yield self.assertCommandFailure(
            (b'git', b'push', b'origin', b'master'), path=clone1)
        self.assertIn(
            b"You do not have permission to push to refs/heads/master", error)

    @defer.inlineCallbacks
    def test_push_non_ascii_refs(self):
        # Pushing non-ASCII refs works.
        self.virtinfo.ref_permissions = {
            b'refs/heads/\x80': ['create', 'push'],
            u'refs/heads/\N{SNOWMAN}'.encode('UTF-8'): ['create', 'push'],
            }
        test_root = self.useFixture(TempDir()).path
        clone1 = os.path.join(test_root, 'clone1')
        clone2 = os.path.join(test_root, 'clone2')
        yield self.assertCommandSuccess((b'git', b'clone', self.url, clone1))
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.name', b'Test User'), path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.email', b'test@example.com'),
            path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Non-ASCII test'),
            path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'push', b'origin', b'master:\x80',
             u'master:\N{SNOWMAN}'.encode('UTF-8')), path=clone1)
        # We get the new branches when we re-clone.
        yield self.assertCommandSuccess((b'git', b'clone', self.url, clone2))
        out = yield self.assertCommandSuccess(
            (b'git', b'for-each-ref', b'--format=%(refname)',
             b'refs/remotes/origin/*'),
            path=clone2)
        self.assertEqual(
            sorted([
                b'refs/remotes/origin/\x80',
                u'refs/remotes/origin/\N{SNOWMAN}'.encode('UTF-8')]),
            sorted(out.splitlines()))

    @defer.inlineCallbacks
    def test_force_push(self):
        # Update the test ref_permissions
        self.virtinfo.ref_permissions = {
            b'refs/heads/master': ['create', 'push']}

        # Test a force-push fails if the user has no permissions
        test_root = self.useFixture(TempDir()).path
        clone1 = os.path.join(test_root, 'clone1')

        # Clone the empty repo from the backend and commit to it.
        yield self.assertCommandSuccess((b'git', b'clone', self.url, clone1))
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.name', b'Test User'), path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.email', b'test@example.com'),
            path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Committed test'),
            path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Second test'),
            path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Third test'),
            path=clone1)

        # Push the changes
        yield self.assertCommandSuccess(
            (b'git', b'push', b'origin', b'master'), path=clone1)

        # Squash some commits to force a non-fast-forward commit
        yield self.assertCommandSuccess(
            (b'git', b'reset', b'--soft', b'HEAD~2'),
            path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Rebase'),
            path=clone1)

        output, error = yield self.assertCommandFailure(
            (b'git', b'push', b'origin', b'master', b'--force'), path=clone1)
        self.assertIn(
            b"You do not have permission to force-push to", error)

    @defer.inlineCallbacks
    def test_large_push(self):
        # Test a large push, which behaves a bit differently with some
        # frontends.  For example, when doing a large push, as an
        # optimisation, git-remote-http first probes to find out whether it
        # is permitted to write to the repository before sending large
        # packfile data.  It does this by sending a request containing just
        # a flush-pkt, which causes git-receive-pack to exit successfully
        # with no output.
        test_root = self.useFixture(TempDir()).path
        clone1 = os.path.join(test_root, 'clone1')
        clone2 = os.path.join(test_root, 'clone2')

        # Push a commit large enough to generate a pack that exceeds git's
        # allocated buffer for HTTP pushes, thereby triggering 'probe_rpc'.
        yield self.assertCommandSuccess((b'git', b'clone', self.url, clone1))
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.name', b'Test User'), path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.email', b'test@example.com'),
            path=clone1)
        with open(os.path.join(clone1, 'bigfile'), 'w') as bigfile:
            # Use random contents to defeat compression.
            bigfile.write(bytearray(
                random.getrandbits(8) for _ in range(1024 * 1024)))
        yield self.assertCommandSuccess(
            (b'git', b'add', b'bigfile'), path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'-m', b'Add big file'), path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'push', b'--all', b'origin'), path=clone1)

        # Clone it again and make sure it's there.
        yield self.assertCommandSuccess((b'git', b'clone', self.url, clone2))
        out = yield self.assertCommandSuccess(
            (b'git', b'log', b'--oneline', b'-n', b'1'), path=clone2)
        self.assertIn(b'Add big file', out)

    @defer.inlineCallbacks
    def test_delete_ref(self):
        self.virtinfo.ref_permissions = {
            b'refs/heads/newref': ['create', 'push', 'force_push']}
        test_root = self.useFixture(TempDir()).path
        clone1 = os.path.join(test_root, 'clone1')

        # Clone the empty repo from the backend and commit to it.
        yield self.assertCommandSuccess((b'git', b'clone', self.url, clone1))
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.name', b'Test User'), path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.email', b'test@example.com'),
            path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Committed test'),
            path=clone1)

        yield self.assertCommandSuccess(
            (b'git', b'checkout', b'-b', b'newref'), path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Committed test'),
            path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'push', b'origin', b'newref'), path=clone1)

        out, err, code = yield self.getProcessOutputAndValue(
            b'git', (b'push', b'origin', b':newref'),
            env=os.environ, path=clone1)
        # Check that the GIT_OID_HEX_ZERO does not appear in our output,
        # as it would if the merge-base call has failed because it's attempted
        # to find its ancestry.
        self.assertNotIn(GIT_OID_HEX_ZERO, err)
        self.assertIn(b'[deleted]', err)
        self.assertEqual(0, code)

    @defer.inlineCallbacks
    def test_delete_ref_without_permission(self):
        self.virtinfo.ref_permissions = {
            b'refs/heads/newref': ['create', 'push']}
        test_root = self.useFixture(TempDir()).path
        clone1 = os.path.join(test_root, 'clone1')

        # Clone the empty repo from the backend and commit to it.
        yield self.assertCommandSuccess((b'git', b'clone', self.url, clone1))
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.name', b'Test User'), path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.email', b'test@example.com'),
            path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Committed test'),
            path=clone1)

        yield self.assertCommandSuccess(
            (b'git', b'checkout', b'-b', b'newref'), path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Committed test'),
            path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'push', b'origin', b'newref'), path=clone1)

        out, err, code = yield self.getProcessOutputAndValue(
            b'git', (b'push', b'origin', b':newref'),
            env=os.environ, path=clone1)
        # Check that the GIT_OID_HEX_ZERO does not appear in our output,
        # as it would if the merge-base call has failed because it's attempted
        # to find its ancestry.
        self.assertNotIn(GIT_OID_HEX_ZERO, err)
        self.assertIn(
            b'You do not have permission to force-push to refs/heads/newref',
            err
            )
        self.assertEqual(1, code)


class TestBackendFunctional(FunctionalTestMixin, TestCase):

    scheme = b'git'
    early_error = b'fatal: remote error: '

    @defer.inlineCallbacks
    def setUp(self):
        super(TestBackendFunctional, self).setUp()

        # Set up a PackBackendFactory on a free port in a clean repo root.
        self.startVirtInfo()
        self.startHookRPC()
        self.startPackBackend()
        self.port = self.backend_port

        yield self.assertCommandSuccess(
            (b'git', b'init', b'--bare', b'test'), path=self.root)
        self.url = b'git://localhost:%d/test' % self.port

    def test_push_new_repository(self):
        """It doesn't make sense to test this when connecting directly to
        a backend, since it depends on some reaction from XML-RPC's
        translatePath, called only by PackVirtServer."""
        self.skipTest(
            "Skipping test that depends on XML-RPC when connecting "
            "directly to the backend.")

    test_push_forked_repository = test_push_new_repository


class FrontendFunctionalTestMixin(FunctionalTestMixin):

    @defer.inlineCallbacks
    def setUp(self):
        super(FrontendFunctionalTestMixin, self).setUp()

        self.data_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "data"))

        # Set up a fake authserver.
        self.authserver = FakeAuthServerService()
        self.authserver_listener = reactor.listenTCP(
            0, server.Site(self.authserver))
        self.authserver_port = self.authserver_listener.getHost().port
        self.authserver_url = b'http://localhost:%d/' % self.authserver_port

        # Run a backend server in a repo root containing an empty repo
        # for the path '/test'.
        self.startVirtInfo()
        self.startHookRPC()
        self.startPackBackend()
        self.internal_name = six.ensure_binary(hashlib.sha256(
            b'/test').hexdigest())
        yield self.assertCommandSuccess(
            (b'git', b'init', b'--bare', self.internal_name), path=self.root)

        self.virt_listener = reactor.listenTCP(
            0,
            PackVirtFactory(
                b'localhost', self.backend_port, self.virtinfo_url, 15))
        self.virt_port = self.virt_listener.getHost().port
        self.virtinfo.ref_permissions = {
            b'refs/heads/master': ['create', 'push']}

    @defer.inlineCallbacks
    def tearDown(self):
        super(FrontendFunctionalTestMixin, self).tearDown()
        yield self.virt_listener.stopListening()
        yield self.authserver_listener.stopListening()

    @defer.inlineCallbacks
    def test_read_only(self):
        self.virtinfo.ref_permissions = {
            b'refs/heads/master': ['create', 'push']}
        test_root = self.useFixture(TempDir()).path
        clone1 = os.path.join(test_root, 'clone1')
        clone2 = os.path.join(test_root, 'clone2')

        # Create a read-only clone.
        yield self.assertCommandSuccess(
            (b'git', b'clone', self.ro_url, clone1))
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.name', b'Test User'), path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.email', b'test@example.com'),
            path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Committed test'),
            path=clone1)

        # A push attempt is rejected.
        out = yield self.getProcessOutput(
            b'git', (b'push', b'origin', b'master'),
            env=os.environ, path=clone1, errortoo=True)
        self.assertThat(
            out, StartsWith(self.early_error + b'Repository is read-only'))
        self.assertEqual([], self.virtinfo.push_notifications)

        # The remote repository is still empty.
        out = yield self.getProcessOutput(
            b'git', (b'clone', self.ro_url, clone2),
            env=os.environ, errortoo=True)
        self.assertIn(b'You appear to have cloned an empty repository.', out)

        # But the push succeeds if we switch the remote to the writable URL.
        yield self.assertCommandSuccess(
            (b'git', b'remote', b'set-url', b'origin', self.url), path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'push', b'origin', b'master'), path=clone1)
        self.assertEqual(
            [self.internal_name], self.virtinfo.push_notifications)

    @defer.inlineCallbacks
    def test_unicode_fault(self):
        def fake_translatePath(pathname, permission, auth_params):
            raise xmlrpc.Fault(2, u"홍길동 is not a member of Project Team.")

        test_root = self.useFixture(TempDir()).path
        self.virtinfo.xmlrpc_translatePath = fake_translatePath
        output = yield self.getProcessOutput(
            b'git',
            (b'clone', b'%s://localhost:%d/fail' % (self.scheme, self.port)),
            env=os.environ, path=test_root, errortoo=True)
        self.assertIn(
            b"Cloning into 'fail'...\n" + self.early_error +
            u"홍길동 is not a member of Project Team.".encode("UTF-8"),
            output)


class TestGitFrontendFunctional(FrontendFunctionalTestMixin, TestCase):

    scheme = b'git'
    early_error = b'fatal: remote error: '

    @defer.inlineCallbacks
    def setUp(self):
        yield super(TestGitFrontendFunctional, self).setUp()

        # We run a frontend server connecting to the backend and
        # virtinfo servers started by the mixin.
        self.frontend_listener = reactor.listenTCP(
            0, PackFrontendFactory(b'localhost', self.virt_port))
        self.port = self.frontend_listener.getHost().port

        # Always use a writable URL for now.
        self.url = b'git://localhost:%d/+rw/test' % self.port
        self.ro_url = b'git://localhost:%d/test' % self.port

    @defer.inlineCallbacks
    def tearDown(self):
        yield super(TestGitFrontendFunctional, self).tearDown()
        yield self.frontend_listener.stopListening()


class TestSmartHTTPFrontendFunctional(FrontendFunctionalTestMixin, TestCase):

    scheme = b'http'
    early_error = b'remote: '

    @defer.inlineCallbacks
    def setUp(self):
        yield super(TestSmartHTTPFrontendFunctional, self).setUp()

        # We run a frontend server connecting to the backend and
        # virtinfo servers started by the mixin.
        frontend_site = server.Site(
            SmartHTTPFrontendResource({
                "main_site_root": "https://launchpad.test/",
                "pack_virt_host": "localhost",
                "pack_virt_port": self.virt_port,
                "repo_store": self.root,
                "virtinfo_endpoint": self.virtinfo_url,
                "virtinfo_timeout": "15",
                }))
        self.frontend_listener = reactor.listenTCP(0, frontend_site)
        self.port = self.frontend_listener.getHost().port

        # Always use a writable URL for now.
        self.url = b'http://localhost:%d/+rw/test' % self.port
        self.ro_url = b'http://localhost:%d/test' % self.port

    @defer.inlineCallbacks
    def tearDown(self):
        yield super(TestSmartHTTPFrontendFunctional, self).tearDown()
        yield self.frontend_listener.stopListening()

    @defer.inlineCallbacks
    def test_root_revision_header(self):
        response = yield client.Agent(reactor).request(
            b'HEAD', b'http://localhost:%d/' % self.port)
        self.assertEqual(302, response.code)
        self.assertEqual(
            [version_info['revision_id']],
            response.headers.getRawHeaders(b'X-Turnip-Revision'))

    def make_set_symbolic_ref_request(self, line):
        parsed_url = urlsplit(self.url)
        url = urlunsplit([
            parsed_url.scheme,
            b'%s:%d' % (parsed_url.hostname, parsed_url.port),
            parsed_url.path + b'/turnip-set-symbolic-ref', b'', b''])
        headers = {
            b'Content-Type': [
                b'application/x-turnip-set-symbolic-ref-request',
                ],
            }
        if parsed_url.username:
            headers[b'Authorization'] = [
                b'Basic ' + base64.b64encode(
                    b'%s:%s' % (parsed_url.username, parsed_url.password)),
                ]
        data = helpers.encode_packet(line) + helpers.encode_packet(None)
        return client.Agent(reactor).request(
            b'POST', url, headers=http_headers.Headers(headers),
            bodyProducer=client.FileBodyProducer(io.BytesIO(data)))

    @defer.inlineCallbacks
    def get_symbolic_ref(self, path, name):
        out = yield self.getProcessOutput(
            b'git', (b'symbolic-ref', name), env=os.environ, path=path)
        defer.returnValue(out.rstrip(b'\n'))

    @defer.inlineCallbacks
    def test_turnip_set_symbolic_ref(self):
        repo = os.path.join(self.root, self.internal_name)
        head_target = yield self.get_symbolic_ref(repo, b'HEAD')
        self.assertEqual(b'refs/heads/master', head_target)
        response = yield self.make_set_symbolic_ref_request(
            b'HEAD refs/heads/new-head')
        self.assertEqual(200, response.code)
        body = yield client.readBody(response)
        self.assertEqual((b'ACK HEAD\n', b''), helpers.decode_packet(body))
        head_target = yield self.get_symbolic_ref(repo, b'HEAD')
        self.assertEqual(b'refs/heads/new-head', head_target)
        self.assertEqual(
            [self.internal_name], self.virtinfo.push_notifications)

    @defer.inlineCallbacks
    def test_turnip_set_symbolic_ref_error(self):
        repo = os.path.join(self.root, self.internal_name)
        head_target = yield self.get_symbolic_ref(repo, b'HEAD')
        self.assertEqual(b'refs/heads/master', head_target)
        response = yield self.make_set_symbolic_ref_request(b'HEAD --evil')
        # This is a little weird since an error occurred, but it's
        # consistent with other HTTP pack protocol responses.
        self.assertEqual(200, response.code)
        body = yield client.readBody(response)
        self.assertEqual(
            (b'ERR Symbolic ref target may not start with "-"\n', b''),
            helpers.decode_packet(body))
        head_target = yield self.get_symbolic_ref(repo, b'HEAD')
        self.assertEqual(b'refs/heads/master', head_target)


class TestSmartHTTPFrontendWithAuthFunctional(TestSmartHTTPFrontendFunctional):

    @defer.inlineCallbacks
    def setUp(self):
        yield super(TestSmartHTTPFrontendWithAuthFunctional, self).setUp()

        self.virtinfo.require_auth = True
        self.url = (
            b'http://test-user:test-password@localhost:%d/+rw/test' %
            self.port)
        self.ro_url = (
            b'http://test-user:test-password@localhost:%d/test' % self.port)

    @defer.inlineCallbacks
    def test_authenticated(self):
        test_root = self.useFixture(TempDir()).path
        clone = os.path.join(test_root, 'clone')
        yield self.assertCommandSuccess((b'git', b'clone', self.ro_url, clone))
        expected_requests = 1 if self.protocol_version in (b'0', b'1') else 2
        self.assertEqual(
            [(b'test-user', b'test-password')] * expected_requests,
            self.virtinfo.authentications)
        self.assertEqual(expected_requests, len(self.virtinfo.translations))
        for translation in self.virtinfo.translations:
            self.assertThat(translation, MatchesListwise([
                Equals(b'/test'), Equals(b'read'),
                MatchesDict({
                    b'can-authenticate': Is(True),
                    b'request-id': Not(Is(None)),
                    b'user': Equals(b'test-user')})
                ]))

    @defer.inlineCallbacks
    def test_authenticated_push(self):
        test_root = self.useFixture(TempDir()).path
        clone = os.path.join(test_root, 'clone')
        yield self.assertCommandSuccess((b'git', b'clone', self.url, clone))
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.name', b'Test User'), path=clone)
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.email', b'test@example.com'),
            path=clone)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Committed test'),
            path=clone)
        yield self.assertCommandSuccess(
            (b'git', b'push', b'origin', b'master'), path=clone)
        self.assertThat(self.virtinfo.ref_permissions_checks, MatchesListwise([
            MatchesListwise([
                Equals(self.internal_name),
                Equals([b'refs/heads/master']),
                MatchesDict({
                    b'can-authenticate': Is(True),
                    b'request-id': Not(Is(None)),
                    b'user': Equals(b'test-user'),
                    })])]))


class TestSmartSSHServiceFunctional(FrontendFunctionalTestMixin, TestCase):

    scheme = b'ssh'
    early_error = b'fatal: remote error: '

    @defer.inlineCallbacks
    def setUp(self):
        yield super(TestSmartSSHServiceFunctional, self).setUp()

        config = os.path.join(self.root, "ssh-config")
        known_hosts = os.path.join(self.root, "known_hosts")
        private_key = os.path.join(self.root, "ssh-key")
        shutil.copy2(os.path.join(self.data_dir, "ssh-key"), private_key)
        os.chmod(private_key, stat.S_IRUSR | stat.S_IWUSR)
        public_key = os.path.join(self.data_dir, "ssh-key.pub")
        with open(config, "w") as config_file:
            print("IdentitiesOnly yes", file=config_file)
            print("IdentityFile %s" % private_key, file=config_file)
            print("StrictHostKeyChecking no", file=config_file)
            print("User example", file=config_file)
            print("UserKnownHostsFile %s" % known_hosts, file=config_file)
        git_ssh = os.path.join(self.root, "ssh-wrapper")
        with open(git_ssh, "w") as git_ssh_file:
            print('#! /bin/sh', file=git_ssh_file)
            print('ssh -F %s "$@"' % config, file=git_ssh_file)
        new_mode = (
            os.stat(git_ssh).st_mode |
            stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.chmod(git_ssh, new_mode)
        self.useFixture(EnvironmentVariable("GIT_SSH", git_ssh))

        self.authserver.addSSHKey("example", public_key)

        # We run a service connecting to the backend and authserver servers
        # started by the mixin.
        private_host_key = os.path.join(self.root, "ssh-host-key")
        shutil.copy2(
            os.path.join(self.data_dir, "ssh-host-key"), private_host_key)
        os.chmod(private_host_key, stat.S_IRUSR | stat.S_IWUSR)
        public_host_key = os.path.join(self.data_dir, "ssh-host-key.pub")
        self.service = SmartSSHService(
            b'localhost', self.virt_port, self.authserver_url,
            private_key_path=private_host_key, public_key_path=public_host_key,
            main_log="turnip", access_log="turnip.access",
            access_log_path=os.path.join(self.root, "access.log"),
            strport=b'tcp:0', moduli_path="/etc/ssh/moduli")
        self.service.startService()
        self.addCleanup(self.service.stopService)
        socket = self.service.service._waitingForPort.result.socket
        self.port = socket.getsockname()[1]

        # Connect to the service with the command "true".  We expect this to
        # fail, but it will populate known_hosts as a side-effect so that we
        # don't have to filter out "Warning: Permanently added ..." messages
        # later on.
        code = yield utils.getProcessValue(
            git_ssh.encode("UTF-8"),
            (b'-p', str(self.port).encode("UTF-8"), b'localhost', b'true'))
        self.assertNotEqual(0, code)

        # Always use a writable URL for now.
        self.url = b'ssh://localhost:%d/+rw/test' % self.port
        self.ro_url = b'ssh://localhost:%d/test' % self.port
