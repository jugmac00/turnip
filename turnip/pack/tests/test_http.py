# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from io import BytesIO
import json
import os
from unittest import mock

from fixtures import TempDir
from openid.consumer import consumer
from paste.auth.cookie import encode as encode_cookie
import six
from testtools import TestCase
from testtools.deferredruntest import AsynchronousDeferredRunTest
from twisted.internet import (
    defer,
    reactor as default_reactor,
    task,
    testing,
    )
from twisted.internet.address import IPv4Address
from twisted.web import server
from twisted.web.test import requesthelper

from turnip.api import store
from turnip.config import config
from turnip.pack import (
    helpers,
    http,
    )
from turnip.pack.helpers import encode_packet
from turnip.pack.http import (
    get_protocol_version_from_request,
    HTTPAuthLoginResource,
    )
from turnip.pack.tests.fake_servers import FakeVirtInfoService
from turnip.version_info import version_info


class LessDummyRequest(requesthelper.DummyRequest):

    startedWriting = 0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.content = BytesIO()
        self.cookies = {}

    @property
    def value(self):
        return b"".join(self.written)

    def write(self, data):
        self.startedWriting = 1
        super().write(data)

    def registerProducer(self, prod, s):
        # Avoid DummyRequest.registerProducer calling resumeProducing
        # forever, never giving the reactor a chance to run.
        if not s:
            super().registerProducer(prod, s)

    def getUser(self):
        return None

    def getPassword(self):
        return None

    def getClientAddress(self):
        return IPv4Address('TCP', '127.0.0.1', '80')

    def getCookie(self, name):
        return self.cookies.get(name)


class AuthenticatedLessDummyRequest(LessDummyRequest):
    def getUser(self):
        return 'dummy-username'

    def getPassword(self):
        return 'dummy-password'


def render_resource(resource, request):
    result = resource.render(request)
    if result is server.NOT_DONE_YET:
        if request.finished:
            return defer.succeed(None)
        else:
            return request.notifyFinish()
    elif isinstance(result, bytes):
        request.write(result)
        request.finish()
        return defer.succeed(None)
    else:
        raise AssertionError("Invalid return value: %r" % (result,))


class FakeRoot(object):

    allowed_services = frozenset((
        b'git-upload-pack', b'git-receive-pack', b'turnip-set-symbolic-ref'))

    def __init__(self, repo_store=None):
        self.backend_transport = None
        self.client_factory = None
        self.backend_connected = defer.Deferred()
        self.repo_store = repo_store
        self.cgit_exec_path = config.get("cgit_exec_path")
        self.site_name = 'turnip'

    def authenticateWithPassword(self, user, password):
        """Pretends to talk to Launchpad XML-RPC service to authenticate the user.

        This method returns a dict with different data types to make sure
        nothing breaks when forwarding this data across the layers.
        """
        return {
            "lp-int-data": 1, "lp-text-data": "banana",
            "lp-float-data": 1.23987, "lp-bool-data": True,
            "lp-none-data": None, "lp-bytes-data": b"bytes"}

    def connectToBackend(self, client_factory):
        self.client_factory = client_factory
        self.backend_transport = testing.StringTransportWithDisconnection()
        p = client_factory.buildProtocol(None)
        self.backend_transport.protocol = p
        p.makeConnection(self.backend_transport)
        self.backend_connected.callback(None)


class ErrorTestMixin(object):

    @defer.inlineCallbacks
    def performRequest(self, backend_response=None,
                       service=b'git-upload-pack'):
        """Perform an info/refs request.

        If backend_response is None, it is asserted that a backend
        connection is never established. Otherwise it sent as a response
        from the backend transport and the connection closed.
        """
        if service:
            self.request.addArg(b'service', service)
        self.request.content = BytesIO(b'boo')
        rendered = render_resource(self.makeResource(service), self.request)
        if backend_response is not None:
            yield self.root.backend_connected
            self.assertIsNot(None, self.root.backend_transport)
            self.root.backend_transport.protocol.dataReceived(backend_response)
            self.root.backend_transport.loseConnection()
        else:
            self.assertIs(None, self.root.backend_transport)
        yield rendered
        return self.request

    @defer.inlineCallbacks
    def test_backend_immediately_dies(self):
        # If the backend disappears before it says anything, that's OK.
        yield self.performRequest(b'')
        self.assertEqual(200, self.request.responseCode)
        self.assertEqual(b'', self.request.value)

    @defer.inlineCallbacks
    def test_backend_virt_error(self):
        # A virt error with a known code is mapped to a specific HTTP status.
        yield self.performRequest(
            helpers.encode_packet(b'ERR turnip virt error: NOT_FOUND enoent'))
        self.assertEqual(404, self.request.responseCode)
        self.assertEqual(b'enoent', self.request.value)

    @defer.inlineCallbacks
    def test_backend_virt_error_unknown(self):
        # A virt error with an unknown code is an internal server error.
        yield self.performRequest(
            helpers.encode_packet(b'ERR turnip virt error: random yay'))
        self.assertEqual(500, self.request.responseCode)
        self.assertEqual(b'yay', self.request.value)


