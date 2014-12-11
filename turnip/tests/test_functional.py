from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import hashlib
import os.path

from fixtures import TempDir
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
    server,
    xmlrpc,
    )

from turnip.packproto import (
    PackBackendFactory,
    PackFrontendFactory,
    PackVirtFactory,
    )
from turnip.smarthttp import SmartHTTPFrontendResource


class FakeVirtInfoService(xmlrpc.XMLRPC):
    """A trivial virt information XML-RPC service.

    Translates a path to its SHA-256 hash. The repo is writable if the
    path is prefixed with '/+rw'
    """

    def xmlrpc_translatePath(self, pathname, permission):
        writable = False
        if pathname.startswith('/+rw'):
            writable = True
            pathname = pathname[4:]

        if permission != b'read' and not writable:
            raise xmlrpc.Fault(2, "Repository is read-only")
        return {'path': hashlib.sha256(pathname).hexdigest()}


class FunctionalTestMixin(object):

    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=5)

    @defer.inlineCallbacks
    def assertCommandSuccess(self, command, path='.'):
        out, err, code = yield utils.getProcessOutputAndValue(
            command[0], command[1:], path=path)
        if code != 0:
            self.addDetail('stdout', text_content(out))
            self.addDetail('stderr', text_content(err))
            self.assertEqual(0, code)
        defer.returnValue(out)

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
            (b'git', b'config', b'user.email', b'test@example.com'),
            path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Committed test'),
            path=clone1)

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
    def test_no_repo(self):
        test_root = self.useFixture(TempDir()).path
        output = yield utils.getProcessOutput(
            b'git',
            (b'clone', b'%s://localhost:%d/fail' % (self.scheme, self.port)),
            path=test_root, errortoo=True)
        self.assertIn(
            b"Cloning into 'fail'...\n" + self.early_error + b'fatal: ',
            output)
        self.assertIn(b'does not appear to be a git repository', output)


class TestBackendFunctional(FunctionalTestMixin, TestCase):

    scheme = b'git'
    early_error = b'fatal: remote error: '

    @defer.inlineCallbacks
    def setUp(self):
        super(TestBackendFunctional, self).setUp()
        # Set up a PackBackendFactory on a free port in a clean repo root.
        self.root = self.useFixture(TempDir()).path
        self.listener = reactor.listenTCP(0, PackBackendFactory(self.root))
        self.port = self.listener.getHost().port

        yield self.assertCommandSuccess(
            (b'git', b'init', b'--bare', b'test'), path=self.root)
        self.url = b'git://localhost:%d/test' % self.port

    @defer.inlineCallbacks
    def tearDown(self):
        super(TestBackendFunctional, self).tearDown()
        yield self.listener.stopListening()


class FrontendFunctionalTestMixin(FunctionalTestMixin):

    @defer.inlineCallbacks
    def setUp(self):
        super(FrontendFunctionalTestMixin, self).setUp()

        # Set up a fake virt information XML-RPC server which just
        # maps paths to their SHA-256 hash.
        self.virtinfo_listener = reactor.listenTCP(
            0, server.Site(FakeVirtInfoService()))
        self.virtinfo_port = self.virtinfo_listener.getHost().port

        # Run a backend server in a repo root containing an empty repo
        # for the path '/test'.
        self.root = self.useFixture(TempDir()).path
        internal_name = hashlib.sha256(b'/test').hexdigest()
        yield self.assertCommandSuccess(
            (b'git', b'init', b'--bare', internal_name), path=self.root)
        self.backend_listener = reactor.listenTCP(
            0, PackBackendFactory(self.root))
        self.backend_port = self.backend_listener.getHost().port

        self.virt_listener = reactor.listenTCP(
            0,
            PackVirtFactory(
                b'localhost', self.backend_port,
                b'http://localhost:%d/' % self.virtinfo_port))
        self.virt_port = self.virt_listener.getHost().port

    @defer.inlineCallbacks
    def tearDown(self):
        super(FrontendFunctionalTestMixin, self).tearDown()
        yield self.virt_listener.stopListening()
        yield self.backend_listener.stopListening()
        yield self.virtinfo_listener.stopListening()

    @defer.inlineCallbacks
    def test_read_only(self):
        test_root = self.useFixture(TempDir()).path
        clone1 = os.path.join(test_root, 'clone1')
        clone2 = os.path.join(test_root, 'clone2')

        # Create a read-only clone.
        yield self.assertCommandSuccess(
            (b'git', b'clone', self.ro_url, clone1))
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.email', b'test@example.com'),
            path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Committed test'),
            path=clone1)

        # A push attempt is rejected.
        out = yield utils.getProcessOutput(
            b'git', (b'push', b'origin', b'master'), path=clone1,
            errortoo=True)
        self.assertThat(
            out, StartsWith(self.early_error + b'Repository is read-only'))

        # The remote repository is still empty.
        out = yield utils.getProcessOutput(
            b'git', (b'clone', self.ro_url, clone2), errortoo=True)
        self.assertIn(b'You appear to have cloned an empty repository.', out)

        # But the push succeeds if we switch the remote to the writable URL.
        yield self.assertCommandSuccess(
            (b'git', b'remote', b'set-url', b'origin', self.url), path=clone1)
        yield self.assertCommandSuccess(
            (b'git', b'push', b'origin', b'master'), path=clone1)


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
            SmartHTTPFrontendResource(b'localhost', self.virt_port))
        self.frontend_listener = reactor.listenTCP(0, frontend_site)
        self.port = self.frontend_listener.getHost().port

        # Always use a writable URL for now.
        self.url = b'http://localhost:%d/+rw/test' % self.port
        self.ro_url = b'http://localhost:%d/test' % self.port

    @defer.inlineCallbacks
    def tearDown(self):
        yield super(TestSmartHTTPFrontendFunctional, self).tearDown()
        yield self.frontend_listener.stopListening()
