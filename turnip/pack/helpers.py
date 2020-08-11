# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from collections import OrderedDict
import enum
import hashlib
import os.path
import re
import stat
import sys
from tempfile import (
    mktemp,
    NamedTemporaryFile,
    )

from pygit2 import Repository
import six
import yaml

import turnip.pack.hooks
from turnip.version_info import version_info

FLUSH_PKT = b'0000'
DELIM_PKT = b'0001'
PKT_LEN_SIZE = 4
PKT_PAYLOAD_MAX = 65520
INCOMPLETE_PKT = object()


def encode_packet(payload):
    if payload is None:
        # flush-pkt.
        return FLUSH_PKT
    else:
        # data-pkt
        if len(payload) > PKT_PAYLOAD_MAX:
            raise ValueError(
                "data-pkt payload must not exceed %d bytes" % PKT_PAYLOAD_MAX)
        pkt_len = ('%04x' % (len(payload) + PKT_LEN_SIZE)).encode('ascii')
        return pkt_len + payload


def decode_packet(input):
    """Consume a packet, returning the payload and any unconsumed tail."""
    if len(input) < PKT_LEN_SIZE:
        return (INCOMPLETE_PKT, input)
    if input.startswith(FLUSH_PKT):
        # flush-pkt
        return (None, input[PKT_LEN_SIZE:])
    # data-pkt
    try:
        pkt_len = int(input[:PKT_LEN_SIZE], 16)
    except ValueError:
        pkt_len = 0
    if not (PKT_LEN_SIZE <= pkt_len <= (PKT_LEN_SIZE + PKT_PAYLOAD_MAX)):
        raise ValueError("Invalid pkt-len")
    if len(input) < pkt_len:
        # Some of the packet is yet to be received.
        return (INCOMPLETE_PKT, input)
    # v2 protocol "hides" extra parameters after the end of the packet.
    if len(input) > pkt_len and b'version=2\x00' in input:
        if FLUSH_PKT not in input:
            return INCOMPLETE_PKT, input
        end = input.index(FLUSH_PKT)
        return input[PKT_LEN_SIZE:end], input[end + len(FLUSH_PKT):]
    return (input[PKT_LEN_SIZE:pkt_len], input[pkt_len:])


def decode_packet_list(data):
    remaining = data
    retval = []
    while remaining:
        pkt, remaining = decode_packet(remaining)
        retval.append(pkt)
    return retval


def decode_protocol_v2_params(data):
    """Parse the protocol v2 extra parameters hidden behind the end of v1
    protocol.

    :return: An ordered dict with parsed v2 parameters.
    """
    params = OrderedDict()
    cmd, remaining = decode_packet(data)
    cmd = cmd.split(b'=', 1)[-1].strip()
    capabilities, args = remaining.split(DELIM_PKT)
    params[b"command"] = cmd
    params[b"capabilities"] = decode_packet_list(capabilities)
    for arg in decode_packet_list(args):
        if arg is None:
            continue
        arg = arg.strip('\n')
        if b' ' in arg:
            k, v = arg.split(b' ', 1)
            if k not in params:
                params[k] = []
            params[k].append(v)
        else:
            params[arg] = b""
    return params


def decode_request(data):
    """Decode a turnip-proto-request.

    turnip-proto-request is a superset of git-proto-request, supporting
    multiple named parameters. A turnip-proto-request with no parameters
    other than 'host' is also a git-proto-request.
    """
    if b' ' not in data:
        raise ValueError('Invalid git-proto-request')
    command, rest = data.split(b' ', 1)
    bits = rest.split(b'\0')
    # Following the command is a pathname, then any number of named
    # parameters. Each of these is NUL-terminated.
    # After that, v1 should end (v2 might have extra commands).
    if len(bits) < 2 or (b'version=2' not in bits and bits[-1] != b''):
        raise ValueError('Invalid git-proto-request')
    pathname = bits[0]
    params = OrderedDict()
    for index, param in enumerate(bits[1:-1]):
        if param == b'':
            if (index < len(bits) - 1):
                # we skip over the second NUL byte here
                # and move on to the extra parameter after
                # the 2 NUL bytes to parse it
                continue
        if b'=' not in param:
            raise ValueError('Parameters must have values')
        name, value = param.split(b'=', 1)
        if name in params:
            raise ValueError('Parameters must not be repeated')
        params[name] = value

    # If there are remaining bits at the end, we must be dealing with v2
    # protocol. So, we append v2 parameters at the end of original parameters.
    if bits[-1]:
        for k, v in decode_protocol_v2_params(bits[-1]).items():
            params[k] = v

    return command, pathname, params


def get_encoded_value(value):
    """Encode a value for serialization on encode_request"""
    if value is None:
        return b''
    if isinstance(value, list):
        if any(b'\n' in i for i in value):
            raise ValueError('Metacharacter in list argument')
        return b"\n".join(get_encoded_value(i) for i in value)
    if isinstance(value, bool):
        return b'1' if value else b''
    return six.ensure_binary(value)


