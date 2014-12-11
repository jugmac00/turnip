from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from cStringIO import StringIO
import sys
import zlib

from twisted.internet import (
    defer,
    protocol,
    reactor,
    )
from twisted.web import (
    http,
    resource,
    server,
    )

from turnip.helpers import (
    encode_packet,
    encode_request,
    )
from turnip.packproto import (
    ERROR_PREFIX,
    PackProtocol,
    VIRT_ERROR_PREFIX,
    )
# twisted.web.xmlrpc doesn't exist for Python 3 yet, but the non-XML-RPC
# bits of this module work.
if sys.version_info.major < 3:
    from twisted.web import xmlrpc


class HTTPPackClientProtocol(PackProtocol):

    user_error_possible = True

    def backendConnected(self):
        """Called when the backend is connected and has sent a good packet."""
        raise NotImplementedError()

    def backendConnectionFailed(self, msg):
        """Called when the backend fails to connect or returns an error."""
        # stateless-rpc doesn't send a greeting, so we can't always tell if a
        # backend failed to start at all or rejected some user input
        # afterwards. But we can make educated guesses.
        error_code = None
        if msg.startswith(VIRT_ERROR_PREFIX):
            error_name, msg = msg[len(VIRT_ERROR_PREFIX):].split(b' ', 1)
            if error_name == b'NOT_FOUND':
                error_code = http.NOT_FOUND
            elif error_name == b'FORBIDDEN':
                error_code = http.FORBIDDEN
            elif error_name == b'UNAUTHORIZED':
                error_code = http.UNAUTHORIZED
                self.factory.http_request.setHeader(
                    b'WWW-Authenticate', b'Basic realm=turnip')
            else:
                error_code = http.INTERNAL_SERVER_ERROR
        elif msg.startswith(b'Repository is read-only'):
            error_code = http.FORBIDDEN
        elif not self.user_error_possible:
            error_code = http.INTERNAL_SERVER_ERROR

        if error_code is not None:
            # This is probably a system error (bad auth, not found,
            # repository corruption, etc.), so fail the request.
            self.factory.http_request.setResponseCode(error_code)
            self.factory.http_request.setHeader(b'Content-Type', b'text/plain')
            self.factory.http_request.write(msg)
            self.transport.loseConnection()
        else:
            # We don't know it was a system error, so just send it back to the
            # client as a remote error and continue.
            self.rawDataReceived(encode_packet(ERROR_PREFIX + msg))

    def connectionMade(self):
        self.sendPacket(
            encode_request(
                self.factory.command, self.factory.pathname,
                self.factory.params))
        self.sendRawData(self.factory.body.read())

    def packetReceived(self, data):
        self.raw = True
        if data is not None and data.startswith(ERROR_PREFIX):
            self.backendConnectionFailed(data[len(ERROR_PREFIX):])
        else:
            self.backendConnected()
            self.rawDataReceived(encode_packet(data))

    def rawDataReceived(self, data):
        self.factory.http_request.write(data)

    def connectionLost(self, reason):
        self.factory.http_request.finish()


class HTTPPackClientRefsProtocol(HTTPPackClientProtocol):

    # The only user input is the request line, which the virt proxy should
    # cause to always be valid. Any unrecognised error is probably a backend
    # failure from repository corruption or similar.
    user_error_possible = False

    def backendConnected(self):
        self.factory.http_request.setHeader(
            b'Content-Type',
            b'application/x-%s-advertisement' % self.factory.command)
        self.rawDataReceived(
            encode_packet(b'# service=%s\n' % self.factory.command))
        self.rawDataReceived(encode_packet(None))


class HTTPPackClientCommandProtocol(HTTPPackClientProtocol):

    def backendConnected(self):
        self.factory.http_request.setHeader(
            b'Content-Type',
            b'application/x-%s-result' % self.factory.command)


class HTTPPackClientFactory(protocol.ClientFactory):

    def __init__(self, command, pathname, params, body, http_request):
        self.command = command
        self.pathname = pathname
        self.params = params
        self.body = body
        self.http_request = http_request


class HTTPPackClientCommandFactory(HTTPPackClientFactory):

    protocol = HTTPPackClientCommandProtocol


