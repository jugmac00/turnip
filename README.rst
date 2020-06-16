======
turnip
======

turnip is a flexible and scalable Git server suite written in Python
using Twisted.

The various servers provide customisable virtual hosting, with flexible
authentication and authorisation, and individual horizontal scaling from
the frontend to the storage layer.

None of the Python interfaces here should be considered stable.


Architecture
============

turnip's architecture is designed to maximise simplicity, scalability
and robustness. Each server provides roughly one service, and an
installation need only run the servers that it desires. Most servers
eschew local state to ease horizontal scaling, and those that do have
local state can replicate and/or shard it.

There are two separate server stacks: pack and API. The pack stack
communicates with Git clients via the pack protocol (git://), smart
HTTP, or smart SSH. The HTTP and SSH frontends unwrap the tunneled pack
protocol, and forward it onto the midends as a normal pack protocol
connection. The separate HTTP API stack provides a programmatic remote
interface to high-level read and write operations on the repositories


Frontends:
 * Pack
 * Smart HTTP
 * Smart SSH
 * HTTP API

Midends:
 * Pack virtualisation
 * API virtualisation

Backends:
 * Pack
 * API


Internal protocol
=================

turnip uses an extension of the Git pack protocol for most communication
between its servers. The only change is that turnip requests can specify
arbitrary named parameters, not just a hostname.

The relevant part of the Git pack protocol's git-proto-request is
represented in ABNF as follows::

   git-proto-request = request-command SP pathname NUL [ host-parameter NUL ]
   host-parameter = "host=" hostname [ ":" port ]

turnip-proto-request alters it to this::

   turnip-proto-request = request-command SP pathname NUL \*( param NUL )
   param = param-name "=" param-value
   param-name = \*( %x01-3C / %x3E-FF ) ; exclude NUL and =
   param-value = \*%x01-FF ; exclude NUL

The only additional parameters implemented today are
'turnip-stateless-rpc' and 'turnip-advertise-refs', which are used by
the smart HTTP server to proxy to the standard pack protocol.

turnip implements one externally-visible extension: a
'turnip-set-symbolic-ref' service that sets a symbolic ref (currently only
'HEAD' is permitted) to a given target. This may be used over the various
protocols (git, SSH, smart HTTP), requesting the service in the same way as
the existing 'git-upload-pack' and 'git-receive-pack' services::

   turnip-set-symbolic-ref-request = set-symbolic-ref-line
                                     flush-pkt
   set-symbolic-ref-line           = PKT-LINE(refname SP refname)

The server replies with an ACK indicating the symbolic ref name that was
changed, or an error message::

   turnip-set-symbolic-ref-response = set-symbolic-ref-ack / error-line
   set-symbolic-ref-ack             = PKT-LINE("ACK" SP refname)


Internally, Turnip implements an extension to create repositories:
'turnip-create-repo'. It receives, apart from the pathname, the same
authentication parameters used by the external interface. This
authentication is used to confirm/abort the repository creation on Launchpad.

Development
===========

Setup
-----

Create a bionic container (optional)::

        lxc launch ubuntu:bionic turnip-bionic

(You may want to use a profile to bind-mount your home directory as well.)

Run the following::

        sudo add-apt-repository ppa:launchpad/ppa
        sudo apt-get update
        cat system-dependencies.txt charm/packages.txt | sudo xargs apt-get install -y --no-install-recommends
        make bootstrap
        mkdir -p /var/tmp/git.launchpad.test

Running
-------

Pack smart-http/ssh services can be started with:

    make run-pack

The HTTP API can be started with:

   make run-api                  


Running LP locally as a Git client to Turnip
--------------------------------------------

Turnip container needs to be able to talk to xmlrpc-private.launchpad.test.

In the Turnip container the hosts file needs to point to the LP container (x.x.x.x is the IP address of LP):

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

A basic test that can be performed by dropping into the Turnip container shell. Below exception is expected as Repository '1' did not exist when the RPC call was performed, it does show however that Turnip is able to resolve xmlrpc-private.launchpad.test and there is connectivity between LP and Turnip:
	user@launchpad:~$ lxc exec turnip python
	...
	>>> from xmlrpclib import ServerProxy
	>>> proxy = ServerProxy('http://xmlrpc-private.launchpad.test:8087/git')
	>>> proxy.translatePath('1', 'read', {})
	Traceback (most recent call last):
	...
	xmlrpclib.Fault: <Fault 290: "Repository '1' not found.">
	>>> exit()
	root@turnip-bionic:~#

In your LP container edit ~/.gitconfig and add these lines, where USER is your Launchpad username:

	[url "git+ssh://USER@git.launchpad.test/"]
		insteadof = lptest:

Create a new repository locally (user@launchpad:~/repo in LP container in below example) and push it to LP&Turnip:

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


Your local LP user must exist in LP - created with "utilities/make-lp-user USER" - and have an ssh key in local LP.
When adding the SSH key to LP if emails can't go out the SSH key addition will fail. 
One possible workaround is to use Fakeemail: https://github.com/tomwardill/fakeemail
It is recommended that this is done in a venv (https://pypi.org/project/pipsi/):

	sudo apt install pipsi
	pipsi install fakeemail
	~/.local/bin/fakeemail  25 8082 0.0.0.0
	Message stored for: root@localhost

When creating and pushing new branches to LP with this local setup, the branches need to be scanned (data about the branch copied into the Launchpad database).
On production, this happens via the magic of cron.
Locally you can make it happen by running in your launchpad directory:

    cronscripts/process-job-source.py IGitRefScanJobSource

Now you have a fully working and up-to-date branch -- you should be able to look at the branch page in Launchpad, view the source in codebrowse, and so on.


Deployment
==========

Turnip is deployed with its own set of charms.  See charm/README.
