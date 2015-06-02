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
    rules = [re.compile(glob_to_re(l.rstrip(b'\n'))) for l in rule_lines]
    # Match each ref against each rule.
    errors = []
    for ref_line in ref_lines:
        old, new, ref = ref_line.rstrip(b'\n').split(b' ', 2)
        if any(rule.match(ref) for rule in rules):
            errors.append(b"You can't push to %s." % ref)
    return errors


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
    hook = os.path.basename(sys.argv[0])
    rpc_key = os.environ['TURNIP_HOOK_RPC_KEY']
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(os.environ['TURNIP_HOOK_RPC_SOCK'])
    if hook == 'pre-receive':
        rule_lines = rpc_invoke(sock, b'list_ref_rules', {'key': rpc_key})
        errors = match_rules(rule_lines, sys.stdin.readlines())
        for error in errors:
            sys.stdout.write(error + b'\n')
        sys.exit(1 if errors else 0)
    elif hook == 'post-receive':
        if sys.stdin.readlines():
            rule_lines = rpc_invoke(sock, b'notify_push', {'key': rpc_key})
        sys.exit(0)
    else:
        sys.stderr.write(b'Invalid hook name: %s' % hook)
