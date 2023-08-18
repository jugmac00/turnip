# Copyright 2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import absolute_import, print_function, unicode_literals

from charmhelpers.core import hookenv, host
from charms.layer import status
from charms.reactive import (
    clear_flag,
    endpoint_from_flag,
    set_flag,
    when,
    when_not,
)

from charms.turnip.base import (
    add_nagios_e2e_checks,
    configure_service,
    deconfigure_service,
    ensure_user,
    find_git_service,
    publish_website,
    user,
)
from charms.turnip.http import configure_cgit


@when("turnip.created_user")
@when_not("turnip.created_cgit_user")
def create_cgit_user():
    ensure_user(
        hookenv.config()["cgit_user"],
        hookenv.config()["cgit_group"],
        uid=hookenv.config()["cgit_user_id"],
        gid=hookenv.config()["cgit_group_id"],
    )
    # This lets the main service user execute cgitwrap.
    host.add_user_to_group(user(), hookenv.config()["cgit_group"])
    set_flag("turnip.created_cgit_user")


@when("turnip-pack-virt.available")
@when_not("turnip.configured")
def turnip_pack_virt_available():
    turnip_pack_virt = endpoint_from_flag("turnip-pack-virt.available")
    config = hookenv.config()
    address, port = find_git_service(turnip_pack_virt, "turnip-pack-virt")
    if address is not None and port is not None:
        config["pack_virt_host"] = address
        config["pack_virt_port"] = port
        set_flag("turnip.services.pack-virt")
    else:
        clear_flag("turnip.services.pack-virt")
        status.blocked(
            "turnip-pack-virt must be related to the http interface"
        )


@when(
    "turnip.created_cgit_user",
    "turnip.installed",
    "turnip.services.pack-virt",
    "turnip.storage.available",
)
@when_not("turnip.configured")
def configure_turnip():
    configure_cgit()
    configure_service("turnip-pack-frontend-http")
    set_flag("turnip.configured")
    clear_flag("turnip.storage.nrpe-external-master.published")
    clear_flag("turnip.nrpe-external-master.published")
    clear_flag("turnip.turnip-pack-frontend-http.published")
    status.active("Ready")


@when("turnip.configured")
@when_not("turnip.storage.available")
def deconfigure_turnip():
    deconfigure_service("turnip-pack-frontend-http")
    clear_flag("turnip.configured")
    status.blocked("Waiting for storage to be available")


@when("nrpe-external-master.available", "turnip.configured")
@when_not("turnip.nrpe-external-master.published")
def nrpe_available():
    nagios = endpoint_from_flag("nrpe-external-master.available")
    config = hookenv.config()
    nagios.add_check(
        [
            "/usr/lib/nagios/plugins/check_http",
            "-H",
            "localhost",
            "-p",
            str(config["port"]),
            "-j",
            "OPTIONS",
        ],
        name="check_turnip_pack_frontend_http",
        description="Git smart HTTP check",
        context=config["nagios_context"],
    )
    add_nagios_e2e_checks(nagios)
    set_flag("turnip.nrpe-external-master.published")


@when("turnip.nrpe-external-master.published")
@when_not("nrpe-external-master.available")
def nrpe_unavailable():
    clear_flag("turnip.nrpe-external-master.published")


@when("turnip-pack-frontend-http.available", "turnip.configured")
@when_not("turnip.turnip-pack-frontend-http.published")
def turnip_pack_frontend_http_available():
    turnip_pack_frontend_http = endpoint_from_flag(
        "turnip-pack-frontend-http.available"
    )
    publish_website(
        turnip_pack_frontend_http,
        "turnip-pack-frontend-http",
        hookenv.config()["port"],
    )
    set_flag("turnip.turnip-pack-frontend-http.published")


@when("turnip.turnip-pack-frontend-http.published")
@when_not("turnip-pack-frontend-http.available")
def turnip_pack_frontend_http_unavailable():
    clear_flag("turnip.turnip-pack-frontend-http.published")