class TestSmartHTTPRefsResource(ErrorTestMixin, TestCase):

    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=10)

    request_method = 'GET'

    def setUp(self):
        super().setUp()
        self.root = FakeRoot()
        self.request = LessDummyRequest([b''])
        self.request.method = b'GET'

    def makeResource(self, service):
        return http.SmartHTTPRefsResource(self.root, b'/foo')

    @defer.inlineCallbacks
    def test_dumb_client_rejected(self):
        yield self.performRequest(service=None)
        self.assertEqual(404, self.request.responseCode)
        self.assertEqual(
            b"Only git smart HTTP clients are supported.", self.request.value)

    @defer.inlineCallbacks
    def test_unsupported_service(self):
        yield self.performRequest(service=b'foo')
        # self.assertEqual(403, self.request.responseCode)
        self.assertEqual(b"Unsupported service.", self.request.value)

    @defer.inlineCallbacks
    def test_backend_error(self):
        # Unlike a command request, an unknown error is treated as a
        # crash here, since the user input for a refs request is limited
        # to the path.
        yield self.performRequest(
            helpers.encode_packet(b'ERR so borked'))
        self.assertEqual(500, self.request.responseCode)
        self.assertEqual(b'so borked', self.request.value)

    @defer.inlineCallbacks
    def test_good(self):
        yield self.performRequest(
            helpers.encode_packet(b'I am git protocol data.') +
            b'And I am raw, since we got a good packet to start with.')
        self.assertEqual(200, self.request.responseCode)
        self.assertEqual(
            b'001e# service=git-upload-pack\n'
            b'0000001bI am git protocol data.'
            b'And I am raw, since we got a good packet to start with.',
            self.request.value)

    @defer.inlineCallbacks
    def test_good_authenticated(self):
        self.request = AuthenticatedLessDummyRequest([b''])
        yield self.performRequest(
            helpers.encode_packet(b'I am git protocol data.') +
            b'And I am raw, since we got a good packet to start with.')
        self.assertEqual(200, self.request.responseCode)
        self.assertEqual(
            b'001e# service=git-upload-pack\n'
            b'0000001bI am git protocol data.'
            b'And I am raw, since we got a good packet to start with.',
            self.request.value)

    @defer.inlineCallbacks
    def test_good_v2_included_version_and_capabilities(self):
        self.request.requestHeaders.addRawHeader("Git-Protocol", "version=2")
        yield self.performRequest(
            helpers.encode_packet(b'I am git protocol data.') +
            b'And I am raw, since we got a good packet to start with.')
        self.assertEqual(200, self.request.responseCode)
        self.assertEqual(self.root.client_factory.params, {
            b'version': b'2',
            b'turnip-advertise-refs': b'yes',
            b'turnip-can-authenticate': b'yes',
            b'turnip-request-id': mock.ANY,
            b'turnip-stateless-rpc': b'yes'})

        ver = six.ensure_binary(version_info["revision_id"])
        capabilities = (
            encode_packet(b'version 2\n') +
            encode_packet(b'agent=git/2.25.1@turnip/%s\n' % ver) +
            encode_packet(b'ls-refs\n') +
            encode_packet(b'fetch=shallow\n') +
            encode_packet(b'server-option\n') +
            b'0000'
            )
        self.assertEqual(
            capabilities +
            b'001e# service=git-upload-pack\n'
            b'0000001bI am git protocol data.'
            b'And I am raw, since we got a good packet to start with.',
            self.request.value)


class TestSmartHTTPCommandResource(ErrorTestMixin, TestCase):

    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=10)

    def setUp(self):
        super().setUp()
        self.root = FakeRoot()
        self.request = LessDummyRequest([''])
        self.request.method = b'POST'
        self.request.requestHeaders.addRawHeader(
            b'Content-Type', b'application/x-git-upload-pack-request')

    def makeResource(self, service):
        return http.SmartHTTPCommandResource(self.root, service, b'/foo')

    @defer.inlineCallbacks
    def test_backend_error(self):
        # Unlike a refs request, an unknown error is treated as user
        # error here, since the request body could be bad.
        yield self.performRequest(
            helpers.encode_packet(b'ERR so borked'))
        self.assertEqual(200, self.request.responseCode)
        self.assertEqual(b'0011ERR so borked', self.request.value)

    @defer.inlineCallbacks
    def test_good(self):
        yield self.performRequest(
            helpers.encode_packet(b'I am git protocol data.') +
            b'And I am raw, since we got a good packet to start with.')
        self.assertEqual(200, self.request.responseCode)
        self.assertEqual(
            b'001bI am git protocol data.'
            b'And I am raw, since we got a good packet to start with.',
            self.request.value)


