# Copyright 2015-2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import base64
import contextlib
import uuid

from six.moves import xmlrpc_client
from testtools import (
    ExpectedException,
    TestCase,
    )
from testtools.deferredruntest import (
    assert_fails_with,
    AsynchronousDeferredRunTest,
    )
from testtools.matchers import (
    Equals,
    IsInstance,
    MatchesAll,
    MatchesListwise,
    MatchesStructure,
    )
from twisted.internet import (
    defer,
    reactor,
    task,
    testing,
    )
from twisted.web import (
    server,
    xmlrpc,
    )

from turnip.pack import hookrpc
from turnip.pack.tests.fake_servers import FakeVirtInfoService


class DummyJSONNetstringProtocol(hookrpc.JSONNetstringProtocol):

    response_deferred = None

    def __init__(self):
        self.test_value_log = []
        self.test_invalid_log = []

    def valueReceived(self, val):
        self.test_value_log.append(val)

    def invalidValueReceived(self, string):
        self.test_invalid_log.append(string)

    def sendValue(self, value):
        # Hack to allow tests to block until a response is sent, since
        # dataReceived can't return a Deferred without breaking things.
        hookrpc.JSONNetstringProtocol.sendValue(self, value)
        if self.response_deferred is not None:
            d = self.response_deferred
            self.response_deferred = None
            d.callback()


class TestJSONNetStringProtocol(TestCase):
    """Test the JSON netstring protocol."""

    def setUp(self):
        super(TestJSONNetStringProtocol, self).setUp()
        self.proto = DummyJSONNetstringProtocol()
        self.transport = testing.StringTransportWithDisconnection()
        self.transport.protocol = self.proto
        self.proto.makeConnection(self.transport)

    def test_calls_valueReceived(self):
        # A valid netstring containing valid JSON is given to
        # valueReceived.
        self.proto.dataReceived(b'14:{"foo": "bar"},')
        self.proto.dataReceived(b'19:[{"it": ["works"]}],')
        self.assertEqual(
            [{"foo": "bar"}, [{"it": ["works"]}]],
            self.proto.test_value_log)

    def test_calls_invalidValueReceived(self):
        # A valid nestring containing invalid JSON calls
        # invalidValueReceived. Framing is preserved, so the connection
        # need not be destroyed.
        self.proto.dataReceived(b'12:{"foo": "bar,')
        self.proto.dataReceived(b'3:"ga,')
        self.assertEqual([], self.proto.test_value_log)
        self.assertEqual(
            [b'{"foo": "bar', b'"ga'], self.proto.test_invalid_log)

    def test_sendValue(self):
        # sendValue serialises to JSON and encodes as a netstring.
        self.proto.sendValue({"yay": "it works"})
        self.assertEqual(b'19:{"yay": "it works"},', self.transport.value())


def async_rpc_method(proto, args):
    d = defer.Deferred()
    d.callback(list(args.items()))
    return d


def sync_rpc_method(proto, args):
    return list(args.items())


def timeout_rpc_method(proto, args):
    raise defer.TimeoutError()


def unauthorized_rpc_method(proto, args):
    raise xmlrpc.Fault(410, 'Authorization required.')


def internal_server_error_rpc_method(proto, args):
    raise xmlrpc.Fault(500, 'Boom')


class TestRPCServerProtocol(TestCase):
    """Test the socket server that handles git hook callbacks."""

    def setUp(self):
        super(TestRPCServerProtocol, self).setUp()
        self.proto = hookrpc.RPCServerProtocol({
            'sync': sync_rpc_method,
            'async': async_rpc_method,
            'timeout': timeout_rpc_method,
            'unauthorized': unauthorized_rpc_method,
            'internal_server_error': internal_server_error_rpc_method,
            })
        self.transport = testing.StringTransportWithDisconnection()
        self.transport.protocol = self.proto
        self.proto.makeConnection(self.transport)

    def test_call_sync(self):
        self.proto.dataReceived(b'28:{"op": "sync", "bar": "baz"},')
        self.assertEqual(
            b'28:{"result": [["bar", "baz"]]},', self.transport.value())

    def test_call_async(self):
        self.proto.dataReceived(b'29:{"op": "async", "bar": "baz"},')
        self.assertEqual(
            b'28:{"result": [["bar", "baz"]]},', self.transport.value())

    def test_bad_op(self):
        self.proto.dataReceived(b'27:{"op": "bar", "bar": "baz"},')
        self.assertEqual(
            b'28:{"error": "Unknown op: bar"},', self.transport.value())

    def test_no_op(self):
        self.proto.dataReceived(b'28:{"nop": "bar", "bar": "baz"},')
        self.assertEqual(
            b'28:{"error": "No op specified"},', self.transport.value())

    def test_bad_value(self):
        self.proto.dataReceived(b'14:["foo", "bar"],')
        self.assertEqual(
            b'42:{"error": "Command must be a JSON object"},',
            self.transport.value())

    def test_bad_json(self):
        self.proto.dataReceived(b'12:["nop", "bar,')
        self.assertEqual(
            b'42:{"error": "Command must be a JSON object"},',
            self.transport.value())

    def test_timeout(self):
        self.proto.dataReceived(b'31:{"op": "timeout", "bar": "baz"},')
        self.assertEqual(
            b'30:{"error": "timeout timed out"},', self.transport.value())

    def test_unauthorized(self):
        self.proto.dataReceived(b'36:{"op": "unauthorized", "bar": "baz"},')
        self.assertEqual(
            b'50:{"error": "UNAUTHORIZED: Authorization required."},',
            self.transport.value())

    def test_internal_server_error(self):
        self.proto.dataReceived(
            b'45:{"op": "internal_server_error", "bar": "baz"},')
        self.assertEqual(
            b'40:{"error": "INTERNAL_SERVER_ERROR: Boom"},',
            self.transport.value())


