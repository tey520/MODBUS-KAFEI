from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
import socket
import threading
import time
from typing import Callable

from .codec import decode_point
from .merge import ReadBlock, build_read_blocks
from .models import Point, Project, Quality
from .modbus import ModbusProtocolError, ModbusTcpClient, ReadResponse
from .network import ping_host


RECONNECT_DELAYS = (1.0, 2.0, 5.0, 10.0, 30.0)
PING_INTERVAL_SECONDS = 5.0
PING_TIMEOUT_MS = 1000


@dataclass(slots=True)
class PointState:
    value: object = None
    quality: Quality = Quality.STALE
    updated_at: datetime | None = None
    response_ms: float | None = None
    last_error: str = ""


@dataclass(slots=True)
class DeviceStats:
    successful: int = 0
    failed: int = 0
    consecutive_failures: int = 0
    total_response_ms: float = 0.0
    max_response_ms: float = 0.0
    last_response_ms: float | None = None
    last_error: str = ""
    next_reconnect_at: float = 0.0
    ping_reachable: bool | None = None

    @property
    def average_response_ms(self) -> float:
        return self.total_response_ms / self.successful if self.successful else 0.0


@dataclass(slots=True)
class DebugRecord:
    timestamp: datetime
    device_name: str
    transaction_id: int | None
    unit_id: int
    function_code: int
    start_address: int
    quantity: int
    request_hex: str
    response_hex: str
    error: str
    exception_code: int | None
    elapsed_ms: float | None
    retry: int
    point_names: list[str]


