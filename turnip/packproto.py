from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import sys

from twisted.internet import (
    defer,
    protocol,
    reactor,
    )
# twisted.web.xmlrpc doesn't exist for Python 3 yet, but the non-XML-RPC
# bits of this module work.
if sys.version_info.major < 3:
    from twisted.web import xmlrpc

from turnip import helpers


SAFE_PARAMS = frozenset(['host'])


class GitProcessProtocol(protocol.ProcessProtocol):

    _err_buffer = b''

    def __init__(self, peer):
        self.peer = peer

    def connectionMade(self):
        self.peer.setPeer(self)
        self.peer.resumeProducing()

    def outReceived(self, data):
        self.peer.sendData(data)

    def errReceived(self, data):
        # Just store it up so we can forward it as a single ERR packet
        # when the process is done.
        self._err_buffer += data

    def outConnectionLost(self):
        # Close stdin so processEnded can fire. We should possibly do
        # this as soon as the negotation completes, but we need a better
        # understanding of the protocol for that.
        self.transport.closeStdin()
        if self._err_buffer:
            self.peer.die(self._err_buffer)

    def sendData(self, data):
        self.transport.write(data)

    def processEnded(self, status):
        self.peer.transport.loseConnection()


class PackProtocol(protocol.Protocol):

    paused = False
    raw = False

    peer = None
    __buffer = b''

    def setPeer(self, peer):
        self.peer = peer

    def packetReceived(self, payload):
        raise NotImplementedError()

    def die(self, message):
        raise NotImplementedError()

    def dataReceived(self, raw_data):
        assert not self.paused
        self.__buffer += raw_data
        while True:
            if self.paused:
                break
            if self.raw:
                # We don't care about the content any more. Just forward the
                # bytes.
                self.peer.sendData(self.__buffer)
                self.__buffer = b''
                return
            try:
                payload, self.__buffer = helpers.decode_packet(self.__buffer)
            except ValueError as e:
                self.die(str(e).encode('utf-8'))
                break
            if payload is helpers.INCOMPLETE_PKT:
                break
            self.packetReceived(payload)

    def connectionLost(self, reason):
        if self.peer is not None:
            self.peer.transport.loseConnection()

    def sendData(self, data):
        self.transport.write(data)

    def pauseProducing(self):
        self.paused = True
        self.transport.pauseProducing()

    def resumeProducing(self):
        self.paused = False
        self.dataReceived(b'')
        self.transport.resumeProducing()

    def stopProducing(self):
        self.pauseProducing()


class PackServerProtocol(PackProtocol):

    got_request = False
    peer = None

    def requestReceived(self, command, pathname, params):
        """Begin handling of a git pack protocol request.

        Implementations must set peer to an IProtocol connected to the
        backend transport, and perform any connection setup there (eg.
        sending a modified request line). Calling resumeProducing()
        will begin passing data through to the backend.
        """
        raise NotImplementedError()

    def packetReceived(self, data):
        if not self.got_request:
            if data is None:
                self.die(b'Bad request: flush-pkt instead')
                return None
            try:
                command, pathname, params = helpers.decode_request(data)
            except ValueError as e:
                self.die(str(e).encode('utf-8'))
                return
            self.pauseProducing()
            self.got_request = True
            self.requestReceived(command, pathname, params)
            return
        if data is None:
            self.raw = True
        self.peer.sendData(helpers.encode_packet(data))

    def die(self, message):
        self.sendData(
            helpers.encode_packet(b'ERR ' + message + b'\n'))
        self.transport.loseConnection()


class PackClientProtocol(PackProtocol):
    """Dumb protocol which just forwards between two others."""

    def connectionMade(self):
        self.peer.setPeer(self)
        self.peer.resumeProducing()

    def packetReceived(self, data):
        self.raw = True
        self.peer.sendData(helpers.encode_packet(data))

    def die(self, message):
        # The error always goes to the other side.
        self.peer.sendData(
            helpers.encode_packet(b'ERR backend error: ' + message + b'\n'))
        self.transport.loseConnection()


