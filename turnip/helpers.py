from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os.path


PKT_LEN_SIZE = 4
PKT_PAYLOAD_MAX = 65520
INCOMPLETE_PKT = object()


def compose_path(root, path):
    # Construct the full path, stripping any leading slashes so we
    # resolve absolute paths within the root.
    full_path = os.path.abspath(os.path.join(
        root, path.lstrip(os.path.sep.encode('utf-8'))))
    if not full_path.startswith(os.path.abspath(root)):
        raise ValueError('Path not contained within root')
    return full_path


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
    """Decode a git-proto-request.

    Returns a tuple of (command, pathname, host). host may be None if
    there was no host-parameter.
    """
    if b' ' not in data:
        raise ValueError('Invalid git-proto-request')
    command, rest = data.split(b' ', 1)
    args = rest.split(b'\0')
    # Arguments consist of a pathname optionally followed by a
    # host-parameter. Each argument is NUL-terminated.
    if len(args) not in (2, 3) or args[-1] != b'':
        raise ValueError('Invalid git-proto-request')
    pathname = args[0]
    if len(args) == 3:
        if not args[1].startswith(b'host='):
            raise ValueError('Invalid host-parameter')
        host = args[1][len(b'host='):]
    else:
        host = None
    return (command, pathname, host)


def encode_request(command, pathname, host=None):
    """Encode a command, pathname and optional host into a git-proto-request.
    """
    if b' ' in command or b'\0' in pathname or (host and b'\0' in host):
        raise ValueError('Metacharacter in arguments')
    bits = [pathname]
    if host is not None:
        bits.append(b'host=' + host)
    return command + b' ' + b'\0'.join(bits) + b'\0'
