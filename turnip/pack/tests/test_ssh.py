# Copyright 2020 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from testtools import TestCase
from twisted.conch.interfaces import ISession, ISessionSetEnv

from turnip.pack.ssh import SmartSSHSession


class TestSSHSessionProtocolVersion(TestCase):
    """Tests that SSH session gets protocol version from env variable."""

    def test_implements_env_interfaces(self):
        session = SmartSSHSession(None)
        self.assertTrue(ISession.providedBy(session))
        self.assertTrue(ISessionSetEnv.providedBy(session))

    def test_getProtocolVersion_default_zero(self):
        session = SmartSSHSession(None)
        self.assertEqual(b"0", session.getProtocolVersion())

    def test_getProtocolVersion_fallback_to_zero(self):
        session = SmartSSHSession(None)
        session.setEnv("GIT_PROTOCOL", b"invalid")
        self.assertEqual(b"0", session.getProtocolVersion())

    def test_getProtocolVersion_from_env(self):
        session = SmartSSHSession(None)
        session.setEnv("GIT_PROTOCOL", b"version=2")
        self.assertEqual(b"2", session.getProtocolVersion())
