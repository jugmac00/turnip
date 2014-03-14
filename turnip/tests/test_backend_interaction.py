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


class TestBackendIntegration(TestCase):

    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=30)

    def setUp(self):
        super(TestBackendIntegration, self).setUp()
        # Set up a GitBackendFactory on a free port in a clean repo root.
        self.root = self.useFixture(TempDir()).path
        self.listener = reactor.listenTCP(0, GitBackendFactory(self.root))
        self.port = self.listener.getHost().port

    @defer.inlineCallbacks
    def tearDown(self):
        super(TestBackendIntegration, self).tearDown()
        yield self.listener.stopListening()

    @defer.inlineCallbacks
    def assertCommandSuccess(self, command, path):
        out, err, code = yield utils.getProcessOutputAndValue(
            command[0], command[1:], path=path)
        self.assertEqual(0, code)
        defer.returnValue(out)

    @defer.inlineCallbacks
    def test_clone_and_push(self):
        # Test a full clone, commit, push, clone cycle using the backend
        # server.
        yield self.assertCommandSuccess(
            (b'git', b'init', b'--bare', b'test'), path=self.root)
        url = b'git://localhost:%d/test' % self.port
        test_root = self.useFixture(TempDir()).path

        # Clone the empty repo from the backend and commit to it.
        yield self.assertCommandSuccess(
            (b'git', b'clone', url), path=test_root)
        yield self.assertCommandSuccess(
            (b'git', b'config', b'user.email', b'test@example.com'),
            path=os.path.join(test_root, b'test'))
        yield self.assertCommandSuccess(
            (b'git', b'commit', b'--allow-empty', b'-m', b'Committed test'),
            path=os.path.join(test_root, b'test'))

        # Push it back up to the backend.
        yield self.assertCommandSuccess(
            (b'git', b'push', b'origin', b'master'),
            path=os.path.join(test_root, b'test'))

        # Re-clone and check that we got the fresh commit.
        yield self.assertCommandSuccess(
            (b'git', b'clone', url, b'test2'), path=test_root)
        out = yield self.assertCommandSuccess(
            (b'git', b'log', b'--oneline', b'-n', b'1'),
            path=os.path.join(test_root, b'test2'))
        self.assertIn(b'Committed test', out)

    @defer.inlineCallbacks
    def test_no_repo(self):
        test_root = self.useFixture(TempDir()).path
        output = yield utils.getProcessOutput(
            b'git', (b'clone', b'git://localhost:%d/test' % self.port),
            path=test_root, errortoo=True)
        self.assertIn(b'fatal:', output)
