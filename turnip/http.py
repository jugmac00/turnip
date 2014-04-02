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
from twisted.internet.utils import getProcessValue
from twisted.web import (
    resource,
    server,
    static,
    xmlrpc,
    )

from turnip.helpers import (
    compose_path,
    encode_packet,
    encode_request,
    )


class TurnipAPIResource(resource.Resource):

    def __init__(self, root):
        resource.Resource.__init__(self)
        self.root = root

    def getChild(self, name, request):
        if name == b'':
            return static.Data(b'Turnip API endpoint', type=b'text/plain')
        if name == b'create':
            return CreateResource(self.root)
        else:
            return resource.NoResource(b'No such resource')

    def render_GET(self, request):
        return b'Turnip API service endpoint'


class CreateResource(resource.Resource):

    def __init__(self, root):
        resource.Resource.__init__(self)
        self.root = root

    @defer.inlineCallbacks
    def createRepo(self, request, raw_path):
        repo_path = compose_path(self.root, raw_path)
        ret = yield getProcessValue('git', ('init', '--bare', repo_path))
        if ret != 0:
            raise Exception("'git init' failed")
        request.write(b'OK')
        request.finish()

    def render_POST(self, request):
        path = request.args['path'][0]
        d = self.createRepo(request, path)
        d.addErrback(request.processingFailed)
        return server.NOT_DONE_YET


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


class SmartHTTPRefsResource(resource.Resource):

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
        d.addErrback(request.processingFailed)
        return server.NOT_DONE_YET

    @defer.inlineCallbacks
    def doIt(self, request, service):
        request.setHeader(
            b'Content-Type', b'application/x-%s-advertisement' % service)
        request.write(encode_packet(b'# service=%s\n' % service))
        request.write(encode_packet(None))

        proxy = xmlrpc.Proxy(self.root.virtinfo_endpoint)
        try:
            translated = yield proxy.callRemote(b'translatePath', self.path)
            self.pathname = translated['path']
            self.writable = translated['writable']
        except Exception as e:
            self.die(request, b"Boom: %r" % e)
            return

        if service != b'git-upload-pack' and not self.writable:
            self.die(request, b'Repository is read-only')
            return

        client_factory = HTTPPackClientFactory(
            service, self.pathname,
            {b'turnip-advertise-refs': b'yes'}, request.content, request)
        reactor.connectTCP(
            self.root.backend_host, self.root.backend_port, client_factory)

    def die(self, request, message):
        request.write(encode_packet(b'ERR ' + message + b'\n'))
        request.finish()


class SmartHTTPCommandResource(resource.Resource):

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
        d.addErrback(request.processingFailed)
        return server.NOT_DONE_YET

    @defer.inlineCallbacks
    def doIt(self, request, content, service):
        request.setHeader(
            b'Content-Type', b'application/x-%s-result' % self.service)
        proxy = xmlrpc.Proxy(self.root.virtinfo_endpoint)
        try:
            translated = yield proxy.callRemote(b'translatePath', self.path)
            self.pathname = translated['path']
            self.writable = translated['writable']
        except Exception as e:
            self.die(request, b"Boom: %r" % e)
            return

        if service != b'git-upload-pack' and not self.writable:
            self.die(request, b'Repository is read-only')
            return

        client_factory = HTTPPackClientFactory(
            self.service, self.pathname,
            {b'turnip-stateless-rpc': b'yes'}, content, request)
        reactor.connectTCP(
            self.root.backend_host, self.root.backend_port, client_factory)

    def die(self, request, message):
        request.write(encode_packet(b'ERR ' + message + b'\n'))
        request.finish()


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
