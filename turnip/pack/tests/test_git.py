# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import hashlib
import os.path

from fixtures import TempDir, MonkeyPatch
from pygit2 import init_repository
import six
from testtools import TestCase
from testtools.matchers import (
    ContainsDict,
    Equals,
    MatchesListwise,
    )
from testtools.twistedsupport import AsynchronousDeferredRunTest
from twisted.internet import (
    defer,
    reactor as default_reactor,
    task,
    testing,
    )
from twisted.web import server
from twisted.web.xmlrpc import Fault

from turnip.config import config
from turnip.pack import (
    git,
    helpers,
    )
from turnip.pack.tests.fake_servers import FakeVirtInfoService
from turnip.pack.tests.test_hooks import MockHookRPCHandler
from turnip.tests.compat import mock


class DummyPackServerProtocol(git.PackServerProtocol):

    test_request = None

    def requestReceived(self, command, pathname, host):
        if self.test_request is not None:
            raise AssertionError('Request already received')
        self.test_request = (command, pathname, host)


class TestPackServerProtocol(TestCase):
    """Test the base implementation of the git pack network protocol."""

    def setUp(self):
        super(TestPackServerProtocol, self).setUp()
        self.proto = DummyPackServerProtocol()
        self.transport = testing.StringTransportWithDisconnection()
        self.transport.protocol = self.proto
        self.proto.makeConnection(self.transport)

    def assertKilledWith(self, message):
        self.assertFalse(self.transport.connected)
        self.assertEqual(
            (b'ERR ' + message + b'\n', b''),
            helpers.decode_packet(self.transport.value()))

    def test_calls_requestReceived(self):
        # dataReceived waits for a complete request packet and calls
        # requestReceived.
        self.proto.dataReceived(
            b'002egit-upload-pack /foo.git\0host=example.com\0')
        self.assertEqual(
            (b'git-upload-pack', b'/foo.git', {b'host': b'example.com'}),
            self.proto.test_request)

    def test_handles_fragmentation(self):
        # dataReceived handles fragmented request packets.
        self.proto.dataReceived(b'002')
        self.proto.dataReceived(b'egit-upload-pack /foo.git\0hos')
        self.proto.dataReceived(b't=example.com\0')
        self.assertEqual(
            (b'git-upload-pack', b'/foo.git', {b'host': b'example.com'}),
            self.proto.test_request)
        self.assertTrue(self.transport.connected)

    def test_buffers_trailing_data(self):
        # Any input after the request packet is buffered until the
        # implementation handles requestReceived() and calls
        # resumeProducing().
        self.proto.dataReceived(
            b'002egit-upload-pack /foo.git\0host=example.com\0lol')
        self.assertEqual(
            (b'git-upload-pack', b'/foo.git', {b'host': b'example.com'}),
            self.proto.test_request)
        self.assertEqual(b'lol', self.proto._PackProtocol__buffer)

    def test_drops_bad_packet(self):
        # An invalid packet causes the connection to be dropped.
        self.proto.dataReceived(b'abcg')
        self.assertKilledWith(b'Invalid pkt-line')

    def test_drops_bad_request(self):
        # An invalid request causes the connection to be dropped.
        self.proto.dataReceived(b'0007lol')
        self.assertKilledWith(b'Invalid git-proto-request')

    def test_drops_flush_request(self):
        # A flush packet is not a valid request, so the connection is
        # dropped.
        self.proto.dataReceived(b'0000')
        self.assertKilledWith(b'Bad request: flush-pkt instead')


class DummyPackBackendProtocol(git.PackBackendProtocol):

    test_process = None

    def spawnProcess(self, cmd, args, env=None):
        if self.test_process is not None:
            raise AssertionError('Process already spawned.')
        self.test_process = (cmd, args, env)


class TestPackFrontendServerProtocol(TestCase):

    def setUp(self):
        super(TestPackFrontendServerProtocol, self).setUp()
        self.factory = git.PackFrontendFactory('example.com', 12345)
        self.proto = git.PackFrontendServerProtocol()
        self.proto.factory = self.factory
        self.transport = testing.StringTransportWithDisconnection()
        self.transport.protocol = self.proto
        self.proto.makeConnection(self.transport)

    def assertKilledWith(self, message):
        self.assertFalse(self.transport.connected)
        self.assertEqual(
            (b'ERR ' + message + b'\n', b''),
            helpers.decode_packet(self.transport.value()))

    def test_git_receive(self):
        self.proto.pauseProducing()
        self.proto.got_request = True
        yield self.proto.requestReceived(
            b'git-upload-pack', b'/foo.git', {b'host': b'example.com'})
        self.assertEqual(b'git-upload-pack', self.proto.command)
        self.transport.loseConnection()

    def test_git_receive_with_version_param(self):
        self.proto.pauseProducing()
        self.proto.got_request = True
        yield self.proto.requestReceived(
            b'git-upload-pack', b'/test_repo',
            {b'host': 'example.com', 'version': '2'}),
        self.assertIn(b'host', self.proto.params)
        self.assertIn('version', self.proto.params)
        self.transport.loseConnection()

    def test_git_receive_with_extra_undefined_params(self):
        self.proto.pauseProducing()
        self.proto.got_request = True
        yield self.proto.requestReceived(
            b'git-upload-pack', b'/test_repo',
            {b'host': 'example.com', 'version': '2',
             'undefined_param': 'value'}),
        self.assertIsNone(self.proto.params)
        self.assertKilledWith(b'Illegal request parameters')
        self.transport.loseConnection()


