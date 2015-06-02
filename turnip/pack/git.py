# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import sys
import uuid

from twisted.internet import (
    defer,
    protocol,
    reactor,
    )
from twisted.internet.interfaces import IHalfCloseableProtocol
# twisted.web.xmlrpc doesn't exist for Python 3 yet, but the non-XML-RPC
# bits of this module work.
if sys.version_info.major < 3:  # noqa
    from twisted.web import xmlrpc
from zope.interface import implementer

from turnip.helpers import compose_path
from turnip.pack.helpers import (
    decode_packet,
    decode_request,
    encode_packet,
    encode_request,
    ensure_config,
    ensure_hooks,
    INCOMPLETE_PKT,
    )


ERROR_PREFIX = b'ERR '
VIRT_ERROR_PREFIX = b'turnip virt error: '

SAFE_PARAMS = frozenset(['host'])


class UnstoppableProducerWrapper(object):
    """An `IPushProducer` that won't be stopped.

    Used to avoid closing TCP connections just because one direction has
    been closed.
    """

    def __init__(self, producer):
        self.producer = producer

    def pauseProducing(self):
        self.producer.pauseProducing()

    def resumeProducing(self):
        self.producer.resumeProducing()

    def stopProducing(self):
        pass


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
                payload, self.__buffer = decode_packet(self.__buffer)
            except ValueError:
                invalid = self.__buffer
                self.__buffer = b''
                self.invalidPacketReceived(invalid)
                break
            if payload is INCOMPLETE_PKT:
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
        self.sendRawData(encode_packet(data))

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


@implementer(IHalfCloseableProtocol)
class PackProxyProtocol(PackProtocol):

    peer = None

    def invalidPacketReceived(self, packet):
        self.die(b'Invalid pkt-line')

    def setPeer(self, peer):
        self.peer = peer

    def die(self, message):
        raise NotImplementedError()

    def readConnectionLost(self):
        if self.peer is not None:
            self.peer.transport.loseWriteConnection()

    def writeConnectionLost(self):
        pass

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
                command, pathname, params = decode_request(data)
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
        self.peer.transport.registerProducer(self, True)
        self.transport.registerProducer(
            UnstoppableProducerWrapper(self.peer.transport), True)
        self.peer.resumeProducing()

    def outReceived(self, data):
        self.peer.sendRawData(data)

    def errReceived(self, data):
        # Just store it up so we can forward it as a single ERR packet
        # when the process is done.
        self._err_buffer += data

    def outConnectionLost(self):
        if self._err_buffer:
            self.peer.die(self._err_buffer)

    def sendPacket(self, data):
        self.sendRawData(encode_packet(data))

    def sendRawData(self, data):
        self.transport.write(data)

    def loseReadConnection(self):
        self.transport.closeChildFD(1)
        self.transport.closeChildFD(2)

    def loseWriteConnection(self):
        self.transport.closeChildFD(0)

    def processEnded(self, status):
        self.peer.transport.loseConnection()

    def pauseProducing(self):
        self.transport.pauseProducing()

    def resumeProducing(self):
        self.transport.resumeProducing()

    def stopProducing(self):
        # XXX: On a push we possibly don't want to just kill it.
        self.transport.loseConnection()


class PackClientProtocol(PackProxyProtocol):
    """Dumb protocol which just forwards between two others."""

    def connectionMade(self):
        self.peer.setPeer(self)
        self.peer.transport.registerProducer(self.transport, True)
        self.transport.registerProducer(self.peer.transport, True)
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
                encode_request(
                    self.command, self.pathname, self.params))
        PackServerProtocol.resumeProducing(self)


class PackBackendProtocol(PackServerProtocol):
    """Filesystem-backed turnip-flavoured Git pack protocol implementation.

    Invokes the reference C Git implementation.
    """

    hookrpc_key = None

    def requestReceived(self, command, raw_pathname, params):
        path = compose_path(self.factory.root, raw_pathname)
        if command == b'git-upload-pack':
            subcmd = b'upload-pack'
        elif command == b'git-receive-pack':
            subcmd = b'receive-pack'
        else:
            self.die(b'Unsupported command in request')
            return

        cmd = b'git'
        args = [b'git', subcmd]
        if params.pop(b'turnip-stateless-rpc', None):
            args.append(b'--stateless-rpc')
        if params.pop(b'turnip-advertise-refs', None):
            args.append(b'--advertise-refs')
        args.append(path)

        env = {}
        if subcmd == b'receive-pack' and self.factory.hookrpc_handler:
            # This is a write operation, so prepare config, hooks, the hook
            # RPC server, and the environment variables that link them up.
            ensure_config(path)
            self.hookrpc_key = str(uuid.uuid4())
            self.factory.hookrpc_handler.registerKey(
                self.hookrpc_key, raw_pathname, [])
            ensure_hooks(path)
            env[b'TURNIP_HOOK_RPC_SOCK'] = self.factory.hookrpc_sock
            env[b'TURNIP_HOOK_RPC_KEY'] = self.hookrpc_key

        self.peer = GitProcessProtocol(self)
        reactor.spawnProcess(self.peer, cmd, args, env=env)

    def readConnectionLost(self):
        if self.peer is not None:
            self.peer.loseWriteConnection()

    def writeConnectionLost(self):
        if self.peer is not None:
            self.peer.loseReadConnection()

    def connectionLost(self, reason):
        if self.hookrpc_key:
            self.factory.hookrpc_handler.unregisterKey(self.hookrpc_key)
        PackServerProtocol.connectionLost(self, reason)


class PackBackendFactory(protocol.Factory):

    protocol = PackBackendProtocol

    def __init__(self, root, hookrpc_handler=None, hookrpc_sock=None):
        self.root = root
        self.hookrpc_handler = hookrpc_handler
        self.hookrpc_sock = hookrpc_sock


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
            auth_uid = params.get(b'turnip-authenticated-uid')
            translated = yield proxy.callRemote(
                b'translatePath', pathname, permission,
                int(auth_uid) if auth_uid is not None else None,
                can_authenticate)
            if 'trailing' in translated and translated['trailing']:
                self.die(
                    VIRT_ERROR_PREFIX +
                    b'NOT_FOUND Repository does not exist.')
            pathname = translated['path']
        except xmlrpc.Fault as e:
            if e.faultCode in (1, 290):
                fault_type = b'NOT_FOUND'
            elif e.faultCode in (2, 310):
                fault_type = b'FORBIDDEN'
            elif e.faultCode in (3, 410):
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
