# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import uuid

from twisted.internet import (
    defer,
    error,
    protocol,
    reactor as default_reactor,
    )
from twisted.internet.interfaces import IHalfCloseableProtocol
from twisted.logger import Logger
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
    translate_xmlrpc_fault,
    )


ERROR_PREFIX = b'ERR '
VIRT_ERROR_PREFIX = b'turnip virt error: '

SAFE_PARAMS = frozenset(['host', 'version'])


class RequestIDLogger(Logger):

    def emit(self, level, format=None, **kwargs):
        request_id = getattr(self.source, 'request_id')
        if format is not None and request_id is not None:
            format = '[request-id=%s] [%s] %s' % (
                request_id, self.source.__class__.__name__, format)
        super(RequestIDLogger, self).emit(level, format=format, **kwargs)


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


@implementer(IHalfCloseableProtocol)
class PackServerProtocol(PackProxyProtocol):

    got_request = False
    peer = None

    request_id = None
    log = RequestIDLogger()

    def extractRequestMeta(self, command, pathname, params):
        self.request_id = params.get(b'turnip-request-id', None)
        self.log.info(
            "Request received: '{command} {pathname}', params={params}",
            command=command, pathname=pathname, params=params)

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

    def readConnectionLost(self):
        # Implementations need to forward the stdin down the stack,
        # otherwise the backend will never know its work is done.
        raise NotImplementedError()

    def writeConnectionLost(self):
        pass

    def connectionLost(self, reason):
        if reason.check(error.ConnectionDone):
            self.log.info('Connection closed.')
        else:
            self.log.failure('Connection lost.', failure=reason)
        PackProxyProtocol.connectionLost(self, reason)

    def die(self, message):
        self.log.info('Dying: {message}', message=message)
        self.sendPacket(ERROR_PREFIX + message + b'\n')
        self.transport.loseConnection()

    def createAuthParams(self, params):
        auth_params = {}
        for key, value in params.items():
            if key.startswith(b'turnip-authenticated-'):
                decoded_key = key[len(b'turnip-authenticated-'):].decode(
                    'utf-8')
                auth_params[decoded_key] = value
        if 'uid' in auth_params:
            auth_params['uid'] = int(auth_params['uid'])
        if params.get(b'turnip-can-authenticate') == b'yes':
            auth_params['can-authenticate'] = True
        if self.request_id is not None:
            auth_params['request-id'] = self.request_id
        return auth_params


class GitProcessProtocol(protocol.ProcessProtocol):

    _err_buffer = b''

    def __init__(self, peer):
        self.peer = peer
        self.out_started = False

    def connectionMade(self):
        self.peer.setPeer(self)
        self.peer.transport.registerProducer(self, True)
        self.transport.registerProducer(
            UnstoppableProducerWrapper(self.peer.transport), True)
        self.peer.resumeProducing()

    def outReceived(self, data):
        self.out_started = True
        self.peer.sendRawData(data)

    def errReceived(self, data):
        # Just store it up so we can forward and/or log it when the
        # process is done.
        self._err_buffer += data

    def outConnectionLost(self):
        if self._err_buffer:
            # Originally we'd always return stderr as an ERR packet for
            # debugging, but it breaks HTTP shallow clones: the second
            # HTTP request causes the backend to die when it tries to
            # read more than was sent, but the reference
            # git-http-backend conveniently just sends the error to the
            # server log so the client doesn't notice. So now we always
            # log any stderr, but only forward it to the client if the
            # subprocess never wrote to stdout.
            if not self.out_started:
                self.peer.log.info(
                    'git wrote to stderr first; returning to client: {buf}',
                    buf=repr(self._err_buffer))
                self.peer.sendPacket(ERROR_PREFIX + self._err_buffer + b'\n')
            else:
                self.peer.log.info(
                    "git wrote to stderr: {buf}", buf=repr(self._err_buffer))

    def sendPacket(self, data):
        self.sendRawData(encode_packet(data))

    def sendRawData(self, data):
        self.transport.write(data)

    def loseReadConnection(self):
        self.transport.closeChildFD(1)
        self.transport.closeChildFD(2)

    def loseWriteConnection(self):
        self.transport.closeChildFD(0)

    def processEnded(self, reason):
        if reason.check(error.ProcessDone) and reason.value.exitCode != 0:
            code = reason.value.exitCode
            self.peer.log.info(
                'git exited {code} with no output; synthesising an error',
                code=code)
            self.peer.sendPacket(ERROR_PREFIX + 'backend exited %d' % code)
        self.peer.processEnded(reason)

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
        self.peer.log.info(
            "Backend connection established: {host} -> {peer}",
            host=self.transport.getHost(), peer=self.transport.getPeer())
        self.peer.setPeer(self)
        self.peer.transport.registerProducer(self.transport, True)
        self.transport.registerProducer(self.peer.transport, True)
        self.peer.resumeProducing()

    def packetReceived(self, data):
        self.raw = True
        self.peer.sendPacket(data)

    def rawDataReceived(self, data):
        self.peer.sendRawData(data)

    def connectionLost(self, reason):
        if reason.check(error.ConnectionDone):
            self.peer.log.info('Backend connection closed.')
        else:
            self.peer.log.failure('Backend connection lost.', failure=reason)
        PackProxyProtocol.connectionLost(self, reason)

    def die(self, message):
        # The error always goes to the other side.
        self.peer.die(b'backend error: ' + message)
        self.transport.loseConnection()