class HTTPPackClientRefsFactory(HTTPPackClientFactory):

    protocol = HTTPPackClientRefsProtocol


class BaseSmartHTTPResource(resource.Resource):

    extra_params = {}

    def errback(self, failure, request):
        request.write(self.error(request, repr(failure)))
        request.finish()

    def error(self, request, message, code=http.INTERNAL_SERVER_ERROR):
        request.setResponseCode(code)
        request.setHeader(b'Content-Type', b'text/plain')
        return message

    @defer.inlineCallbacks
    def authenticateUser(self, request):
        if request.getUser():
            proxy = xmlrpc.Proxy(self.root.virtinfo_endpoint)
            try:
                translated = yield proxy.callRemote(
                    b'authenticateWithPassword', request.getUser(),
                    request.getPassword())
            except xmlrpc.Fault as e:
                if e.faultCode == 3:
                    defer.returnValue(None)
                else:
                    raise
            defer.returnValue(translated['user'])

    @defer.inlineCallbacks
    def connectToBackend(self, factory, service, path, content, request):
        params = {b'turnip-can-authenticate': b'yes'}
        authenticated_user = yield self.authenticateUser(request)
        if authenticated_user:
            params[b'turnip-authenticated-user'] = authenticated_user
        params.update(self.extra_params)
        client_factory = factory(service, path, params, content, request)
        reactor.connectTCP(
            self.root.backend_host, self.root.backend_port, client_factory)


class SmartHTTPRefsResource(BaseSmartHTTPResource):

    isLeaf = True

    extra_params = {b'turnip-advertise-refs': b'yes'}

    def __init__(self, root, path):
        self.root = root
        self.path = path

    def render_GET(self, request):
        try:
            service = request.args['service'][0]
        except (KeyError, IndexError):
            return self.error(
                request, b'Only git smart HTTP clients are supported.',
                code=http.NOT_FOUND)

        if service not in self.root.allowed_services:
            return self.error(
                request, b'Unsupported service.', code=http.FORBIDDEN)

        d = self.connectToBackend(
            HTTPPackClientRefsFactory, service, self.path, request.content,
            request)
        d.addErrback(self.errback, request)
        return server.NOT_DONE_YET


class SmartHTTPCommandResource(BaseSmartHTTPResource):

    isLeaf = True

    extra_params = {b'turnip-stateless-rpc': b'yes'}

    def __init__(self, root, service, path):
        self.root = root
        self.service = service
        self.path = path

    def render_POST(self, request):
        content_type = request.requestHeaders.getRawHeaders(b'Content-Type')
        if content_type != [b'application/x-%s-request' % self.service]:
            return self.error(
                request, b'Invalid Content-Type for service.',
                code=http.BAD_REQUEST)

        content = request.content
        # XXX: We really need to hack twisted.web to stream the request
        # body, and decode it in a less hacky manner (git always uses
        # C-E: gzip without negotiating).
        content_encoding = request.requestHeaders.getRawHeaders(
            b'Content-Encoding')
        if content_encoding == [b'gzip']:
            content = StringIO(
                zlib.decompress(request.content.read(), 16 + zlib.MAX_WBITS))
        d = self.connectToBackend(
            HTTPPackClientCommandFactory, self.service, self.path, content,
            request)
        d.addErrback(self.errback, request)
        return server.NOT_DONE_YET


class SmartHTTPFrontendResource(resource.Resource):

    allowed_services = frozenset((b'git-upload-pack', b'git-receive-pack'))

    def __init__(self, backend_host, backend_port, virtinfo_endpoint):
        resource.Resource.__init__(self)
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.virtinfo_endpoint = virtinfo_endpoint

    def getChild(self, path, request):
        if request.path.endswith(b'/info/refs'):
            return SmartHTTPRefsResource(
                self, request.path[:-len(b'/info/refs')])

        try:
            path, service = request.path.rsplit(b'/', 1)
        except ValueError:
            path = request.path
            service = None
        if service in self.allowed_services:
            return SmartHTTPCommandResource(self, service, path)

        return resource.NoResource(b'No such resource')
