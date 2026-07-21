from __future__ import annotations

import socket
import socketserver
import struct
import threading
import time
import unittest

from kafei.engine import PollingEngine
from kafei.models import Device, Point, Project, Quality
from kafei.modbus import ModbusProtocolError, ModbusTcpClient


def receive_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            return b""
        data.extend(chunk)
    return bytes(data)


class ModbusHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        while True:
            header = receive_exact(self.request, 7)
            if not header:
                return
            tid, protocol, length, unit = struct.unpack(">HHHB", header)
            pdu = receive_exact(self.request, length - 1)
            if not pdu:
                return
            fc, start, quantity = struct.unpack(">BHH", pdu)
            if getattr(self.server, "drop_responses", False):
                return
            if unit == 99:
                response_pdu = bytes((fc | 0x80, 2))
            elif fc in (1, 2):
                payload = bytearray((quantity + 7) // 8)
                for index in range(quantity):
                    if (start + index) % 2:
                        payload[index // 8] |= 1 << (index % 8)
                response_pdu = bytes((fc, len(payload))) + bytes(payload)
            elif fc in (3, 4):
                payload = b"".join(struct.pack(">H", start + index + 100) for index in range(quantity))
                response_pdu = bytes((fc, len(payload))) + payload
            else:
                response_pdu = bytes((fc | 0x80, 1))
            response_header = struct.pack(">HHHB", tid, protocol, len(response_pdu) + 1, unit)
            # Send in pieces to verify receive_exact rather than relying on packet boundaries.
            self.request.sendall(response_header[:3])
            self.request.sendall(response_header[3:] + response_pdu)


class ThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class HangingHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        header = receive_exact(self.request, 7)
        if header:
            _tid, _protocol, length, _unit = struct.unpack(">HHHB", header)
            receive_exact(self.request, length - 1)
            self.request.recv(1)


class ModbusIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingServer(("127.0.0.1", 0), ModbusHandler)
        cls.server.drop_responses = False
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def client(self) -> ModbusTcpClient:
        return ModbusTcpClient("127.0.0.1", self.port, 0.5, 0.5)

    def test_fc01_to_fc04(self) -> None:
        client = self.client()
        try:
            self.assertEqual(client.read(1, 1, 0, 5).values, [False, True, False, True, False])
            self.assertEqual(client.read(1, 2, 1, 3).values, [True, False, True])
            self.assertEqual(client.read(1, 3, 5, 2).values, [105, 106])
            self.assertEqual(client.read(1, 4, 7, 1).values, [107])
        finally:
            client.close()

    def test_modbus_exception_is_reported(self) -> None:
        client = self.client()
        try:
            with self.assertRaises(ModbusProtocolError) as caught:
                client.read(99, 3, 0, 1)
            self.assertEqual(caught.exception.exception_code, 2)
            self.assertTrue(caught.exception.request)
            self.assertTrue(caught.exception.response)
        finally:
            client.close()

    def test_engine_merges_updates_and_records_debug(self) -> None:
        device = Device(name="Mock", host="127.0.0.1", port=self.port, scan_interval_ms=50, retries=0, max_read_block=125)
        points = [
            Point(name="A", device_id=device.id, address=0, scan_interval_ms=50),
            Point(name="B", device_id=device.id, address=1, scan_interval_ms=50),
        ]
        engine = PollingEngine(Project(devices=[device], points=points), debug_capacity=100)
        engine.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            states = engine.states_snapshot()
            if all(state.quality is Quality.GOOD for state in states.values()):
                break
            time.sleep(0.02)
        engine.stop()
        states = engine.states_snapshot(mark_stale=False)
        self.assertEqual([states[point.id].value for point in points], [100, 101])
        self.assertTrue(all(state.quality is Quality.GOOD for state in states.values()))
        self.assertEqual(len(engine.blocks), 1)
        self.assertGreaterEqual(len(engine.debug_snapshot()), 1)

    def test_old_value_is_preserved_but_quality_is_not_good(self) -> None:
        device = Device(name="Drop", host="127.0.0.1", port=self.port, scan_interval_ms=50, request_timeout=0.1, retries=0)
        point = Point(name="P", device_id=device.id, address=0, scan_interval_ms=50)
        engine = PollingEngine(Project(devices=[device], points=[point]))
        engine.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and engine.states_snapshot()[point.id].quality is not Quality.GOOD:
            time.sleep(0.02)
        good = engine.states_snapshot(mark_stale=False)[point.id]
        self.assertEqual((good.value, good.quality), (100, Quality.GOOD))
        self.server.drop_responses = True
        try:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and engine.states_snapshot(mark_stale=False)[point.id].quality is Quality.GOOD:
                time.sleep(0.02)
            failed = engine.states_snapshot(mark_stale=False)[point.id]
        finally:
            self.server.drop_responses = False
            engine.stop()
        self.assertEqual(failed.value, 100)
        self.assertIsNot(failed.quality, Quality.GOOD)
        self.assertEqual(failed.updated_at, good.updated_at)

    def test_stop_interrupts_blocked_socket(self) -> None:
        server = ThreadingServer(("127.0.0.1", 0), HangingHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        device = Device(name="Hang", host="127.0.0.1", port=server.server_address[1], request_timeout=10, retries=0, scan_interval_ms=50)
        point = Point(name="P", device_id=device.id, scan_interval_ms=50)
        engine = PollingEngine(Project(devices=[device], points=[point]))
        try:
            engine.start()
            time.sleep(0.15)
            started = time.perf_counter()
            engine.stop(timeout=5)
            elapsed = time.perf_counter() - started
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertLess(elapsed, 1.0)

    def test_disconnected_device_uses_backoff(self) -> None:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        unused_port = probe.getsockname()[1]
        probe.close()
        device = Device(name="Offline", host="127.0.0.1", port=unused_port, connect_timeout=0.1, request_timeout=0.1, retries=0, scan_interval_ms=50)
        point = Point(name="P", device_id=device.id, scan_interval_ms=50)
        engine = PollingEngine(Project(devices=[device], points=[point]))
        engine.start()
        time.sleep(0.45)
        stats = engine.stats_snapshot()[device.id]
        state = engine.states_snapshot(mark_stale=False)[point.id]
        engine.stop()
        self.assertEqual(stats.failed, 1)
        self.assertGreater(stats.next_reconnect_at, time.monotonic())
        # Windows may surface an unused/rejected endpoint as either 10060
        # (timeout) or 10061 (connection refused), both require backoff.
        self.assertIn(state.quality, (Quality.TIMEOUT, Quality.DISCONNECTED))
        self.assertTrue(engine.debug_snapshot()[0].request_hex)


if __name__ == "__main__":
    unittest.main()
