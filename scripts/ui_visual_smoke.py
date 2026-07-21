from __future__ import annotations

import ctypes
from pathlib import Path
import sys
import time
from tkinter import ttk
from types import SimpleNamespace

from PIL import ImageGrab


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kafei.engine import DeviceStats  # noqa: E402
from kafei.models import Device, Point, Project  # noqa: E402
from kafei.point_ops import ALL_GROUPS  # noqa: E402
from kafei.ui import AboutDialog, KafeiApp, POINT_FUNCTION_LABELS, PointDialog  # noqa: E402


def build_sample_project() -> Project:
    devices = [
        Device(name="Meter-Lab-01", host="192.0.2.10", group="Demo"),
        Device(name="Meter-Lab-02", host="192.0.2.11", group="Demo"),
        Device(name="Meter-Lab-03", host="192.0.2.12", group="Demo"),
    ]
    points: list[Point] = []
    for device_index, device in enumerate(devices):
        for point_index, (name, group, unit) in enumerate(
            (
                ("Voltage L1-N", "Power/Voltage", "V"),
                ("Current L1", "Power/Current", "A"),
                ("Active Power", "Power/Demand", "kW"),
            )
        ):
            points.append(
                Point(
                    name=f"{name} {device_index + 1}",
                    display_name=name,
                    device_id=device.id,
                    group_path=f"{device.name}/{group}",
                    address=point_index * 2,
                    quantity=2,
                    data_type="FLOAT32",
                    engineering_unit=unit,
                )
            )
    return Project(name="UI Visual Smoke", devices=devices, points=points)


class PreviewEngine:
    running = True

    def __init__(self, project: Project) -> None:
        ping_values = (True, False, None)
        self._stats = {
            device.id: DeviceStats(ping_reachable=ping_values[index])
            for index, device in enumerate(project.devices)
        }

    def stats_snapshot(self) -> dict[str, DeviceStats]:
        return self._stats

    def states_snapshot(self) -> dict[str, object]:
        return {}

    def debug_snapshot(self) -> list[object]:
        return []


