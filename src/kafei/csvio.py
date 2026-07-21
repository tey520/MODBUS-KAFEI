from __future__ import annotations

import copy
import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping

from .models import AddressMode, Device, Point, Project, new_id


@dataclass(slots=True)
class ValidationIssue:
    level: str
    row: int
    field: str
    value: str
    message: str
    suggestion: str = ""


@dataclass(slots=True)
class ImportResult:
    project: Project
    issues: list[ValidationIssue] = field(default_factory=list)
    added: int = 0
    updated: int = 0
    ignored: int = 0

    @property
    def error_count(self) -> int:
        return sum(issue.level == "ERROR" for issue in self.issues)

    @property
    def warning_count(self) -> int:
        return sum(issue.level == "WARNING" for issue in self.issues)

    @property
    def can_apply(self) -> bool:
        return self.error_count == 0


REQUIRED_FIELDS = (
    "device_name",
    "point_name",
    "unit_id",
    "function_code",
    "address_mode",
    "address",
    "data_type",
)


def _integer(row: dict[str, str], key: str) -> int:
    value = row.get(key, "").strip()
    if value == "":
        raise ValueError("不得空白")
    return int(value, 10)


def _float(row: dict[str, str], key: str, default: float) -> float:
    value = row.get(key, "").strip()
    return default if value == "" else float(value)


def _boolean(value: str, default: bool = True) -> bool:
    normalized = value.strip().casefold()
    if normalized == "":
        return default
    if normalized in ("1", "true", "yes", "y", "on", "啟用"):
        return True
    if normalized in ("0", "false", "no", "n", "off", "停用"):
        return False
    raise ValueError("布林值只接受 true/false、1/0、yes/no 或啟用/停用")


def _tags(value: str) -> list[str]:
    # Semicolon remains accepted for compatibility with existing templates.
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def import_points_csv(path: str | Path, base_project: Project, update_by: str = "name") -> ImportResult:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        headers = [header.strip() for header in (reader.fieldnames or []) if header]
    return import_point_rows(rows, headers, base_project, update_by=update_by)


def import_point_rows(
    rows: Iterable[Mapping[str, object]],
    headers: Iterable[str],
    base_project: Project,
    update_by: str = "name",
) -> ImportResult:
    candidate = copy.deepcopy(base_project)
    result = ImportResult(candidate)
    normalized_headers = {header.strip() for header in headers}
    for required in REQUIRED_FIELDS:
        if required not in normalized_headers:
            result.issues.append(ValidationIssue("ERROR", 1, required, "", "缺少必要欄位", f"新增欄位 {required}"))
    if result.error_count:
        return result

    devices_by_name = {device.name.casefold(): device for device in candidate.devices}
    points_by_name = {point.name.casefold(): point for point in candidate.points}
    points_by_id = {point.id: point for point in candidate.points}
    points_by_address = {
        (point.device_id, point.unit_id, point.function_code, point.raw_address): point
        for point in candidate.points
    }
    for row_number, source_row in enumerate(rows, start=2):
        row = {str(key).strip(): "" if value is None else str(value).strip() for key, value in source_row.items()}
        if not any(row.values()):
            result.ignored += 1
            continue
        try:
            missing = next((key for key in REQUIRED_FIELDS if not row.get(key, "")), None)
            if missing:
                result.issues.append(ValidationIssue("ERROR", row_number, missing, "", "必要欄位不得空白", "填入明確值"))
                continue
            mode = row["address_mode"].casefold()
            if mode not in (AddressMode.ZERO_BASED.value, AddressMode.REFERENCE.value):
                result.issues.append(
                    ValidationIssue("ERROR", row_number, "address_mode", row["address_mode"], "不得猜測地址模式", "填入 zero_based 或 reference")
                )
                continue
            device_name = row["device_name"]
            device = devices_by_name.get(device_name.casefold())
            if device is None:
                host = row.get("device_host", "")
                if not host:
                    result.issues.append(
                        ValidationIssue("ERROR", row_number, "device_host", "", "新設備必須提供 device_host", "填入 IP 或 Hostname")
                    )
                    continue
                device = Device(
                    name=device_name,
                    host=host,
                    port=int(row.get("device_port") or 502),
                    scan_interval_ms=int(row.get("device_scan_ms") or 1000),
                )
                device_errors = device.validate()
                if device_errors:
                    result.issues.extend(
                        ValidationIssue("ERROR", row_number, "device", device_name, message) for message in device_errors
                    )
                    continue
                candidate.devices.append(device)
                devices_by_name[device.name.casefold()] = device

            point_id = row.get("point_id", "")
            function_code = _integer(row, "function_code")
            data_type = row["data_type"].upper()
            default_quantity = 2 if data_type in ("INT32", "UINT32", "FLOAT32") else 1
            point = Point(
                id=point_id or new_id(),
                name=row["point_name"],
                display_name=row.get("display_name", ""),
                source_code=row.get("source_code", ""),
                device_id=device.id,
                group_path=row.get("group", ""),
                tags=_tags(row.get("tags", "")),
                unit_id=_integer(row, "unit_id"),
                function_code=function_code,
                address_mode=mode,
                address=_integer(row, "address"),
                quantity=int(row.get("quantity") or default_quantity),
                data_type=data_type,
                byte_order=(row.get("byte_order") or "ABCD").upper(),
                bit_index=int(row["bit_index"]) if row.get("bit_index", "") else None,
                scale=_float(row, "scale", 1.0),
                offset=_float(row, "offset", 0.0),
                decimals=int(row.get("decimals") or (0 if function_code in (1, 2) else 2)),
                engineering_unit=row.get("unit", ""),
                scan_interval_ms=None,
                merge_mode=row.get("merge_mode") or "inherit",
                enabled=_boolean(row.get("enabled", "")),
                description=row.get("description", ""),
            )
            if row.get("scan_ms", ""):
                result.issues.append(
                    ValidationIssue("INFO", row_number, "scan_ms", row["scan_ms"], "點位掃描週期已停用，改為跟隨通訊設備設定")
                )
            point_errors = point.validate(candidate.device_map())
            if point_errors:
                result.issues.extend(
                    ValidationIssue("ERROR", row_number, "point", point.name, message) for message in point_errors
                )
                continue

            existing: Point | None
            if update_by == "id":
                existing = points_by_id.get(point.id) if point_id else None
            elif update_by == "address":
                existing = points_by_address.get((point.device_id, point.unit_id, point.function_code, point.raw_address))
            else:
                existing = points_by_name.get(point.name.casefold())
            if existing is None:
                candidate.points.append(point)
                result.added += 1
            else:
                index = candidate.points.index(existing)
                if not point_id:
                    point.id = existing.id
                candidate.points[index] = point
                result.updated += 1
            points_by_name[point.name.casefold()] = point
            points_by_id[point.id] = point
            points_by_address[(point.device_id, point.unit_id, point.function_code, point.raw_address)] = point
        except (TypeError, ValueError) as exc:
            result.issues.append(ValidationIssue("ERROR", row_number, "row", str(source_row), str(exc), "檢查數字及布林格式"))

    _append_overlap_warnings(candidate, result)
    return result


