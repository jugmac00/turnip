from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from cStringIO import StringIO
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
    xmlrpc,
    )

from turnip.helpers import (
    encode_packet,
    encode_request,
    )


class HTTPPackClientProtocol(protocol.Protocol):

    def connectionMade(self):
        self.transport.write(
            encode_packet(encode_request(
                self.factory.command, self.factory.pathname,
                self.factory.params)))
        self.transport.write(self.factory.body.read())

    def dataReceived(self, data):
        self.factory.http_request.write(data)

    def connectionLost(self, reason):
        self.factory.http_request.finish()


class HTTPPackClientFactory(protocol.ClientFactory):

    protocol = HTTPPackClientProtocol

    def __init__(self, command, pathname, params, body, http_request):
        self.command = command
        self.pathname = pathname
        self.params = params
        self.body = body
        self.http_request = http_request


class BaseSmartHTTPResource(resource.Resource):

    @defer.inlineCallbacks
    def translateRequestPath(self, request):
        proxy = xmlrpc.Proxy(self.root.virtinfo_endpoint)
        translated = yield proxy.callRemote(b'translatePath', self.path)
        self.pathname = translated['path']
        self.writable = translated['writable']

    def die(self, request, message):
        request.setResponseCode(http.INTERNAL_SERVER_ERROR)
        request.write(encode_packet(b'ERR ' + message + b'\n'))
        request.finish()

    def die_eb(self, failure, request, message):
        self.die(request, message + bytes(failure.value))


class SmartHTTPRefsResource(BaseSmartHTTPResource):

    isLeaf = True

    def __init__(self, root, path):
        self.root = root
        self.path = path

    def render_GET(self, request):
        try:
            service = request.args['service'][0]
        except (KeyError, IndexError):
            return resource.NoResource(
                b'Only git smart HTTP clients are supported.')

        d = self.doIt(request, service)
        d.addErrback(self.die_eb, request, b'Boom: ')
        d.addErrback(request.processingFailed)
        return server.NOT_DONE_YET

    @defer.inlineCallbacks
    def doIt(self, request, service):
        request.setHeader(
            b'Content-Type', b'application/x-%s-advertisement' % service)
        request.write(encode_packet(b'# service=%s\n' % service))
        request.write(encode_packet(None))

        yield self.translateRequestPath(request)

        if service != b'git-upload-pack' and not self.writable:
            self.die(request, b'Repository is read-only')
            return

        client_factory = HTTPPackClientFactory(
            service, self.pathname,
            {b'turnip-advertise-refs': b'yes'}, request.content, request)
        reactor.connectTCP(
            self.root.backend_host, self.root.backend_port, client_factory)


class SmartHTTPCommandResource(BaseSmartHTTPResource):

    isLeaf = True

    def __init__(self, root, service, path):
        self.root = root
        self.service = service
        self.path = path

    def render_POST(self, request):
        content = request.content
        # XXX: We really need to hack twisted.web to stream the request
        # body, and decode it in a less hacky manner (git always uses
        # C-E: gzip without negotiating).
        content_encoding = request.requestHeaders.getRawHeaders(
            b'Content-Encoding', default=(None,))[0]
        if content_encoding == b'gzip':
            content = StringIO(
                zlib.decompress(request.content.read(), 16 + zlib.MAX_WBITS))

        d = self.doIt(request, content, self.service)
        d.addErrback(self.die_eb, request, b'Boom: ')
        d.addErrback(request.processingFailed)
        return server.NOT_DONE_YET

    @defer.inlineCallbacks
    def doIt(self, request, content, service):
        request.setHeader(
            b'Content-Type', b'application/x-%s-result' % self.service)

        yield self.translateRequestPath(request)

        if service != b'git-upload-pack' and not self.writable:
            self.die(request, b'Repository is read-only')
            return

        client_factory = HTTPPackClientFactory(
            self.service, self.pathname,
            {b'turnip-stateless-rpc': b'yes'}, content, request)
        reactor.connectTCP(
            self.root.backend_host, self.root.backend_port, client_factory)


class SmartHTTPFrontendResource(resource.Resource):

    def __init__(self, backend_host, backend_port, virtinfo_endpoint):
        resource.Resource.__init__(self)
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.virtinfo_endpoint = virtinfo_endpoint

    def getChild(self, path, request):
        if request.path.endswith(b'/info/refs'):
            return SmartHTTPRefsResource(
                self, request.path[:-len(b'/info/refs')])
        elif request.path.endswith(b'/git-upload-pack'):
            return SmartHTTPCommandResource(
                self, b'git-upload-pack',
                request.path[:-len(b'/git-upload-pack')])
        elif request.path.endswith(b'/git-receive-pack'):
            return SmartHTTPCommandResource(
                self, b'git-receive-pack',
                request.path[:-len(b'/git-receive-pack')])
        else:
            return resource.NoResource(b'No such resource')