def main() -> int:
    output_dir = ROOT / ".work" / "ui_visual_smoke"
    output_dir.mkdir(parents=True, exist_ok=True)

    app = KafeiApp()
    app.project = build_sample_project()
    app._refresh_all()
    app.update_idletasks()
    app.update()

    if sys.platform == "win32":
        user32 = ctypes.windll.user32
        user32.GetParent.restype = ctypes.c_void_p
        user32.SendMessageW.restype = ctypes.c_ssize_t
        window = int(app.winfo_id())
        parent = user32.GetParent(ctypes.c_void_p(window))
        target = int(parent) if parent else window
        native_icons = [user32.SendMessageW(ctypes.c_void_p(target), 0x007F, kind, 0) for kind in (0, 1, 2)]
        assert app._native_icon_handles, "native Windows icon handles were not created"
        assert any(native_icons), native_icons

    tab_labels = [app.notebook.tab(tab_id, "text") for tab_id in app.notebook.tabs()]
    assert tab_labels == ["通訊設備", "點位管理", "通訊除錯"], tab_labels
    menu_labels = [app.menu_bar.entrycget(index, "label") for index in range(app.menu_bar.index("end") + 1)]
    assert menu_labels == ["檔案", "通訊", "說明"], menu_labels

    style = ttk.Style(app)
    selected_padding = style.lookup("Kafei.TNotebook.Tab", "padding", ("selected",))
    inactive_padding = style.lookup("Kafei.TNotebook.Tab", "padding", ("!selected",))
    assert selected_padding == inactive_padding, (selected_padding, inactive_padding)

    groups = tuple(app.group_combo.cget("values"))
    assert groups[0] == ALL_GROUPS, groups
    assert "Meter-Lab-01" in groups, groups
    assert "Meter-Lab-01/Power" in groups, groups

    app.group_var.set("Meter-Lab-01/Power")
    app._refresh_points()
    assert len(app.point_tree.get_children()) == 3
    app.group_var.set(ALL_GROUPS)
    app._refresh_points()

    app.notebook.select(1)
    app.update_idletasks()
    app.update()
    app.point_tree.column("enabled", minwidth=20, width=20)
    app.point_tree.update_idletasks()
    separator_x = next(
        x
        for x in range(1, min(app.point_tree.winfo_width(), 120))
        if app.point_tree.identify_region(x, 5) == "separator"
    )
    result = app._tree_header_double_click(SimpleNamespace(widget=app.point_tree, x=separator_x, y=5))
    assert result == "break"
    assert int(app.point_tree.column("enabled", "width")) > 20

    rows = list(app.point_tree.get_children())
    app._point_range_anchor = rows[1]
    app._select_point_range_to(rows[4])
    assert app.point_tree.selection() == tuple(rows[1:5]), app.point_tree.selection()
    app._refresh_points()
    assert app.point_tree.selection() == tuple(rows[1:5]), app.point_tree.selection()

    app.point_tree.selection_set(rows[0])
    app.point_tree.focus(rows[0])
    source_bbox = app.point_tree.bbox(rows[0])
    target_bbox = app.point_tree.bbox(rows[2])
    app._point_drag_press(SimpleNamespace(x=10, y=source_bbox[1] + source_bbox[3] // 2, state=0))
    app._point_drag_motion(SimpleNamespace(x=10, y=target_bbox[1] + target_bbox[3] - 1, state=0))
    app._point_drag_release(SimpleNamespace(x=10, y=target_bbox[1] + target_bbox[3] - 1, state=0))
    reordered_ids = [point.id for point in app.project.points]
    assert reordered_ids[:3] == [rows[1], rows[2], rows[0]], reordered_ids[:3]
    assert app.status_var.get() == "點位順序已更新"

    rows = list(app.point_tree.get_children())
    before_multi_drag = [point.id for point in app.project.points]
    app.point_tree.selection_set(rows[0], rows[1])
    source_bbox = app.point_tree.bbox(rows[0])
    target_bbox = app.point_tree.bbox(rows[2])
    app._point_drag_press(SimpleNamespace(x=10, y=source_bbox[1] + source_bbox[3] // 2, state=0))
    assert app._point_drag_candidate is None
    app._point_drag_motion(SimpleNamespace(x=10, y=target_bbox[1] + target_bbox[3] - 1, state=0))
    app._point_drag_release(SimpleNamespace(x=10, y=target_bbox[1] + target_bbox[3] - 1, state=0))
    assert [point.id for point in app.project.points] == before_multi_drag

    app.engine = PreviewEngine(app.project)  # type: ignore[assignment]
    app._refresh_devices()
    ping_values = [app.device_ping_tree.item(device.id, "values")[0] for device in app.project.devices]
    assert ping_values == ["●", "無回應", ""], ping_values

    app.lift()
    app.attributes("-topmost", True)
    app.update()
    time.sleep(0.3)

    x = app.winfo_rootx()
    y = app.winfo_rooty()
    bbox = (x, y, x + app.winfo_width(), y + app.winfo_height())
    for index, name in enumerate(("devices", "points", "debug")):
        app.notebook.select(index)
        app.update_idletasks()
        app.update()
        assert app.notebook.index(app.notebook.select()) == index
        time.sleep(0.5)
        ImageGrab.grab(bbox=bbox, all_screens=True).save(output_dir / f"{name}.png")

    dialog_checked = False
    app.attributes("-topmost", False)

    def inspect_point_dialog() -> None:
        nonlocal dialog_checked
        dialogs = [item for item in app.winfo_children() if isinstance(item, PointDialog)]
        assert len(dialogs) == 1, dialogs
        dialog = dialogs[0]
        assert dialog.address_entry.value() == ""
        assert dialog.focus_get() == dialog.address_entry, dialog.focus_get()
        assert dialog.device_combo.cget("style") == "Point.TCombobox"
        assert dialog.unit_id_entry.cget("style") == "Point.TEntry"
        assert dialog.address_entry.cget("style") == "Address.TEntry"
        assert dialog.address_border.cget("background") == "#8BAAB1"
        assert int(dialog.enabled_switch.canvas.cget("width")) == 56
        enabled_header = dialog.enabled_frame.winfo_children()[0]
        assert any(getattr(child, "cget", lambda _key: "")("text") == "點位啟停" for child in enabled_header.winfo_children())

        assert int(dialog.device_combo.master.grid_info()["row"]) == 0
        assert int(dialog.device_combo.master.grid_info()["column"]) == 0
        assert int(dialog.device_combo.master.grid_info()["columnspan"]) == 2
        assert int(dialog.name_entry.master.grid_info()["column"]) == 2
        assert int(dialog.name_entry.master.grid_info()["columnspan"]) == 2
        assert int(dialog.unit_id_entry.master.grid_info()["row"]) == 1
        assert int(dialog.unit_id_entry.master.grid_info()["column"]) == 0
        assert int(dialog.address_border.master.grid_info()["row"]) == 1
        assert int(dialog.address_border.master.grid_info()["column"]) == 3
        assert int(dialog.data_type_combo.master.grid_info()["columnspan"]) == 2
        assert int(dialog.order_combo.master.grid_info()["row"]) == 3
        assert int(dialog.order_combo.master.grid_info()["column"]) == 0
        assert int(dialog.bit_entry.master.grid_info()["column"]) == 1
        assert int(dialog.scale_entry.master.grid_info()["column"]) == 2
        assert int(dialog.offset_entry.master.grid_info()["column"]) == 3
        assert int(dialog.merge_combo.master.grid_info()["row"]) == 4
        assert int(dialog.enabled_frame.grid_info()["column"]) == 3
        assert tuple(dialog.merge_combo.cget("values")) == ("自動合併輪詢", "獨立輪詢")
        assert dialog.enabled_switch.get()
        assert all("同一個 IP" not in tooltip.text for tooltip in dialog._tooltips)

        dialog.fc_var.set(POINT_FUNCTION_LABELS[1])
        dialog._function_changed()
        assert dialog.data_type_var.get() == "BOOL"
        assert dialog.quantity_var.get() == "1"
        assert dialog.decimals_var.get() == "0"
        assert str(dialog.bit_entry.cget("state")) == "disabled"

        dialog.fc_var.set(POINT_FUNCTION_LABELS[3])
        dialog._function_changed()
        assert dialog.data_type_var.get() == "UINT16"
        assert dialog.decimals_var.get() == "2"
        dialog.data_type_var.set("BIT")
        dialog._update_interlocks()
        assert str(dialog.bit_entry.cget("state")) == "normal"
        dialog.bit_var.set("7")
        dialog._update_interlocks()
        assert "Bit 7" in str(dialog.data_type_note.cget("text"))

        dialog.data_type_var.set("BOOL")
        dialog._update_interlocks()
        assert "CONVERT_ERROR" in str(dialog.data_type_note.cget("text"))
        dialog.address_entry.set_value("0")
        dialog.tags_entry.set_value("電壓, 三相")
        draft = dialog._build_draft()
        assert draft.tags == ["電壓", "三相"]
        assert draft.scan_interval_ms is None
        assert draft.display_name == ""

        dialog.attributes("-topmost", True)
        dialog.update_idletasks()
        dialog.update()
        time.sleep(0.3)
        dialog_bbox = (
            dialog.winfo_rootx(),
            dialog.winfo_rooty(),
            dialog.winfo_rootx() + dialog.winfo_width(),
            dialog.winfo_rooty() + dialog.winfo_height(),
        )
        ImageGrab.grab(bbox=dialog_bbox, all_screens=True).save(output_dir / "point-dialog.png")
        ImageGrab.grab(all_screens=True).save(output_dir / "point-dialog-full-screen.png")
        dialog_checked = True
        dialog.destroy()

    new_point = Point(name="點位-新增", device_id=app.project.devices[0].id)
    app.after(350, inspect_point_dialog)
    app.after(5_000, lambda: [item.destroy() for item in app.winfo_children() if isinstance(item, PointDialog)])
    PointDialog(app, app.project, new_point, adding=True)
    assert dialog_checked
    print("add point dialog smoke: PASS", flush=True)

    edit_dialog_checked = False

    def inspect_edit_point_dialog() -> None:
        nonlocal edit_dialog_checked
        dialogs = [item for item in app.winfo_children() if isinstance(item, PointDialog)]
        assert len(dialogs) == 1, dialogs
        dialog = dialogs[0]
        assert dialog.title() == "編輯點位"
        assert dialog.address_entry.value() == str(app.project.points[0].address)
        assert dialog.focus_get() == dialog.address_entry, dialog.focus_get()
        assert int(dialog.address_border.master.grid_info()["column"]) == 3
        assert int(dialog.enabled_switch.canvas.cget("width")) == 56
        dialog.attributes("-topmost", True)
        dialog.update_idletasks()
        dialog.update()
        time.sleep(0.3)
        dialog_bbox = (
            dialog.winfo_rootx(),
            dialog.winfo_rooty(),
            dialog.winfo_rootx() + dialog.winfo_width(),
            dialog.winfo_rooty() + dialog.winfo_height(),
        )
        ImageGrab.grab(bbox=dialog_bbox, all_screens=True).save(output_dir / "point-dialog-edit.png")
        edit_dialog_checked = True
        dialog.destroy()

    app.after(350, inspect_edit_point_dialog)
    app.after(5_000, lambda: [item.destroy() for item in app.winfo_children() if isinstance(item, PointDialog)])
    PointDialog(app, app.project, app.project.points[0], adding=False)
    assert edit_dialog_checked
    print("edit point dialog smoke: PASS", flush=True)

    about_checked = False

    def inspect_about_dialog() -> None:
        nonlocal about_checked
        dialogs = [item for item in app.winfo_children() if isinstance(item, AboutDialog)]
        assert len(dialogs) == 1, dialogs
        dialog = dialogs[0]
        assert dialog.tagline_label.cget("text") == "工具太難用 自己搞一個"
        assert dialog.copyright_label.cget("text") == AboutDialog.COPYRIGHT
        assert dialog.coffee_image is not None
        assert dialog.coffee_image.width() >= 64
        dialog.attributes("-topmost", True)
        dialog.update_idletasks()
        dialog.update()
        time.sleep(0.3)
        dialog_bbox = (
            dialog.winfo_rootx(),
            dialog.winfo_rooty(),
            dialog.winfo_rootx() + dialog.winfo_width(),
            dialog.winfo_rooty() + dialog.winfo_height(),
        )
        ImageGrab.grab(bbox=dialog_bbox, all_screens=True).save(output_dir / "about-dialog.png")
        ImageGrab.grab(all_screens=True).save(output_dir / "about-dialog-full-screen.png")
        about_checked = True
        dialog.destroy()

    app.after(250, inspect_about_dialog)
    app.after(5_000, lambda: [item.destroy() for item in app.winfo_children() if isinstance(item, AboutDialog)])
    app._show_about()
    assert about_checked
    print("about dialog smoke: PASS", flush=True)

    app.attributes("-topmost", False)
    app.destroy()
    print(f"GUI smoke passed; screenshots: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
