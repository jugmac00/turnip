# Copyright 2020 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from six import with_metaclass


__all__ = [
    "BaseEnum",
    ]


class MetaEnum(type):
    def __contains__(cls, x):
        values = [getattr(cls, i) for i in dir(cls) if not i.startswith("_")]
        return x in values


class BaseEnum(with_metaclass(MetaEnum)):
    pass
