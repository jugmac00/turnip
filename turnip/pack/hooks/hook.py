#!/usr/bin/python

# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import json
import os
import re
import socket
import sys

import pygit2


def glob_to_re(s):
    """Convert a glob to a regular expression.

    The only wildcard supported is "*", to match any path segment.
    """
    return b'^%s\Z' % (
        b''.join(b'[^/]*' if c == b'*' else re.escape(c) for c in s))


def get_repo():
    # Find the repo we're concerned about.
    # The hook is guaranteed to be in the hooks/ subdirectory
    # of the repository. We need the root of the repository,
    # so find the parent directory of the current file.
    repo_path = os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        os.pardir)
    repo = pygit2.Repository(repo_path)
    return repo


def make_regex(pattern):
    return re.compile(glob_to_re(pattern.rstrip(b'\n')))


def match_rules(rule_lines, ref_lines):
    result = []
    for rule in rule_lines:
        rule['pattern'] = make_regex(rule['pattern'])
    # Match each ref against each rule.
    for ref_line in ref_lines:
        old, new, ref = ref_line.rstrip(b'\n').split(b' ', 2)
        error = determine_permissions_outcome(old, ref, rule_lines)
        if error:
            result.append(error)
    return result


def match_update_rules(rule_lines, ref_line):
    """ Match update hook refs against rules and check permissions.

    Called by the update hook to check against force-push
    """
    ref, old, new = ref_line
    repo = get_repo()

    # If it's a create, the old ref doesn't exist
    if old == pygit2.GIT_OID_HEX_ZERO:
        return []

    # Find common ancestors: if there aren't any, it's a non-fast-forward
    base = repo.merge_base(old, new)
    if base and base.hex == old:
        # This is a fast-forwardable merge
        return []

    # If it's not fast forwardable, check that user has permissions
    for rule in rule_lines:
        match = make_regex(rule['pattern']).match(ref)
        if not match:
            continue
        if 'force_push' in rule['permissions']:
            return []
        # We only check the first matching rule
        break
    return [b'You are not allowed to force push to %s' % ref]


def determine_permissions_outcome(old, ref, rules):
    for rule in rules:
        match = rule['pattern'].match(ref)
        # If we don't match this ref, move on
        if not match:
            continue
        # If we match, but empty permissions array, user has no write access
        if not rule['permissions']:
            return b"You can't push to %s." % ref
        # We have force-push permission, implies push, therefore okay
        # This is confirmed in match_update_rules
        if 'force_push' in rule['permissions']:
            return
        # We are creating a new ref and have the correct permission
        if 'create' in rule['permissions'] and old == pygit2.GIT_OID_HEX_ZERO:
            return
        # We are creating a new ref, but we don't have permission
        if 'create' not in rule['permissions'] and old == pygit2.GIT_OID_HEX_ZERO:
            return b'You do not have permissions to create ref %s.' % ref
        # We have push permission, everything is okay
        # force_push is checked later (in update-hook)
        if 'push' in rule['permissions']:
            return
        # We only check the first matching rule
        break
    # If we're here, there are no matching rules
    return b"You can't push to %s." % ref


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
    s = sock.recv(length)
    assert sock.recv(1) == b','
    return s


def rpc_invoke(sock, method, args):
    msg = dict(args)
    assert 'op' not in msg
    msg['op'] = method
    netstring_send(sock, json.dumps(msg))
    res = json.loads(netstring_recv(sock))
    if 'error' in res:
        raise Exception(res)
    return res['result']


if __name__ == '__main__':
    # Connect to the RPC server, authenticating using the random key
    # from the environment.
    rpc_key = os.environ['TURNIP_HOOK_RPC_KEY']
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(os.environ['TURNIP_HOOK_RPC_SOCK'])

    hook = os.path.basename(sys.argv[0])
    if hook == 'pre-receive':
        # Verify the proposed changes against rules from the server.
        # This currently just lets virtinfo forbid certain ref paths.
        rule_lines = rpc_invoke(sock, b'list_ref_rules', {'key': rpc_key})
        errors = match_rules(rule_lines, sys.stdin.readlines())
        for error in errors:
            sys.stdout.write(error + b'\n')
        sys.exit(1 if errors else 0)
    elif hook == 'post-receive':
        # Notify the server about the push if there were any changes.
        # Details of the changes aren't currently included.
        if sys.stdin.readlines():
            rule_lines = rpc_invoke(sock, b'notify_push', {'key': rpc_key})
        sys.exit(0)
    elif hook == 'update':
        rule_lines = rpc_invoke(sock, b'list_ref_rules', {'key': rpc_key})
        errors = match_update_rules(rule_lines, sys.argv[1:4])
        for error in errors:
            sys.stdout.write(error + b'\n')
        sys.exit(1 if errors else 0)
    else:
        sys.stderr.write(b'Invalid hook name: %s' % hook)
        sys.exit(1)