class PackClientFactory(protocol.ClientFactory):

    protocol = PackClientProtocol

    def __init__(self, server, deferred):
        self.server = server
        self.deferred = deferred

    def startedConnecting(self, connector):
        self.server.log.info(
            "Connecting to backend: {dest}.", dest=connector.getDestination())

    def buildProtocol(self, *args, **kwargs):
        p = protocol.ClientFactory.buildProtocol(self, *args, **kwargs)
        p.setPeer(self.server)
        self.deferred.callback(None)
        return p

    def clientConnectionFailed(self, connector, reason):
        self.deferred.errback(reason)


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
        d = defer.Deferred()
        client = self.client_factory(self, d)
        default_reactor.connectTCP(
            self.factory.backend_host, self.factory.backend_port, client)
        return d

    def resumeProducing(self):
        # Send our translated request and then open the gate to the
        # client.
        if not self.request_sent:
            self.log.info(
                "Forwarding request to backend: '{command} {pathname}', "
                "params={params}", command=self.command,
                pathname=self.pathname, params=self.params)
            self.request_sent = True
            self.peer.sendPacket(
                encode_request(
                    self.command, self.pathname, self.params))
        PackServerProtocol.resumeProducing(self)

    def readConnectionLost(self):
        # Forward the closed stdin down the stack.
        if self.peer is not None and self.peer.transport.connected:
            self.peer.transport.loseWriteConnection()


class PackBackendProtocol(PackServerProtocol):
    """Filesystem-backed turnip-flavoured Git pack protocol implementation.

    Invokes the reference C Git implementation.
    """

    hookrpc_key = None
    expect_set_symbolic_ref = False

    def requestReceived(self, command, raw_pathname, params):
        self.extractRequestMeta(command, raw_pathname, params)
        self.command = command
        self.raw_pathname = raw_pathname
        self.path = compose_path(self.factory.root, self.raw_pathname)

        if command == b'turnip-set-symbolic-ref':
            self.expect_set_symbolic_ref = True
            self.resumeProducing()
            return

        write_operation = False
        if command == b'git-upload-pack':
            subcmd = b'upload-pack'
        elif command == b'git-receive-pack':
            subcmd = b'receive-pack'
            write_operation = True
        else:
            self.die(b'Unsupported command in request')
            return

        args = []
        if params.pop(b'turnip-stateless-rpc', None):
            args.append(b'--stateless-rpc')
        if params.pop(b'turnip-advertise-refs', None):
            args.append(b'--advertise-refs')
        args.append(self.path)
        auth_params = self.createAuthParams(params)
        self.spawnGit(subcmd,
                      args,
                      write_operation=write_operation,
                      auth_params=auth_params)

    def spawnGit(self, subcmd, extra_args, write_operation=False,
                 send_path_as_option=False, auth_params=None):
        cmd = b'git'
        args = [b'git']
        if send_path_as_option:
            args.extend([b'-C', self.path])
        args.append(subcmd)
        args.extend(extra_args)

        env = {}
        if write_operation and self.factory.hookrpc_handler:
            # This is a write operation, so prepare config, hooks, the hook
            # RPC server, and the environment variables that link them up.
            ensure_config(self.path)
            self.hookrpc_key = str(uuid.uuid4())
            self.factory.hookrpc_handler.registerKey(
                self.hookrpc_key, self.raw_pathname, auth_params)
            ensure_hooks(self.path)
            env[b'TURNIP_HOOK_RPC_SOCK'] = self.factory.hookrpc_sock
            env[b'TURNIP_HOOK_RPC_KEY'] = self.hookrpc_key

        self.log.info('Spawning {args}', args=args)
        self.peer = GitProcessProtocol(self)
        self.spawnProcess(cmd, args, env=env)

    def spawnProcess(self, cmd, args, env=None):
        default_reactor.spawnProcess(self.peer, cmd, args, env=env)

    def packetReceived(self, data):
        if self.expect_set_symbolic_ref:
            if data is None:
                self.die(b'Bad request: flush-pkt instead')
                return
            self.pauseProducing()
            self.expect_set_symbolic_ref = False
            if b' ' not in data:
                self.die(b'Invalid set-symbolic-ref-line')
                return
            name, target = data.split(b' ', 1)
            # Be careful about extending this to anything other than HEAD.
            # We use "git symbolic-ref" because it gives us locking and
            # logging, but it doesn't prevent writing a ref to ../something.
            # Fortunately it does at least refuse to point HEAD outside of
            # refs/.
            if name != b'HEAD':
                self.die(b'Symbolic ref name must be "HEAD"')
                return
            if target.startswith(b'-'):
                self.die(b'Symbolic ref target may not start with "-"')
                return
            elif b' ' in target:
                self.die(b'Symbolic ref target may not contain " "')
                return
            self.symbolic_ref_name = name
            self.spawnGit(
                b'symbolic-ref', [name, target], write_operation=True,
                send_path_as_option=True)
            return

        PackServerProtocol.packetReceived(self, data)

    @defer.inlineCallbacks
    def processEnded(self, reason):
        message = None
        if self.command == b'turnip-set-symbolic-ref':
            if reason.check(error.ProcessDone):
                try:
                    yield self.factory.hookrpc_handler.notify(
                        self.raw_pathname)
                    self.sendPacket(b'ACK %s\n' % self.symbolic_ref_name)
                except Exception as e:
                    message = str(e)
            else:
                message = (
                    'git symbolic-ref exited with status %d' %
                    reason.value.exitCode)
        if message is None:
            self.transport.loseConnection()
        else:
            self.die(message)

    def readConnectionLost(self):
        # Forward the closed stdin down the stack.
        if self.peer is not None:
            self.peer.loseWriteConnection()

    def connectionLost(self, reason):
        if self.hookrpc_key:
            self.factory.hookrpc_handler.unregisterKey(self.hookrpc_key)
        PackServerProtocol.connectionLost(self, reason)


