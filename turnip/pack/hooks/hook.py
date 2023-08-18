#!/usr/bin/python3

# Copyright 2015-2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import base64
import json
import os
import socket
import subprocess
import sys

import six

from turnip.pack.helpers import get_repack_data

# XXX twom 2018-10-23 This should be a pygit2 import, but
# that currently causes CFFI warnings to be returned to the client.
GIT_OID_HEX_ZERO = b"0" * 40


# Users cannot update references in this list.
READONLY_REF_NAMESPACES = [
    b"refs/merge/",
]


def check_ancestor(old, new):
    # This is a delete, setting the new ref.
    if new == GIT_OID_HEX_ZERO:
        return False
    # https://git-scm.com/docs/git-merge-base#_discussion
    return_code = subprocess.call(
        [b"git", b"merge-base", b"--is-ancestor", old, new]
    )
    return return_code == 0


def is_default_branch(pushed_branch):
    default_branch = subprocess.check_output(
        ["git", "symbolic-ref", "HEAD"]
    ).rstrip(b"\n")
    return pushed_branch == default_branch


def determine_permissions_outcome(old, ref, rule_lines):
    if any(ref.startswith(i) for i in READONLY_REF_NAMESPACES):
        return b"%s is in a read-only namespace." % ref
    rule = rule_lines.get(ref, [])
    if old == GIT_OID_HEX_ZERO:
        # We are creating a new ref
        if "create" in rule:
            return
        else:
            return (
                b"You do not have permission to create %s."
                % six.ensure_binary(ref, "UTF-8")
            )
    # We have force-push permission, implies push, therefore okay
    if "force_push" in rule:
        return
    # We have push permission, everything is okay
    # force_push is checked later (in update-hook)
    if "push" in rule:
        return
    # If we're here, there are no matching rules
    return b"You do not have permission to push to %s." % six.ensure_binary(
        ref, "UTF-8"
    )


def match_rules(rule_lines, ref_lines):
    """Check if the list of refs is allowable by the rule_lines.

    Called by the pre-receive hook, checks each ref in turn to see if
    there is a matching rule line and that the operation is allowable.
    Does not confirm that the operation is a merge or force-push, that is
    performed by the update hook and match_update_rules.
    """
    result = []
    # Match each ref against each rule.
    for ref_line in ref_lines:
        old, new, ref = ref_line.rstrip(b"\n").split(b" ", 2)
        error = determine_permissions_outcome(old, ref, rule_lines)
        if error:
            result.append(error)
    return result


def match_update_rules(rule_lines, ref_line):
    """Match update hook refs against rules and check permissions.

    Called by the update hook, checks if the operation is a merge or
    a force-push. In the case of a force-push, checks the ref against
    the rule_lines to confirm that the user has permissions for that operation.
    """
    ref, old, new = ref_line
    if any(ref.startswith(i) for i in READONLY_REF_NAMESPACES):
        return [b"%s is in a read-only namespace." % ref]

    # If it's a create, the old ref doesn't exist
    if old == GIT_OID_HEX_ZERO:
        return []

    # https://git-scm.com/docs/git-merge-base#_discussion
    if check_ancestor(old, new):
        return []

    # If it's not fast forwardable, check that user has permissions
    rule = rule_lines.get(ref, [])
    if "force_push" in rule:
        return []
    return [
        b"You do not have permission to force-push to %s."
        % six.ensure_binary(ref, "UTF-8")
    ]


def netstring_send(sock, s):
    sock.sendall(b"%d:%s," % (len(s), s))


def netstring_recv(sock):
    c = sock.recv(1)
    lengthstr = b""
    while c != b":":
        if not c.isdigit():
            raise ValueError(
                "Invalid response: %s" % (six.ensure_text(c + sock.recv(256)))
            )
        lengthstr += c
        c = sock.recv(1)
    length = int(lengthstr)
    s = bytearray()
    while len(s) < length:
        s += sock.recv(length - len(s))
    ending = sock.recv(1)
    if ending != b",":
        raise ValueError(
            "Length error for message '%s': ending='%s'"
            % (six.ensure_text(bytes(s)), six.ensure_text(ending))
        )
    return bytes(s)


