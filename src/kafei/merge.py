from __future__ import annotations

from dataclasses import dataclass, field
from itertools import groupby

from .models import Device, Point, Project, PROTOCOL_LIMITS


@dataclass(slots=True)
class ReadBlock:
    device_id: str
    unit_id: int
    function_code: int
    start_address: int
    quantity: int
    interval_ms: int
    points: list[Point] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.device_id}:{self.unit_id}:{self.function_code}:{self.start_address}:{self.quantity}:{self.interval_ms}"


def _point_group_key(point: Point, device: Device) -> tuple[object, ...]:
    no_merge = device.merge_mode == "none" or point.merge_mode == "none"
    unique = point.id if no_merge else ""
    return (point.device_id, point.unit_id, point.function_code, point.effective_interval(device), unique)


def build_read_blocks(project: Project) -> list[ReadBlock]:
    devices = project.device_map()
    valid_points = [
        point
        for point in project.points
        if point.enabled
        and point.device_id in devices
        and devices[point.device_id].enabled
        and not point.validate(devices)
    ]
    valid_points.sort(key=lambda item: (_point_group_key(item, devices[item.device_id]), item.raw_address, item.span))
    blocks: list[ReadBlock] = []
    for group_key, members_iter in groupby(valid_points, key=lambda item: _point_group_key(item, devices[item.device_id])):
        members = list(members_iter)
        device = devices[members[0].device_id]
        fc = members[0].function_code
        protocol_max = PROTOCOL_LIMITS[fc]
        block_limit = min(protocol_max, device.max_read_block)
        allowed_gap = 0 if device.merge_mode == "strict" else device.allowed_gap
        current: ReadBlock | None = None
        for point in members:
            start = point.raw_address
            end = start + point.span
            if point.span > block_limit:
                continue
            if current is None:
                current = ReadBlock(point.device_id, point.unit_id, fc, start, point.span, point.effective_interval(device), [point])
                continue
            current_end = current.start_address + current.quantity
            gap = max(0, start - current_end)
            candidate_end = max(current_end, end)
            candidate_quantity = candidate_end - current.start_address
            if gap <= allowed_gap and candidate_quantity <= block_limit:
                current.quantity = candidate_quantity
                current.points.append(point)
            else:
                blocks.append(current)
                current = ReadBlock(point.device_id, point.unit_id, fc, start, point.span, point.effective_interval(device), [point])
        if current is not None:
            blocks.append(current)
    return blocks


def optimization_summary(project: Project, blocks: list[ReadBlock] | None = None) -> tuple[int, int, float]:
    devices = project.device_map()
    original = sum(
        1 for point in project.points
        if point.enabled and point.device_id in devices and devices[point.device_id].enabled and not point.validate(devices)
    )
    merged = len(blocks if blocks is not None else build_read_blocks(project))
    reduction = 0.0 if original == 0 else (original - merged) / original * 100
    return original, merged, reduction