class TestHTTPAuthLoginResource(TestCase):
    """Unit tests for login resource."""
    def setUp(self):
        super().setUp()
        self.root = FakeRoot(self.useFixture(TempDir()).path)
        self.root.cgit_secret = b'dont-tell-anyone shuuu'

    def getResourceInstance(self, mock_response):
        resource = HTTPAuthLoginResource(self.root)
        resource._makeConsumer = mock.Mock()
        resource._makeConsumer.return_value.complete.return_value = (
            mock_response)
        return resource

    def test_render_GET_success(self):
        response = mock.Mock()
        response.status = consumer.SUCCESS
        response.identity_url = 'http://lp.test/XopAlqp'
        response.getSignedNS.return_value = {
            'nickname': 'pappacena', 'country': 'BR'
        }

        request = LessDummyRequest([''])
        request.method = b'GET'
        request.path = b'/example'
        request.args = {
            b'openid.return_to': [b'https://return.to.test'],
            b'back_to': [b'https://return.to.test']
        }

        resource = self.getResourceInstance(response)
        self.assertEqual(b'', resource.render_GET(request))
        encoded_cookie = resource.signer.sign(encode_cookie(json.dumps({
            'identity_url': response.identity_url,
            'user': 'pappacena'
        })))
        expected_cookie = b'TURNIP_COOKIE=%s; Path=/; secure;' % encoded_cookie
        self.assertEqual({
            b'Set-Cookie': [expected_cookie],
            b'Location': [b'https://return.to.test']
        }, dict(request.responseHeaders.getAllRawHeaders()))

    def test_getSession(self):
        response = mock.Mock()
        request = LessDummyRequest([''])
        request.method = b'GET'
        request.path = b'/example'
        request.args = {
            b'openid.return_to': [b'https://return.to.test'],
            b'back_to': [b'https://return.to.test']
        }

        resource = self.getResourceInstance(response)
        cookie_data = {
            'identity_url': 'http://localhost',
            'user': 'pappacena'}
        cookie_content = resource.signer.sign(
            encode_cookie(json.dumps(cookie_data)))
        request.cookies[resource.cookie_name] = cookie_content

        self.assertEqual(cookie_data, resource._getSession(request))


class TestHTTPAuthRootResource(TestCase):

    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=10)

    def setUp(self):
        super().setUp()
        self.root = root = FakeRoot(self.useFixture(TempDir()).path)
        self.virtinfo = virtinfo = FakeVirtInfoService(allowNone=True)
        virtinfo_listener = default_reactor.listenTCP(0, server.Site(virtinfo))
        virtinfo_port = virtinfo_listener.getHost().port
        virtinfo_url = b'http://localhost:%d/' % virtinfo_port
        self.addCleanup(virtinfo_listener.stopListening)
        root.virtinfo_endpoint = virtinfo_url
        root.virtinfo_timeout = 15
        root.reactor = task.Clock()
        root.cgit_secret = None

    def test__beginLogin(self):
        root = self.root
        root.openid_provider_root = 'https://testopenid.test/'
        request = LessDummyRequest([''])
        request.method = b'GET'
        request.path = b'/example'
        session = {}
        openid_request = mock.Mock()
        openid_request.redirectURL.return_value = 'http://redirected.test'

        # Quite a lot of things are mocked here, mainly because we only want
        # to make sure that we redirect to OpenID correctly.
        resource = http.HTTPAuthRootResource(root)
        resource._makeConsumer = mock.Mock()
        resource._makeConsumer.return_value.begin.return_value = openid_request

        resource._beginLogin(request, session)
        self.assertEqual(302, request.responseCode)
        self.assertEqual(
            [b'http://redirected.test'],
            request.responseHeaders.getRawHeaders(b'location'))

    def test_translatePath_timeout(self):
        root = self.root
        request = LessDummyRequest([''])
        request.method = b'GET'
        request.path = b'/example'
        d = render_resource(http.HTTPAuthRootResource(root), request)
        root.reactor.advance(1)
        self.assertFalse(d.called)
        root.reactor.advance(15)
        self.assertTrue(d.called)
        self.assertEqual(504, request.responseCode)
        self.assertEqual(b'Path translation timed out.', request.value)

    @defer.inlineCallbacks
    def test_render_root_repo(self):
        root = self.root
        request = LessDummyRequest([''])
        store.init_repo(os.path.join(
            root.repo_store, self.virtinfo.getInternalPath('/example')))
        request.method = b'GET'
        request.path = b'/example'
        request.uri = b'http://dummy/example'
        yield render_resource(http.HTTPAuthRootResource(root), request)
        response_content = b''.join(request.written)
        self.assertIn(b"Repository seems to be empty", response_content)


class TestProtocolVersion(TestCase):
    def test_get_protocol_version_from_request_default_zero(self):
        request = LessDummyRequest("/foo")
        self.assertEqual(b'0', get_protocol_version_from_request(request))

    def test_get_protocol_version_from_request_fallback_to_zero(self):
        request = LessDummyRequest("/foo")
        request.requestHeaders.setRawHeaders('git-protocol', [b'invalid'])
        self.assertEqual(b'0', get_protocol_version_from_request(request))

    def test_get_protocol_version_from_request(self):
        request = LessDummyRequest("/foo")
        request.requestHeaders.setRawHeaders('git-protocol', [b'version=2'])
        self.assertEqual(b'2', get_protocol_version_from_request(request))