def rpc_invoke(sock, method, args):
    msg = dict(args)
    assert "op" not in msg
    msg["op"] = method
    netstring_send(sock, six.ensure_binary(json.dumps(msg), "UTF-8"))
    res = json.loads(netstring_recv(sock))
    if "error" in res:
        raise Exception(res)
    return res["result"]


def check_ref_permissions(sock, rpc_key, ref_paths):
    ref_paths = [
        base64.b64encode(six.ensure_binary(path)).decode("UTF-8")
        for path in ref_paths
    ]
    rule_lines = rpc_invoke(
        sock, "check_ref_permissions", {"key": rpc_key, "paths": ref_paths}
    )
    return {
        base64.b64decode(path.encode("UTF-8")): permissions
        for path, permissions in rule_lines.items()
    }


def send_mp_url(received_line):
    _, new_sha, ref = received_line.rstrip(b"\n").split(b" ", 2)

    # The new sha will be zero when deleting branch
    # in which case we do not want to send the MP URL.
    if new_sha == GIT_OID_HEX_ZERO:
        return

    # Check for branch ref here - we're interested in
    # heads and not tags.
    if ref.startswith(b"refs/heads/") and not is_default_branch(ref):
        pushed_branch = ref[len(b"refs/heads/") :]
        if not is_default_branch(pushed_branch):
            mp_url = rpc_invoke(
                sock,
                "get_mp_url",
                {
                    "key": rpc_key,
                    "branch": six.ensure_text(pushed_branch, "UTF-8"),
                },
            )
            if mp_url is not None:
                stdout = sys.stdout.buffer
                stdout.write(b"      \n")
                stdout.write(
                    b"Create a merge proposal for '%s' on Launchpad by"
                    b" visiting:\n" % pushed_branch
                )
                stdout.write(b"      %s\n" % six.ensure_binary(mp_url, "UTF8"))
                stdout.write(b"      \n")


if __name__ == "__main__":
    # Connect to the RPC server, authenticating using the random key
    # from the environment.
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    rpc_key = os.environ["TURNIP_HOOK_RPC_KEY"]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(os.environ["TURNIP_HOOK_RPC_SOCK"])
    hook = os.path.basename(sys.argv[0])
    if hook == "pre-receive":
        # Verify the proposed changes against rules from the server.
        raw_paths = stdin.readlines()
        ref_paths = [p.rstrip(b"\n").split(b" ", 2)[2] for p in raw_paths]
        rule_lines = check_ref_permissions(sock, rpc_key, ref_paths)
        errors = match_rules(rule_lines, raw_paths)
        for error in errors:
            stdout.write(error + b"\n")
        sys.exit(1 if errors else 0)
    elif hook == "post-receive":
        # Notify the server about the push if there were any changes.
        # Details of the changes aren't currently included.
        lines = stdin.readlines()
        if lines:
            loose_object_count, pack_count = get_repack_data()
            rpc_invoke(
                sock,
                "notify_push",
                {
                    "key": rpc_key,
                    "loose_object_count": loose_object_count,
                    "pack_count": pack_count,
                },
            )
        if len(lines) == 1:
            send_mp_url(lines[0])
        sys.exit(0)
    elif hook == "update":
        argvb = [os.fsencode(i) for i in sys.argv]
        ref = argvb[1]
        rule_lines = check_ref_permissions(sock, rpc_key, [ref])
        errors = match_update_rules(rule_lines, argvb[1:4])
        for error in errors:
            stdout.write(error + b"\n")
        sys.exit(1 if errors else 0)
    else:
        sys.stderr.write("Invalid hook name: %s" % hook)
        sys.exit(1)
