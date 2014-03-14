from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os.path

from fixtures import TempDir
from testtools import TestCase
from testtools.deferredruntest import AsynchronousDeferredRunTest
from twisted.internet import (
    defer,
    reactor,
    utils,
    )

from turnip.git import GitBackendFactory


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
