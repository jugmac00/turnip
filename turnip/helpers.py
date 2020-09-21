# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os.path

import six
from six.moves import xmlrpc_client


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


class TimeoutTransport(xmlrpc_client.Transport):

    def __init__(self, timeout, use_datetime=0):
        self.timeout = timeout
        xmlrpc_client.Transport.__init__(self, use_datetime)

    def make_connection(self, host):
        connection = xmlrpc_client.Transport.make_connection(self, host)
        connection.timeout = self.timeout
        return connection


class TimeoutServerProxy(xmlrpc_client.ServerProxy):

    def __init__(self, uri, timeout=10, transport=None, encoding=None,
                 verbose=0, allow_none=0, use_datetime=0):
        t = TimeoutTransport(timeout)
        xmlrpc_client.ServerProxy.__init__(
            self, uri, t, encoding, verbose, allow_none, use_datetime)
