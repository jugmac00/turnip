# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Main entry point."""

import resource

from pyramid.config import Configurator


def main(global_config, **settings):
    # Allow slack for lots of open pack files.
    _, hard_nofile = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(
        resource.RLIMIT_NOFILE, (min(4096, hard_nofile), hard_nofile))

    config = Configurator(settings=settings)
    config.include("cornice")
    config.scan("turnip.api.views")
    return config.make_wsgi_app()
