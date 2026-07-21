from __future__ import annotations

from dataclasses import dataclass
import socket
import struct
import threading
import time


class ModbusError(Exception):
    """Base Modbus error."""


class ModbusProtocolError(ModbusError):
    def __init__(
        self,
        message: str,
        *,
        exception_code: int | None = None,
        request: bytes = b"",
        response: bytes = b"",
        transaction_id: int | None = None,
        elapsed_ms: float | None = None,
    ) -> None:
        super().__init__(message)
        self.exception_code = exception_code
        self.request = request
        self.response = response
        self.transaction_id = transaction_id
        self.elapsed_ms = elapsed_ms


@dataclass(slots=True)
class ReadResponse:
    values: list[int | bool]
    request: bytes
    response: bytes
    elapsed_ms: float
    transaction_id: int


class ModbusTcpClient:
    """Small persistent Modbus TCP client for FC01–FC04."""

    def __init__(self, host: str, port: int, connect_timeout: float, request_timeout: float) -> None:
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self._socket: socket.socket | None = None
        self._transaction_id = 0
        self._lock = threading.Lock()

    def close(self) -> None:
        # Deliberately do not wait for the request lock: shutdown must be able to
        # interrupt a blocked recv during application stop.
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def _connect(self) -> socket.socket:
        if self._socket is None:
            sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
            sock.settimeout(self.request_timeout)
            self._socket = sock
        return self._socket

    @staticmethod
    def _receive_exact(sock: socket.socket, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = sock.recv(size - len(chunks))
            if not chunk:
                raise ConnectionError("遠端已關閉連線")
            chunks.extend(chunk)
        return bytes(chunks)

    def read(self, unit_id: int, function_code: int, start_address: int, quantity: int) -> ReadResponse:
        if function_code not in (1, 2, 3, 4):
            raise ValueError("read 只支援 FC01–FC04")
        max_quantity = 2000 if function_code in (1, 2) else 125
        if not 1 <= quantity <= max_quantity:
            raise ValueError(f"讀取數量必須在 1–{max_quantity}")
        if not 0 <= start_address <= 65535 or start_address + quantity - 1 > 65535:
            raise ValueError("讀取範圍超出 0–65535")
        with self._lock:
            self._transaction_id = (self._transaction_id + 1) & 0xFFFF
            transaction_id = self._transaction_id
            pdu = struct.pack(">BHH", function_code, start_address, quantity)
            header = struct.pack(">HHHB", transaction_id, 0, len(pdu) + 1, unit_id)
            request = header + pdu
            started = time.perf_counter()
            try:
                sock = self._connect()
                sock.sendall(request)
                response_header = self._receive_exact(sock, 7)
                response_tid, protocol_id, length, response_unit = struct.unpack(">HHHB", response_header)
                if length < 2 or length > 260:
                    raise ModbusProtocolError(f"MBAP 長度無效: {length}")
                response_pdu = self._receive_exact(sock, length - 1)
                response = response_header + response_pdu
            except (OSError, ModbusError) as exc:
                try:
                    setattr(exc, "request", request)
                    setattr(exc, "transaction_id", transaction_id)
                    setattr(exc, "elapsed_ms", (time.perf_counter() - started) * 1000)
                except (AttributeError, TypeError):
                    pass
                sock, self._socket = self._socket, None
                if sock is not None:
                    sock.close()
                raise
            elapsed_ms = (time.perf_counter() - started) * 1000
            def protocol_error(message: str, exception_code: int | None = None) -> ModbusProtocolError:
                error = ModbusProtocolError(
                    message,
                    exception_code=exception_code,
                    request=request,
                    response=response,
                    transaction_id=transaction_id,
                    elapsed_ms=elapsed_ms,
                )
                if exception_code is None:
                    self.close()
                return error
            if response_tid != transaction_id:
                raise protocol_error(f"Transaction ID 不一致: {response_tid} != {transaction_id}")
            if protocol_id != 0 or response_unit != unit_id:
                raise protocol_error("MBAP Protocol ID 或 Unit ID 不一致")
            response_fc = response_pdu[0]
            if response_fc == function_code | 0x80:
                code = response_pdu[1] if len(response_pdu) > 1 else None
                raise protocol_error(f"Modbus Exception: {code}", code)
            if response_fc != function_code or len(response_pdu) < 2:
                raise protocol_error("回應功能碼或長度無效")
            byte_count = response_pdu[1]
            data = response_pdu[2:]
            if byte_count != len(data):
                raise protocol_error("Byte Count 與實際資料長度不一致")
            if function_code in (1, 2):
                if len(data) < (quantity + 7) // 8:
                    raise protocol_error("Bit 回應資料不足")
                values = [bool(data[index // 8] & (1 << (index % 8))) for index in range(quantity)]
            else:
                if byte_count != quantity * 2:
                    raise protocol_error("Register 回應資料長度不符")
                values = list(struct.unpack(f">{quantity}H", data))
            return ReadResponse(values, request, response, elapsed_ms, transaction_id)