class TestPackBackendProtocol(TestCase):
    """Test the Git pack backend protocol."""

    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=5)

    def setUp(self):
        super(TestPackBackendProtocol, self).setUp()
        self.root = self.useFixture(TempDir()).path
        self.hookrpc_handler = MockHookRPCHandler()
        self.hookrpc_sock = os.path.join(self.root, 'hookrpc_sock')
        self.factory = git.PackBackendFactory(
            self.root, self.hookrpc_handler, self.hookrpc_sock)
        self.proto = DummyPackBackendProtocol()
        self.proto.factory = self.factory
        self.transport = testing.StringTransportWithDisconnection()
        self.transport.protocol = self.proto
        self.proto.makeConnection(self.transport)

        # XML-RPC server
        self.virtinfo = FakeVirtInfoService(allowNone=True)
        self.virtinfo_listener = default_reactor.listenTCP(0, server.Site(
            self.virtinfo))
        self.virtinfo_port = self.virtinfo_listener.getHost().port
        self.virtinfo_url = b'http://localhost:%d/' % self.virtinfo_port
        self.addCleanup(self.virtinfo_listener.stopListening)
        self.setupConfig()

    def setupConfig(self):
        config.defaults['virtinfo_endpoint'] = self.virtinfo_url

    def assertKilledWith(self, message):
        self.assertFalse(self.transport.connected)
        self.assertEqual(
            (b'ERR ' + message + b'\n', b''),
            helpers.decode_packet(self.transport.value()))

    @mock.patch("turnip.pack.git.store")
    @defer.inlineCallbacks
    def test_create_repo_command_allows_further_requests(self, store):
        # If command is 'turnip-create-repo', it should allow us to run more
        # than one command.
        yield self.proto.requestReceived(
            b'turnip-create-repo', b'foo.git', {b"clone_from": None})

        yield self.proto.requestReceived(
            b'git-upload-pack', b'/foo.git', {b'host': b'example.com'})

        full_path = os.path.join(six.ensure_binary(self.root), b'foo.git')
        self.assertEqual(
            [mock.call(full_path, None)], store.init_repo.call_args_list)
        self.assertEqual([], store.delete_repo.call_args_list)

        self.assertEqual(
            [('foo.git', )], self.virtinfo.confirm_repo_creation_call_args)

        self.assertEqual(
            (b'git', [b'git', b'upload-pack', full_path], {}),
            self.proto.test_process)

    @defer.inlineCallbacks
    def test_create_repo_fails_to_confirm(self):
        self.virtinfo.xmlrpc_confirmRepoCreation = mock.Mock(
            side_effect=Fault(1, "?"))
        store = mock.Mock()
        self.useFixture(MonkeyPatch("turnip.pack.git.store", store))

        params = {b'host': b'example.com'}
        yield self.proto.requestReceived(
            b'turnip-create-repo', b'foo.git', {"clone_from": None})

        full_path = os.path.join(six.ensure_binary(self.root), b'foo.git')
        self.assertEqual(
            [mock.call(full_path, None)], store.init_repo.call_args_list)
        self.assertEqual(
            [mock.call(full_path, )], store.delete_repo.call_args_list)

        auth_params = self.proto.createAuthParams(params)
        self.assertEqual([(b'foo.git', )],
                         self.virtinfo.abort_repo_creation_call_args)
        self.assertEqual(
            [mock.call(mock.ANY, b'foo.git', auth_params)],
            self.virtinfo.xmlrpc_confirmRepoCreation.call_args_list)

        self.assertIsNone(self.proto.test_process)

    def test_git_upload_pack_calls_spawnProcess(self):
        # If the command is git-upload-pack, requestReceived calls
        # spawnProcess with appropriate arguments.
        self.proto.requestReceived(
            b'git-upload-pack', b'/foo.git', {b'host': b'example.com'})
        full_path = os.path.join(six.ensure_binary(self.root), b'foo.git')
        self.assertEqual(
            (b'git',
             [b'git', b'upload-pack', full_path],
             {}),
            self.proto.test_process)

    def test_git_receive_pack_calls_spawnProcess(self):
        # If the command is git-receive-pack, requestReceived calls
        # spawnProcess with appropriate arguments.
        repo_dir = os.path.join(self.root, 'foo.git')
        init_repository(repo_dir, bare=True)
        self.proto.requestReceived(
            b'git-receive-pack', b'/foo.git', {b'host': b'example.com'})
        self.assertThat(
            self.proto.test_process, MatchesListwise([
                Equals(b'git'),
                Equals([b'git', b'receive-pack', repo_dir.encode('utf-8')]),
                ContainsDict(
                    {b'TURNIP_HOOK_RPC_SOCK': Equals(self.hookrpc_sock)})]))

    def test_turnip_set_symbolic_ref_calls_spawnProcess(self):
        # If the command is turnip-set-symbolic-ref, requestReceived does
        # not spawn a process, but packetReceived calls spawnProcess with
        # appropriate arguments.
        repo_dir = os.path.join(self.root, 'foo.git')
        init_repository(repo_dir, bare=True)
        self.proto.requestReceived(b'turnip-set-symbolic-ref', b'/foo.git', {})
        self.assertIsNone(self.proto.test_process)
        self.proto.packetReceived(b'HEAD refs/heads/master')
        self.assertThat(
            self.proto.test_process, MatchesListwise([
                Equals(b'git'),
                Equals([
                    b'git', b'-C', repo_dir.encode('utf-8'), b'symbolic-ref',
                    b'HEAD', b'refs/heads/master']),
                ContainsDict(
                    {b'TURNIP_HOOK_RPC_SOCK': Equals(self.hookrpc_sock)})]))

    def test_turnip_set_symbolic_ref_requires_valid_line(self):
        # The turnip-set-symbolic-ref command requires a valid
        # set-symbolic-ref-line packet.
        self.proto.requestReceived(b'turnip-set-symbolic-ref', b'/foo.git', {})
        self.assertIsNone(self.proto.test_process)
        self.proto.packetReceived(b'HEAD')
        self.assertKilledWith(b'Invalid set-symbolic-ref-line')

    def test_turnip_set_symbolic_ref_name_must_be_HEAD(self):
        # The turnip-set-symbolic-ref command's "name" parameter must be
        # "HEAD".
        self.proto.requestReceived(b'turnip-set-symbolic-ref', b'/foo.git', {})
        self.assertIsNone(self.proto.test_process)
        self.proto.packetReceived(b'another-symref refs/heads/master')
        self.assertKilledWith(b'Symbolic ref name must be "HEAD"')

    def test_turnip_set_symbolic_ref_target_not_option(self):
        # The turnip-set-symbolic-ref command's "target" parameter may not
        # start with "-".
        self.proto.requestReceived(b'turnip-set-symbolic-ref', b'/foo.git', {})
        self.assertIsNone(self.proto.test_process)
        self.proto.packetReceived(b'HEAD --evil')
        self.assertKilledWith(b'Symbolic ref target may not start with "-"')

    def test_turnip_set_symbolic_ref_target_no_space(self):
        # The turnip-set-symbolic-ref command's "target" parameter may not
        # contain " ".
        self.proto.requestReceived(b'turnip-set-symbolic-ref', b'/foo.git', {})
        self.assertIsNone(self.proto.test_process)
        self.proto.packetReceived(b'HEAD evil lies')
        self.assertKilledWith(b'Symbolic ref target may not contain " "')


