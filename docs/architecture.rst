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
 