def _append_overlap_warnings(project: Project, result: ImportResult) -> None:
    grouped: dict[tuple[str, int, int], list[Point]] = {}
    for point in project.points:
        grouped.setdefault((point.device_id, point.unit_id, point.function_code), []).append(point)
    for points in grouped.values():
        points.sort(key=lambda item: item.raw_address)
        previous: Point | None = None
        for point in points:
            if previous and point.raw_address < previous.raw_address + previous.span:
                result.issues.append(
                    ValidationIssue("WARNING", 0, "address", str(point.address), f"點位 {point.name} 與 {previous.name} 位址重疊", "確認是否刻意以不同型別解析相同資料")
                )
            if previous is None or point.raw_address + point.span > previous.raw_address + previous.span:
                previous = point


def export_latest_csv(path: str | Path, project: Project, states: Mapping[str, object]) -> None:
    target = Path(path)
    devices = project.device_map()
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("設備", "群組", "點位", "文件地址", "通訊地址", "功能碼", "工程值", "單位", "品質", "回應時間_ms", "更新時間", "錯誤"))
        for point in project.points:
            state = states.get(point.id)
            timestamp = getattr(state, "updated_at", None)
            writer.writerow(
                (
                    devices.get(point.device_id).name if point.device_id in devices else "",
                    point.group_path,
                    point.display_name or point.name,
                    point.document_address if point.document_address is not None else "",
                    point.raw_address,
                    f"{point.function_code:02d}",
                    getattr(state, "value", ""),
                    point.engineering_unit,
                    getattr(getattr(state, "quality", None), "value", "CONFIG_ERROR"),
                    getattr(state, "response_ms", ""),
                    timestamp.astimezone().isoformat(timespec="milliseconds") if timestamp else "",
                    getattr(state, "last_error", ""),
                )
            )


def export_debug_csv(path: str | Path, records: Iterable[object]) -> None:
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("時間", "設備", "Transaction ID", "Unit ID", "FC", "起始位址", "數量", "Request Hex", "Response Hex", "Exception", "回應時間_ms", "重試", "點位"))
        for record in records:
            timestamp = getattr(record, "timestamp", datetime.now().astimezone())
            writer.writerow(
                (
                    timestamp.astimezone().isoformat(timespec="milliseconds"),
                    getattr(record, "device_name", ""),
                    getattr(record, "transaction_id", ""),
                    getattr(record, "unit_id", ""),
                    getattr(record, "function_code", ""),
                    getattr(record, "start_address", ""),
                    getattr(record, "quantity", ""),
                    getattr(record, "request_hex", ""),
                    getattr(record, "response_hex", ""),
                    getattr(record, "error", ""),
                    getattr(record, "elapsed_ms", ""),
                    getattr(record, "retry", ""),
                    ";".join(getattr(record, "point_names", [])),
                )
            )


def write_csv_template(path: str | Path) -> None:
    headers = list(REQUIRED_FIELDS) + [
        "device_port", "point_id", "display_name", "group", "tags", "quantity", "byte_order",
        "bit_index", "scale", "offset", "decimals", "unit", "merge_mode", "enabled", "description",
    ]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerow(headers)
