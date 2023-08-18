Development
===========

Prerequisites
-------------

* LXD is installed and set up. See
  `<https://documentation.ubuntu.com/lxd/en/latest/getting_started/>`_
  for more details.

* A working Launchpad development environment is available. See
  `<https://launchpad.readthedocs.io/en/latest/how-to/running.html>`_ for more
  details.

* A local user on the Launchpad development instance with an SSH key added.
  Use the ``utilities/make-lp-user`` script inside the Launchpad container
  to create a new user account. This script looks for SSH public keys in
  the home directory of the user and automatically adds them to the created
  account.


Setup
-----

These instructions should work with Ubuntu 18.04 (bionic) or 20.04 (focal).

Create a LXD container.

.. code:: bash

    lxc launch ubuntu:bionic turnip-bionic -p ${USER}

Replace ``bionic`` in the above command with ``focal`` to create a
``focal`` container.

It is useful to use a LXD profile to bind-mount your home directory inside
this container. See the `Launchpad setup guide`_ for an example of how to
do this.

.. _Launchpad setup guide:  https://launchpad.readthedocs.io/en/latest/how-to/running.html#create-a-lxd-container

Log in into the new container using SSH (you can do this by finding the IP
address of the turnip container from the output of the ``lxc ls`` command and
then running ``ssh <IP address of the container>``) and navigate to top-level
directory of the turnip repository.

Create a Python virtual environment.

.. code:: bash

    sudo apt update
    sudo apt install -y python3-venv
    python3 -m venv env
    source env/bin/activate

Run the following commands to install turnip's dependencies and bootstrap it.

.. code:: bash

        sudo add-apt-repository ppa:launchpad/ppa
        sudo apt-get update
        cat system-dependencies.txt dependencies-devel.txt | sudo xargs apt-get install -y --no-install-recommends
        make bootstrap
        mkdir -p /var/tmp/git.launchpad.test

.. note::
    If you are running a different Ubuntu version on your container
    (e.g. focal), you might need to run:

    .. code:: bash

        make clean
        make

Running
-------

Start the pack smart-http/ssh services with:

.. code:: bash

    make run-pack

Start the HTTP API with:

.. code:: bash

   make run-api


Running Launchpad locally as a Git client to turnip
---------------------------------------------------

The turnip container needs to be able to communicate with
``xmlrpc-private.launchpad.test`` for this to work.

In the turnip container, update the hosts file to point to the Launchpad
container, where ``x.x.x.x`` is its IP address.

.. code:: bash

    user@turnip-bionic:~/turnip$ cat /etc/hosts
    ...
    x.x.x.x launchpad.test launchpad.test answers.launchpad.test archive.launchpad.test api.launchpad.test bazaar.launchpad.test bazaar-internal.launchpad.test blueprints.launchpad.test bugs.launchpad.test code.launchpad.test feeds.launchpad.test keyserver.launchpad.test lists.launchpad.test ppa.launchpad.test private-ppa.launchpad.test testopenid.test translations.launchpad.test xmlrpc-private.launchpad.test xmlrpc.launchpad.test
    ...

Perform a basic test of the connectivity by running the following
commands and statements.

.. code:: bash

    user@launchpad:~$ lxc exec turnip-bionic python3

.. code:: python

    ...
    >>> from xmlrpc.client import ServerProxy
    >>> proxy = ServerProxy('http://xmlrpc-private.launchpad.test:8087/git')
    >>> proxy.translatePath('1', 'read', {})
    Traceback (most recent call last):
    ...
    xmlrpclib.Fault: <Fault 290: "Repository '1' not found.">
    >>> exit()
    root@turnip-bionic:~#

The above exception is expected as ``Repository '1'`` did not exist when
the RPC call was performed. But it shows that turnip is able to resolve
``xmlrpc-private.launchpad.test`` and that there is connectivity between
Launchpad and turnip.

In the Launchpad container, update the hosts file to point to the turnip
container, where ``x.x.x.x`` is its IP address.

.. code:: bash

    user@launchpad:~$ cat /etc/hosts
    ...
    x.x.x.x git.launchpad.test
    ...

Also edit ``~/.gitconfig`` in the Launchpad container and add these lines,
where ``USER`` is your Launchpad username on the local instance.

