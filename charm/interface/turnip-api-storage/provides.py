# Copyright 2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import absolute_import, print_function, unicode_literals

from charmhelpers.core import hookenv
from charms.reactive import (
    clear_flag,
    Endpoint,
    set_flag,
    when,
    when_not,
    )


class TurnipAPIStorageProvides(Endpoint):

    @when('endpoint.{endpoint_name}.joined')
    def joined(self):
        set_flag(self.expand_name('endpoint.{endpoint_name}.available'))

    @when_not('endpoint.{endpoint_name}.joined')
    def broken(self):
        clear_flag(self.expand_name('endpoint.{endpoint_name}.available'))

    def publish_info(self, port, hostname=None):
        # XXX cjwatson 2018-12-12: For scaling, we should be publishing
        # haproxy's IP address instead; but it only matters to turnipcake,
        # and in the development case it's OK for that to just pick a unit.
        if hostname is None:
            hostname = hookenv.unit_get('private-address')
        for relation in self.relations:
            relation.to_publish.update({
                'hostname': hostname,
                'port': port,
                })