class DummyPackBackendFactory(git.PackBackendFactory):

    test_protocol = None

    def buildProtocol(self, *args, **kwargs):
        self.test_protocol = git.PackBackendFactory.buildProtocol(
            self, *args, **kwargs)
        return self.test_protocol


class TestPackVirtServerProtocol(TestCase):
    """Test the Git pack virt protocol."""

    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=5)

    def assertKilledWith(self, message):
        self.assertFalse(self.transport.connected)
        self.assertEqual(
            (b'ERR turnip virt error: ' + message + b'\n', b''),
            helpers.decode_packet(self.transport.value()))

    def setUp(self):
        super(TestPackVirtServerProtocol, self).setUp()
        self.root = self.useFixture(TempDir()).path
        self.hookrpc_handler = MockHookRPCHandler()
        self.hookrpc_sock = os.path.join(self.root, 'hookrpc_sock')
        self.backend_factory = DummyPackBackendFactory(
            self.root, self.hookrpc_handler, self.hookrpc_sock)
        self.backend_factory.protocol = DummyPackBackendProtocol
        self.backend_listener = default_reactor.listenTCP(
            0, self.backend_factory)
        self.backend_port = self.backend_listener.getHost().port
        self.addCleanup(self.backend_listener.stopListening)

        self.virtinfo = FakeVirtInfoService(allowNone=True)
        self.virtinfo_listener = default_reactor.listenTCP(0, server.Site(
            self.virtinfo))
        self.virtinfo_port = self.virtinfo_listener.getHost().port
        self.virtinfo_url = b'http://localhost:%d/' % self.virtinfo_port
        self.addCleanup(self.virtinfo_listener.stopListening)
        factory = git.PackVirtFactory(
            b'localhost', self.backend_port, self.virtinfo_url, 5)
        self.proto = git.PackVirtServerProtocol()
        self.proto.factory = factory
        self.transport = testing.StringTransportWithDisconnection()
        self.transport.protocol = self.proto
        self.proto.makeConnection(self.transport)
        self.proto.pauseProducing()
        self.proto.got_request = True

    @defer.inlineCallbacks
    def test_translatePath(self):
        yield self.proto.requestReceived(b'git-upload-pack', b'/example', {})
        _, pathname, _, _ = self.proto.requests[0]
        self.assertEqual(
            hashlib.sha256(b'/example').hexdigest(), pathname)
        self.backend_factory.test_protocol.transport.loseConnection()

    @defer.inlineCallbacks
    def test_multiple_calls_to_backend(self):
        self.backend_factory.protocol._createRepo = mock.Mock()

        ANY = mock.ANY
        yield self.proto.runOnBackend(
            b'turnip-create-repo', b'/path_1', {b"clone_from": b'1'})

        self.assertEqual(
            [(b'turnip-create-repo', b'/path_1', {b"clone_from": b'1'}, ANY)],
            self.proto.requests)
        self.assertEqual(1, self.proto.requests_sent)
        self.assertTrue(self.proto.requests[0][-1].called)

        yield self.proto.runOnBackend(
            b'git-upload-pack', b'/path_2', {b"param": b'2'})
        self.backend_factory.test_protocol.transport.loseConnection()

        self.assertEqual(
            [(b'turnip-create-repo', b'/path_1', {b"clone_from": b'1'}, ANY),
             (b'git-upload-pack', b'/path_2', {b"param": b'2'}, ANY)],
            self.proto.requests)
        self.assertEqual(2, self.proto.requests_sent)
        self.assertTrue(self.proto.requests[1][-1].called)

    @defer.inlineCallbacks
    def test_git_push_for_new_repository_runs_both_commands(self):
        path = b'/+rwexample-new/clone-from:foo-repo'

        yield self.proto.requestReceived(b'git-upload-pack', path, {})
        self.backend_factory.test_protocol.transport.loseConnection()

        digest = hashlib.sha256(b'example-new/clone-from:foo-repo').hexdigest()
        clone_digest = hashlib.sha256(b'foo-repo').hexdigest()
        ANY = mock.ANY
        self.assertEqual(
            [('turnip-create-repo', digest, {'clone_from': clone_digest}, ANY),
             ('git-upload-pack', digest, {}, ANY)],
            self.proto.requests)

    def test_translatePath_timeout(self):
        root = self.useFixture(TempDir()).path
        hookrpc_handler = MockHookRPCHandler()
        hookrpc_sock = os.path.join(root, 'hookrpc_sock')
        backend_listener = default_reactor.listenTCP(
            0, git.PackBackendFactory(root, hookrpc_handler, hookrpc_sock))
        backend_port = backend_listener.getHost().port
        self.addCleanup(backend_listener.stopListening)
        virtinfo = FakeVirtInfoService(allowNone=True)
        virtinfo_listener = default_reactor.listenTCP(0, server.Site(virtinfo))
        virtinfo_port = virtinfo_listener.getHost().port
        virtinfo_url = b'http://localhost:%d/' % virtinfo_port
        self.addCleanup(virtinfo_listener.stopListening)
        clock = task.Clock()
        factory = git.PackVirtFactory(
            b'localhost', backend_port, virtinfo_url, 15, reactor=clock)
        proto = git.PackVirtServerProtocol()
        proto.factory = factory
        self.transport = testing.StringTransportWithDisconnection()
        self.transport.protocol = proto
        proto.makeConnection(self.transport)
        d = proto.requestReceived(b'git-upload-pack', b'/example', {})
        clock.advance(1)
        self.assertFalse(d.called)
        clock.advance(15)
        self.assertTrue(d.called)
        self.assertKilledWith(b'GATEWAY_TIMEOUT Path translation timed out.')