class PackBackendProtocol(PackServerProtocol):
    """Filesystem-backed turnip-flavoured Git pack protocol implementation.

    Invokes the reference C Git implementation.
    """

    def requestReceived(self, command, raw_pathname, params):
        path = helpers.compose_path(self.factory.root, raw_pathname)
        if command == b'git-upload-pack':
            subcmd = b'upload-pack'
        elif command == b'git-receive-pack':
            subcmd = b'receive-pack'
        else:
            self.die(b'Unsupport command in request')
            return

        cmd = b'git'
        args = [b'git', subcmd]
        if params.pop(b'turnip-stateless-rpc', None):
            args.append(b'--stateless-rpc')
        if params.pop(b'turnip-advertise-refs', None):
            args.append(b'--advertise-refs')
        args.append(path)

        self.peer = GitProcessProtocol(self)
        reactor.spawnProcess(self.peer, cmd, args)


class PackBackendFactory(protocol.Factory):

    protocol = PackBackendProtocol

    def __init__(self, root):
        self.root = root


class PackClientFactory(protocol.ClientFactory):

    protocol = PackClientProtocol

    def __init__(self, server):
        self.server = server

    def buildProtocol(self, *args, **kwargs):
        p = protocol.ClientFactory.buildProtocol(self, *args, **kwargs)
        p.setPeer(self.server)
        return p

    def clientConnectionFailed(self, connector, reason):
        self.server.transport.loseConnection()


class PackProxyServerProtocol(PackServerProtocol):
    """Abstract turnip-flavoured Git pack protocol proxy.

    requestReceived can validate or transform requests arbitrarily
    before forwarding them to the backend.
    """

    command = pathname = params = None

    def connectToBackend(self, command, pathname, params):
        self.command = command
        self.pathname = pathname
        self.params = params
        client = PackClientFactory(self)
        reactor.connectTCP(
            self.factory.backend_host, self.factory.backend_port, client)

    def resumeProducing(self):
        # Send our translated request and then open the gate to the
        # client.
        self.peer.sendData(
            helpers.encode_packet(helpers.encode_request(
                self.command, self.pathname, self.params)))
        PackServerProtocol.resumeProducing(self)


class PackVirtServerProtocol(PackProxyServerProtocol):
    """Turnip-flavoured Git pack protocol virtualisation proxy.

    Translates the request path and authorises access via a request to a
    remote XML-RPC endpoint.
    """

    @defer.inlineCallbacks
    def requestReceived(self, command, pathname, params):
        proxy = xmlrpc.Proxy(self.factory.virtinfo_endpoint)
        try:
            translated = yield proxy.callRemote(b'translatePath', pathname)
            pathname = translated['path']
            writable = translated['writable']
        except Exception as e:
            self.die(b'Virtualisation failed: %r' % e)
            return
        if command != b'git-upload-pack' and not writable:
            self.die(b'Repository is read-only')
            return
        self.connectToBackend(command, pathname, params)


class PackVirtFactory(protocol.Factory):

    protocol = PackVirtServerProtocol

    def __init__(self, backend_host, backend_port, virtinfo_endpoint):
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.virtinfo_endpoint = virtinfo_endpoint


class PackFrontendServerProtocol(PackProxyServerProtocol):
    """Standard Git pack protocol conversion proxy.

    Ensures that it's a vanilla, not turnip-flavoured, pack protocol
    request before forwarding to a backend.
    """

    def requestReceived(self, command, pathname, params):
        if set(params.keys()) - SAFE_PARAMS:
            self.die(b'Illegal request parameters')
            return
        self.connectToBackend(command, pathname, params)


class PackFrontendFactory(protocol.Factory):

    protocol = PackFrontendServerProtocol

    def __init__(self, backend_host, backend_port):
        self.backend_host = backend_host
        self.backend_port = backend_port
