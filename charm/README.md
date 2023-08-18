# Overview

These charms provide the [Turnip](https://launchpad.net/turnip) service, a
flexible and scalable Git server suite written in Python with Twisted and
Pyramid.

# Usage

The simplest way to deploy the full charmed stack is by using the [Mojo
spec](https://git.launchpad.net/launchpad-mojo-specs/tree/lp-git/README.md).

If you need to rebuild individual charms locally, you can run `charmcraft
pack` in the `turnip-*` subdirectories here.

Turnip must be linked to a separate service that defines things like how to
translate repository paths, how to authenticate users, and so on.  This may
be Launchpad or the stub "turnipcake" implementation.  The Mojo spec is set
up by default to use a local Launchpad deployment, though you will need to
ensure that `xmlrpc-private.launchpad.test` is set up to point to an
appropriate local IP address in `/etc/hosts`.

# Contact Information

Colin Watson <cjwatson@canonical.com>
https://launchpad.net/~cjwatson
