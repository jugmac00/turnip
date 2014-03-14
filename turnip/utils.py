from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os.path


def compose_path(root, path):
    full_path = os.path.join(root, path)
    if not os.path.realpath(full_path).startswith(root):
        raise ValueError(path)
    return full_path
