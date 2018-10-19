# -*- coding: utf-8 -*-
# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import base64
from collections import defaultdict
import hashlib
import io
import os
import random
import shutil
import stat
import tempfile
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
from lazr.sshserver.auth import NoSuchPersonWithName
from testtools import TestCase
from testtools.content import text_content
from testtools.deferredruntest import AsynchronousDeferredRunTest
from testtools.matchers import StartsWith
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
from turnip.version_info import version_info


class FakeAuthServerService(xmlrpc.XMLRPC):
    """A fake version of the Launchpad authserver service."""

    def __init__(self):
        xmlrpc.XMLRPC.__init__(self)
        self.keys = defaultdict(list)

    def addSSHKey(self, username, public_key_path):
        with open(public_key_path, "r") as f:
            public_key = f.read()
        kind, keytext, _ = public_key.split(" ", 2)
        if kind == "ssh-rsa":
            keytype = "RSA"
        elif kind == "ssh-dss":
            keytype = "DSA"
        else:
            raise Exception("Unrecognised public key type %s" % kind)
        self.keys[username].append((keytype, keytext))

    def xmlrpc_getUserAndSSHKeys(self, username):
        if username not in self.keys:
            raise NoSuchPersonWithName(username)
        return {
            "id": hash(username) % (2 ** 31),
            "name": username,
            "keys": self.keys[username],
            }


class FakeVirtInfoService(xmlrpc.XMLRPC):
    """A trivial virt information XML-RPC service.

    Translates a path to its SHA-256 hash. The repo is writable if the
    path is prefixed with '/+rw'
    """

    def __init__(self, *args, **kwargs):
        xmlrpc.XMLRPC.__init__(self, *args, **kwargs)
        self.require_auth = False
        self.translations = []
        self.authentications = []
        self.push_notifications = []
        self.ref_rules = []

    def xmlrpc_translatePath(self, pathname, permission, auth_params):
        if self.require_auth and 'user' not in auth_params:
            raise xmlrpc.Fault(3, "Unauthorized")

        self.translations.append((pathname, permission, auth_params))
        writable = False
        if pathname.startswith('/+rw'):
            writable = True
            pathname = pathname[4:]

        if permission != b'read' and not writable:
            raise xmlrpc.Fault(2, "Repository is read-only")
        return {'path': hashlib.sha256(pathname).hexdigest()}

    def xmlrpc_authenticateWithPassword(self, username, password):
        self.authentications.append((username, password))
        return {'user': username}

    def xmlrpc_notify(self, path):
        self.push_notifications.append(path)

    def xmlrpc_checkRefPermissions(self, path, ref_paths, auth_params):
        return self.ref_rules


