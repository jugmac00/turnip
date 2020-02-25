#!/usr/bin/python

# Copyright 2015-2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import base64
import json
import os
import socket
import subprocess
import sys

# XXX twom 2018-10-23 This should be a pygit2 import, but
# that currently causes CFFI warnings to be returned to the client.
GIT_OID_HEX_ZERO = '0'*40


def check_ancestor(old, new):
    # This is a delete, setting the new ref.
    if new == GIT_OID_HEX_ZERO:
        return False
    # https://git-scm.com/docs/git-merge-base#_discussion
    return_code = subprocess.call(
        ['git', 'merge-base', '--is-ancestor', old, new])
    return return_code == 0


def is_default_branch(pushed_branch):
    branch = subprocess.check_output(
        ['git', 'symbolic-ref', 'HEAD']).rstrip(b'\n')
    if pushed_branch == branch:
        return True
    else:
        return False


def determine_permissions_outcome(old, ref, rule_lines):
    rule = rule_lines.get(ref, [])
    if old == GIT_OID_HEX_ZERO:
        # We are creating a new ref
        if 'create' in rule:
            return
        else:
            return b'You do not have permission to create ' + ref + b'.'
    # We have force-push permission, implies push, therefore okay
    if 'force_push' in rule:
        return
    # We have push permission, everything is okay
    # force_push is checked later (in update-hook)
    if 'push' in rule:
        return
    # If we're here, there are no matching rules
    return b"You do not have permission to push to " + ref + b"."


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
        old, new, ref = ref_line.rstrip(b'\n').split(b' ', 2)
        error = determine_permissions_outcome(old, ref, rule_lines)
        if error:
            result.append(error)
    return result


def match_update_rules(rule_lines, ref_line):
    """ Match update hook refs against rules and check permissions.

    Called by the update hook, checks if the operation is a merge or
    a force-push. In the case of a force-push, checks the ref against
    the rule_lines to confirm that the user has permissions for that operation.
    """
    ref, old, new = ref_line

    # If it's a create, the old ref doesn't exist
    if old == GIT_OID_HEX_ZERO:
        return []

    # https://git-scm.com/docs/git-merge-base#_discussion
    if check_ancestor(old, new):
        return []

    # If it's not fast forwardable, check that user has permissions
    rule = rule_lines.get(ref, [])
    if 'force_push' in rule:
        return []
    return [b'You do not have permission to force-push to ' + ref + b'.']


def netstring_send(sock, s):
    sock.send(b'%d:%s,' % (len(s), s))


def netstring_recv(sock):
    c = sock.recv(1)
    lengthstr = ''
    while c != b':':
        assert c.isdigit()
        lengthstr += c
        c = sock.recv(1)
    length = int(lengthstr)
    s = bytearray()
    while len(s) < length:
        s += sock.recv(length - len(s))
    assert sock.recv(1) == b','
    return bytes(s)


def rpc_invoke(sock, method, args):
    msg = dict(args)
    assert 'op' not in msg
    msg['op'] = method
    netstring_send(sock, json.dumps(msg))
    res = json.loads(netstring_recv(sock))
    if 'error' in res:
        raise Exception(res)
    return res['result']


def check_ref_permissions(sock, rpc_key, ref_paths):
    ref_paths = [base64.b64encode(path).decode('UTF-8') for path in ref_paths]
    rule_lines = rpc_invoke(
        sock, b'check_ref_permissions',
        {'key': rpc_key, 'paths': ref_paths})
    return {
        base64.b64decode(path.encode('UTF-8')): permissions
        for path, permissions in rule_lines.items()}


def send_mp_url(received_lines):
    refs = [p.rstrip(b'\n').split(b' ', 2)[2] for p in received_lines]
    # check for branch ref here (we're interested in
    # heads and not tags)
    ref_type = refs[0].split('/', 2)[1]
    if ref_type == "heads":
        pushed_branch = refs[0]
        if not is_default_branch(pushed_branch):
            mp_url = rpc_invoke(
                sock, b'get_mp_url',
                {'key': rpc_key, 'branch': pushed_branch})
            if mp_url is not None:
                sys.stdout.write(b'      \n')
                sys.stdout.write(
                    b"Create a merge proposal for '%s' on Launchpad by"
                    b" visiting:\n" % pushed_branch)
                sys.stdout.write(b'      %s\n' % mp_url)
                sys.stdout.write(b'      \n')

if __name__ == '__main__':
    # Connect to the RPC server, authenticating using the random key
    # from the environment.
    rpc_key = os.environ['TURNIP_HOOK_RPC_KEY']
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(os.environ['TURNIP_HOOK_RPC_SOCK'])
    hook = os.path.basename(sys.argv[0])
    if hook == 'pre-receive':
        # Verify the proposed changes against rules from the server.
        raw_paths = sys.stdin.readlines()
        ref_paths = [p.rstrip(b'\n').split(b' ', 2)[2] for p in raw_paths]
        rule_lines = check_ref_permissions(sock, rpc_key, ref_paths)
        errors = match_rules(rule_lines, raw_paths)
        for error in errors:
            sys.stdout.write(error + b'\n')
        sys.exit(1 if errors else 0)
    elif hook == 'post-receive':
        # Notify the server about the push if there were any changes.
        # Details of the changes aren't currently included.
        lines = sys.stdin.readlines()
        if lines:
            rpc_invoke(sock, b'notify_push', {'key': rpc_key})
        if len(lines) == 1:
            send_mp_url(lines)
        sys.exit(0)
    elif hook == 'update':
        ref = sys.argv[1]
        rule_lines = check_ref_permissions(sock, rpc_key, [ref])
        errors = match_update_rules(rule_lines, sys.argv[1:4])
        for error in errors:
            sys.stdout.write(error + b'\n')
        sys.exit(1 if errors else 0)
    else:
        sys.stderr.write('Invalid hook name: %s' % hook)
        sys.exit(1)
