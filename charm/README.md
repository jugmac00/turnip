# Overview

These charms provide the [Turnip](https://launchpad.net/turnip) service, a
flexible and scalable Git server suite written in Python with Twisted and
Pyramid.

# Usage

    $ juju add-model turnip
    $ make deploy

This will deploy Turnip itself, but it must be linked to a separate service
that defines things like how to translate repository paths, how to
authenticate users, and so on.  This may be Launchpad or the stub
"turnipcake" implementation.  To deploy the latter:

    $ git clone https://git.launchpad.net/~canonical-launchpad-branches/turnip/+git/turnipcake
    $ cd turnipcake/charm
    $ make deploy
    $ juju add-relation turnip-api turnipcake
    $ juju add-relation haproxy turnipcake:turnipcake
    $ HAPROXY_ADDRESS="$(juju status --format=json haproxy | jq -r '.applications.haproxy.units[]."public-address"')"
    $ VIRT_ENDPOINT="http://$HAPROXY_ADDRESS:6543/githosting"
    $ juju config turnip-pack-backend virtinfo_endpoint="$VIRT_ENDPOINT"
    $ juju config turnip-pack-virt virtinfo_endpoint="$VIRT_ENDPOINT"
    $ juju config turnip-pack-frontend-http virtinfo_endpoint="$VIRT_ENDPOINT"
    $ juju config turnip-celery virtinfo_endpoint="$VIRT_ENDPOINT"

# Contact Information

Colin Watson <cjwatson@canonical.com>
https://launchpad.net/~cjwatson