class PollingEngine:
    def __init__(
        self,
        project: Project,
        *,
        debug_capacity: int = 2000,
        on_update: Callable[[], None] | None = None,
        ping_probe: Callable[[str, int], bool] | None = None,
        ping_interval_seconds: float = PING_INTERVAL_SECONDS,
    ) -> None:
        self.project = project
        self.debug_records: deque[DebugRecord] = deque(maxlen=max(100, debug_capacity))
        self._states = {point.id: PointState(quality=Quality.DISABLED if not point.enabled else Quality.STALE) for point in project.points}
        self._stats = {device.id: DeviceStats() for device in project.devices}
        self._clients: dict[str, ModbusTcpClient] = {}
        self._blocks: list[ReadBlock] = []
        self._blocks_by_device: dict[str, list[ReadBlock]] = {}
        self._next_due: dict[str, float] = {}
        self._inflight_devices: set[str] = set()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._scheduler: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._ping_executor: ThreadPoolExecutor | None = None
        self._next_ping_due: dict[str, float] = {}
        self._inflight_pings: set[str] = set()
        self._ping_probe = ping_probe or ping_host
        self._ping_interval_seconds = max(1.0, ping_interval_seconds)
        self._on_update = on_update

    @property
    def running(self) -> bool:
        return self._scheduler is not None and self._scheduler.is_alive()

    @property
    def blocks(self) -> list[ReadBlock]:
        return list(self._blocks)

    def start(self) -> None:
        if self.running:
            return
        devices = self.project.device_map()
        for point in self.project.points:
            errors = point.validate(devices)
            if errors:
                self._states[point.id] = PointState(quality=Quality.CONFIG_ERROR, last_error="; ".join(errors))
            elif not point.enabled or not devices[point.device_id].enabled:
                self._states[point.id] = PointState(quality=Quality.DISABLED)
        self._blocks = build_read_blocks(self.project)
        self._blocks_by_device = {}
        for block in self._blocks:
            self._blocks_by_device.setdefault(block.device_id, []).append(block)
        now = time.monotonic()
        self._next_due = {block.key: now for block in self._blocks}
        enabled_devices = [item for item in self.project.devices if item.enabled]
        worker_count = max(1, min(16, len(enabled_devices)))
        self._executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="kafei-modbus")
        if enabled_devices:
            ping_workers = max(1, min(8, len(enabled_devices)))
            self._ping_executor = ThreadPoolExecutor(max_workers=ping_workers, thread_name_prefix="kafei-ping")
            stagger = min(0.1, 1.0 / len(enabled_devices))
            self._next_ping_due = {device.id: now + index * stagger for index, device in enumerate(enabled_devices)}
        self._stop_event.clear()
        self._scheduler = threading.Thread(target=self._schedule_loop, name="kafei-scheduler", daemon=True)
        self._scheduler.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        for client in list(self._clients.values()):
            client.close()
        scheduler, self._scheduler = self._scheduler, None
        if scheduler is not None and scheduler is not threading.current_thread():
            scheduler.join(timeout=max(0.0, timeout))
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        ping_executor, self._ping_executor = self._ping_executor, None
        if ping_executor is not None:
            ping_executor.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            self._inflight_devices.clear()
            self._inflight_pings.clear()

    def _schedule_loop(self) -> None:
        devices = self.project.device_map()
        while not self._stop_event.wait(0.05):
            now = time.monotonic()
            self._schedule_pings(devices, now)
            for device_id, device_blocks in self._blocks_by_device.items():
                with self._lock:
                    if device_id in self._inflight_devices:
                        continue
                    stats = self._stats[device_id]
                    if stats.next_reconnect_at > now:
                        continue
                    due = [block for block in device_blocks if self._next_due[block.key] <= now]
                    if not due:
                        continue
                    due.sort(key=lambda item: self._next_due[item.key])
                    block = due[0]
                    self._next_due[block.key] = now + block.interval_ms / 1000.0
                    self._inflight_devices.add(device_id)
                executor = self._executor
                if executor is None:
                    return
                try:
                    future = executor.submit(self._poll_block, block, devices[device_id])
                    future.add_done_callback(lambda item, selected=device_id: self._poll_finished(selected, item))
                except RuntimeError:
                    with self._lock:
                        self._inflight_devices.discard(device_id)
                    return

    def _schedule_pings(self, devices: dict[str, object], now: float) -> None:
        executor = self._ping_executor
        if executor is None:
            return
        for device_id, due_at in tuple(self._next_ping_due.items()):
            if due_at > now:
                continue
            with self._lock:
                if device_id in self._inflight_pings:
                    continue
                self._inflight_pings.add(device_id)
                self._stats[device_id].ping_reachable = None
                self._next_ping_due[device_id] = now + self._ping_interval_seconds
            try:
                future = executor.submit(self._ping_probe, getattr(devices[device_id], "host"), PING_TIMEOUT_MS)
                future.add_done_callback(lambda item, selected=device_id: self._ping_finished(selected, item))
            except RuntimeError:
                with self._lock:
                    self._inflight_pings.discard(device_id)
                return

    def _ping_finished(self, device_id: str, future: Future[bool]) -> None:
        try:
            reachable = bool(future.result())
        except Exception:
            reachable = False
        with self._lock:
            if device_id in self._stats:
                self._stats[device_id].ping_reachable = reachable
            self._inflight_pings.discard(device_id)
        self._notify()

    def _poll_finished(self, device_id: str, future: Future[None]) -> None:
        try:
            future.result()
        except Exception as exc:  # defensive containment: a worker must not kill scheduling
            with self._lock:
                stats = self._stats[device_id]
                stats.last_error = f"內部輪詢錯誤: {exc}"
        finally:
            with self._lock:
                self._inflight_devices.discard(device_id)
        self._notify()

    def _client_for(self, device: object) -> ModbusTcpClient:
        device_id = getattr(device, "id")
        client = self._clients.get(device_id)
        if client is None:
            client = ModbusTcpClient(
                getattr(device, "host"), getattr(device, "port"), getattr(device, "connect_timeout"), getattr(device, "request_timeout")
            )
            self._clients[device_id] = client
        return client

    def _poll_block(self, block: ReadBlock, device: object) -> None:
        client = self._client_for(device)
        response: ReadResponse | None = None
        last_error: Exception | None = None
        for attempt in range(getattr(device, "retries") + 1):
            if self._stop_event.is_set():
                return
            try:
                response = client.read(block.unit_id, block.function_code, block.start_address, block.quantity)
                self._record_debug(block, getattr(device, "name"), response, None, attempt)
                break
            except (OSError, ModbusProtocolError) as exc:
                last_error = exc
                self._record_debug(block, getattr(device, "name"), None, exc, attempt)
                if isinstance(exc, ModbusProtocolError) and exc.exception_code is not None:
                    break
        if response is None:
            self._mark_block_failure(block, getattr(device, "id"), last_error or RuntimeError("未知通訊錯誤"), client)
            return
        with self._lock:
            stats = self._stats[getattr(device, "id")]
            stats.successful += 1
            stats.consecutive_failures = 0
            stats.last_error = ""
            stats.next_reconnect_at = 0.0
            stats.last_response_ms = response.elapsed_ms
            stats.total_response_ms += response.elapsed_ms
            stats.max_response_ms = max(stats.max_response_ms, response.elapsed_ms)
        for point in block.points:
            offset = point.raw_address - block.start_address
            point_values = response.values[offset : offset + point.span]
            try:
                value = decode_point(point, point_values)
                state = PointState(value, Quality.GOOD, datetime.now().astimezone(), response.elapsed_ms, "")
            except (TypeError, ValueError) as exc:
                previous = self._states.get(point.id, PointState())
                state = PointState(previous.value, Quality.CONVERT_ERROR, previous.updated_at, response.elapsed_ms, str(exc))
            with self._lock:
                self._states[point.id] = state

    def _mark_block_failure(self, block: ReadBlock, device_id: str, error: Exception, client: ModbusTcpClient) -> None:
        if isinstance(error, (TimeoutError, socket.timeout)):
            quality = Quality.TIMEOUT
        elif isinstance(error, ModbusProtocolError):
            quality = Quality.MODBUS_ERROR
        else:
            quality = Quality.DISCONNECTED
        now = time.monotonic()
        with self._lock:
            stats = self._stats[device_id]
            stats.failed += 1
            stats.consecutive_failures += 1
            stats.last_error = str(error)
            if quality in (Quality.TIMEOUT, Quality.DISCONNECTED):
                delay = RECONNECT_DELAYS[min(stats.consecutive_failures - 1, len(RECONNECT_DELAYS) - 1)]
                stats.next_reconnect_at = now + delay
            for point in block.points:
                previous = self._states.get(point.id, PointState())
                self._states[point.id] = PointState(previous.value, quality, previous.updated_at, None, str(error))
        if quality in (Quality.TIMEOUT, Quality.DISCONNECTED):
            client.close()

    def _record_debug(self, block: ReadBlock, device_name: str, response: ReadResponse | None, error: Exception | None, retry: int) -> None:
        request_bytes = response.request if response else getattr(error, "request", b"")
        response_bytes = response.response if response else getattr(error, "response", b"")
        record = DebugRecord(
            timestamp=datetime.now().astimezone(),
            device_name=device_name,
            transaction_id=response.transaction_id if response else getattr(error, "transaction_id", None),
            unit_id=block.unit_id,
            function_code=block.function_code,
            start_address=block.start_address,
            quantity=block.quantity,
            request_hex=request_bytes.hex(" ").upper() if request_bytes else "",
            response_hex=response_bytes.hex(" ").upper() if response_bytes else "",
            error=str(error) if error else "",
            exception_code=getattr(error, "exception_code", None),
            elapsed_ms=response.elapsed_ms if response else getattr(error, "elapsed_ms", None),
            retry=retry,
            point_names=[point.name for point in block.points],
        )
        with self._lock:
            self.debug_records.append(record)

    def states_snapshot(self, *, mark_stale: bool = True) -> dict[str, PointState]:
        devices = self.project.device_map()
        now = datetime.now().astimezone()
        with self._lock:
            snapshot = {key: replace(value) for key, value in self._states.items()}
        if mark_stale:
            points = {point.id: point for point in self.project.points}
            for point_id, state in snapshot.items():
                point = points.get(point_id)
                if point and state.quality is Quality.GOOD and state.updated_at:
                    interval = point.effective_interval(devices[point.device_id])
                    if (now - state.updated_at).total_seconds() * 1000 > max(1000, interval * 2.5):
                        state.quality = Quality.STALE
        return snapshot

    def stats_snapshot(self) -> dict[str, DeviceStats]:
        with self._lock:
            return {key: replace(value) for key, value in self._stats.items()}

    def debug_snapshot(self) -> list[DebugRecord]:
        with self._lock:
            return [replace(item) for item in self.debug_records]

    def clear_debug(self) -> None:
        with self._lock:
            self.debug_records.clear()

    def _notify(self) -> None:
        if self._on_update:
            try:
                self._on_update()
            except Exception:
                pass
