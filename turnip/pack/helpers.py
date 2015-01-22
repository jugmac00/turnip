from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )


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
