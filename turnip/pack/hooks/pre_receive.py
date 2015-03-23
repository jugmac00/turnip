#!/usr/bin/python

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os
import re
import sys

def glob_to_re(s):
    """Convert a glob to a regular expression.

    The only wildcard supported is "*", to match any path segment.
    """
    return b'^%s\Z' % (
        b''.join(b'[^/]*' if c == b'*' else re.escape(c) for c in s))

if __name__ == '__main__':
    rejected = False

    # Parse the rules file.
    with open(os.environ[b'TURNIP_HOOK_REF_RULES'], 'rb') as f:
        rules = [
            re.compile(glob_to_re(l.rstrip(b'\n'))) for l in f.readlines()]

    # Match each ref against each rule.
    for ref_line in sys.stdin.readlines():
        old, new, ref = ref_line.rstrip(b'\n').split(b' ', 2)
        if any(rule.match(ref) for rule in rules):
            print(b"You can't push to %s." % ref)
            rejected = True

    sys.exit(1 if rejected else 0)
