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
``turnip-stateless-rpc`` and ``turnip-advertise-refs``, which are used by
the smart HTTP server to proxy to the standard pack protocol.

turnip implements one externally-visible extension: a
``turnip-set-symbolic-ref`` service that sets a symbolic ref (currently only
``HEAD`` is permitted) to a given target. This may be used over the various
protocols (git, SSH, smart HTTP), requesting the service in the same way as
the existing ``git-upload-pack`` and ``git-receive-pack`` services::

   turnip-set-symbolic-ref-request = set-symbolic-ref-line
                                     flush-pkt
   set-symbolic-ref-line           = PKT-LINE(refname SP refname)

The server replies with an ACK indicating the symbolic ref name that was
changed, or an error message::

   turnip-set-symbolic-ref-response = set-symbolic-ref-ack / error-line
   set-symbolic-ref-ack             = PKT-LINE("ACK" SP refname)


Internally, Turnip also implements an extension to create repositories:
``turnip-create-repo``. It receives the new repository's pathname and the same
authentication parameters used by the external interface. The authentication
details are used to confirm/abort the repository creation on Launchpad.