def encode_request(command, pathname, params):
    """Encode a command, pathname and parameters into a turnip-proto-request.
    """
    command = six.ensure_binary(command)
    pathname = six.ensure_binary(pathname)
    if b' ' in command or b'\0' in pathname:
        raise ValueError('Metacharacter in arguments')
    bits = [pathname]
    for name in params:
        value = get_encoded_value(params[name])
        name = six.ensure_binary(name)
        if b'=' in name or b'\0' in name + value:
            raise ValueError('Metacharacter in arguments')
        bits.append(name + b'=' + value)
    return command + b' ' + b'\0'.join(bits) + b'\0'


def ensure_config(repo_root):
    """Put a repository's configuration into the desired state.

    pygit2.Config handles locking itself, so we don't need to think too hard
    about concurrency.
    """
    with open('git.config.yaml') as config_file:
        git_config_defaults = yaml.safe_load(config_file)
    config = Repository(repo_root).config
    for key, val in git_config_defaults.items():
        config[key] = val


_orig_hook = None


def read_orig_hook():
    """Read hook.py and adjust it for writing to a repository's hooks.

    We need to mangle the #! line so that it uses the correct virtualenv.
    """
    global _orig_hook
    if _orig_hook is None:
        # Always use the py, not the pyc, for consistency
        orig_hook_path = os.path.join(
            os.path.dirname(turnip.pack.hooks.__file__), 'hook.py')
        with open(orig_hook_path, 'rb') as f:
            contents = f.read()
        _orig_hook = re.sub(
            br'\A#!.*', ('#!' + sys.executable).encode('UTF-8'), contents,
            count=1)
    return _orig_hook


def ensure_hooks(repo_root):
    """Put a repository's hooks into the desired state.

    Consistency is maintained even if there are multiple invocations
    running concurrently. Files starting with tmp* are ignored, and any
    directories will cause an exception.
    """

    wanted_hooks = ('pre-receive', 'update', 'post-receive')
    target_name = 'hook.py'

    def hook_path(name):
        root = six.ensure_text(repo_root, "utf8")
        name = six.ensure_text(name, "utf8")
        return os.path.join(root, 'hooks', name)

    if not os.path.exists(hook_path(target_name)):
        need_target = True
    elif not os.stat(hook_path(target_name)).st_mode & stat.S_IXUSR:
        need_target = True
    else:
        # Always use the py, not the pyc, for consistency
        wanted = hashlib.sha256(read_orig_hook()).hexdigest()
        with open(hook_path(target_name), 'rb') as f:
            have = hashlib.sha256(f.read()).hexdigest()
        need_target = wanted != have

    if need_target:
        with NamedTemporaryFile(dir=hook_path('.'), delete=False) as this:
            this.write(read_orig_hook())
        os.chmod(this.name, 0o755)
        os.rename(this.name, hook_path(target_name))

    for hook in wanted_hooks:
        # Not actually insecure, since os.symlink fails if the file exists.
        path = mktemp(dir=hook_path('.'))
        os.symlink(target_name, path)
        os.rename(path, hook_path(hook))

    for name in os.listdir(hook_path('.')):
        if (name != target_name and name not in wanted_hooks and
                not name.startswith('tmp')):
            try:
                os.unlink(hook_path(name))
            except OSError:
                # May have raced with another invocation.
                pass


@enum.unique
class TurnipFaultCode(enum.Enum):
    """An internal vocabulary of possible faults from the virtinfo service."""

    NOT_FOUND = 1
    FORBIDDEN = 2
    UNAUTHORIZED = 3
    GATEWAY_TIMEOUT = 4
    INTERNAL_SERVER_ERROR = 5


def translate_xmlrpc_fault(code):
    """Translate an XML-RPC fault code into an internal vocabulary.

    The turnipcake and Launchpad implementations of the virtinfo service
    return different codes in some cases.
    """
    if code in (1, 290):
        result = TurnipFaultCode.NOT_FOUND
    elif code in (2, 310):
        result = TurnipFaultCode.FORBIDDEN
    elif code in (3, 410):
        result = TurnipFaultCode.UNAUTHORIZED
    elif code == 504:
        result = TurnipFaultCode.GATEWAY_TIMEOUT
    else:
        result = TurnipFaultCode.INTERNAL_SERVER_ERROR
    return result


def get_capabilities_advertisement(version='1'):
    """Returns the capability advertisement binary string to be sent to
    clients for a given protocol version requested.

    If no binary data is sent, no advertisement is done and we declare to
    not be compatible with that specific version."""
    if version != '2':
        return b""
    turnip_version = six.ensure_binary(version_info.get("revision_id", '-1'))
    return (
        encode_packet(b"version 2\n") +
        encode_packet(b"agent=turnip/%s\n" % turnip_version) +
        encode_packet(b"ls-refs\n") +
        encode_packet(b"fetch=shallow\n") +
        encode_packet(b"server-option\n") +
        FLUSH_PKT
    )
