from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


SCHEMA_VERSION = 1


class AddressMode(str, Enum):
    ZERO_BASED = "zero_based"
    REFERENCE = "reference"


class Quality(str, Enum):
    GOOD = "GOOD"
    TIMEOUT = "TIMEOUT"
    DISCONNECTED = "DISCONNECTED"
    MODBUS_ERROR = "MODBUS_ERROR"
    CONVERT_ERROR = "CONVERT_ERROR"
    STALE = "STALE"
    DISABLED = "DISABLED"
    CONFIG_ERROR = "CONFIG_ERROR"


REFERENCE_BASES = {1: 1, 2: 10001, 4: 30001, 3: 40001}
PROTOCOL_LIMITS = {1: 2000, 2: 2000, 3: 125, 4: 125}
DATA_TYPE_UNITS = {
    "BOOL": 1,
    "BIT": 1,
    "INT16": 1,
    "UINT16": 1,
    "INT32": 2,
    "UINT32": 2,
    "FLOAT32": 2,
    "HEX": 1,
    "BINARY": 1,
    "ASCII": 1,
}


def new_id() -> str:
    return str(uuid4())


def protocol_address(function_code: int, mode: AddressMode | str, address: int) -> int:
    try:
        selected = AddressMode(mode)
    except ValueError as exc:
        raise ValueError("地址模式必須是 zero_based 或 reference") from exc
    if function_code not in REFERENCE_BASES:
        raise ValueError(f"不支援的功能碼: {function_code}")
    raw = address if selected is AddressMode.ZERO_BASED else address - REFERENCE_BASES[function_code]
    if not 0 <= raw <= 65535:
        raise ValueError(f"位址超出通訊範圍: {raw}")
    if selected is AddressMode.REFERENCE and raw > 9998:
        base = REFERENCE_BASES[function_code]
        raise ValueError(f"Reference 地址必須在 {base}–{base + 9998}")
    return raw


def reference_address(function_code: int, raw_address: int) -> int:
    if function_code not in REFERENCE_BASES:
        raise ValueError(f"不支援的功能碼: {function_code}")
    if not 0 <= raw_address <= 65535:
        raise ValueError(f"位址超出通訊範圍: {raw_address}")
    if raw_address > 9998:
        raise ValueError("通訊位址無法使用傳統 5 位 Reference 表示")
    return REFERENCE_BASES[function_code] + raw_address


@dataclass(slots=True)
class Device:
    id: str = field(default_factory=new_id)
    name: str = "新設備"
    host: str = "127.0.0.1"
    port: int = 502
    connect_timeout: float = 3.0
    request_timeout: float = 2.0
    retries: int = 1
    scan_interval_ms: int = 1000
    max_concurrent_requests: int = 1
    connection_mode: str = "persistent"
    merge_mode: str = "auto"
    max_read_block: int = 125
    allowed_gap: int = 0
    enabled: bool = True
    group: str = ""
    notes: str = ""

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.name.strip():
            errors.append("設備名稱不得空白")
        if not self.host.strip():
            errors.append("IP/Hostname 不得空白")
        if not 1 <= self.port <= 65535:
            errors.append("TCP Port 必須在 1–65535")
        if self.connect_timeout <= 0 or self.request_timeout <= 0:
            errors.append("逾時必須大於 0")
        if self.retries < 0:
            errors.append("重試次數不得小於 0")
        if self.scan_interval_ms < 50:
            errors.append("設備掃描週期不得小於 50 ms")
        if self.max_concurrent_requests != 1:
            errors.append("v0.1 每台設備固定為 1 個同時請求")
        if self.connection_mode != "persistent":
            errors.append("v0.1 連線模式只支援 persistent")
        if self.merge_mode not in ("auto", "strict", "none"):
            errors.append("合併模式只支援 auto、strict 或 none")
        if self.max_read_block < 1:
            errors.append("最大讀取區塊必須大於 0")
        if self.allowed_gap < 0:
            errors.append("允許空白位址數不得小於 0")
        return errors


