# Copyright 2015-2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from collections import defaultdict
import hashlib

from lazr.sshserver.auth import NoSuchPersonWithName
from six.moves import xmlrpc_client
from twisted.web import xmlrpc

__all__ = [
    "FakeAuthServerService",
    "FakeVirtInfoService",
    ]


class FakeAuthServerService(xmlrpc.XMLRPC):
    """A fake version of the Launchpad authserver service."""

    def __init__(self):
        xmlrpc.XMLRPC.__init__(self)
        self.keys = defaultdict(list)

    def addSSHKey(self, username, public_key_path):
        with open(public_key_path, "r") as f:
            public_key = f.read()
        kind, keytext, _ = public_key.split(" ", 2)
        if kind == "ssh-rsa":
            keytype = "RSA"
        elif kind == "ssh-dss":
            keytype = "DSA"
        else:
            raise Exception("Unrecognised public key type %s" % kind)
        self.keys[username].append((keytype, keytext))

    def xmlrpc_getUserAndSSHKeys(self, username):
        if username not in self.keys:
            raise NoSuchPersonWithName(username)
        return {
            "id": hash(username) % (2 ** 31),
            "name": username,
            "keys": self.keys[username],
            }


class FakeVirtInfoService(xmlrpc.XMLRPC):
    """A trivial virt information XML-RPC service.

    Translates a path to its SHA-256 hash. The repo is writable if the
    path is prefixed with '/+rw'
    """

    def __init__(self, *args, **kwargs):
        xmlrpc.XMLRPC.__init__(self, *args, **kwargs)
        self.require_auth = False
        self.translations = []
        self.authentications = []
        self.push_notifications = []
        self.ref_permissions_checks = []
        self.ref_permissions = {}

    def xmlrpc_translatePath(self, pathname, permission, auth_params):
        if self.require_auth and 'user' not in auth_params:
            raise xmlrpc.Fault(3, "Unauthorized")

        self.translations.append((pathname, permission, auth_params))
        writable = False
        if pathname.startswith('/+rw'):
            writable = True
            pathname = pathname[4:]

        if permission != b'read' and not writable:
            raise xmlrpc.Fault(2, "Repository is read-only")
        return {'path': hashlib.sha256(pathname).hexdigest()}

    def xmlrpc_authenticateWithPassword(self, username, password):
        self.authentications.append((username, password))
        return {'user': username}

    def xmlrpc_notify(self, path):
        self.push_notifications.append(path)

    def xmlrpc_checkRefPermissions(self, path, ref_paths, auth_params):
        self.ref_permissions_checks.append((path, ref_paths, auth_params))
        return [
            (xmlrpc_client.Binary(ref), permissions)
            for ref, permissions in self.ref_permissions.items()]
