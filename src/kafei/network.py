from __future__ import annotations

import ctypes
from ctypes import wintypes
import socket
import sys


class _IpOptionInformation(ctypes.Structure):
    _fields_ = (
        ("ttl", ctypes.c_ubyte),
        ("tos", ctypes.c_ubyte),
        ("flags", ctypes.c_ubyte),
        ("options_size", ctypes.c_ubyte),
        ("options_data", ctypes.c_void_p),
    )


class _IcmpEchoReply(ctypes.Structure):
    _fields_ = (
        ("address", wintypes.ULONG),
        ("status", wintypes.ULONG),
        ("round_trip_time", wintypes.ULONG),
        ("data_size", wintypes.USHORT),
        ("reserved", wintypes.USHORT),
        ("data", ctypes.c_void_p),
        ("options", _IpOptionInformation),
    )


def ping_host(host: str, timeout_ms: int = 1000) -> bool:
    """Return whether a Windows ICMP echo reply was received."""
    if sys.platform != "win32":
        return False
    try:
        address_text = socket.gethostbyname(host)
        ip_helper = ctypes.WinDLL("iphlpapi.dll")
        winsock = ctypes.WinDLL("ws2_32.dll")
        ip_helper.IcmpCreateFile.restype = wintypes.HANDLE
        ip_helper.IcmpSendEcho.argtypes = (
            wintypes.HANDLE,
            wintypes.ULONG,
            ctypes.c_void_p,
            wintypes.WORD,
            ctypes.POINTER(_IpOptionInformation),
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        ip_helper.IcmpSendEcho.restype = wintypes.DWORD
        ip_helper.IcmpCloseHandle.argtypes = (wintypes.HANDLE,)
        winsock.inet_addr.argtypes = (ctypes.c_char_p,)
        winsock.inet_addr.restype = wintypes.ULONG

        handle = ip_helper.IcmpCreateFile()
        if not handle or int(handle) == ctypes.c_void_p(-1).value:
            return False
        try:
            destination = winsock.inet_addr(address_text.encode("ascii"))
            payload = ctypes.create_string_buffer(b"KAFEI")
            reply_size = ctypes.sizeof(_IcmpEchoReply) + len(payload.raw) + 8
            reply_buffer = ctypes.create_string_buffer(reply_size)
            reply_count = ip_helper.IcmpSendEcho(
                handle,
                destination,
                payload,
                len(payload.raw) - 1,
                None,
                reply_buffer,
                reply_size,
                max(1, int(timeout_ms)),
            )
            if not reply_count:
                return False
            return _IcmpEchoReply.from_buffer(reply_buffer).status == 0
        finally:
            ip_helper.IcmpCloseHandle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return False
