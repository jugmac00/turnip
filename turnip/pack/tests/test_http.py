# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from cStringIO import StringIO

from testtools import TestCase
from testtools.deferredruntest import AsynchronousDeferredRunTest
from twisted.internet import defer
from twisted.test import proto_helpers
from twisted.web import server
from twisted.web.test import requesthelper

from turnip.pack import (
    helpers,
    http,
    )


class LessDummyRequest(requesthelper.DummyRequest):

    @property
    def value(self):
        return "".join(self.written)

    def registerProducer(self, prod, s):
        # Avoid DummyRequest.registerProducer calling resumeProducing
        # forever, never giving the reactor a chance to run.
        if not s:
            super(LessDummyRequest, self).registerProducer(prod, s)

    def getUser(self):
        return None

    def getPassword(self):
        return None


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

    allowed_services = frozenset((b'git-upload-pack', b'git-receive-pack'))

    def __init__(self):
        self.backend_transport = None
        self.backend_connected = defer.Deferred()

    def authenticateWithPassword(self, user, password):
        return None, None

    def connectToBackend(self, client_factory):
        self.backend_transport = (
            proto_helpers.StringTransportWithDisconnection())
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
        self.request.content = StringIO(b'boo')
        rendered = render_resource(self.makeResource(service), self.request)
        if backend_response is not None:
            yield self.root.backend_connected
            self.assertIsNot(None, self.root.backend_transport)
            self.root.backend_transport.protocol.dataReceived(backend_response)
            self.root.backend_transport.loseConnection()
        else:
            self.assertIs(None, self.root.backend_transport)
        yield rendered
        defer.returnValue(self.request)

    @defer.inlineCallbacks
    def test_backend_immediately_dies(self):
        # If the backend disappears before it says anything, that's an
        # internal server error.
        yield self.performRequest('')
        self.assertEqual(500, self.request.responseCode)
        self.assertEqual('Backend connection lost.', self.request.value)

    @defer.inlineCallbacks
    def test_backend_virt_error(self):
        # A virt error with a known code is mapped to a specific HTTP status.
        yield self.performRequest(
            helpers.encode_packet(b'ERR turnip virt error: NOT_FOUND enoent'))
        self.assertEqual(404, self.request.responseCode)
        self.assertEqual('enoent', self.request.value)

    @defer.inlineCallbacks
    def test_backend_virt_error_unknown(self):
        # A virt error with an unknown code is an internal server error.
        yield self.performRequest(
            helpers.encode_packet(b'ERR turnip virt error: random yay'))
        self.assertEqual(500, self.request.responseCode)
        self.assertEqual('yay', self.request.value)


class TestSmartHTTPRefsResource(ErrorTestMixin, TestCase):

    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=1)

    request_method = 'GET'

    def setUp(self):
        super(TestSmartHTTPRefsResource, self).setUp()
        self.root = FakeRoot()
        self.request = LessDummyRequest([''])
        self.request.method = b'GET'

    def makeResource(self, service):
        return http.SmartHTTPRefsResource(self.root, b'/foo')

    @defer.inlineCallbacks
    def test_dumb_client_rejected(self):
        yield self.performRequest(service=None)
        self.assertEqual(404, self.request.responseCode)
        self.assertEqual(
            "Only git smart HTTP clients are supported.", self.request.value)

    @defer.inlineCallbacks
    def test_unsupported_service(self):
        yield self.performRequest(service=b'foo')
        # self.assertEqual(403, self.request.responseCode)
        self.assertEqual("Unsupported service.", self.request.value)

    @defer.inlineCallbacks
    def test_backend_error(self):
        # Unlike a command request, an unknown error is treated as a
        # crash here, since the user input for a refs request is limited
        # to the path.
        yield self.performRequest(
            helpers.encode_packet(b'ERR so borked'))
        self.assertEqual(500, self.request.responseCode)
        self.assertEqual('so borked', self.request.value)

    @defer.inlineCallbacks
    def test_good(self):
        yield self.performRequest(
            helpers.encode_packet(b'I am git protocol data.') +
            b'And I am raw, since we got a good packet to start with.')
        self.assertEqual(200, self.request.responseCode)
        self.assertEqual(
            '001e# service=git-upload-pack\n'
            '0000001bI am git protocol data.'
            'And I am raw, since we got a good packet to start with.',
            self.request.value)


class TestSmartHTTPCommandResource(ErrorTestMixin, TestCase):

    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=1)

    def setUp(self):
        super(TestSmartHTTPCommandResource, self).setUp()
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
        self.assertEqual('0011ERR so borked', self.request.value)

    @defer.inlineCallbacks
    def test_good(self):
        yield self.performRequest(
            helpers.encode_packet(b'I am git protocol data.') +
            b'And I am raw, since we got a good packet to start with.')
        self.assertEqual(200, self.request.responseCode)
        self.assertEqual(
            '001bI am git protocol data.'
            'And I am raw, since we got a good packet to start with.',
            self.request.value)