class FunctionalTestMixin(object):

    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=5)

    def startVirtInfo(self):
        # Set up a fake virt information XML-RPC server which just
        # maps paths to their SHA-256 hash.
        self.virtinfo = FakeVirtInfoService(allowNone=True)
        self.virtinfo_listener = reactor.listenTCP(
            0, server.Site(self.virtinfo))
        self.virtinfo_port = self.virtinfo_listener.getHost().port
        self.virtinfo_url = b'http://localhost:%d/' % self.virtinfo_port
        self.addCleanup(self.virtinfo_listener.stopListening)
        self.virtinfo.ref_rules = {'refs/heads/master': ['create', 'push']}

    def startHookRPC(self):
        self.hookrpc_handler = HookRPCHandler(self.virtinfo_url)
        dir = tempfile.mkdtemp(prefix='turnip-test-hook-')
        self.addCleanup(shutil.rmtree, dir, ignore_errors=True)

        self.hookrpc_path = os.path.join(dir, 'hookrpc_sock')
        self.hookrpc_listener = reactor.listenUNIX(
            self.hookrpc_path, HookRPCServerFactory(self.hookrpc_handler))
        self.addCleanup(self.hookrpc_listener.stopListening)

    def startPackBackend(self):
        self.root = tempfile.mkdtemp(prefix='turnip-test-root-')
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.backend_listener = reactor.listenTCP(
            0,
            PackBackendFactory(
                self.root, self.hookrpc_handler, self.hookrpc_path,
                virtinfo_endpoint=self.virtinfo_url))
        self.backend_port = self.backend_listener.getHost().port
        self.addCleanup(self.backend_listener.stopListening)

    @defer.inlineCallbacks
    def assertCommandSuccess(self, command, path='.'):
        out, err, code = yield utils.getProcessOutputAndValue(
            command[0], command[1:], env=os.environ, path=path)
        if code != 0:
            self.addDetail('stdout', text_content(out))
            self.addDetail('stderr', text_content(err))
            self.assertEqual(0, code)
        defer.returnValue(out)

    @defer.inlineCallbacks
    def assertCommandFailure(self, command, path='.'):
        out, err, code = yield utils.getProcessOutputAndValue(
            command[0], command[1:], env=os.environ, path=path)
        if code == 0:
            self.addDetail('stdout', text_content(out))
            self.addDetail('stderr', text_content(err))
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
        out, err, code = yield utils.getProcessOutputAndValue(
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
        output = yield utils.getProcessOutput(
            b'git', (b'clone', fail_url),
            env=os.environ, path=test_root, errortoo=True)
        self.assertIn(
            b"Cloning into 'fail'...\n" + self.early_error + b'fatal: ',
            output)
        self.assertIn(b'does not appear to be a git repository', output)

    @defer.inlineCallbacks
    def test_no_permissions(self):
        # Update the test ref_rules
        self.virtinfo.ref_rules = {'refs/heads/master': ['push']}
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
        self.virtinfo.ref_rules = {'refs/heads/master': ['create']}
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
    def test_force_push(self):
        # Update the test ref_rules
        self.virtinfo.ref_rules = {'refs/heads/master': ['create', 'push']}

        # Test a force push fails if the user has no permissions
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
            b"You do not have permission to force push to", error)

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
        self.internal_name = hashlib.sha256(b'/test').hexdigest()
        yield self.assertCommandSuccess(
            (b'git', b'init', b'--bare', self.internal_name), path=self.root)

        self.virt_listener = reactor.listenTCP(
            0,
            PackVirtFactory(
                b'localhost', self.backend_port, self.virtinfo_url))
        self.virt_port = self.virt_listener.getHost().port
        self.virtinfo.ref_rules = {'refs/heads/master': ['create', 'push']}

    @defer.inlineCallbacks
    def tearDown(self):
        super(FrontendFunctionalTestMixin, self).tearDown()
        yield self.virt_listener.stopListening()
        yield self.authserver_listener.stopListening()

    @defer.inlineCallbacks
    def test_read_only(self):
        self.virtinfo.ref_rules = {'refs/heads/master': ['create', 'push']}
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
        out = yield utils.getProcessOutput(
            b'git', (b'push', b'origin', b'master'),
            env=os.environ, path=clone1, errortoo=True)
        self.assertThat(
            out, StartsWith(self.early_error + b'Repository is read-only'))
        self.assertEqual([], self.virtinfo.push_notifications)

        # The remote repository is still empty.
        out = yield utils.getProcessOutput(
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
        output = yield utils.getProcessOutput(
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
            SmartHTTPFrontendResource(
                b'localhost', {
                    "pack_virt_port": self.virt_port,
                    "virtinfo_endpoint": self.virtinfo_url,
                    "repo_store": self.root,
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
        out = yield utils.getProcessOutput(
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
        self.assertEqual((b'ACK HEAD\n', ''), helpers.decode_packet(body))
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
            (b'ERR Symbolic ref target may not start with "-"\n', ''),
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
        self.assertEqual(
            [(b'test-user', b'test-password')], self.virtinfo.authentications)
        self.assertEqual(
            [(b'/test', b'read',
              {b'can-authenticate': True, b'user': b'test-user'})],
            self.virtinfo.translations)


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
