# Copyright 2015 Canonical Ltd.  All rights reserved.

"""Main entry point
"""
from pyramid.config import Configurator


def main(global_config, **settings):
    config = Configurator(settings=settings)
    config.include("cornice")
    config.scan("turnip.api.views")
    return config.make_wsgi_app()
