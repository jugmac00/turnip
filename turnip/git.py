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
        try:
            packet, tail = helpers.decode_packet(self._buffer)
        except ValueError as e:
            self.die(b'Bad request: ' + str(e).encode('utf-8'))
            return None
        if packet is None:
            self.die(b'Bad request: flush-pkt instead')
            return None
        if packet is helpers.INCOMPLETE_PKT:
            return None
        self._buffer = tail
        return packet

    def dataReceived(self, data):
        if not self.got_request:
            self._buffer += data
            data = self.readPacket()
            if data is None:
                return
            try:
                command, pathname, host = helpers.decode_request(data)
            except ValueError as e:
                self.die(str(e).encode('utf-8'))
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
            helpers.encode_packet(b'ERR ' + message))
        self.transport.loseConnection()

    def sendData(self, data):
        self.transport.write(data)


class GitBackendProtocol(GitServerProtocol):

    def requestReceived(self, command, raw_pathname, host):
        path = helpers.compose_path(self.factory.root, raw_pathname)
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
                helpers.encode_packet(helpers.encode_request(
                    self.command, self.pathname, self.host)))


class GitFrontendFactory(protocol.Factory):

    protocol = GitFrontendProtocol

    def __init__(self, backend_host, backend_port, virt_endpoint):
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.virt_endpoint = virt_endpoint
