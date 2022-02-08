============================
How to qa turnip API changes
============================

qa is usually performed on https://qastaging.launchpad.net.

Prerequisites
=============

- You need access to ``launchpad-bastion-ps5.internal``

- Your user needs to be able to sudo into ``stg-launchpad-git``

- You need to have ``lp-shell`` installed in your development environment

Access to above host and the sudo permission are granted by
`IS <https://portal.admin.canonical.com/new/>`_.

``lp-shell`` can be installed via

.. code-block:: bash

    sudo apt install lptools

Example qa for querying the commits endpoint
============================================

- Pick a project on https://qastaging.launchpad.net, e.g.
  https://qastaging.launchpad.net/turnip

- From above project, pick one git repository, and get its ``repo id`` via

  .. code-block:: bash

     $ lp-shell qastaging devel

     >>> lp.git_repositories.getByPath(path='~canonical-launchpad-branches/turnip/+git/turnip').id
     3683

  This id will be used for building the query,
  e.g. for querying the commit API (``/repo/<id>/commits>``).

  When you want to work with a repository,
  please pay attention to the special git URLs,
  i.e. the ``paddev`` part of them:

  git+ssh://git.qastaging.paddev.net/ and https://git.qastaging.paddev.net/

- Turn on company VPN

- Log into the bastion host

  .. code-block:: bash

     $ ssh launchpad-bastion-ps5.internal

- Switch to the service user

  .. code-block:: bash

     ubuntu@juju-a7beac-stg-launchpad-git-7:~$ sudo -iu stg-launchpad-git

- Get an overview of the available staging servers via Juju

  .. code-block:: bash

     $ stg-launchpad-git@launchpad-bastion-ps5:~$ juju status

     ...

     turnip-api ...
     turnip-celery ...
     turnip-pack...

- Log into one of the API servers

  .. code-block:: bash

     stg-launchpad-git@launchpad-bastion-ps5:~$ juju ssh turnip-api/0

- Perform the query

  .. code-block:: bash

     $ curl \
     -H "Content-Type: application/json" \
     -d '{"commits": ["180ad564a7297ee61fbdfe70fdf53d95febd1e09"], "filter_paths": ["config.yaml"]}' \
     http://0.0.0.0:19417/repo/3683/commits

     $ <results>

- <optional> You can inspect the turnip logs in ``/srv/turnip/logs/``.

Further steps
=============

Once the changes are verified,
you can mark the corresponding commit on https://deployable.ols.canonical.com/project/turnip as deployable.

And finally, the changes `can be deployed <https://wiki.canonical.com/InformationInfrastructure/WebOps/LP/LaunchpadGitDeployment#Code_upgrade>`_!
