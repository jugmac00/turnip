# Copyright 2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import absolute_import, print_function, unicode_literals

from charms.layer import status
from charms.reactive import clear_flag, hook, set_flag, when, when_not

from charms.turnip.base import (
    PayloadError,
    ensure_directories,
    ensure_user,
    group,
    group_id,
    install_services,
    user,
    user_id,
)


@when_not("turnip.created_user")
def install():
    ensure_user(user(), group(), uid=user_id(), gid=group_id())
    ensure_directories()
    set_flag("turnip.created_user")


@when("turnip.created_user")
@when_not("turnip.installed")
def install_turnip():
    try:
        install_services()
    except PayloadError as e:
        status.blocked(str(e))
        return
    set_flag("turnip.installed")
    clear_flag("turnip.configured")
    status.maintenance("Service installed, but not configured")


@hook("upgrade-charm")
def upgrade_charm():
    clear_flag("turnip.installed")


@when("config.changed.build_label")
def build_label_changed():
    clear_flag("turnip.installed")


@when("config.changed")
def config_changed():
    clear_flag("turnip.configured")
