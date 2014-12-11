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


ERROR_PREFIX = b'ERR '
VIRT_ERROR_PREFIX = b'turnip virt error: '

SAFE_PARAMS = frozenset(['host'])


class PackProtocol(protocol.Protocol):

    paused = False
    raw = False

    __buffer = b''

    def packetReceived(self, payload):
        raise NotImplementedError()

    def rawDataReceived(self, payload):
        raise NotImplementedError()

    def invalidPacketReceived(self, packet):
        raise NotImplementedError()

    def dataReceived(self, raw_data):
        self.__buffer += raw_data
        while not self.paused and not self.raw:
            try:
                payload, self.__buffer = helpers.decode_packet(self.__buffer)
            except ValueError:
                invalid = self.__buffer
                self.__buffer = b''
                self.invalidPacketReceived(invalid)
                break
            if payload is helpers.INCOMPLETE_PKT:
                break
            self.packetReceived(payload)
        else:
            if not self.paused:
                # We don't care about the content any more. Just forward the
                # bytes.
                raw_data = self.__buffer
                self.__buffer = b''
                self.rawDataReceived(raw_data)
                return

    def sendPacket(self, data):
        self.sendRawData(helpers.encode_packet(data))

    def sendRawData(self, data):
        self.transport.write(data)

    def pauseProducing(self):
        self.paused = True
        self.transport.pauseProducing()

    def resumeProducing(self):
        self.paused = False
        self.transport.resumeProducing()
        self.dataReceived(b'')

    def stopProducing(self):
        self.paused = True
        self.transport.stopProducing()


class PackProxyProtocol(PackProtocol):

    peer = None

    def invalidPacketReceived(self, packet):
        self.die(b'Invalid pkt-line')

    def setPeer(self, peer):
        self.peer = peer

    def die(self, message):
        raise NotImplementedError()

    def connectionLost(self, reason):
        if self.peer is not None:
            self.peer.transport.loseConnection()


class PackServerProtocol(PackProxyProtocol):

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
        self.peer.sendPacket(data)

    def rawDataReceived(self, data):
        self.peer.sendRawData(data)

    def die(self, message):
        self.sendPacket(ERROR_PREFIX + message + b'\n')
        self.transport.loseConnection()


class GitProcessProtocol(protocol.ProcessProtocol):

    _err_buffer = b''

    def __init__(self, peer):
        self.peer = peer

    def connectionMade(self):
        self.peer.setPeer(self)
        self.peer.resumeProducing()

    def outReceived(self, data):
        self.peer.sendRawData(data)

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

    def sendPacket(self, data):
        self.sendRawData(helpers.encode_packet(data))

    def sendRawData(self, data):
        self.transport.write(data)

    def processEnded(self, status):
        self.peer.transport.loseConnection()


class PackClientProtocol(PackProxyProtocol):
    """Dumb protocol which just forwards between two others."""

    def connectionMade(self):
        self.peer.setPeer(self)
        self.peer.resumeProducing()

    def packetReceived(self, data):
        self.raw = True
        self.peer.sendPacket(data)

    def rawDataReceived(self, data):
        self.peer.sendRawData(data)

    def die(self, message):
        # The error always goes to the other side.
        self.peer.die(b'backend error: ' + message)
        self.transport.loseConnection()


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
    request_sent = False
    client_factory = PackClientFactory

    def connectToBackend(self, command, pathname, params):
        self.command = command
        self.pathname = pathname
        self.params = params
        client = self.client_factory(self)
        reactor.connectTCP(
            self.factory.backend_host, self.factory.backend_port, client)

    def resumeProducing(self):
        # Send our translated request and then open the gate to the
        # client.
        if not self.request_sent:
            self.request_sent = True
            self.peer.sendPacket(
                helpers.encode_request(
                    self.command, self.pathname, self.params))
        PackServerProtocol.resumeProducing(self)


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


class PackVirtServerProtocol(PackProxyServerProtocol):
    """Turnip-flavoured Git pack protocol virtualisation proxy.

    Translates the request path and authorises access via a request to a
    remote XML-RPC endpoint.
    """

    @defer.inlineCallbacks
    def requestReceived(self, command, pathname, params):
        permission = b'read' if command == b'git-upload-pack' else b'write'
        proxy = xmlrpc.Proxy(self.factory.virtinfo_endpoint, allowNone=True)
        try:
            can_authenticate = (
                params.get(b'turnip-can-authenticate') == b'yes')
            translated = yield proxy.callRemote(
                b'translatePath', pathname, permission,
                params.get(b'turnip-authenticated-user'), can_authenticate)
            pathname = translated['path']
        except xmlrpc.Fault as e:
            if e.faultCode == 1:
                fault_type = b'NOT_FOUND'
            elif e.faultCode == 2:
                fault_type = b'FORBIDDEN'
            elif e.faultCode == 3:
                fault_type = b'UNAUTHORIZED'
            else:
                fault_type = b'INTERNAL_SERVER_ERROR'
            self.die(VIRT_ERROR_PREFIX + fault_type + b' ' + e.faultString)
        except Exception as e:
            self.die(VIRT_ERROR_PREFIX + b'INTERNAL_SERVER_ERROR ' + str(e))
            return
        else:
            self.connectToBackend(command, pathname, params)


class PackVirtFactory(protocol.Factory):

    protocol = PackVirtServerProtocol

    def __init__(self, backend_host, backend_port, virtinfo_endpoint):
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.virtinfo_endpoint = virtinfo_endpoint


class PackFrontendClientProtocol(PackClientProtocol):

    def packetReceived(self, data):
        self.raw = True
        if data and data.startswith(ERROR_PREFIX + VIRT_ERROR_PREFIX):
            # Remove the internal metadata from any virt errors. We
            # don't have the ability to ask for auth.
            _, msg = data[len(ERROR_PREFIX + VIRT_ERROR_PREFIX):].split(
                b' ', 1)
            data = ERROR_PREFIX + msg
        self.peer.sendPacket(data)


class PackFrontendClientFactory(PackClientFactory):

    protocol = PackFrontendClientProtocol


class PackFrontendServerProtocol(PackProxyServerProtocol):
    """Standard Git pack protocol conversion proxy.

    Ensures that it's a vanilla, not turnip-flavoured, pack protocol
    request before forwarding to a backend.
    """

    client_factory = PackFrontendClientFactory

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
