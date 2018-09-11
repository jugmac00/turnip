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


def glob_to_re(s):
    """Convert a glob to a regular expression.

    The only wildcard supported is "*", to match any path segment.
    """
    return b'^%s\Z' % (
        b''.join(b'[^/]*' if c == b'*' else re.escape(c) for c in s))


def match_rules(rule_lines, ref_lines):
    #rules = [re.compile(glob_to_re(l.rstrip(b'\n'))) for l in rule_lines]
    rules = []
    for rule in rule_lines:
        new_rule = {
            'reg_pattern': re.compile(glob_to_re(rule['pattern'].rstrip(b'\n'))),
            'permissions': rule['permissions']
        }
        rules.append(new_rule)
    # Match each ref against each rule.
    for ref_line in ref_lines:
        old, new, ref = ref_line.rstrip(b'\n').split(b' ', 2)
        sys.stderr.write(old + '\n')
        sys.stderr.write(new + '\n')
        sys.stderr.write(ref + '\n')
        return determine_permissions_outcome(old, ref, rules)
    return []

def determine_permissions_outcome(old, ref, rules):
    creation_ref = '0000000000000000000000000000000000000000'
    for rule in rules:
        match = rule['reg_pattern'].match(ref)
        # If we don't match this ref, move on
        if not match:
            continue
        # If we match, but empty permissions array, user has no write access
        if not rule['permissions']:
            return [b'You do not have permissions to push to %s' % ref]
        # We are creating a new ref and have the correct permission
        if 'create' in rule['permissions'] and old == creation_ref:
            return []
        # We are creating a new ref, but we don't have permission
        if 'create' not in rule['permissions'] and old == creation_ref:
            return [b'You do not have permissions to create ref %s' % ref]
        # We have push permission, everything is okay
        # force_push is checked later (in update-hook)
        if 'push' in rule['permissions']:
            return []
        # We have force-push permission, implies push, therefore okay
        if 'force_push' in rule['permissions']:
            return []
    # If we're here, there are no matching rules
    return [b'There are no matching permissions for %s' % ref]

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
    else:
        sys.stderr.write(b'Invalid hook name: %s' % hook)
        sys.exit(1)
