from __future__ import annotations

import math
import struct
from typing import Sequence

from .models import Point


def _ordered_bytes(raw: bytes, order: str) -> bytes:
    if order == "ABCD":
        return raw
    if order == "BADC":
        swapped = bytearray()
        for index in range(0, len(raw), 2):
            swapped.extend(raw[index : index + 2][::-1])
        return bytes(swapped)
    if order == "CDAB":
        words = [raw[index : index + 2] for index in range(0, len(raw), 2)]
        return b"".join(reversed(words))
    if order == "DCBA":
        return raw[::-1]
    raise ValueError(f"不支援的 Byte Order: {order}")


def _register_bytes(registers: Sequence[int]) -> bytes:
    if any(not 0 <= value <= 0xFFFF for value in registers):
        raise ValueError("Register 值超出 0–65535")
    return b"".join(struct.pack(">H", value) for value in registers)


def decode_point(point: Point, values: Sequence[int | bool]) -> object:
    """Decode a point-sized slice returned by a Modbus read block."""
    kind = point.data_type.upper()
    if point.function_code in (1, 2):
        if not values:
            raise ValueError("回應不含所需 bit")
        if kind == "BINARY" and len(values) > 1:
            decoded: object = "".join("1" if bool(item) else "0" for item in values)
        else:
            decoded = bool(values[0])
    else:
        registers = [int(item) for item in values]
        if not registers:
            raise ValueError("回應不含所需 register")
        raw = _register_bytes(registers)
        if kind in ("INT16", "UINT16", "BOOL", "BIT"):
            data = _ordered_bytes(raw[:2], point.byte_order)
            unsigned = struct.unpack(">H", data)[0]
            if kind == "INT16":
                decoded = struct.unpack(">h", data)[0]
            elif kind == "UINT16":
                decoded = unsigned
            elif kind == "BOOL":
                if unsigned not in (0, 1):
                    raise ValueError(f"BOOL 嚴格模式只接受 0 或 1，收到 {unsigned}")
                decoded = bool(unsigned)
            else:
                if point.bit_index is None:
                    raise ValueError("BIT 資料型別必須指定 Bit Index")
                decoded = bool((unsigned >> point.bit_index) & 1)
        elif kind in ("INT32", "UINT32", "FLOAT32"):
            if len(raw) < 4:
                raise ValueError(f"{kind} 需要兩個 registers")
            data = _ordered_bytes(raw[:4], point.byte_order)
            format_code = {"INT32": ">i", "UINT32": ">I", "FLOAT32": ">f"}[kind]
            decoded = struct.unpack(format_code, data)[0]
            if isinstance(decoded, float) and not math.isfinite(decoded):
                raise ValueError("浮點值為 NaN 或 Infinity")
        elif kind == "HEX":
            decoded = raw.hex(" ").upper()
        elif kind == "BINARY":
            decoded = " ".join(f"{value:016b}" for value in registers)
        elif kind == "ASCII":
            decoded = raw.rstrip(b"\x00").decode("ascii", errors="replace")
        else:
            raise ValueError(f"不支援的資料型別: {kind}")

    if isinstance(decoded, (int, float)) and not isinstance(decoded, bool):
        if any(decoded == invalid for invalid in point.invalid_values):
            raise ValueError(f"解析值 {decoded} 被設定為無效值")
        decoded = decoded * point.scale + point.offset
        if isinstance(decoded, float):
            decoded = round(decoded, point.decimals)
    return decoded


def format_value(value: object, decimals: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    if isinstance(value, float):
        return f"{value:.{decimals}f}"
    return str(value)
