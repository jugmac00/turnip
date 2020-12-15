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
import six
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
    path is prefixed with '/+rw', and is new if its name contains '-new'.

    For new repositories, to fake a response with "clone_from", include in
    its name the pattern "/clone-from:REPO_NAME" in the end of pathname.

    Examples of repositories:
        - /example: Simple read-only repo
        - /+rw/example: Read & write repo
        - /example-new: Non-existing repository, read only
        - /+rw/example-new/clone-from:foo: New repository called "example-new",
            that can be written and cloned from "foo"
    """

    def __init__(self, *args, **kwargs):
        xmlrpc.XMLRPC.__init__(self, *args, **kwargs)
        self.require_auth = False
        self.translations = []
        self.authentications = []
        self.push_notifications = []
        self.merge_proposal_url = 'http://bogus.test'
        self.merge_proposal_url_fault = None
        self.ref_permissions_checks = []
        self.ref_permissions = {}
        self.ref_permissions_fault = None
        self.confirm_repo_creation_call_args = []
        self.abort_repo_creation_call_args = []

    def getInternalPath(self, pathname):
        pathname = six.ensure_binary(pathname)
        if pathname.startswith(b'/+rw'):
            pathname = pathname[4:]
        return hashlib.sha256(pathname).hexdigest()

    def xmlrpc_translatePath(self, pathname, permission, auth_params):
        if self.require_auth and 'user' not in auth_params:
            raise xmlrpc.Fault(3, "Unauthorized")

        self.translations.append((pathname, permission, auth_params))
        writable = pathname.startswith('/+rw')

        if permission != 'read' and not writable:
            raise xmlrpc.Fault(2, "Repository is read-only")
        retval = {'path': self.getInternalPath(pathname)}

        if "-new" in pathname:
            if "/clone-from:" in pathname:
                clone_path = pathname.split("/clone-from:", 1)[1]
                clone_from = self.getInternalPath(clone_path)
            else:
                clone_from = None
            retval["creation_params"] = {"clone_from": clone_from}
        return retval

    def xmlrpc_authenticateWithPassword(self, username, password):
        self.authentications.append((username, password))
        return {'user': username}

    def xmlrpc_notify(self, path):
        self.push_notifications.append(path)

    def xmlrpc_checkRefPermissions(self, path, ref_paths, auth_params):
        self.ref_permissions_checks.append((path, ref_paths, auth_params))
        if self.ref_permissions_fault is not None:
            raise self.ref_permissions_fault
        return [
            (xmlrpc_client.Binary(ref), permissions)
            for ref, permissions in self.ref_permissions.items()]

    def xmlrpc_getMergeProposalURL(self, path, branch, auth_params):
        if self.merge_proposal_url_fault is not None:
            raise self.merge_proposal_url_fault
        else:
            # Launchpad fails if these parameters are binary. So, replicate
            # this behavior with a couple of asserts.
            assert isinstance(path, six.string_types)
            assert isinstance(branch, six.string_types)
            return self.merge_proposal_url

    def xmlrpc_confirmRepoCreation(self, pathname, auth_params):
        self.confirm_repo_creation_call_args.append((pathname, ))

    def xmlrpc_abortRepoCreation(self, pathname, auth_params):
        self.abort_repo_creation_call_args.append((pathname, ))
