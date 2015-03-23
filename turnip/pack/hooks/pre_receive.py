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


def match_rules(rule_lines, ref_lines):
    rules = [re.compile(glob_to_re(l.rstrip(b'\n'))) for l in rule_lines]
    # Match each ref against each rule.
    errors = []
    for ref_line in ref_lines:
        old, new, ref = ref_line.rstrip(b'\n').split(b' ', 2)
        if any(rule.match(ref) for rule in rules):
            errors.append(b"You can't push to %s." % ref)
    return errors


if __name__ == '__main__':
    with open(os.environ[b'TURNIP_HOOK_REF_RULES'], 'rb') as f:
        rule_lines = f.readlines()
    errors = match_rules(rule_lines, sys.stdin.readlines())
    for error in errors:
        print(error)
    sys.exit(1 if errors else 0)
