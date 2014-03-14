from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from twisted.internet import (
    defer,
    protocol,
    reactor,
    )
from twisted.web import xmlrpc

from turnip.utils import compose_path


PKT_LEN_SIZE = 4
INCOMPLETE_PKT = object()


def encode_packet(data):
    if data is None:
        # flush-pkt.
        return b'0000'
    else:
        # data-pkt
        if len(data) > 65520:
            raise ValueError("data-pkt payload must not exceed 65520 bytes")
        return (b'%04x' % (len(data) + PKT_LEN_SIZE)).encode('ascii') + data


def decode_packet(input):
    """Consume a packet, returning the payload and any unconsumed tail."""
    if len(input) < PKT_LEN_SIZE:
        return (INCOMPLETE_PKT, input)
    if input.startswith(b'0000'):
        return (None, input[PKT_LEN_SIZE:])
    else:
        try:
            pkt_len = int(input[:PKT_LEN_SIZE], 16)
            if not (4 <= pkt_len <= 65524):
                raise ValueError("Invalid pkt-len")
        except ValueError:
            raise ValueError("Invalid pkt-len")
        if len(input) < pkt_len:
            # Some of the packet is yet to be received.
            return (INCOMPLETE_PKT, input)
        return (input[PKT_LEN_SIZE:pkt_len], input[pkt_len:])


def decode_request(data):
    """Decode a git-proto-request.

    Returns a tuple of (command, pathname, host). host may be None if
    there was no host-parameter.
    """
    if b' ' not in data:
        raise ValueError('Invalid git-proto-request')
    command, rest = data.split(b' ', 1)
    args = rest.split(b'\0')
    if len(args) not in (2, 3) or args[-1] != b'':
        raise ValueError('Invalid git-proto-request')
    pathname = args[0]
    if len(args) == 3:
        if not args[1].startswith(b'host='):
            raise ValueError('Invalid host-parameter')
        host = args[1][len(b'host='):]
    else:
        host = None
    return (command, pathname, host)


def encode_request(command, pathname, host=None):
    """Encode a command, pathname and optional host into a git-proto-request.
    """
    if b' ' in command or b'\0' in pathname or (host and b'\0' in host):
        raise ValueError('Metacharacter in arguments')
    bits = [pathname]
    if host is not None:
        bits.append(b'host=' + host)
    return command + b' ' + b'\0'.join(bits) + b'\0'


class GitProcessProtocol(protocol.ProcessProtocol):

    def setPeer(self, peer):
        self.peer = peer

    def outReceived(self, data):
        self.peer.sendData(data)

    def errReceived(self, data):
        self.peer.sendData(data)

    def processEnded(self, status):
        if self.peer is not None:
            self.peer.transport.loseConnection()


class GitServerProtocol(protocol.Protocol):

    got_request = False
    _buffer = b''
    peer = None

    def readPacket(self):
        if len(self._buffer) < PKT_LEN_SIZE:
            return
        try:
            pkt_len = int(self._buffer[:PKT_LEN_SIZE], 16)
            if not (4 <= pkt_len <= 65524):
                raise ValueError()
        except ValueError:
            self.die(b'Invalid pkt-len in request')
            return
        if len(self._buffer) < pkt_len:
            # Some of the packet is yet to be received.
            return
        data = self._buffer[PKT_LEN_SIZE:pkt_len]
        self._buffer = self._buffer[pkt_len:]
        return data

    def dataReceived(self, data):
        if not self.got_request:
            self._buffer += data
            data = self.readPacket()
            if data is None:
                return
            try:
                command, pathname, host = decode_request(data)
            except ValueError as e:
                self.die(e.message.encode('utf-8'))
                return
            self.requestReceived(command, pathname, host)
            self.got_request = True
            # There should be no further traffic from the client until
            # the server is up and has sent its refs.
            if len(self._buffer) > 0:
                self.die(b'Garbage after request packet')
        elif self.peer is None:
            self.die(b'Garbage after request packet')
        else:
            self.peer.transport.write(data)

    def connectionLost(self, reason):
        if self.peer:
            self.peer.setPeer(None)
            self.peer.transport.loseConnection()

    def die(self, message):
        self.transport.write(
            encode_packet(b'ERR ' + message))
        self.transport.loseConnection()

    def sendData(self, data):
        self.transport.write(data)


class GitBackendProtocol(GitServerProtocol):

    def requestReceived(self, command, raw_pathname, host):
        path = compose_path(self.factory.root, raw_pathname)
        if command == b'git-upload-pack':
            cmd = b'git'
            args = [b'git', b'upload-pack', path]
        elif command == b'git-receive-pack':
            cmd = b'git'
            args = [b'git', b'receive-pack', path]
        else:
            self.die(b'Unsupport command in request')
            return
        self.peer = GitProcessProtocol()
        self.peer.setPeer(self)
        reactor.spawnProcess(self.peer, cmd, args)


class GitBackendFactory(protocol.Factory):

    protocol = GitBackendProtocol

    def __init__(self, root):
        self.root = root


class GitClientProtocol(protocol.Protocol):

    def setPeer(self, peer):
        self.peer = peer

    def connectionMade(self):
        self.peer.setPeer(self)

    def dataReceived(self, data):
        self.peer.transport.write(data)

    def connectionLost(self, status):
        if self.peer is not None:
            self.peer.setPeer(None)
            self.peer.transport.loseConnection()


class GitClientFactory(protocol.ClientFactory):

    protocol = GitClientProtocol

    def setServer(self, server):
        self.server = server

    def buildProtocol(self, *args, **kw):
        proto = protocol.ClientFactory.buildProtocol(self, *args, **kw)
        proto.setPeer(self.server)
        return proto

    def clientConnectionFailed(self, connector, reason):
        self.server.transport.loseConnection()


class GitFrontendProtocol(GitServerProtocol):

    command = pathname = host = write = None

    @defer.inlineCallbacks
    def requestReceived(self, command, pathname, host):

        proxy = xmlrpc.Proxy(self.factory.virt_endpoint)
        try:
            translated = yield proxy.callRemote(b'translatePath', pathname)
            self.pathname = translated['path']
            self.writable = translated['writable']
        except Exception as e:
            self.die(b"Boom: %r" % e)
            return
        self.command = command
        self.host = host

        if command != b'git-upload-pack' and not self.writable:
            self.die(b'Repository is read-only')
            return

        client = GitClientFactory()
        client.setServer(self)
        reactor.connectTCP(
            self.factory.backend_host, self.factory.backend_port, client)

    def setPeer(self, peer):
        self.peer = peer
        if self.peer is not None:
            self.peer.transport.write(
                encode_packet(
                    encode_request(self.command, self.pathname, self.host)))


class GitFrontendFactory(protocol.Factory):

    protocol = GitFrontendProtocol

    def __init__(self, backend_host, backend_port, virt_endpoint):
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.virt_endpoint = virt_endpoint
