# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from cStringIO import StringIO
import json
import os.path
import tempfile
import textwrap
try:
    from urllib.parse import urlencode
except ImportError:
    from urllib import urlencode
import sys
import zlib

from openid.consumer import consumer
from openid.extensions.sreg import (
    SRegRequest,
    SRegResponse,
    )
from paste.auth.cookie import (
    AuthCookieSigner,
    decode as decode_cookie,
    encode as encode_cookie,
    )
from twisted.internet import (
    defer,
    protocol,
    reactor,
    )
from twisted.python import (
    compat,
    log,
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
        failure.printTraceback()
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
        static.Data.__init__(self, self.robots_txt, b'text/plain')


class CGitScriptResource(twcgi.CGIScript):
    """HTTP resource to run cgit."""

    def __init__(self, root, repo_url, repo_path, trailing, private):
        twcgi.CGIScript.__init__(self, root.cgit_exec_path)
        self.root = root
        self.repo_url = repo_url
        self.repo_path = repo_path
        self.trailing = trailing
        self.private = private
        self.cgit_config = None

    def _finished(self, ignored):
        if self.cgit_config is not None:
            self.cgit_config.close()

    def runProcess(self, env, request, *args, **kwargs):
        request.notifyFinish().addBoth(self._finished)
        self.cgit_config = tempfile.NamedTemporaryFile(
            mode='w+', prefix='turnip-cgit-')
        os.chmod(self.cgit_config.name, 0o644)
        fmt = {'repo_url': self.repo_url, 'repo_path': self.repo_path}
        if self.root.site_name is not None:
            prefixes = " ".join(
                "{}://{}".format(scheme, self.root.site_name)
                for scheme in ("git", "git+ssh", "https"))
            print("clone-prefix={}".format(prefixes), file=self.cgit_config)
        if self.private:
            fmt['css'] = '/static/cgit-private.css'
        else:
            fmt['css'] = '/static/cgit-public.css'
        print(textwrap.dedent("""\
            css={css}
            enable-http-clone=0
            enable-index-owner=0
            logo=/static/launchpad-logo.png
            """).format(**fmt), end='', file=self.cgit_config)
        if self.private:
            top = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            print(
                'header={top}/static/private-banner.html'.format(top=top),
                file=self.cgit_config)
        print(file=self.cgit_config)
        print(textwrap.dedent("""\
            repo.url={repo_url}
            repo.path={repo_path}
            """).format(**fmt), end='', file=self.cgit_config)
        self.cgit_config.flush()
        env["CGIT_CONFIG"] = self.cgit_config.name
        env["PATH_INFO"] = "/%s%s" % (self.repo_url, self.trailing)
        env["SCRIPT_NAME"] = "/"
        twcgi.CGIScript.runProcess(self, env, request, *args, **kwargs)


class BaseHTTPAuthResource(resource.Resource):
    """Base HTTP resource for OpenID authentication handling."""

    session_var = 'turnip.session'
    cookie_name = 'TURNIP_COOKIE'
    anonymous_id = '+launchpad-anonymous'

    def __init__(self, root):
        resource.Resource.__init__(self)
        self.root = root
        if root.cgit_secret is not None:
            self.signer = AuthCookieSigner(root.cgit_secret)
        else:
            self.signer = None

    def _getSession(self, request):
        if self.signer is not None:
            cookie = request.getCookie(self.cookie_name)
            if cookie is not None:
                content = self.signer.auth(cookie)
                if content:
                    return json.loads(decode_cookie(content))
        return {}

    def _putSession(self, request, session):
        if self.signer is not None:
            content = self.signer.sign(encode_cookie(json.dumps(session)))
            cookie = '%s=%s; Path=/; secure;' % (self.cookie_name, content)
            request.setHeader(b'Set-Cookie', cookie.encode('UTF-8'))

    def _setErrorCode(self, request, code=http.INTERNAL_SERVER_ERROR):
        request.setResponseCode(code)
        request.setHeader(b'Content-Type', b'text/plain')

    def _error(self, request, message, code=http.INTERNAL_SERVER_ERROR):
        self._setErrorCode(request, code=code)
        request.write(message.encode('UTF-8'))
        request.finish()

    def _makeConsumer(self, session):
        """Build an OpenID `Consumer` object with standard arguments."""
        # Multiple instances need to share a store or not use one at all (in
        # which case they will use check_authentication).  Using no store is
        # easier, and check_authentication is cheap.
        return consumer.Consumer(session, None)


class HTTPAuthLoginResource(BaseHTTPAuthResource):
    """HTTP resource to complete OpenID authentication."""

    isLeaf = True

    def render_GET(self, request):
        """Complete the OpenID authentication process.

        Here we handle the result of the OpenID process.  If the process
        succeeded, we record the identity URL and username in the session
        and redirect the user to the page they were trying to view that
        triggered the login attempt.  In the various failure cases we return
        a 401 Unauthorized response with a brief explanation of what went
        wrong.
        """
        session = self._getSession(request)
        query = {k: v[-1] for k, v in request.args.items()}
        response = self._makeConsumer(session).complete(
            query, query['openid.return_to'])
        if response.status == consumer.SUCCESS:
            log.msg('OpenID response: SUCCESS')
            sreg_info = SRegResponse.fromSuccessResponse(response)
            if not sreg_info:
                log.msg('sreg_info is None')
                self._setErrorCode(request, http.UNAUTHORIZED)
                return (
                    b"You don't have a Launchpad account.  Check that you're "
                    b"logged in as the right user, or log into Launchpad and "
                    b"try again.")
            else:
                session['identity_url'] = response.identity_url
                session['user'] = sreg_info['nickname']
                self._putSession(request, session)
                request.redirect(query['back_to'])
                return b''
        elif response.status == consumer.FAILURE:
            log.msg('OpenID response: FAILURE: %s' % response.message)
            self._setErrorCode(request, http.UNAUTHORIZED)
            return response.message.encode('UTF-8')
        elif response.status == consumer.CANCEL:
            log.msg('OpenID response: CANCEL')
            self._setErrorCode(request, http.UNAUTHORIZED)
            return b'Authentication cancelled.'
        else:
            log.msg('OpenID response: UNKNOWN')
            self._setErrorCode(request, http.UNAUTHORIZED)
            return b'Unknown OpenID response.'


class HTTPAuthLogoutResource(BaseHTTPAuthResource):
    """HTTP resource to log out of OpenID authentication."""

    isLeaf = True

    def render_GET(self, request):
        """Log out of turnip.

        Clear the cookie and redirect to `next_to`.
        """
        self._putSession(request, {})
        if 'next_to' in request.args:
            next_url = request.args['next_to'][-1]
        else:
            next_url = self.root.main_site_root
        request.redirect(next_url)
        return b''


class HTTPAuthRootResource(BaseHTTPAuthResource):
    """HTTP resource to translate a path and authenticate if necessary.

    Requests that require further authentication are denied or sent through
    OpenID redirection, as appropriate.  Properly-authenticated requests are
    passed on to cgit.
    """

    isLeaf = True

    def _beginLogin(self, request, session):
        """Start the process of authenticating with OpenID.

        We redirect the user to Launchpad to identify themselves.  Launchpad
        will then redirect them to our +login page with enough information
        that we can then redirect them again to the page they were looking
        at, with a cookie that gives us the identity URL and username.
        """
        openid_request = self._makeConsumer(session).begin(
            self.root.openid_provider_root)
        openid_request.addExtension(SRegRequest(required=['nickname']))
        base_url = 'https://%s' % compat.nativeString(
            request.getRequestHostname())
        back_to = base_url + request.uri
        target = openid_request.redirectURL(
            base_url + '/',
            base_url + '/+login/?' + urlencode({'back_to': back_to}))
        request.redirect(target.encode('UTF-8'))
        request.finish()

    def _translatePathCallback(self, translated, request):
        if 'path' not in translated:
            self._error(request, 'translatePath response did not include path')
            return
        repo_url = request.path.rstrip('/')
        # cgit simply parses configuration values up to the end of a line
        # following the first '=', so almost anything is safe, but
        # double-check that there are no newlines to confuse things.
        if '\n' in repo_url:
            self._error(request, 'repository URL may not contain newlines')
            return
        try:
            repo_path = compose_path(self.root.repo_store, translated['path'])
        except ValueError as e:
            self._error(request, str(e))
            return
        trailing = translated.get('trailing')
        if trailing:
            if not trailing.startswith('/'):
                trailing = '/' + trailing
            if not repo_url.endswith(trailing):
                self._error(
                    request,
                    'translatePath returned inconsistent response: '
                    '"%s" does not end with "%s"' % (repo_url, trailing))
                return
            repo_url = repo_url[:-len(trailing)]
        repo_url = repo_url.strip('/')
        cgit_resource = CGitScriptResource(
            self.root, repo_url, repo_path, trailing, translated['private'])
        request.render(cgit_resource)

    def _translatePathErrback(self, failure, request, session):
        e = failure.value
        message = e.faultString
        if e.faultCode in (1, 290):
            error_code = http.NOT_FOUND
        elif e.faultCode in (2, 310):
            error_code = http.FORBIDDEN
        elif e.faultCode in (3, 410):
            if 'user' in session:
                error_code = http.FORBIDDEN
                message = (
                    'You are logged in as %s, but do not have access to this '
                    'repository.' % session['user'])
            elif self.signer is None:
                error_code = http.FORBIDDEN
                message = 'Server does not support OpenID authentication.'
            else:
                self._beginLogin(request, session)
                return
        else:
            error_code = http.INTERNAL_SERVER_ERROR
        self._error(request, message, code=error_code)

    def render_GET(self, request):
        session = self._getSession(request)
        identity_url = session.get('identity_url', self.anonymous_id)
        proxy = xmlrpc.Proxy(self.root.virtinfo_endpoint, allowNone=True)
        d = proxy.callRemote(
            b'translatePath', request.path, b'read', identity_url, True)
        d.addCallback(self._translatePathCallback, request)
        d.addErrback(self._translatePathErrback, request, session)
        return server.NOT_DONE_YET


class HTTPAuthResource(resource.Resource):
    """Container for the various HTTP authentication resources."""

    def __init__(self, root):
        resource.Resource.__init__(self)
        self.root = root
        self.putChild('+login', HTTPAuthLoginResource(root))
        self.putChild('+logout', HTTPAuthLogoutResource(root))

    def getChild(self, path, request):
        # Delegate to a child resource without consuming a path element.
        request.postpath.insert(0, path)
        request.prepath.pop()
        return HTTPAuthRootResource(self.root)


class SmartHTTPFrontendResource(resource.Resource):
    """HTTP resource to translate Git smart HTTP requests to pack protocol."""

    allowed_services = frozenset((b'git-upload-pack', b'git-receive-pack'))

    def __init__(self, backend_host, config):
        resource.Resource.__init__(self)
        self.backend_host = backend_host
        self.backend_port = config.get("pack_virt_port")
        self.virtinfo_endpoint = config.get("virtinfo_endpoint")
        # XXX cjwatson 2015-03-30: Knowing about the store path here
        # violates turnip's layering and may cause scaling problems later,
        # but for now cgit needs direct filesystem access.
        self.repo_store = config.get("repo_store")
        self.cgit_exec_path = config.get("cgit_exec_path")
        self.openid_provider_root = config.get("openid_provider_root")
        self.site_name = config.get("site_name")
        self.main_site_root = config.get("main_site_root")
        self.putChild('', SmartHTTPRootResource())
        cgit_data_path = config.get("cgit_data_path")
        if cgit_data_path is not None:
            static_resource = DirectoryWithoutListings(
                cgit_data_path, defaultType='text/plain')
            top = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            stdir = os.path.join(top, 'static')
            for name in ('launchpad-logo.png', 'notification-private.png'):
                path = os.path.join(stdir, name)
                static_resource.putChild(name, static.File(path))
            with open(os.path.join(cgit_data_path, 'cgit.css'), 'rb') as f:
                css = f.read()
            with open(os.path.join(stdir, 'ubuntu-webfonts.css'), 'rb') as f:
                css += b'\n' + f.read()
            with open(os.path.join(stdir, 'global.css'), 'rb') as f:
                css += b'\n' + f.read()
            with open(os.path.join(stdir, 'private.css'), 'rb') as f:
                private_css = css + b'\n' + f.read()
            static_resource.putChild(
                'cgit-public.css', static.Data(css, b'text/css'))
            static_resource.putChild(
                'cgit-private.css', static.Data(private_css, b'text/css'))
            self.putChild('static', static_resource)
            favicon = os.path.join(stdir, 'launchpad.png')
            self.putChild('favicon.ico', static.File(favicon))
            self.putChild('robots.txt', RobotsResource())
        cgit_secret_path = config.get("cgit_secret_path")
        if cgit_secret_path:
            with open(cgit_secret_path, 'rb') as cgit_secret_file:
                self.cgit_secret = cgit_secret_file.read()
        else:
            self.cgit_secret = None

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
            # Delegate to a child resource without consuming a path element.
            request.postpath.insert(0, path)
            request.prepath.pop()
            return HTTPAuthResource(self)
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
