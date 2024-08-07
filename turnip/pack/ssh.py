# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import shlex
import uuid

import six
from lazr.sshserver.auth import LaunchpadAvatar, PublicKeyFromLaunchpadChecker
from lazr.sshserver.service import SSHService
from lazr.sshserver.session import DoNothingSession
from twisted.conch.interfaces import ISession, ISessionSetEnv
from twisted.cred.portal import IRealm, Portal
from twisted.internet import defer, protocol, reactor
from twisted.internet.error import ProcessTerminated
from twisted.python import components, failure
from twisted.web.xmlrpc import Proxy
from zope.interface import implementer

from turnip.pack.git import ERROR_PREFIX, VIRT_ERROR_PREFIX, PackProtocol
from turnip.pack.helpers import encode_packet, encode_request

__all__ = [
    "SmartSSHSession",
]


class SSHPackClientProtocol(PackProtocol):
    """Bridge between a Git pack connection and a smart SSH request.

    The transport must be a connection to a Git pack server.
    factory.ssh_protocol is a Git smart SSH session process protocol.

    Upon backend connection, all data is forwarded between backend and
    client.
    """

    def __init__(self):
        self._closed = False

    def backendConnectionFailed(self, msg):
        """Called when the backend fails to connect or returns an error."""
        # Rewrite virt errors as more friendly-looking ordinary errors.  The
        # distinction is for the benefit of the smart HTTP frontend, and is
        # not otherwise useful here.
        if msg.startswith(VIRT_ERROR_PREFIX):
            _, msg = msg[len(VIRT_ERROR_PREFIX) :].split(b" ", 1)
        self.factory.ssh_protocol.outReceived(
            encode_packet(ERROR_PREFIX + msg)
        )

    def connectionMade(self):
        """Forward the command and arguments to the backend."""
        self.factory.deferred.callback(self)
        self.sendPacket(
            encode_request(
                self.factory.command,
                self.factory.pathname,
                self.factory.params,
            )
        )

    def packetReceived(self, data):
        """Check and forward the first packet from the backend.

        Assume that any non-error packet indicates a success response,
        so we can just forward raw data afterward.
        """
        self.raw = True
        if data is not None and data.startswith(ERROR_PREFIX):
            self.backendConnectionFailed(data[len(ERROR_PREFIX) :])
        else:
            self.rawDataReceived(encode_packet(data))

    def rawDataReceived(self, data):
        self.factory.ssh_protocol.outReceived(data)

    def connectionLost(self, reason):
        if not self._closed:
            self._closed = True
            self.factory.ssh_protocol.processEnded(
                failure.Failure(ProcessTerminated(exitCode=0))
            )


class SSHPackClientFactory(protocol.ClientFactory):
    protocol = SSHPackClientProtocol

    def __init__(self, command, pathname, params, ssh_protocol, deferred):
        self.command = command
        self.pathname = pathname
        self.params = params
        self.ssh_protocol = ssh_protocol
        self.deferred = deferred

    def clientConnectionFailed(self, connector, reason):
        self.deferred.errback(reason)


@implementer(ISession, ISessionSetEnv)
class SmartSSHSession(DoNothingSession):
    """SSH session allowing only Git smart SSH requests."""

    allowed_services = frozenset(
        ("git-upload-pack", "git-receive-pack", "turnip-set-symbolic-ref")
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pack_protocol = None
        self.env = {}

    def setEnv(self, name, value):
        """Set an environment variable for this SSH session.

        Note that it might be insecure to forward every env variable from
        the user to subprocesses.
        """
        self.env[name] = value

    def getProtocolVersion(self):
        version = self.env.get("GIT_PROTOCOL", b"version=0")
        try:
            return six.ensure_binary(version.split(b"version=", 1)[1])
        except IndexError:
            return b"0"

    @defer.inlineCallbacks
    def connectToBackend(self, factory, service, path, ssh_protocol):
        """Establish a pack connection to the backend.

        The turnip-authenticated-user parameter is set to the username
        recorded in the session avatar.
        """
        params = {
            b"turnip-authenticated-user": self.avatar.username.encode("utf-8"),
            b"turnip-authenticated-uid": str(self.avatar.user_id),
            b"turnip-request-id": str(uuid.uuid4()),
            b"version": self.getProtocolVersion(),
        }
        d = defer.Deferred()
        client_factory = factory(service, path, params, ssh_protocol, d)
        service = self.avatar.service
        conn = reactor.connectTCP(
            service.backend_host, service.backend_port, client_factory
        )
        self.pack_protocol = yield d
        ssh_protocol.makeConnection(conn.transport)

    def execCommand(self, protocol, command):
        """See `ISession`."""
        command = six.ensure_text(command)
        words = shlex.split(command)
        if len(words) > 1 and words[0] == "git":
            # Accept "git foo" as if the caller said "git-foo".  (This
            # matches the behaviour of "git shell".)
            git_cmd = "git-" + words[1]
            args = words[2:]
        else:
            git_cmd = words[0]
            args = words[1:]
        if git_cmd not in self.allowed_services:
            self.errorWithMessage(protocol, b"Unsupported service.")
            return
        if not args:
            self.errorWithMessage(
                protocol, b"%s requires an argument.\r\n" % git_cmd
            )
            return
        try:
            self.connectToBackend(
                SSHPackClientFactory, git_cmd, args[0], protocol
            )
        except Exception as e:
            self.errorWithMessage(protocol, str(e).encode("UTF-8"))

    def closed(self):
        if self.pack_protocol is not None:
            self.pack_protocol.transport.loseConnection()

    def eofReceived(self):
        if (
            self.pack_protocol is not None
            and self.pack_protocol.transport.connected
        ):
            self.pack_protocol.transport.loseWriteConnection()


class SmartSSHAvatar(LaunchpadAvatar):
    """An SSH avatar specific to the Git smart SSH server."""

    def __init__(self, user_dict, service):
        LaunchpadAvatar.__init__(self, user_dict)
        self.current_session = None
        self.service = service

        # Disable SFTP.
        self.subsystemLookup = {}


@implementer(IRealm)
class SmartSSHRealm:
    def __init__(self, service, authentication_proxy):
        self.service = service
        self.authentication_proxy = authentication_proxy

    @defer.inlineCallbacks
    def requestAvatar(self, avatar_id, mind, *interfaces):
        # Fetch the user's details from the authserver.
        user_dict = yield mind.lookupUserDetails(
            self.authentication_proxy, avatar_id
        )
        avatar = SmartSSHAvatar(user_dict, self.service)
        return interfaces[0], avatar, avatar.logout


class SmartSSHService(SSHService):
    def _makePortal(self, authentication_endpoint):
        authentication_proxy = Proxy(authentication_endpoint)
        realm = SmartSSHRealm(self, authentication_proxy)
        checkers = [PublicKeyFromLaunchpadChecker(authentication_proxy)]
        return Portal(realm, checkers=checkers)

    def __init__(
        self,
        backend_host,
        backend_port,
        authentication_endpoint,
        *args,
        **kwargs,
    ):
        SSHService.__init__(
            self,
            portal=self._makePortal(
                six.ensure_binary(authentication_endpoint)
            ),
            *args,
            **kwargs,
        )
        self.backend_host = backend_host
        self.backend_port = backend_port


components.registerAdapter(
    SmartSSHSession, SmartSSHAvatar, ISession, ISessionSetEnv
)
