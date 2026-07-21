from __future__ import annotations

from dataclasses import replace
import re

from .models import Point, Project, new_id


ALL_GROUPS = "全部群組"
_COPY_SUFFIX = re.compile(r"\s+-\s+複製(?:\s+\d+)?$")


def group_filter_values(points: list[Point]) -> list[str]:
    """Return all explicit group paths and their hierarchy parents."""
    groups: set[str] = set()
    for point in points:
        parts = [part.strip() for part in point.group_path.replace("\\", "/").split("/") if part.strip()]
        for length in range(1, len(parts) + 1):
            groups.add("/".join(parts[:length]))
    return [ALL_GROUPS, *sorted(groups, key=str.casefold)]


def matches_group_filter(group_path: str, selected_group: str) -> bool:
    if not selected_group or selected_group == ALL_GROUPS:
        return True
    normalized = "/".join(part.strip() for part in group_path.replace("\\", "/").split("/") if part.strip())
    selected = selected_group.rstrip("/")
    return normalized == selected or normalized.startswith(selected + "/")


def reorder_points_by_visible_order(project: Project, ordered_ids: list[str] | tuple[str, ...]) -> bool:
    """Reorder visible point slots while leaving filtered-out points in place."""
    ids = list(ordered_ids)
    if not ids:
        return False
    if len(ids) != len(set(ids)):
        raise ValueError("點位排序清單包含重複 ID")
    points_by_id = {point.id: point for point in project.points}
    missing = [point_id for point_id in ids if point_id not in points_by_id]
    if missing:
        raise ValueError(f"找不到要排序的點位 ID: {missing[0]}")
    visible = set(ids)
    slots = [index for index, point in enumerate(project.points) if point.id in visible]
    current = [project.points[index].id for index in slots]
    if current == ids:
        return False
    for index, point_id in zip(slots, ids):
        project.points[index] = points_by_id[point_id]
    return True


def create_incremented_copy(project: Project, source: Point) -> Point:
    """Create a uniquely named, non-overlapping copy after the source point."""
    base_name = _COPY_SUFFIX.sub("", source.name).strip()
    base_display = _COPY_SUFFIX.sub("", source.display_name).strip() if source.display_name else ""
    used_names = {point.name.casefold() for point in project.points}
    copy_number = 1
    while f"{base_name} - 複製 {copy_number}".casefold() in used_names:
        copy_number += 1

    name = f"{base_name} - 複製 {copy_number}"
    display_name = f"{base_display} - 複製 {copy_number}" if base_display else ""
    candidate_address = source.address + source.span
    comparable = [
        point
        for point in project.points
        if point.device_id == source.device_id
        and point.unit_id == source.unit_id
        and point.function_code == source.function_code
    ]
    attempts = 65536 // max(1, source.span) + 1
    for _ in range(attempts):
        draft = replace(
            source,
            id=new_id(),
            name=name,
            display_name=display_name,
            address=candidate_address,
        )
        try:
            start = draft.raw_address
        except ValueError:
            break
        end = start + draft.span
        if end > 65536:
            break
        overlaps = any(
            start < point.raw_address + point.span and point.raw_address < end
            for point in comparable
        )
        if not overlaps:
            errors = draft.validate(project.device_map())
            if errors:
                raise ValueError("\n".join(errors))
            return draft
        candidate_address += source.span
    raise ValueError("找不到可用的下一個地址，請檢查點位範圍或 65535 上限")