class PackBackendFactory(protocol.Factory):

    protocol = PackBackendProtocol

    def __init__(self,
                 root,
                 hookrpc_handler=None,
                 hookrpc_sock=None):
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
        self.extractRequestMeta(command, pathname, params)
        permission = b'read' if command == b'git-upload-pack' else b'write'
        proxy = xmlrpc.Proxy(self.factory.virtinfo_endpoint, allowNone=True)
        try:
            auth_params = self.createAuthParams(params)
            self.log.info("Translating request.")
            translated = yield proxy.callRemote(
                'translatePath', pathname, permission,
                auth_params).addTimeout(
                    self.factory.virtinfo_timeout, self.factory.reactor)
            self.log.info(
                "Translation result: {translated}", translated=translated)
            if 'trailing' in translated and translated['trailing']:
                self.die(
                    VIRT_ERROR_PREFIX +
                    b'NOT_FOUND Repository does not exist.')
            pathname = translated['path']
        except xmlrpc.Fault as e:
            fault_type = translate_xmlrpc_fault(
                e.faultCode).name.encode('UTF-8')
            fault_string = e.faultString
            if not isinstance(fault_string, bytes):
                fault_string = fault_string.encode('UTF-8')
            self.die(VIRT_ERROR_PREFIX + fault_type + b' ' + fault_string)
        except defer.TimeoutError:
            self.die(
                VIRT_ERROR_PREFIX +
                b'GATEWAY_TIMEOUT Path translation timed out.')
        except Exception as e:
            self.die(VIRT_ERROR_PREFIX + b'INTERNAL_SERVER_ERROR ' + str(e))
        else:
            try:
                yield self.connectToBackend(command, pathname, params)
            except Exception as e:
                self.server.log.failure('Backend connection failed.')
                self.server.die(b'Backend connection failed.')


class PackVirtFactory(protocol.Factory):

    protocol = PackVirtServerProtocol

    def __init__(self, backend_host, backend_port, virtinfo_endpoint,
                 virtinfo_timeout, reactor=None):
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.virtinfo_endpoint = virtinfo_endpoint
        self.virtinfo_timeout = virtinfo_timeout
        self.reactor = reactor or default_reactor


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
        self.request_id = str(uuid.uuid4())
        self.log.info(
            "Request received: '{command} {pathname}', params={params}",
            command=command, pathname=pathname, params=params)
        if set(params.keys()) - SAFE_PARAMS:
            self.die(b'Illegal request parameters')
            return
        params[b'turnip-request-id'] = self.request_id
        self.connectToBackend(command, pathname, params)


class PackFrontendFactory(protocol.Factory):

    protocol = PackFrontendServerProtocol

    def __init__(self, backend_host, backend_port):
        self.backend_host = backend_host
        self.backend_port = backend_port
