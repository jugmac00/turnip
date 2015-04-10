from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from cStringIO import StringIO
import os.path
import tempfile
import textwrap
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
    static,
    twcgi,
    )

from turnip.helpers import compose_path
from turnip.pack.git import (
    ERROR_PREFIX,
    PackProtocol,
    VIRT_ERROR_PREFIX,
    )
from turnip.pack.helpers import (
    encode_packet,
    encode_request,
    )
# twisted.web.xmlrpc doesn't exist for Python 3 yet, but the non-XML-RPC
# bits of this module work.
if sys.version_info.major < 3:
    from twisted.web import xmlrpc


class HTTPPackClientProtocol(PackProtocol):
    """Abstract bridge between a Git pack connection and a smart HTTP request.

    The transport must be a connection to a Git pack server.
    factory.http_request is a Git smart HTTP client request.

    Upon backend connection, a factory-defined request is sent, followed
    by the client's request body. If the immediate response is a known
    error, it is mapped to an HTTP response and the connection is
    terminated. Otherwise all data is forwarded from backend to client.

    Concrete implementations must override backendConnected to prepare
    the HTTP response for the backend's reply.
    """

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
            self.factory.http_request.unregisterProducer()
            self.factory.http_request.finish()
            return True
        # We don't know it was a system error, so just send it back to
        # the client as a remote error and proceed to forward data
        # regardless.
        return False

    def _finish(self, result):
        # Ensure the backend dies if the client disconnects.
        if self.transport is not None:
            self.transport.stopProducing()

    def connectionMade(self):
        """Forward the request and the client's payload to the backend."""
        self.factory.http_request.notifyFinish().addBoth(self._finish)
        self.factory.http_request.registerProducer(
            self.transport, True)
        self.sendPacket(
            encode_request(
                self.factory.command, self.factory.pathname,
                self.factory.params))
        self.sendRawData(self.factory.body.read())

    def packetReceived(self, data):
        """Check and forward the first packet from the backend.

        Assume that any non-error packet indicates a success response,
        so we can just forward raw data afterward.
        """
        self.raw = True
        if data is not None and data.startswith(ERROR_PREFIX):
            # Handle the error nicely if it's known (eg. 404 on
            # nonexistent repo). If it's unknown, just forward the error
            # along to the client and forward as normal.
            virt_error = self.backendConnectionFailed(data[len(ERROR_PREFIX):])
        else:
            virt_error = False
        if not virt_error:
            self.backendConnected()
            self.rawDataReceived(encode_packet(data))

    def rawDataReceived(self, data):
        if not self.factory.http_request.finished:
            self.factory.http_request.write(data)

    def connectionLost(self, reason):
        self.factory.http_request.unregisterProducer()
        if not self.factory.http_request.finished:
            if not self.raw:
                self.factory.http_request.setResponseCode(
                    http.INTERNAL_SERVER_ERROR)
                self.factory.http_request.setHeader(
                    b'Content-Type', b'text/plain')
                self.factory.http_request.write(b'Backend connection lost.')
            self.factory.http_request.finish()


class HTTPPackClientRefsProtocol(HTTPPackClientProtocol):

    # The only user input is the request line, which the virt proxy should
    # cause to always be valid. Any unrecognised error is probably a backend
    # failure from repository corruption or similar.
    user_error_possible = False

    def backendConnected(self):
        """Prepare the HTTP response for forwarding from the backend."""
        self.factory.http_request.setResponseCode(http.OK)
        self.factory.http_request.setHeader(
            b'Content-Type',
            b'application/x-%s-advertisement' % self.factory.command)
        self.rawDataReceived(
            encode_packet(b'# service=%s\n' % self.factory.command))
        self.rawDataReceived(encode_packet(None))


