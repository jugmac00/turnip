# Copyright 2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from charmhelpers.core import hookenv, unitdata
from charms.layer import status
from charms.reactive import (
    clear_flag,
    endpoint_from_flag,
    set_flag,
    when,
    when_not,
    when_not_all,
)

from charms.turnip.api import configure_wsgi
from charms.turnip.base import (
    configure_service,
    deconfigure_service,
    get_rabbitmq_url,
    publish_website,
)


@when("turnip.installed", "turnip.storage.available", "turnip.amqp.available")
@when_not("turnip.configured")
def configure_turnip():
    configure_wsgi()
    configure_service()
    set_flag("turnip.configured")
    clear_flag("turnip.storage.nrpe-external-master.published")
    clear_flag("turnip.nrpe-external-master.published")
    clear_flag("turnip.turnip-api.published")
    status.active("Ready")


@when("turnip.configured")
@when_not_all("turnip.storage.available", "turnip.amqp.available")
def deconfigure_turnip():
    deconfigure_service("turnip-api")
    clear_flag("turnip.configured")
    status.blocked("Waiting for storage and rabbitmq to be available")


@when("amqp.connected")
@when_not("turnip.amqp.requested-access")
def request_amqp_access(rabbitmq):
    # Clear any previous request so that the rabbitmq-server charm notices
    # the change.
    rabbitmq.request_access(username="", vhost="")
    rabbitmq.request_access(username="turnip", vhost="/")
    set_flag("turnip.amqp.requested-access")


@when("turnip.amqp.requested-access")
@when_not("amqp.connected")
def unrequest_amqp_access():
    clear_flag("turnip.amqp.requested-access")


@when("amqp.available")
@when_not("turnip.amqp.available")
def get_amqp_broker(rabbitmq):
    unitdata.kv().set("turnip.amqp.url", get_rabbitmq_url())
    set_flag("turnip.amqp.available")


@when("turnip.amqp.available")
@when_not("amqp.available")
def clear_amqp_broker():
    unitdata.kv().unset("turnip.amqp.url")
    clear_flag("turnip.amqp.available")


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
            "-u",
            "/repo",
        ],
        name="check_api",
        description="Git API check",
        context=config["nagios_context"],
    )
    set_flag("turnip.nrpe-external-master.published")


@when("turnip.nrpe-external-master.published")
@when_not("nrpe-external-master.available")
def nrpe_unavailable():
    clear_flag("turnip.nrpe-external-master.published")


@when("turnip-api.available", "turnip.configured")
@when_not("turnip.turnip-api.published")
def turnip_api_available():
    turnip_api = endpoint_from_flag("turnip-api.available")
    publish_website(turnip_api, "turnip-api", hookenv.config()["port"])
    set_flag("turnip.turnip-api.published")


@when("turnip.turnip-api.published")
@when_not("turnip-api.available")
def turnip_api_unavailable():
    clear_flag("turnip.turnip-api.published")
