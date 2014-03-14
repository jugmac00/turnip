from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import hashlib
import os.path

from fixtures import TempDir
from testtools import TestCase
from testtools.deferredruntest import AsynchronousDeferredRunTest
from twisted.internet import (
    defer,
    reactor,
    utils,
    )
from twisted.web import (
    server,
    xmlrpc,
    )

from turnip.git import (
    GitBackendFactory,
    GitFrontendFactory,
    )


class FakeVirtService(xmlrpc.XMLRPC):
    """A trivial virt information XML-RPC service.

    Translates a path to its SHA-256 hash. The repo is writable if the
    path is prefixed with '/+rw'
    """

    def xmlrpc_translatePath(self, pathname):
        writable = False
        if pathname.startswith('/+rw'):
            writable = True
            pathname = pathname[4:]
        return {
            'path': hashlib.sha256(pathname).hexdigest(),
            'writable': writable,
            }


class IntegrationTestMixin(object):

    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=30)

    @defer.inlineCallbacks
    def assertCommandSuccess(self, command, path='.'):
        out, err, code = yield utils.getProcessOutputAndValue(
            command[0], command[1:], path=path)
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
            b'git', (b'clone', b'git://localhost:%d/fail' % self.port),
            path=test_root, errortoo=True)
        self.assertIn(b'fatal:', output)


class TestBackendIntegration(IntegrationTestMixin, TestCase):

    @defer.inlineCallbacks
    def setUp(self):
        super(TestBackendIntegration, self).setUp()
        # Set up a GitBackendFactory on a free port in a clean repo root.
        self.root = self.useFixture(TempDir()).path
        self.listener = reactor.listenTCP(0, GitBackendFactory(self.root))
        self.port = self.listener.getHost().port

        yield self.assertCommandSuccess(
            (b'git', b'init', b'--bare', b'test'), path=self.root)
        self.url = b'git://localhost:%d/test' % self.port

    @defer.inlineCallbacks
    def tearDown(self):
        super(TestBackendIntegration, self).tearDown()
        yield self.listener.stopListening()


class TestFrontendIntegration(IntegrationTestMixin, TestCase):

    @defer.inlineCallbacks
    def setUp(self):
        super(TestFrontendIntegration, self).setUp()

        # Set up a fake virt information XML-RPC server which just
        # maps paths to their SHA-256 hash.
        self.virt_listener = reactor.listenTCP(
            0, server.Site(FakeVirtService()))
        self.virt_port = self.virt_listener.getHost().port

        # Run a backend server in a repo root containing an empty repo
        # for the path '/test'.
        self.root = self.useFixture(TempDir()).path
        internal_name = hashlib.sha256(b'/test').hexdigest()
        yield self.assertCommandSuccess(
            (b'git', b'init', b'--bare', internal_name), path=self.root)
        self.backend_listener = reactor.listenTCP(
            0, GitBackendFactory(self.root))
        self.backend_port = self.backend_listener.getHost().port

        # And finally run a frontend server connecting to the backend
        # and virt info servers.
        self.frontend_listener = reactor.listenTCP(
            0,
            GitFrontendFactory(
                b'localhost', self.backend_port,
                b'http://localhost:%d/' % self.virt_port))
        self.port = self.frontend_listener.getHost().port

        # Always use a writable URL for now.
        self.url = b'git://localhost:%d/+rw/test' % self.port

    @defer.inlineCallbacks
    def tearDown(self):
        super(TestFrontendIntegration, self).tearDown()
        yield self.frontend_listener.stopListening()
        yield self.backend_listener.stopListening()
        yield self.virt_listener.stopListening()
