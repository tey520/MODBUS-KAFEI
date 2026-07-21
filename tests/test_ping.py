from __future__ import annotations

import sys
import threading
import time
import unittest

from kafei.engine import PollingEngine
from kafei.models import Device, Project
from kafei.network import ping_host


class PingTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows ICMP implementation")
    def test_localhost_is_reachable(self) -> None:
        self.assertTrue(ping_host("127.0.0.1", 500))

    def test_engine_is_blank_while_detecting_then_updates(self) -> None:
        device = Device(name="Ping", host="reachable.test")
        started = threading.Event()
        release = threading.Event()

        def probe(host: str, timeout_ms: int) -> bool:
            self.assertEqual((host, timeout_ms), (device.host, 1000))
            started.set()
            release.wait(1)
            return True

        engine = PollingEngine(
            Project(devices=[device]),
            ping_probe=probe,
            ping_interval_seconds=60,
        )
        try:
            engine.start()
            self.assertTrue(started.wait(1))
            self.assertIsNone(engine.stats_snapshot()[device.id].ping_reachable)
            release.set()
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and engine.stats_snapshot()[device.id].ping_reachable is None:
                time.sleep(0.01)
            self.assertIs(engine.stats_snapshot()[device.id].ping_reachable, True)
        finally:
            release.set()
            engine.stop()

    def test_unreachable_result_is_false_without_counts(self) -> None:
        device = Device(name="Ping", host="unreachable.test")
        engine = PollingEngine(
            Project(devices=[device]),
            ping_probe=lambda _host, _timeout: False,
            ping_interval_seconds=60,
        )
        try:
            engine.start()
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and engine.stats_snapshot()[device.id].ping_reachable is None:
                time.sleep(0.01)
            stats = engine.stats_snapshot()[device.id]
            self.assertIs(stats.ping_reachable, False)
            self.assertEqual((stats.successful, stats.failed), (0, 0))
        finally:
            engine.stop()


if __name__ == "__main__":
    unittest.main()
