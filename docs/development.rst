Development
===========

Setup
-----

Create a bionic container (optional):

.. code:: bash

    lxc launch ubuntu:bionic turnip-bionic -p ${USER}

(You may want to use a profile to bind-mount your home directory as well.)

SSH into the new container and navigate to the turnip repo.

Create a python virtual env:

.. code:: bash

    python3 -m venv env
    source env/bin/activate

.. note::
    If you created a container, you may need to install python virtual env:

    .. code:: bash

        sudo apt-get update
        sudo apt-get install -y python3-venv

Run the following:

.. code:: bash

        sudo add-apt-repository ppa:launchpad/ppa
        sudo apt-get update
        cat system-dependencies.txt dependencies-devel.txt charm/packages.txt | sudo xargs apt-get install -y --no-install-recommends
        make bootstrap
        mkdir -p /var/tmp/git.launchpad.test

.. note::
    If you are running a different ubuntu version on your container (e.g. focal), you might need to run:

    .. code:: bash

        make clean
        make

Running
-------

Pack smart-http/ssh services can be started with:

.. code:: bash

    make run-pack

The HTTP API can be started with:

.. code:: bash

   make run-api


Running LP locally as a Git client to Turnip
--------------------------------------------

Turnip container needs to be able to talk to xmlrpc-private.launchpad.test.

In the turnip container the hosts file needs to point to the LP container
(x.x.x.x is the IP address of LP):

.. code:: bash

    user@turnip-bionic:~/turnip$ cat /etc/hosts
    127.0.0.1 localhost
    x.x.x.x launchpad.test launchpad.test answers.launchpad.test archive.launchpad.test api.launchpad.test bazaar.launchpad.test bazaar-internal.launchpad.test blueprints.launchpad.test bugs.launchpad.test code.launchpad.test feeds.launchpad.test keyserver.launchpad.test lists.launchpad.test ppa.launchpad.test private-ppa.launchpad.test testopenid.test translations.launchpad.test xmlrpc-private.launchpad.test xmlrpc.launchpad.test
    # The following lines are desirable for IPv6 capable hosts
    ::1 ip6-localhost ip6-loopback
    fe00::0 ip6-localnet
    ff00::0 ip6-mcastprefix
    ff02::1 ip6-allnodes
    ff02::2 ip6-allrouters
    ff02::3 ip6-allhosts

A basic test that can be performed by dropping into the turnip container shell.
Below exception is expected as Repository ``1`` did not exist when the RPC
call was performed, it does show however that turnip is able to resolve
``xmlrpc-private.launchpad.test`` and there is connectivity between LP and
turnip:

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

In your LP container the hosts file needs to point to the turnip container
(x.x.x.x is the IP address of turnip):

    x.x.x.x git.launchpad.test

Then, also in your LP container edit ~/.gitconfig and add these lines,
where USER is your Launchpad username:

.. code:: bash

    [url "git+ssh://USER@git.launchpad.test/"]
        insteadof = lptest:

Create a new repository locally (user@launchpad:~/repo in LP container in below
example) and push it to LP&Turnip:

.. code:: bash

    user@launchpad:~/repo$ git remote add origin git+ssh://user@git.launchpad.test:9422/~user/+git/repo
    user@launchpad:~/repo$ git push --set-upstream origin master
    Counting objects: 3, done.
    Writing objects: 100% (3/3), 231 bytes | 231.00 KiB/s, done.
    Total 3 (delta 0), reused 0 (delta 0)
    To git+ssh://git.launchpad.test:9422/~user/+git/repo
    * [new branch]      master -> master
    Branch 'master' set up to track remote branch 'master' from 'origin'.
    user@launchpad:~/repo$ 


The LP log for above push:

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


Your local LP user must exist in LP - created with
``utilities/make-lp-user USER`` - and have an ssh key in local LP.
When adding the SSH key to LP if emails can't go out the SSH key addition will
fail. 
One possible workaround is to use Fakeemail:
https://github.com/tomwardill/fakeemail

It is recommended to install it in a virtual environment,
e.g. via `pipx <https://pypa.github.io/pipx/>`_:

.. code:: bash

    pipx install fakeemail
    ~/.local/bin/fakeemail  25 8082 0.0.0.0
    Message stored for: root@localhost

When creating and pushing new branches to LP with this local setup,
the branches need to be scanned (data about the branch copied into the
Launchpad database).
On production, this happens via the magic of cron.
Locally you can make it happen by running in your launchpad directory:

.. code:: bash

    cronscripts/process-job-source.py IGitRefScanJobSource

Now you have a fully working and up-to-date branch.
You should be able to look at the branch page in Launchpad,
view the source in codebrowse, and so on.