class TestHookRPCHandler(TestCase):
    """Test the hook RPC handler."""

    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=5)

    def setUp(self):
        super(TestHookRPCHandler, self).setUp()
        self.virtinfo = FakeVirtInfoService(allowNone=True)
        self.virtinfo_listener = reactor.listenTCP(
            0, server.Site(self.virtinfo))
        self.virtinfo_port = self.virtinfo_listener.getHost().port
        self.virtinfo_url = b'http://localhost:%d/' % self.virtinfo_port
        self.addCleanup(self.virtinfo_listener.stopListening)
        self.hookrpc_handler = hookrpc.HookRPCHandler(self.virtinfo_url, 15)

    @contextlib.contextmanager
    def registeredKey(self, path, auth_params=None, permissions=None):
        key = str(uuid.uuid4())
        self.hookrpc_handler.registerKey(key, path, auth_params or {})
        if permissions is not None:
            self.hookrpc_handler.ref_permissions[key] = permissions
        try:
            yield key
        finally:
            self.hookrpc_handler.unregisterKey(key)

    def encodeRefPath(self, ref_path):
        return base64.b64encode(ref_path).decode('UTF-8')

    def assertCheckedRefPermissions(self, path, ref_paths, auth_params):
        self.assertThat(self.virtinfo.ref_permissions_checks, MatchesListwise([
            MatchesListwise([
                Equals(path),
                MatchesListwise([
                    MatchesAll(
                        IsInstance(xmlrpc_client.Binary),
                        MatchesStructure.byEquality(data=ref_path))
                    for ref_path in ref_paths
                    ]),
                Equals(auth_params),
                ]),
            ]))

    @defer.inlineCallbacks
    def test_checkRefPermissions_fresh(self):
        self.virtinfo.ref_permissions = {
            b'refs/heads/master': ['push'],
            b'refs/heads/next': ['force_push'],
            }
        encoded_paths = [
            self.encodeRefPath(ref_path)
            for ref_path in sorted(self.virtinfo.ref_permissions)]
        with self.registeredKey('/translated', auth_params={'uid': 42}) as key:
            permissions = yield self.hookrpc_handler.checkRefPermissions(
                None, {'key': key, 'paths': encoded_paths})
        expected_permissions = {
            self.encodeRefPath(ref_path): perms
            for ref_path, perms in self.virtinfo.ref_permissions.items()}
        self.assertEqual(expected_permissions, permissions)
        self.assertCheckedRefPermissions(
            '/translated', [b'refs/heads/master', b'refs/heads/next'],
            {'uid': 42})

    @defer.inlineCallbacks
    def test_checkRefPermissions_cached(self):
        cached_ref_permissions = {
            b'refs/heads/master': ['push'],
            b'refs/heads/next': ['force_push'],
            }
        encoded_master = self.encodeRefPath(b'refs/heads/master')
        with self.registeredKey(
                '/translated', auth_params={'uid': 42},
                permissions=cached_ref_permissions) as key:
            permissions = yield self.hookrpc_handler.checkRefPermissions(
                None, {'key': key, 'paths': [encoded_master]})
        expected_permissions = {encoded_master: ['push']}
        self.assertEqual(expected_permissions, permissions)
        self.assertEqual([], self.virtinfo.ref_permissions_checks)

    def test_checkRefPermissions_timeout(self):
        clock = task.Clock()
        self.hookrpc_handler = hookrpc.HookRPCHandler(
            self.virtinfo_url, 15, reactor=clock)
        encoded_master = self.encodeRefPath(b'refs/heads/master')
        with self.registeredKey('/translated') as key:
            d = self.hookrpc_handler.checkRefPermissions(
                None, {'key': key, 'paths': [encoded_master]})
            clock.advance(1)
            self.assertFalse(d.called)
            clock.advance(15)
            self.assertTrue(d.called)
            return assert_fails_with(d, defer.TimeoutError)

    @defer.inlineCallbacks
    def test_checkRefPermissions_unauthorized(self):
        self.virtinfo.ref_permissions = {
            b'refs/heads/master': ['push'],
            b'refs/heads/next': ['force_push'],
            }
        self.virtinfo.ref_permissions_fault = xmlrpc.Fault(3, 'Unauthorized')
        encoded_paths = [
            self.encodeRefPath(ref_path)
            for ref_path in sorted(self.virtinfo.ref_permissions)]
        with self.registeredKey('/translated', auth_params={'uid': 42}) as key:
            permissions = yield self.hookrpc_handler.checkRefPermissions(
                None, {'key': key, 'paths': encoded_paths})
        expected_permissions = {
            self.encodeRefPath(ref_path): []
            for ref_path in self.virtinfo.ref_permissions}
        self.assertEqual(expected_permissions, permissions)
        self.assertCheckedRefPermissions(
            '/translated', [b'refs/heads/master', b'refs/heads/next'],
            {'uid': 42})

    @defer.inlineCallbacks
    def test_checkRefPermissions_internal_server_error(self):
        self.virtinfo.ref_permissions = {
            b'refs/heads/master': ['push'],
            b'refs/heads/next': ['force_push'],
            }
        self.virtinfo.ref_permissions_fault = xmlrpc.Fault(500, 'Boom')
        encoded_paths = [
            self.encodeRefPath(ref_path)
            for ref_path in sorted(self.virtinfo.ref_permissions)]
        with self.registeredKey('/translated', auth_params={'uid': 42}) as key:
            fault_matcher = MatchesStructure.byEquality(
                faultCode=500, faultString='Boom')
            with ExpectedException(xmlrpc.Fault, fault_matcher):
                yield self.hookrpc_handler.checkRefPermissions(
                    None, {'key': key, 'paths': encoded_paths})
        self.assertCheckedRefPermissions(
            '/translated', [b'refs/heads/master', b'refs/heads/next'],
            {'uid': 42})

    @defer.inlineCallbacks
    def test_notifyPush(self):
        with self.registeredKey('/translated') as key:
            yield self.hookrpc_handler.notifyPush(
                None,
                {'key': key, 'loose_object_count': 19, 'pack_count': 7})

        # notify will now return in this format:
        # [('/translated', '1035 objects, 2298 kilobytes', 2)]
        # with the numbers being different of course for each
        # repository state
        self.assertEqual('/translated',
                         self.virtinfo.push_notifications[0][0])
        self.assertEqual(
            19,
            self.virtinfo.push_notifications[0][1].get(
                'loose_object_count'))
        self.assertEqual(
            7,
            self.virtinfo.push_notifications[0][1].get(
                'pack_count'))

    def test_notifyPush_timeout(self):
        clock = task.Clock()
        self.hookrpc_handler = hookrpc.HookRPCHandler(
            self.virtinfo_url, 15, reactor=clock)
        with self.registeredKey('/translated') as key:
            d = self.hookrpc_handler.notifyPush(
                None,
                {'key': key, 'loose_object_count': 9, 'pack_count': 7})
            clock.advance(1)
            self.assertFalse(d.called)
            clock.advance(15)
            self.assertTrue(d.called)
            return assert_fails_with(d, defer.TimeoutError)

    @defer.inlineCallbacks
    def test_getMergeProposalURL(self):
        clock = task.Clock()
        self.hookrpc_handler = hookrpc.HookRPCHandler(
            self.virtinfo_url, 15, reactor=clock)

        with self.registeredKey('/translated', auth_params={'uid': 42}) as key:
            mp_url = yield self.hookrpc_handler.getMergeProposalURL(
                None, {'key': key, 'branch': 'master', 'uid': 4})
        self.assertIsNotNone(mp_url)

    def test_getMergeProposalURL_timeout(self):
        clock = task.Clock()
        self.hookrpc_handler = hookrpc.HookRPCHandler(
            self.virtinfo_url, 15, reactor=clock)

        with self.registeredKey('/translated', auth_params={'uid': 42}) as key:
            d = self.hookrpc_handler.getMergeProposalURL(
                None, {'key': key, 'branch': 'master', 'uid': 4})
            clock.advance(1)
            self.assertFalse(d.called)
            clock.advance(15)
            self.assertTrue(d.called)
            return assert_fails_with(d, defer.TimeoutError)

    @defer.inlineCallbacks
    def test_getMergeProposalURL_unauthorized(self):
        # we return None for the merge proposal URL
        # when catching and UNAUTHORIZED and NOT_FOUND exception
        self.virtinfo.merge_proposal_url_fault = xmlrpc.Fault(
            3, 'Unauthorized')
        with self.registeredKey('/translated', auth_params={'uid': 42}) as key:
            mp_url = yield self.hookrpc_handler.getMergeProposalURL(
                None, {'key': key, 'branch': 'master', 'uid': 4})
        self.assertIsNone(mp_url)

        self.virtinfo.merge_proposal_url_fault = xmlrpc.Fault(
            1, 'NOT_FOUND')
        with self.registeredKey('/translated', auth_params={'uid': 42}) as key:
            mp_url = yield self.hookrpc_handler.getMergeProposalURL(
                None, {'key': key, 'branch': 'master', 'uid': 4})
        self.assertIsNone(mp_url)
