"""Block non-loopback sockets for the explicit offline pytest lane.

Python imports ``sitecustomize`` in the test process and in ordinary Python
subprocesses because the offline runner prepends this directory to
``PYTHONPATH``.  The guard is inert outside that runner.
"""
from __future__ import annotations

import errno
import ipaddress
import os
import socket


if os.environ.get("KREPORTS_OFFLINE_NETWORK_BLOCK") == "1":
    _original_connect = socket.socket.connect
    _original_connect_ex = socket.socket.connect_ex
    _original_getaddrinfo = socket.getaddrinfo

    def _is_loopback(host: object) -> bool:
        if str(host).lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(str(host)).is_loopback
        except ValueError:
            return False

    def _blocked() -> OSError:
        return OSError(errno.ENETUNREACH, "offline network disabled")

    def _guarded_getaddrinfo(host, *args, **kwargs):
        if host is not None and not _is_loopback(host):
            raise _blocked()
        return _original_getaddrinfo(host, *args, **kwargs)

    def _guarded_connect(sock, address):
        if sock.family != socket.AF_UNIX and isinstance(address, tuple):
            if not _is_loopback(address[0]):
                raise _blocked()
        return _original_connect(sock, address)

    def _guarded_connect_ex(sock, address):
        try:
            return _guarded_connect(sock, address)
        except OSError as error:
            return error.errno or errno.ENETUNREACH

    socket.getaddrinfo = _guarded_getaddrinfo
    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