class HTTPPackClientCommandProtocol(HTTPPackClientProtocol):

    def backendConnected(self):
        """Prepare the HTTP response for forwarding from the backend."""
        self.factory.http_request.setResponseCode(http.OK)
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
    """Base HTTP resource for Git smart HTTP auth and error handling."""

    extra_params = {}

    def errback(self, failure, request):
        """Handle a Twisted failure by returning an HTTP error."""
        if request.finished:
            return
        request.write(self.error(request, repr(failure)))
        request.unregisterProducer()
        request.finish()

    def error(self, request, message, code=http.INTERNAL_SERVER_ERROR):
        """Prepare for an error response and return the body."""
        request.setResponseCode(code)
        request.setHeader(b'Content-Type', b'text/plain')
        return message

    @defer.inlineCallbacks
    def authenticateUser(self, request):
        """Attempt authentication of the request with the virt service."""
        if request.getUser():
            user, uid = yield self.root.authenticateWithPassword(
                request.getUser(), request.getPassword())
            defer.returnValue((user, uid))
        defer.returnValue((None, None))

    @defer.inlineCallbacks
    def connectToBackend(self, factory, service, path, content, request):
        """Establish a pack connection to the backend.

        The turnip-authenticated-user parameter is set to the username
        returned by the virt service, if any.
        """
        params = {b'turnip-can-authenticate': b'yes'}
        authenticated_user, authenticated_uid = yield self.authenticateUser(
            request)
        if authenticated_user:
            params[b'turnip-authenticated-user'] = authenticated_user
            params[b'turnip-authenticated-uid'] = str(authenticated_uid)
        params.update(self.extra_params)
        client_factory = factory(service, path, params, content, request)
        self.root.connectToBackend(client_factory)


class SmartHTTPRefsResource(BaseSmartHTTPResource):
    """HTTP resource for Git smart HTTP ref discovery requests."""

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
    """HTTP resource for Git smart HTTP command requests."""

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


class SmartHTTPRootResource(resource.Resource):
    """HTTP resource to handle operations on the root path."""

    def render_OPTIONS(self, request):
        # Trivially respond to OPTIONS / for the sake of haproxy.
        return b''


class DirectoryWithoutListings(static.File):
    """A static directory resource without support for directory listings."""

    def directoryListing(self):
        return self.childNotFound


class RobotsResource(static.Data):
    """HTTP resource to serve our robots.txt."""

    robots_txt = textwrap.dedent("""\
        User-agent: *
        Disallow: /
        """).encode('US-ASCII')

    def __init__(self):
        static.Data.__init__(self, self.robots_txt, 'text/plain')


class CGitScriptResource(twcgi.CGIScript):
    """HTTP resource to run cgit."""

    def __init__(self, root):
        twcgi.CGIScript.__init__(self, root.cgit_exec_path)
        self.root = root
        self.cgit_config = None

    def _error(self, request, message, code=http.INTERNAL_SERVER_ERROR):
        request.setResponseCode(code)
        request.setHeader(b'Content-Type', b'text/plain')
        request.write(message)
        request.finish()

    def _finished(self, ignored):
        if self.cgit_config is not None:
            self.cgit_config.close()

    def _translatePathCallback(self, translated, env, request,
                               *args, **kwargs):
        if 'path' not in translated:
            self._error(
                request, b'translatePath response did not include path')
            return
        repo_url = request.path.rstrip('/')
        # cgit simply parses configuration values up to the end of a line
        # following the first '=', so almost anything is safe, but
        # double-check that there are no newlines to confuse things.
        if '\n' in repo_url:
            self._error(request, b'repository URL may not contain newlines')
            return
        try:
            repo_path = compose_path(self.root.repo_store, translated['path'])
        except ValueError as e:
            self._error(request, str(e).encode('UTF-8'))
            return
        trailing = translated.get('trailing')
        if trailing:
            if not trailing.startswith('/'):
                trailing = '/' + trailing
            if not repo_url.endswith(trailing):
                self._error(
                    request,
                    b'translatePath returned inconsistent response: '
                    b'"%s" does not end with "%s"' % (
                        repo_url.encode('UTF-8'), trailing.encode('UTF-8')))
                return
            repo_url = repo_url[:-len(trailing)]
        repo_url = repo_url.strip('/')
        request.notifyFinish().addBoth(self._finished)
        self.cgit_config = tempfile.NamedTemporaryFile(
            mode='w+', prefix='turnip-cgit-')
        os.chmod(self.cgit_config.name, 0o644)
        fmt = {'repo_url': repo_url, 'repo_path': repo_path}
        if self.root.site_name is not None:
            prefixes = " ".join(
                "{}://{}".format(scheme, self.root.site_name)
                for scheme in ("git", "git+ssh", "https"))
            print("clone-prefix={}".format(prefixes), file=self.cgit_config)
        print(textwrap.dedent("""\
            css=/static/cgit.css
            enable-http-clone=0
            enable-index-owner=0
            logo=/static/launchpad-logo.png

            repo.url={repo_url}
            repo.path={repo_path}
            """).format(**fmt), file=self.cgit_config)
        self.cgit_config.flush()
        env["CGIT_CONFIG"] = self.cgit_config.name
        env["PATH_INFO"] = "/%s%s" % (repo_url, trailing)
        env["SCRIPT_NAME"] = "/"
        twcgi.CGIScript.runProcess(self, env, request, *args, **kwargs)

    def _translatePathErrback(self, failure, request):
        e = failure.value
        if e.faultCode in (1, 290):
            error_code = http.NOT_FOUND
        elif e.faultCode in (2, 310):
            error_code = http.FORBIDDEN
        elif e.faultCode in (3, 410):
            # XXX cjwatson 2015-03-30: should be UNAUTHORIZED, but we
            # don't implement that yet
            error_code = http.FORBIDDEN
        else:
            error_code = http.INTERNAL_SERVER_ERROR
        self._error(request, e.faultString, code=error_code)

    def runProcess(self, env, request, *args, **kwargs):
        proxy = xmlrpc.Proxy(self.root.virtinfo_endpoint, allowNone=True)
        # XXX cjwatson 2015-03-30: authentication
        d = proxy.callRemote(
            b'translatePath', request.path, b'read', None, False)
        d.addCallback(
            self._translatePathCallback, env, request, *args, **kwargs)
        d.addErrback(self._translatePathErrback, request)
        return server.NOT_DONE_YET