.. code:: bash

    [url "git+ssh://USER@git.launchpad.test:9422/"]
        insteadof = lptest:

Create a new repository, ``~/repo`` in the Launchpad container and push it
to turnip. In the below command, ``USER`` is your Launchpad username on the
local instance.

.. code:: bash

    user@launchpad:~/repo$ git remote add origin lptest:~USER/+git/repo
    user@launchpad:~/repo$ git push --set-upstream origin master
    Counting objects: 3, done.
    Writing objects: 100% (3/3), 231 bytes | 231.00 KiB/s, done.
    Total 3 (delta 0), reused 0 (delta 0)
    To git+ssh://git.launchpad.test:9422/~user/+git/repo
    * [new branch]      master -> master
    Branch 'master' set up to track remote branch 'master' from 'origin'.
    user@launchpad:~/repo$


The Launchpad log for above push should look like:

.. code::

    10.209.173.202 - "" "xmlrpc-private.launchpad.test" [16/Dec/2019:13:41:13 +0300] "POST /authserver HTTP/1.0" 200 1312 4 0.00622892379761 0.00250482559204 0.00320911407471 "Anonymous" "AuthServerApplication:" "" "Twisted/XMLRPClib"

    2019-12-16T13:41:17 INFO lp.code.xmlrpc.git [request-id=057364e1-9e12-48c6-857d-a228c56d88c2] Request received: translatePath('~user/+git/repo', 'write') for 243674

    2019-12-16T13:41:17 INFO lp.code.xmlrpc.git [request-id=057364e1-9e12-48c6-857d-a228c56d88c2] translatePath succeeded: {'writable': True, 'path': '5', 'trailing': '', 'private': False}
    10.209.173.202 - "" "xmlrpc-private.launchpad.test" [16/Dec/2019:13:41:17 +0300] "POST /git HTTP/1.0" 200 899 21 0.0600020885468 0.00421810150146 0.0549690723419 "Anonymous" "GitApplication:" "" "Twisted/XMLRPClib"

    2019-12-16T13:41:18 INFO lp.code.xmlrpc.git [request-id=057364e1-9e12-48c6-857d-a228c56d88c2] Request received: checkRefPermissions('5', ['refs/heads/master']) for 243674

    2019-12-16T13:41:18 INFO lp.code.xmlrpc.git [request-id=057364e1-9e12-48c6-857d-a228c56d88c2] checkRefPermissions succeeded: [('refs/heads/master', ['create', 'push', 'force_push'])]
    10.209.173.202 - "" "xmlrpc-private.launchpad.test" [16/Dec/2019:13:41:18 +0300] "POST /git HTTP/1.0" 200 880 10 0.0158808231354 0.00237107276917 0.0127749443054 "Anonymous" "GitApplication:" "" "Twisted/XMLRPClib"

    2019-12-16T13:41:18 INFO lp.code.xmlrpc.git [request-id=2f4f61d3-8e58-4fd9-9d45-1949e08ad297] Request received: notify('5')

    2019-12-16T13:41:18 INFO lp.code.xmlrpc.git [request-id=2f4f61d3-8e58-4fd9-9d45-1949e08ad297] notify succeeded
    10.209.173.202 - "" "xmlrpc-private.launchpad.test" [16/Dec/2019:13:41:18 +0300] "POST /git HTTP/1.0" 200 588 7 0.0113499164581 0.00207781791687 0.00744009017944 "Anonymous" "GitApplication:" "" "Twisted/XMLRPClib"


When creating and pushing new branches to turnip with this local setup,
the branches have to be scanned (data about the branch copied into the
Launchpad database) for Launchpad to know about them.

Run the following command in the Launchpad container from the top-level
directory of the Launchpad repository to make Launchpad scan the git
branches.

.. code:: bash

    cronscripts/process-job-source.py -v IGitRefScanJobSource

Now the branch should be up-to-date and you can view it in the branch page
in the local Launchpad instance.

Now you can create a merge proposal from a branch. After creating it, generate
the preview diff for the merge proposal by running the following command
inside the Launchpad container from the top-level directory of the Launchpad
repository.

.. code:: bash

    cronscripts/process-job-source.py -v IBranchMergeProposalJobSource

These commands are automatically run in the production environment by cron jobs.
