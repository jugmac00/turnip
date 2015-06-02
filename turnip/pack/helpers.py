# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import hashlib
import os.path
import shutil
import stat
from tempfile import (
    mktemp,
    NamedTemporaryFile,
    )

from pygit2 import Repository
import yaml

import turnip.pack.hooks


PKT_LEN_SIZE = 4
PKT_PAYLOAD_MAX = 65520
INCOMPLETE_PKT = object()


def encode_packet(payload):
    if payload is None:
        # flush-pkt.
        return b'0000'
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
    if input.startswith(b'0000'):
        # flush-pkt
        return (None, input[PKT_LEN_SIZE:])
    else:
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
        return (input[PKT_LEN_SIZE:pkt_len], input[pkt_len:])


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
    if len(bits) < 2 or bits[-1] != b'':
        raise ValueError('Invalid git-proto-request')
    pathname = bits[0]
    params = {}
    for param in bits[1:-1]:
        if b'=' not in param:
            raise ValueError('Parameters must have values')
        name, value = param.split(b'=', 1)
        if name in params:
            raise ValueError('Parameters must not be repeated')
        params[name] = value
    return (command, pathname, params)


def encode_request(command, pathname, params):
    """Encode a command, pathname and parameters into a turnip-proto-request.
    """
    if b' ' in command or b'\0' in pathname:
        raise ValueError('Metacharacter in arguments')
    bits = [pathname]
    for name in sorted(params):
        value = params[name]
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
        git_config_defaults = yaml.load(config_file)
    config = Repository(repo_root).config
    for key, val in git_config_defaults.iteritems():
        config[key] = val


def ensure_hooks(repo_root):
    """Put a repository's hooks into the desired state.

    Consistency is maintained even if there are multiple invocations
    running concurrently. Files starting with tmp* are ignored, and any
    directories will cause an exception.
    """

    wanted_hooks = ('pre-receive', 'post-receive')
    target_name = 'hook.py'

    def hook_path(name):
        return os.path.join(repo_root, 'hooks', name)

    orig_hook_path = os.path.join(
        os.path.dirname(turnip.pack.hooks.__file__), 'hook.py')

    if not os.path.exists(hook_path(target_name)):
        need_target = True
    elif not os.stat(hook_path(target_name)).st_mode & stat.S_IXUSR:
        need_target = True
    else:
        # Always use the py, not the pyc, for consistency
        with open(orig_hook_path, 'rb') as f:
            wanted = hashlib.sha256(f.read()).hexdigest()
        with open(hook_path(target_name), 'rb') as f:
            have = hashlib.sha256(f.read()).hexdigest()
        need_target = wanted != have

    if need_target:
        with open(orig_hook_path, 'rb') as master:
            with NamedTemporaryFile(dir=hook_path('.'), delete=False) as this:
                shutil.copyfileobj(master, this)
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