class SmartHTTPFrontendResource(resource.Resource):
    """HTTP resource to translate Git smart HTTP requests to pack protocol."""

    allowed_services = frozenset((b'git-upload-pack', b'git-receive-pack'))

    def __init__(self, backend_host, backend_port, virtinfo_endpoint,
                 repo_store, cgit_exec_path=None, cgit_data_path=None,
                 site_name=None):
        resource.Resource.__init__(self)
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.virtinfo_endpoint = virtinfo_endpoint
        # XXX cjwatson 2015-03-30: Knowing about the store path here
        # violates turnip's layering and may cause scaling problems later,
        # but for now cgit needs direct filesystem access.
        self.repo_store = repo_store
        self.cgit_exec_path = cgit_exec_path
        self.site_name = site_name
        self.putChild('', SmartHTTPRootResource())
        if cgit_data_path is not None:
            static_resource = DirectoryWithoutListings(
                cgit_data_path, defaultType='text/plain')
            top = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            logo = os.path.join(top, 'images', 'launchpad-logo.png')
            static_resource.putChild('launchpad-logo.png', static.File(logo))
            self.putChild('static', static_resource)
            favicon = os.path.join(top, 'images', 'launchpad.png')
            self.putChild('favicon.ico', static.File(favicon))

    @staticmethod
    def _isGitRequest(request):
        if request.path.endswith(b'/info/refs'):
            service = request.args.get('service', [])
            if service and service[0].startswith('git-'):
                return True
        content_type = request.getHeader(b'Content-Type')
        if content_type is None:
            return False
        return content_type.startswith(b'application/x-git-')

    def getChild(self, path, request):
        if self._isGitRequest(request):
            if request.path.endswith(b'/info/refs'):
                # /PATH/TO/REPO/info/refs
                return SmartHTTPRefsResource(
                    self, request.path[:-len(b'/info/refs')])
            try:
                # /PATH/TO/REPO/SERVICE
                path, service = request.path.rsplit(b'/', 1)
            except ValueError:
                path = request.path
                service = None
            if service in self.allowed_services:
                return SmartHTTPCommandResource(self, service, path)
        elif self.cgit_exec_path is not None:
            return CGitScriptResource(self)
        return resource.NoResource(b'No such resource')

    def connectToBackend(self, client_factory):
        reactor.connectTCP(
            self.backend_host, self.backend_port, client_factory)

    @defer.inlineCallbacks
    def authenticateWithPassword(self, user, password):
        proxy = xmlrpc.Proxy(self.virtinfo_endpoint)
        try:
            translated = yield proxy.callRemote(
                b'authenticateWithPassword', user, password)
        except xmlrpc.Fault as e:
            if e.faultCode in (3, 410):
                defer.returnValue((None, None))
            else:
                raise
        defer.returnValue((translated['user'], translated['uid']))
