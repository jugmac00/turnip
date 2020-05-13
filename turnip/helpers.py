# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os.path

import six


def compose_path(root, path):
    # Construct the full path, stripping any leading slashes so we
    # resolve absolute paths within the root.
    root = six.ensure_binary(root, "utf-8")
    full_path = os.path.abspath(os.path.join(
        root,
        path.lstrip(six.ensure_binary(os.path.sep, 'utf-8'))))
    if not full_path.startswith(os.path.abspath(root)):
        raise ValueError('Path not contained within root')
    return full_path
