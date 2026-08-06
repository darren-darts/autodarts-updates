"""LAN address discovery, used to build a phone-reachable join URL.

The browser showing the main screen may be pointed at "localhost", which a
phone on the same network obviously can't reach — so the QR code needs the
machine's actual LAN IP(s) instead.
"""
from __future__ import annotations

import socket


def list_lan_ips() -> list[str]:
    ips: set[str] = set()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except socket.gaierror:
        pass

    # UDP "connect" doesn't send any packets, it just asks the OS which local
    # interface/address would be used to route to that destination - a
    # reliable way to find the primary outbound LAN IP even if the above
    # hostname lookup fails or is incomplete (common on some Linux setups).
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
    except OSError:
        pass

    return sorted(ips)