@dataclass(slots=True)
class Point:
    id: str = field(default_factory=new_id)
    name: str = "新點位"
    display_name: str = ""
    source_code: str = ""
    device_id: str = ""
    group_path: str = ""
    tags: list[str] = field(default_factory=list)
    unit_id: int = 1
    function_code: int = 3
    address_mode: str = AddressMode.ZERO_BASED.value
    address: int = 0
    quantity: int = 1
    data_type: str = "UINT16"
    byte_order: str = "ABCD"
    bit_index: int | None = None
    scale: float = 1.0
    offset: float = 0.0
    decimals: int = 2
    engineering_unit: str = ""
    scan_interval_ms: int | None = None
    merge_mode: str = "inherit"
    enabled: bool = True
    description: str = ""
    invalid_values: list[float] = field(default_factory=list)

    @property
    def raw_address(self) -> int:
        return protocol_address(self.function_code, self.address_mode, self.address)

    @property
    def document_address(self) -> int | None:
        try:
            return reference_address(self.function_code, self.raw_address)
        except ValueError:
            return None

    @property
    def span(self) -> int:
        return max(self.quantity, DATA_TYPE_UNITS.get(self.data_type.upper(), 1))

    def effective_interval(self, device: Device) -> int:
        return device.scan_interval_ms

    def validate(self, devices: dict[str, Device]) -> list[str]:
        errors: list[str] = []
        if not self.name.strip():
            errors.append("點位名稱不得空白")
        if self.device_id not in devices:
            errors.append("點位未指定有效設備")
        if not 0 <= self.unit_id <= 255:
            errors.append("Unit ID 必須在 0–255")
        if self.function_code not in PROTOCOL_LIMITS:
            errors.append("功能碼只支援 1、2、3、4")
        try:
            raw = self.raw_address
            if raw + self.span - 1 > 65535:
                errors.append("點位範圍超出 65535")
        except ValueError as exc:
            errors.append(str(exc))
        data_type = self.data_type.upper()
        if data_type not in DATA_TYPE_UNITS:
            errors.append(f"不支援的資料型別: {self.data_type}")
        if self.function_code in (1, 2) and data_type != "BOOL":
            errors.append("FC01/FC02 只支援 BOOL")
        expected_quantity = DATA_TYPE_UNITS.get(data_type, 1)
        if data_type in ("ASCII", "HEX", "BINARY") and self.function_code in (3, 4):
            if self.quantity < 1:
                errors.append("占用位址數必須大於 0")
        elif self.quantity != expected_quantity:
            errors.append(f"{data_type} 占用位址數必須是 {expected_quantity}")
        if self.quantity > PROTOCOL_LIMITS.get(self.function_code, 0):
            errors.append("占用位址數超出單次協議限制")
        device = devices.get(self.device_id)
        if device and self.span > min(PROTOCOL_LIMITS.get(self.function_code, 0), device.max_read_block):
            errors.append("點位占用位址數超出設備最大讀取區塊")
        if self.byte_order not in ("ABCD", "BADC", "CDAB", "DCBA"):
            errors.append("Byte Order 無效")
        if self.function_code in (3, 4) and data_type == "BIT":
            if self.bit_index is None:
                errors.append("BIT 資料型別必須指定 Bit Index")
            elif not 0 <= self.bit_index <= 15:
                errors.append("Bit Index 必須在 0–15")
        elif self.bit_index is not None:
            errors.append("只有 FC03/FC04 的 BIT 資料型別可以設定 Bit Index")
        if not 0 <= self.decimals <= 12:
            errors.append("小數位數必須在 0–12")
        if self.merge_mode not in ("inherit", "none"):
            errors.append("點位合併模式只支援 inherit 或 none")
        return errors


@dataclass(slots=True)
class Project:
    name: str = "未命名專案"
    devices: list[Device] = field(default_factory=list)
    points: list[Point] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    display: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def device_map(self) -> dict[str, Device]:
        return {item.id: item for item in self.devices}

    def validate(self) -> list[str]:
        errors: list[str] = []
        device_ids: set[str] = set()
        device_names: set[str] = set()
        for device in self.devices:
            errors.extend(f"設備 {device.name}: {message}" for message in device.validate())
            if device.id in device_ids:
                errors.append(f"設備 ID 重複: {device.id}")
            if device.name.casefold() in device_names:
                errors.append(f"設備名稱重複: {device.name}")
            device_ids.add(device.id)
            device_names.add(device.name.casefold())
        point_ids: set[str] = set()
        point_names: set[str] = set()
        devices = self.device_map()
        for point in self.points:
            errors.extend(f"點位 {point.name}: {message}" for message in point.validate(devices))
            if point.id in point_ids:
                errors.append(f"點位 ID 重複: {point.id}")
            if point.name.casefold() in point_names:
                errors.append(f"點位名稱重複: {point.name}")
            point_ids.add(point.id)
            point_names.add(point.name.casefold())
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "devices": [asdict(item) for item in self.devices],
            "points": [asdict(item) for item in self.points],
            "groups": self.groups,
            "display": self.display,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        version = int(data.get("schema_version", 0))
        if version > SCHEMA_VERSION:
            raise ValueError(f"專案格式版本 {version} 高於本程式支援的 {SCHEMA_VERSION}，拒絕開啟以避免破壞")
        if version < 1:
            raise ValueError("無法識別或過舊的專案格式")
        return cls(
            name=str(data.get("name", "未命名專案")),
            devices=[Device(**item) for item in data.get("devices", [])],
            points=[Point(**item) for item in data.get("points", [])],
            groups=list(data.get("groups", [])),
            display=dict(data.get("display", {})),
            schema_version=SCHEMA_VERSION,
        )
