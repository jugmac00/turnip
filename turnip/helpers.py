from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os.path


def compose_path(root, path):
    # Construct the full path, stripping any leading slashes so we
    # resolve absolute paths within the root.
    full_path = os.path.abspath(os.path.join(
        root, path.lstrip(os.path.sep.encode('utf-8'))))
    if not full_path.startswith(os.path.abspath(root)):
        raise ValueError('Path not contained within root')
    return full_path
