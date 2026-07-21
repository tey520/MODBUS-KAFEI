from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import csv
import ctypes
import sys
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

from .codec import format_value
from .csvio import export_debug_csv, export_latest_csv, import_points_csv, write_csv_template
from .engine import PollingEngine
from .merge import optimization_summary
from .models import AddressMode, DATA_TYPE_UNITS, REFERENCE_BASES, Device, Point, Project, Quality, new_id
from .persistence import load_project, recoverable_autosave, save_autosave, save_project
from .point_ops import (
    ALL_GROUPS,
    create_incremented_copy,
    group_filter_values,
    matches_group_filter,
    reorder_points_by_visible_order,
)


def resource_path(relative_path: str) -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return bundle_root / relative_path


class FormDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, title: str, fields: list[tuple[str, str, object, tuple[str, ...] | None]]) -> None:
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.result: dict[str, str] | None = None
        self._widgets: dict[str, tk.Widget] = {}
        body = ttk.Frame(self, padding=12)
        body.grid(sticky="nsew")
        for row, (key, label, value, choices) in enumerate(fields):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="e", padx=(0, 8), pady=3)
            if choices:
                widget = ttk.Combobox(body, values=choices, state="readonly", width=36)
                widget.set(str(value))
            else:
                widget = ttk.Entry(body, width=39)
                widget.insert(0, "" if value is None else str(value))
            widget.grid(row=row, column=1, sticky="ew", pady=3)
            self._widgets[key] = widget
        buttons = ttk.Frame(body)
        buttons.grid(row=len(fields), column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="確定", command=self._accept).pack(side="right")
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Return>", lambda _event: self._accept())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.winfo_reqwidth()) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.winfo_reqheight()) // 2)
        self.geometry(f"+{x}+{y}")
        self.grab_set()
        next(iter(self._widgets.values())).focus_set()
        parent.wait_window(self)

    def _accept(self) -> None:
        self.result = {key: str(widget.get()).strip() for key, widget in self._widgets.items()}  # type: ignore[attr-defined]
        self.destroy()


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self._window: tk.Toplevel | None = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<FocusIn>", self._schedule, add="+")
        widget.bind("<FocusOut>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.toggle, add="+")

    def _schedule(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        self._cancel_schedule()
        self._after_id = self.widget.after(350, self.show)

    def _cancel_schedule(self) -> None:
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def toggle(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        if self._window is None:
            self.show()
        else:
            self.hide()

    def show(self) -> None:
        self._cancel_schedule()
        if self._window is not None or not self.widget.winfo_exists():
            return
        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.attributes("-topmost", True)
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 6
        y = self.widget.winfo_rooty() + max(0, self.widget.winfo_height() // 2 - 10)
        window.geometry(f"+{x}+{y}")
        label = tk.Label(
            window,
            text=self.text,
            justify="left",
            wraplength=360,
            background="#FFF8D6",
            foreground="#27313A",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=6,
            font=("Microsoft JhengHei UI", 9),
        )
        label.pack()
        self._window = window

    def hide(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        self._cancel_schedule()
        if self._window is not None:
            self._window.destroy()
            self._window = None


class PlaceholderEntry(ttk.Entry):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        placeholder: str,
        value: str = "",
        normal_style: str = "TEntry",
        placeholder_style: str = "Placeholder.TEntry",
        **kwargs: object,
    ) -> None:
        super().__init__(parent, **kwargs)
        self.placeholder = placeholder
        self.normal_style = normal_style
        self.placeholder_style = placeholder_style
        self._showing_placeholder = False
        self.bind("<FocusIn>", self._focus_in, add="+")
        self.bind("<FocusOut>", self._focus_out, add="+")
        self.set_value(value)

    def _focus_in(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        if self._showing_placeholder:
            self.delete(0, "end")
            self.configure(style=self.normal_style)
            self._showing_placeholder = False

    def _focus_out(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        if not super().get().strip():
            self._show_placeholder()

    def _show_placeholder(self) -> None:
        self.delete(0, "end")
        self.insert(0, self.placeholder)
        self.configure(style=self.placeholder_style)
        self._showing_placeholder = True

    def set_placeholder(self, placeholder: str) -> None:
        self.placeholder = placeholder
        if self._showing_placeholder:
            self._show_placeholder()

    def set_value(self, value: str) -> None:
        self.delete(0, "end")
        if value:
            self.insert(0, value)
            self.configure(style=self.normal_style)
            self._showing_placeholder = False
        else:
            self._show_placeholder()

    def value(self) -> str:
        return "" if self._showing_placeholder else super().get().strip()


class ToggleSwitch(ttk.Frame):
    def __init__(self, parent: tk.Misc, *, value: bool = True) -> None:
        super().__init__(parent)
        self.variable = tk.BooleanVar(self, value=value)
        background = ttk.Style(self).lookup("TFrame", "background") or "#F0F0F0"
        self.canvas = tk.Canvas(
            self,
            width=56,
            height=24,
            background=background,
            highlightthickness=0,
            takefocus=True,
            cursor="hand2",
        )
        self.canvas.pack(side="left")
        self.label = ttk.Label(self, text="啟用" if value else "停用")
        self.label.pack(side="left", padx=(7, 0))
        self.canvas.bind("<Button-1>", self._toggle, add="+")
        self.canvas.bind("<space>", self._toggle, add="+")
        self.canvas.bind("<Return>", self._toggle, add="+")
        self.label.bind("<Button-1>", self._toggle, add="+")
        self._draw()

    def _toggle(self, _event: tk.Event[tk.Misc] | None = None) -> str:
        self.variable.set(not self.variable.get())
        self._draw()
        return "break"

    def _draw(self) -> None:
        enabled = self.variable.get()
        track = "#0F4C5C" if enabled else "#B8C2C9"
        self.canvas.delete("all")
        self.canvas.create_oval(1, 1, 23, 23, fill=track, outline=track)
        self.canvas.create_oval(33, 1, 55, 23, fill=track, outline=track)
        self.canvas.create_rectangle(12, 1, 44, 23, fill=track, outline=track)
        knob_left = 34 if enabled else 2
        self.canvas.create_oval(knob_left, 3, knob_left + 18, 21, fill="#FFFFFF", outline="#FFFFFF")
        self.label.configure(text="啟用" if enabled else "停用")

    def get(self) -> bool:
        return self.variable.get()


class AboutDialog(tk.Toplevel):
    COPYRIGHT = "Copyright © 2026 MODBUS KAFEI. All rights reserved."

    def __init__(self, parent: "KafeiApp") -> None:
        super().__init__(parent)
        self.title("關於 MODBUS KAFEI")
        self.resizable(False, False)
        self.transient(parent)
        body = ttk.Frame(self, padding=(34, 24, 34, 18))
        body.grid(sticky="nsew")

        self.coffee_image: tk.PhotoImage | None = None
        try:
            source = tk.PhotoImage(file=str(resource_path("assets/kafei-coffee.png")))
            longest_side = max(source.width(), source.height(), 1)
            scale = max(1, min(8, 96 // longest_side))
            self.coffee_image = source.zoom(scale, scale)
            ttk.Label(body, image=self.coffee_image).pack(pady=(0, 14))
        except (OSError, tk.TclError):
            ttk.Label(body, text="☕", font=("Segoe UI Symbol", 42)).pack(pady=(0, 14))

        self.tagline_label = ttk.Label(
            body,
            text="工具太難用 自己搞一個",
            foreground="#123B48",
            font=("Microsoft JhengHei UI", 15, "bold"),
        )
        self.tagline_label.pack(pady=(0, 18))
        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=(0, 12))
        self.copyright_label = ttk.Label(
            body,
            text=self.COPYRIGHT,
            foreground="#7A8793",
            font=("Microsoft JhengHei UI", 8),
        )
        self.copyright_label.pack()
        ttk.Button(body, text="關閉", command=self.destroy).pack(pady=(16, 0))

        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.winfo_reqwidth()) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.winfo_reqheight()) // 2)
        self.geometry(f"+{x}+{y}")
        self.lift()
        self.grab_set()
        self.after(50, self.focus_force)
        parent.wait_window(self)


POINT_ADDRESS_LABELS = {
    AddressMode.ZERO_BASED.value: "0 Based（通訊位址）",
    AddressMode.REFERENCE.value: "Reference（文件地址）",
}
POINT_ADDRESS_VALUES = {label: value for value, label in POINT_ADDRESS_LABELS.items()}
POINT_FUNCTION_LABELS = {
    1: "FC01 · Coil",
    2: "FC02 · Discrete Input",
    3: "FC03 · Holding Register",
    4: "FC04 · Input Register",
}
POINT_FUNCTION_VALUES = {label: value for value, label in POINT_FUNCTION_LABELS.items()}
POINT_MERGE_LABELS = {"inherit": "自動合併輪詢", "none": "獨立輪詢"}
POINT_MERGE_VALUES = {label: value for value, label in POINT_MERGE_LABELS.items()}
VARIABLE_LENGTH_TYPES = {"ASCII", "HEX", "BINARY"}
REGISTER_DATA_TYPES = ("BOOL", "BIT", "INT16", "UINT16", "INT32", "UINT32", "FLOAT32", "HEX", "BINARY", "ASCII")


class PointDialog(tk.Toplevel):
    def __init__(self, parent: "KafeiApp", project: Project, point: Point, *, adding: bool) -> None:
        super().__init__(parent)
        self.parent_app = parent
        self.project = project
        self.original = point
        self.adding = adding
        self.result: Point | None = None
        self._tooltips: list[ToolTip] = []
        self._decimals_edited = False
        self._previous_fc = point.function_code
        self.title("新增點位" if adding else "編輯點位")
        self.resizable(False, False)
        self.transient(parent)

        style = ttk.Style(self)
        style.configure("Help.TButton", padding=(4, 0), font=("Microsoft JhengHei UI", 8, "bold"))
        style.configure("Point.TEntry", fieldbackground="#FFFFFF")
        style.configure("PointPlaceholder.TEntry", foreground="#8A98A5", fieldbackground="#FFFFFF")
        style.configure("Address.TEntry", fieldbackground="#FFFFFF")
        style.configure("AddressPlaceholder.TEntry", foreground="#7B8992", fieldbackground="#FFFFFF")
        style.configure("Point.TCombobox", foreground="#1F2933", fieldbackground="#FFFFFF", background="#FFFFFF")
        style.map(
            "Point.TCombobox",
            fieldbackground=[("readonly", "#FFFFFF")],
            foreground=[("readonly", "#1F2933")],
            selectbackground=[("readonly", "#FFFFFF")],
            selectforeground=[("readonly", "#1F2933")],
        )
        style.configure("DialogTitle.TLabel", foreground="#123B48", font=("Microsoft JhengHei UI", 13, "bold"))
        style.configure("DialogHint.TLabel", foreground="#64748B")
        style.configure("DialogError.TLabel", foreground="#B42318")

        body = ttk.Frame(self, padding=(18, 14, 18, 16))
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        header = ttk.Frame(body)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(header, text=self.title(), style="DialogTitle.TLabel").pack(side="left")
        ttk.Label(header, text="＊為必填", style="DialogHint.TLabel").pack(side="right")

        form = ttk.Frame(body)
        form.grid(row=1, column=0, sticky="nsew")
        for column in range(4):
            form.columnconfigure(column, weight=1, uniform="point-field", minsize=178)

        self._device_by_label: dict[str, Device] = {}
        device_labels: list[str] = []
        selected_device_label = ""
        for device in project.devices:
            label = f"{device.name} · {device.host}"
            if label in self._device_by_label:
                label = f"{label} · {device.id[:8]}"
            self._device_by_label[label] = device
            device_labels.append(label)
            if device.id == point.device_id:
                selected_device_label = label
        if not selected_device_label and device_labels:
            selected_device_label = device_labels[0]

        name_frame = self._field(form, 0, 2, "點位名稱＊", columnspan=2)
        self.name_entry = PlaceholderEntry(
            name_frame,
            placeholder="例如 L1 電壓",
            value=point.name,
            normal_style="Point.TEntry",
            placeholder_style="PointPlaceholder.TEntry",
        )
        self.name_entry.pack(fill="x")

        device_frame = self._field(form, 0, 0, "通訊設備＊", columnspan=2)
        self.device_var = tk.StringVar(self, value=selected_device_label)
        self.device_combo = ttk.Combobox(
            device_frame,
            textvariable=self.device_var,
            values=device_labels,
            state="readonly",
            style="Point.TCombobox",
        )
        self.device_combo.pack(fill="x")

        address_mode_frame = self._field(
            form,
            1,
            1,
            "地址模式",
            "0 Based 的第一個位址是 0；Reference 使用傳統文件地址，例如 FC03 的 40001。",
        )
        self.address_mode_var = tk.StringVar(self, value=POINT_ADDRESS_LABELS[point.address_mode])
        self.address_mode_combo = ttk.Combobox(
            address_mode_frame,
            textvariable=self.address_mode_var,
            values=tuple(POINT_ADDRESS_LABELS.values()),
            state="readonly",
            style="Point.TCombobox",
        )
        self.address_mode_combo.pack(fill="x")

        fc_frame = self._field(form, 1, 2, "功能碼")
        self.fc_var = tk.StringVar(self, value=POINT_FUNCTION_LABELS[point.function_code])
        self.fc_combo = ttk.Combobox(
            fc_frame,
            textvariable=self.fc_var,
            values=tuple(POINT_FUNCTION_LABELS.values()),
            state="readonly",
            style="Point.TCombobox",
        )
        self.fc_combo.pack(fill="x")

        unit_id_frame = self._field(form, 1, 0, "Unit ID＊", "Modbus TCP 裝置站號，範圍 0～255。")
        self.unit_id_var = tk.StringVar(self, value=str(point.unit_id))
        self.unit_id_entry = ttk.Entry(unit_id_frame, textvariable=self.unit_id_var, style="Point.TEntry")
        self.unit_id_entry.pack(fill="x")
        ttk.Label(unit_id_frame, text="", style="DialogHint.TLabel").pack(anchor="w")

        address_frame = self._field(form, 1, 3, "地址值＊")
        initial_address = "" if adding else str(point.address)
        self.address_border = tk.Frame(address_frame, background="#8BAAB1", padx=1, pady=1)
        self.address_border.pack(fill="x")
        self.address_entry = PlaceholderEntry(
            self.address_border,
            placeholder=self._address_placeholder(),
            value=initial_address,
            normal_style="Address.TEntry",
            placeholder_style="AddressPlaceholder.TEntry",
        )
        self.address_entry.pack(fill="x")
        self.address_error = ttk.Label(address_frame, text="", style="DialogError.TLabel")
        self.address_error.pack(anchor="w")

        data_type_frame = self._field(
            form,
            2,
            0,
            "資料型別＊",
            "FC03／FC04 預設 UINT16。BOOL 只接受 0 或 1；BIT 必須指定 Bit Index。",
            columnspan=2,
        )
        self.data_type_var = tk.StringVar(self, value=point.data_type.upper())
        self.data_type_combo = ttk.Combobox(
            data_type_frame,
            textvariable=self.data_type_var,
            state="readonly",
            style="Point.TCombobox",
        )
        self.data_type_combo.pack(fill="x")
        self.data_type_note = ttk.Label(data_type_frame, text="", style="DialogHint.TLabel")
        self.data_type_note.pack(anchor="w")

        quantity_frame = self._field(
            form,
            2,
            2,
            "占用位址數＊",
            "固定長度型別由系統鎖定；只有 ASCII、HEX、BINARY 可手動設定。",
            columnspan=2,
        )
        quantity_row = ttk.Frame(quantity_frame)
        quantity_row.pack(fill="x")
        self.quantity_var = tk.StringVar(self, value=str(point.quantity))
        self.quantity_entry = ttk.Entry(quantity_row, textvariable=self.quantity_var, style="Point.TEntry")
        self.quantity_entry.pack(side="left", fill="x", expand=True)
        self.quantity_unit = ttk.Label(quantity_row, text="Register（自動）")
        self.quantity_unit.pack(side="left", padx=(8, 0))
        ttk.Label(quantity_frame, text="", style="DialogHint.TLabel").pack(anchor="w")

        bit_frame = self._field(
            form,
            3,
            1,
            "Bit Index",
            "只有 FC03／FC04 的 BIT 型別可使用，必須輸入 0～15。",
        )
        self.bit_var = tk.StringVar(self, value="" if point.bit_index is None else str(point.bit_index))
        self.bit_entry = ttk.Entry(bit_frame, textvariable=self.bit_var, style="Point.TEntry")
        self.bit_entry.pack(fill="x")

        order_frame = self._field(
            form,
            3,
            0,
            "Byte / Word Order",
            "INT16／UINT16 可選 ABCD、BADC；32-bit 型別可選 ABCD、BADC、CDAB、DCBA。",
        )
        self.order_var = tk.StringVar(self, value=point.byte_order)
        self.order_combo = ttk.Combobox(
            order_frame,
            textvariable=self.order_var,
            state="readonly",
            style="Point.TCombobox",
        )
        self.order_combo.pack(fill="x")

        scale_frame = self._field(
            form,
            3,
            2,
            "Scale",
            "工程值 = 解析值 × Scale + Offset；Scale 未填時使用 1。",
        )
        self.scale_var = tk.StringVar(self, value=str(point.scale))
        self.scale_entry = ttk.Entry(scale_frame, textvariable=self.scale_var, style="Point.TEntry")
        self.scale_entry.pack(fill="x")

        offset_frame = self._field(
            form,
            3,
            3,
            "Offset",
            "工程值 = 解析值 × Scale + Offset；Offset 未填時使用 0。",
        )
        self.offset_var = tk.StringVar(self, value=str(point.offset))
        self.offset_entry = ttk.Entry(offset_frame, textvariable=self.offset_var, style="Point.TEntry")
        self.offset_entry.pack(fill="x")

        decimals_frame = self._field(form, 4, 1, "小數位數")
        self.decimals_var = tk.StringVar(self, value=str(point.decimals))
        self.decimals_entry = ttk.Entry(decimals_frame, textvariable=self.decimals_var, style="Point.TEntry")
        self.decimals_entry.pack(fill="x")

        unit_frame = self._field(form, 4, 2, "工程單位")
        self.unit_entry = PlaceholderEntry(
            unit_frame,
            placeholder="例如 V、A、kW",
            value=point.engineering_unit,
            normal_style="Point.TEntry",
            placeholder_style="PointPlaceholder.TEntry",
        )
        self.unit_entry.pack(fill="x")

        group_frame = self._field(
            form,
            5,
            0,
            "群組路徑",
            "使用半形斜線 / 分隔群組層級；不可連續使用或放在開頭、結尾。",
            columnspan=2,
        )
        self.group_entry = PlaceholderEntry(
            group_frame,
            placeholder="例如 M3/MSB/Power Meter",
            value=point.group_path,
            normal_style="Point.TEntry",
            placeholder_style="PointPlaceholder.TEntry",
        )
        self.group_entry.pack(fill="x")
        ttk.Label(group_frame, text="合法分隔符號：半形 /", style="DialogHint.TLabel").pack(anchor="w")

        tags_frame = self._field(form, 5, 2, "標籤", columnspan=2)
        self.tags_entry = PlaceholderEntry(
            tags_frame,
            placeholder="例如 電壓, 三相",
            value=", ".join(point.tags),
            normal_style="Point.TEntry",
            placeholder_style="PointPlaceholder.TEntry",
        )
        self.tags_entry.pack(fill="x")
        ttk.Label(tags_frame, text="", style="DialogHint.TLabel").pack(anchor="w")

        merge_frame = self._field(
            form,
            4,
            0,
            "合併模式",
            "自動合併輪詢：依通訊設備設定，將相同設備、Unit ID、功能碼且地址相容的點位合併成較少請求。",
        )
        self.merge_var = tk.StringVar(self, value=POINT_MERGE_LABELS[point.merge_mode])
        self.merge_combo = ttk.Combobox(
            merge_frame,
            textvariable=self.merge_var,
            values=tuple(POINT_MERGE_LABELS.values()),
            state="readonly",
            style="Point.TCombobox",
        )
        self.merge_combo.pack(fill="x")

        self.enabled_frame = self._field(form, 4, 3, "點位啟停")
        self.enabled_switch = ToggleSwitch(self.enabled_frame, value=point.enabled)
        self.enabled_switch.pack(anchor="w", pady=(1, 0))

        description_frame = self._field(form, 6, 0, "說明", columnspan=4)
        self.description_count = ttk.Label(description_frame, text="0 字", style="DialogHint.TLabel")
        self.description_count.place(relx=1.0, y=-25, anchor="ne")
        self.description_text = tk.Text(
            description_frame,
            height=3,
            wrap="word",
            undo=True,
            font=("Microsoft JhengHei UI", 9),
            relief="solid",
            borderwidth=1,
            padx=6,
            pady=5,
        )
        self.description_text.pack(fill="x")
        self.description_text.insert("1.0", point.description)
        self._update_description_count()

        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="新增點位" if adding else "儲存變更", command=self._accept).pack(side="right")

        self.address_mode_combo.bind("<<ComboboxSelected>>", self._address_mode_changed)
        self.fc_combo.bind("<<ComboboxSelected>>", self._function_changed)
        self.data_type_combo.bind("<<ComboboxSelected>>", self._update_interlocks)
        self.quantity_entry.bind("<KeyRelease>", self._address_input_changed)
        self.bit_entry.bind("<KeyRelease>", self._update_interlocks)
        self.address_entry.bind("<KeyRelease>", self._address_input_changed)
        self.decimals_entry.bind("<KeyRelease>", self._decimals_changed)
        self.description_text.bind("<KeyRelease>", self._update_description_count)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Control-Return>", lambda _event: self._accept())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._update_interlocks()
        self.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.winfo_reqwidth()) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.winfo_reqheight()) // 2)
        self.geometry(f"+{x}+{y}")
        self.lift()
        self.grab_set()
        self._focus_address()
        self.after(50, self._focus_address)
        self.after(200, self._focus_address)
        parent.wait_window(self)

    def _field(
        self,
        parent: ttk.Frame,
        row: int,
        column: int,
        label: str,
        help_text: str | None = None,
        *,
        columnspan: int = 1,
    ) -> ttk.Frame:
        frame = ttk.Frame(parent)
        frame.grid(
            row=row,
            column=column,
            columnspan=columnspan,
            sticky="nsew",
            padx=(0 if column == 0 else 5, 0 if column + columnspan >= 4 else 5),
            pady=(0, 9),
        )
        header = ttk.Frame(frame, height=27)
        header.pack(fill="x")
        header.pack_propagate(False)
        ttk.Label(header, text=label).pack(side="left", anchor="w", pady=(4, 0))
        if help_text:
            button = ttk.Button(header, text="?", width=2, style="Help.TButton", takefocus=True)
            button.pack(side="left", padx=(5, 0), pady=(2, 0))
            self._tooltips.append(ToolTip(button, help_text))
        return frame

    def _selected_fc(self) -> int:
        return POINT_FUNCTION_VALUES[self.fc_var.get()]

    def _selected_address_mode(self) -> str:
        return POINT_ADDRESS_VALUES[self.address_mode_var.get()]

    def _focus_address(self) -> None:
        self.address_entry.focus_force()
        self.address_entry.icursor("end")

    def _address_placeholder(self) -> str:
        return "例如 40001" if self.original.address_mode == AddressMode.REFERENCE.value else "例如 0"

    def _update_address_placeholder(self) -> None:
        fc = self._selected_fc()
        placeholder = f"例如 {REFERENCE_BASES[fc]}" if self._selected_address_mode() == AddressMode.REFERENCE.value else "例如 0"
        self.address_entry.set_placeholder(placeholder)

    def _address_mode_changed(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        self._update_address_placeholder()
        self.address_error.configure(text="")
        self._focus_address()

    def _function_changed(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        fc = self._selected_fc()
        was_bit_function = self._previous_fc in (1, 2)
        if fc in (1, 2):
            self.data_type_var.set("BOOL")
        elif was_bit_function:
            self.data_type_var.set("UINT16")
        if not self._decimals_edited:
            self.decimals_var.set("0" if fc in (1, 2) else "2")
        self._previous_fc = fc
        self._update_address_placeholder()
        self._update_interlocks()

    def _decimals_changed(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        self._decimals_edited = True

    def _address_input_changed(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        self.address_error.configure(text="")

    def _update_interlocks(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        fc = self._selected_fc()
        if fc in (1, 2):
            self.data_type_combo.configure(values=("BOOL",))
            if self.data_type_var.get() != "BOOL":
                self.data_type_var.set("BOOL")
        else:
            self.data_type_combo.configure(values=REGISTER_DATA_TYPES)
            if self.data_type_var.get() not in REGISTER_DATA_TYPES:
                self.data_type_var.set("UINT16")
        data_type = self.data_type_var.get()
        variable_length = data_type in VARIABLE_LENGTH_TYPES and fc in (3, 4)
        if variable_length:
            self.quantity_entry.configure(state="normal")
            if not self.quantity_var.get().strip():
                self.quantity_var.set("1")
            unit_text = "Registers（可調整）"
        else:
            quantity = DATA_TYPE_UNITS.get(data_type, 1)
            self.quantity_var.set(str(quantity))
            self.quantity_entry.configure(state="disabled")
            unit_name = "Bit" if fc in (1, 2) else ("Register" if quantity == 1 else "Registers")
            unit_text = f"{unit_name}（自動）"
        self.quantity_unit.configure(text=unit_text)

        uses_bit_index = fc in (3, 4) and data_type == "BIT"
        self.bit_entry.configure(state="normal" if uses_bit_index else "disabled")
        if not uses_bit_index:
            self.bit_var.set("")

        if data_type in ("INT16", "UINT16") and fc in (3, 4):
            order_values = ("ABCD", "BADC")
            self.order_combo.configure(values=order_values, state="readonly")
            if self.order_var.get() not in order_values:
                self.order_var.set("ABCD")
        elif data_type in ("INT32", "UINT32", "FLOAT32") and fc in (3, 4):
            order_values = ("ABCD", "BADC", "CDAB", "DCBA")
            self.order_combo.configure(values=order_values, state="readonly")
            if self.order_var.get() not in order_values:
                self.order_var.set("ABCD")
        else:
            self.order_var.set("ABCD")
            self.order_combo.configure(values=("ABCD",), state="disabled")

        if uses_bit_index:
            bit_text = self.bit_var.get().strip()
            if bit_text and bit_text.isdigit() and 0 <= int(bit_text) <= 15:
                note = f"BIT 固定占用 1 Register；將讀取 Bit {bit_text}。"
            else:
                note = "BIT 固定占用 1 Register；Bit Index 必須輸入 0～15。"
        elif data_type == "BOOL" and fc in (3, 4):
            note = "BOOL：0 為 False、1 為 True，其他值標記 CONVERT_ERROR。"
        elif fc in (1, 2):
            note = "FC01／FC02 直接讀取 BOOL；Bit Index 不適用。"
        elif variable_length:
            note = f"{data_type} 可手動設定占用位址數；Bit Index 已停用。"
        else:
            quantity = DATA_TYPE_UNITS.get(data_type, 1)
            unit_name = "Register" if quantity == 1 else "Registers"
            note = f"{data_type} 固定占用 {quantity} {unit_name}；Bit Index 已停用。"
        self.data_type_note.configure(text=note)

    def _update_description_count(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        count = len(self.description_text.get("1.0", "end-1c"))
        self.description_count.configure(text=f"{count} 字")

    @staticmethod
    def _integer(value: str, label: str) -> int:
        text = value.strip()
        if not text:
            raise ValueError(f"{label}不得空白")
        try:
            return int(text, 10)
        except ValueError as exc:
            raise ValueError(f"{label}必須是整數") from exc

    @staticmethod
    def _number(value: str, label: str, default: float) -> float:
        text = value.strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError(f"{label}必須是數字") from exc

    def _build_draft(self) -> Point:
        name = self.name_entry.value()
        if not name:
            raise ValueError("點位名稱不得空白")
        device = self._device_by_label.get(self.device_var.get())
        if device is None:
            raise ValueError("請選擇通訊設備")
        address_text = self.address_entry.value()
        if not address_text:
            self.address_error.configure(text="地址值不得空白")
            raise ValueError("地址值不得空白")
        group_path = self.group_entry.value()
        if group_path.startswith("/") or group_path.endswith("/") or "//" in group_path or "\\" in group_path:
            raise ValueError("群組路徑請使用半形 / 分隔，且不得放在開頭、結尾或連續使用")
        tag_text = self.tags_entry.value()
        tags = [item.strip() for item in tag_text.split(",") if item.strip()]
        if ";" in tag_text or "；" in tag_text or "，" in tag_text:
            raise ValueError("標籤請使用半形逗號 , 分隔")

        fc = self._selected_fc()
        data_type = self.data_type_var.get()
        if data_type in VARIABLE_LENGTH_TYPES and fc in (3, 4):
            quantity = self._integer(self.quantity_var.get(), "占用位址數")
        else:
            quantity = DATA_TYPE_UNITS.get(data_type, 1)
        bit_index = self._integer(self.bit_var.get(), "Bit Index") if data_type == "BIT" and fc in (3, 4) else None
        byte_order = self.order_var.get() if data_type in ("INT16", "UINT16", "INT32", "UINT32", "FLOAT32") else "ABCD"
        return replace(
            self.original,
            name=name,
            display_name="",
            device_id=device.id,
            group_path=group_path,
            tags=tags,
            unit_id=self._integer(self.unit_id_var.get(), "Unit ID"),
            function_code=fc,
            address_mode=self._selected_address_mode(),
            address=self._integer(address_text, "地址值"),
            quantity=quantity,
            data_type=data_type,
            byte_order=byte_order,
            bit_index=bit_index,
            scale=self._number(self.scale_var.get(), "Scale", 1.0),
            offset=self._number(self.offset_var.get(), "Offset", 0.0),
            decimals=self._integer(self.decimals_var.get(), "小數位數"),
            engineering_unit=self.unit_entry.value(),
            scan_interval_ms=None,
            merge_mode=POINT_MERGE_VALUES[self.merge_var.get()],
            enabled=self.enabled_switch.get(),
            description=self.description_text.get("1.0", "end-1c").strip(),
        )

    def _accept(self) -> None:
        try:
            draft = self._build_draft()
            errors = draft.validate(self.project.device_map())
            if errors:
                raise ValueError("\n".join(errors))
            if any(item.id != draft.id and item.name.casefold() == draft.name.casefold() for item in self.project.points):
                raise ValueError("點位名稱不得重複")
        except ValueError as exc:
            messagebox.showerror("點位設定錯誤", str(exc), parent=self)
            return
        self.result = draft
        self.destroy()


class KafeiApp(tk.Tk):
    REFRESH_MS = 500

    def __init__(self) -> None:
        super().__init__()
        self.title("磨杯咖啡 MODBUS KAFEI v0.1.5")
        self._set_window_icon()
        self.geometry("1320x780")
        self.minsize(980, 600)
        self.project = Project()
        self.project_path: Path | None = None
        self.engine: PollingEngine | None = None
        self.dirty = False
        self._last_debug_size = -1
        self._build_style()
        self._build_menu()
        self._build_toolbar()
        self._build_notebook()
        self._build_statusbar()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(self.REFRESH_MS, self._refresh_runtime)
        self.after(30_000, self._autosave_tick)
        self._refresh_all()

    def _set_window_icon(self) -> None:
        self._app_icon: tk.PhotoImage | None = None
        self._icon_ico_path: str | None = None
        self._native_icon_handles: list[int] = []
        try:
            icon_png = resource_path("assets/kafei-coffee.png")
            self._app_icon = tk.PhotoImage(file=str(icon_png))
            self.iconphoto(True, self._app_icon)
        except (OSError, tk.TclError):
            self._app_icon = None
        if sys.platform == "win32":
            try:
                self._icon_ico_path = str(resource_path("assets/kafei-coffee.ico"))
                self.iconbitmap(self._icon_ico_path)
            except (OSError, tk.TclError):
                self._icon_ico_path = None
        self.after_idle(self._reapply_window_icon)

    def _reapply_window_icon(self) -> None:
        if self._app_icon is not None:
            self.iconphoto(True, self._app_icon)
        if self._icon_ico_path is not None:
            try:
                self.iconbitmap(self._icon_ico_path)
            except tk.TclError:
                pass
        self._apply_native_windows_icon()

    def _apply_native_windows_icon(self) -> None:
        if sys.platform != "win32" or self._icon_ico_path is None:
            return
        try:
            user32 = ctypes.windll.user32
            user32.LoadImageW.restype = ctypes.c_void_p
            user32.GetParent.restype = ctypes.c_void_p
            user32.SendMessageW.restype = ctypes.c_ssize_t
            window = int(self.winfo_id())
            parent = user32.GetParent(ctypes.c_void_p(window))
            targets = {window, int(parent)} if parent else {window}
            for icon_kind, size in ((0, 16), (1, 32), (2, 16)):
                handle = user32.LoadImageW(None, self._icon_ico_path, 1, size, size, 0x10)
                if not handle:
                    continue
                self._native_icon_handles.append(int(handle))
                for target in targets:
                    user32.SendMessageW(
                        ctypes.c_void_p(target),
                        0x0080,
                        icon_kind,
                        ctypes.c_void_p(handle),
                    )
        except (AttributeError, OSError, tk.TclError):
            pass

    def _build_style(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=("Microsoft JhengHei UI", 9))
        style.configure("TButton", padding=(10, 6))
        style.configure("Toolbar.TFrame", background="#EEF3F6")
        style.configure("Page.TFrame", background="#F7F9FB")
        style.configure("PageHeader.TFrame", background="#F7F9FB")
        style.configure("PageTitle.TLabel", background="#F7F9FB", foreground="#123B48", font=("Microsoft JhengHei UI", 14, "bold"))
        style.configure("PageSubtitle.TLabel", background="#F7F9FB", foreground="#64748B", font=("Microsoft JhengHei UI", 9))
        style.configure("Kafei.TNotebook", background="#DCE6EB", borderwidth=0, tabmargins=(8, 8, 8, 0))
        style.configure(
            "Kafei.TNotebook.Tab",
            padding=(26, 11),
            background="#D7E2E8",
            foreground="#334155",
            font=("Microsoft JhengHei UI", 10, "bold"),
            borderwidth=0,
        )
        style.map(
            "Kafei.TNotebook.Tab",
            background=[("selected", "#0F4C5C"), ("active", "#BFD2DA")],
            foreground=[("selected", "#FFFFFF"), ("active", "#123B48")],
            padding=[("selected", (26, 11))],
        )
        style.configure(
            "Treeview",
            rowheight=27,
            background="#FFFFFF",
            fieldbackground="#FFFFFF",
            foreground="#1F2937",
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background="#2F7180",
            foreground="#FFFFFF",
            font=("Microsoft JhengHei UI", 9, "bold"),
            padding=(7, 7),
            relief="flat",
        )
        style.map("Treeview.Heading", background=[("active", "#245B68")])
        style.map("Treeview", background=[("selected", "#B9DCE7")], foreground=[("selected", "#102A33")])
        style.configure("Good.TLabel", foreground="#167c3a")
        style.configure("Error.TLabel", foreground="#b3261e")

    def _build_menu(self) -> None:
        menu = tk.Menu(self, tearoff=False)
        self.menu_bar = menu
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="新增專案", command=self._new_project, accelerator="Ctrl+N")
        file_menu.add_command(label="開啟專案…", command=self._open_project, accelerator="Ctrl+O")
        file_menu.add_command(label="保存", command=self._save, accelerator="Ctrl+S")
        file_menu.add_command(label="另存新檔…", command=self._save_as)
        file_menu.add_separator()
        file_menu.add_command(label="匯入點位 CSV…", command=self._import_csv)
        file_menu.add_command(label="下載 CSV 範本…", command=self._save_template)
        file_menu.add_command(label="匯出最新結果…", command=self._export_latest)
        file_menu.add_command(label="匯出除錯資料…", command=self._export_debug)
        file_menu.add_separator()
        file_menu.add_command(label="結束", command=self._close)
        menu.add_cascade(label="檔案", menu=file_menu)
        communication = tk.Menu(menu, tearoff=False)
        communication.add_command(label="全部啟動", command=self._start)
        communication.add_command(label="全部停止", command=self._stop)
        communication.add_command(label="清除除錯記錄", command=self._clear_debug)
        menu.add_cascade(label="通訊", menu=communication)
        menu.add_command(label="說明", command=self._show_about)
        self.config(menu=menu)
        self.bind_all("<Control-n>", lambda _event: self._new_project())
        self.bind_all("<Control-o>", lambda _event: self._open_project())
        self.bind_all("<Control-s>", lambda _event: self._save())

    def _show_about(self) -> None:
        AboutDialog(self)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(10, 8), style="Toolbar.TFrame")
        bar.pack(fill="x")
        for label, command in (
            ("新增", self._new_project), ("開啟", self._open_project), ("保存", self._save),
            ("匯入 CSV", self._import_csv), ("▶ 全部啟動", self._start), ("■ 全部停止", self._stop),
        ):
            ttk.Button(bar, text=label, command=command).pack(side="left", padx=(0, 6))
        self.optimization_label = ttk.Label(bar, text="請求最佳化：—")
        self.optimization_label.pack(side="right")

    def _build_notebook(self) -> None:
        self.notebook = ttk.Notebook(self, style="Kafei.TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(4, 7))
        self._build_devices_tab()
        self._build_points_tab()
        self._build_debug_tab()

    @staticmethod
    def _page_heading(parent: tk.Misc, title: str, subtitle: str) -> None:
        heading = ttk.Frame(parent, style="PageHeader.TFrame")
        heading.pack(fill="x", pady=(0, 9))
        ttk.Label(heading, text=title, style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(heading, text=subtitle, style="PageSubtitle.TLabel").pack(anchor="w", pady=(2, 0))

    @staticmethod
    def _configure_tree_tags(tree: ttk.Treeview) -> None:
        tree.tag_configure("row-even", background="#FFFFFF")
        tree.tag_configure("row-odd", background="#F1F6F8")
        tree.tag_configure("state-good", foreground="#166534")
        tree.tag_configure("state-warning", foreground="#9A6700")
        tree.tag_configure("state-error", foreground="#B42318")
        tree.tag_configure("state-disabled", foreground="#8491A3")

    @staticmethod
    def _tree_items(tree: ttk.Treeview, parent: str = "") -> list[str]:
        items: list[str] = []
        for item in tree.get_children(parent):
            items.append(item)
            items.extend(KafeiApp._tree_items(tree, item))
        return items

    def _auto_size_tree_column(self, tree: ttk.Treeview, column: str) -> int:
        style = ttk.Style(tree)
        tree_style = str(tree.cget("style") or "Treeview")
        heading_style = f"{tree_style}.Heading" if tree_style != "Treeview" else "Treeview.Heading"
        body_font = tkfont.Font(root=tree, font=style.lookup(tree_style, "font") or "TkDefaultFont")
        heading_font = tkfont.Font(root=tree, font=style.lookup(heading_style, "font") or "TkHeadingFont")
        heading_text = str(tree.heading(column, "text") or "")
        required = heading_font.measure(heading_text) + 28
        for item in self._tree_items(tree):
            value = str(tree.set(item, column) or "")
            for line in value.splitlines() or ("",):
                required = max(required, body_font.measure(line) + 24)
        min_width = int(tree.column(column, "minwidth") or 50)
        width = max(min_width, min(required, 900))
        tree.column(column, width=width)
        return width

    def _tree_header_double_click(self, event: tk.Event) -> str | None:
        tree = event.widget
        if not isinstance(tree, ttk.Treeview) or tree.identify_region(event.x, event.y) != "separator":
            return None
        column_ref = tree.identify_column(event.x)
        try:
            display_index = int(column_ref.removeprefix("#")) - 1
        except ValueError:
            return None
        display_columns = tree.cget("displaycolumns")
        if display_columns in ("#all", ("#all",)):
            columns = list(tree.cget("columns"))
        else:
            columns = list(display_columns)
        if not 0 <= display_index < len(columns):
            return None
        self._auto_size_tree_column(tree, str(columns[display_index]))
        return "break"

    def _bind_tree_autosize(self, tree: ttk.Treeview) -> None:
        tree.bind("<Double-1>", self._tree_header_double_click, add="+")

    def _tree(self, parent: tk.Misc, columns: tuple[str, ...], headings: tuple[str, ...], widths: tuple[int, ...]) -> ttk.Treeview:
        frame = ttk.Frame(parent, padding=1)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        ybar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        xbar = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        for column, heading, width in zip(columns, headings, widths):
            tree.heading(column, text=heading)
            tree.column(column, width=width, minwidth=50, anchor="w")
        self._configure_tree_tags(tree)
        self._bind_tree_autosize(tree)
        return tree

    def _device_trees(
        self,
        parent: tk.Misc,
        columns: tuple[str, ...],
        headings: tuple[str, ...],
        widths: tuple[int, ...],
    ) -> tuple[ttk.Treeview, ttk.Treeview]:
        frame = ttk.Frame(parent, padding=1)
        frame.pack(fill="both", expand=True)
        device_tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        ping_tree = ttk.Treeview(frame, columns=("ping",), show="headings", selectmode="none", takefocus=False)
        ybar = ttk.Scrollbar(frame, orient="vertical", command=device_tree.yview)
        xbar = ttk.Scrollbar(frame, orient="horizontal", command=device_tree.xview)

        def device_scrolled(first: str, last: str) -> None:
            ybar.set(first, last)
            ping_tree.yview_moveto(first)

        device_tree.configure(yscrollcommand=device_scrolled, xscrollcommand=xbar.set)
        ping_tree.grid(row=0, column=0, sticky="ns")
        device_tree.grid(row=0, column=1, sticky="nsew")
        ybar.grid(row=0, column=2, sticky="ns")
        xbar.grid(row=1, column=1, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        for column, heading, width in zip(columns, headings, widths):
            device_tree.heading(column, text=heading)
            device_tree.column(column, width=width, minwidth=50, anchor="w")
        ping_tree.heading("ping", text="PING")
        ping_tree.column("ping", width=86, minwidth=86, stretch=False, anchor="center")
        self._configure_tree_tags(device_tree)
        self._configure_tree_tags(ping_tree)
        self._bind_tree_autosize(device_tree)
        self._bind_tree_autosize(ping_tree)
        ping_tree.tag_configure("ping-reachable", foreground="#16A34A", font=("Segoe UI Symbol", 13, "bold"))
        ping_tree.tag_configure("ping-unreachable", foreground="#8491A3")

        def scroll_ping(event: tk.Event) -> str:
            units = -1 if event.delta > 0 else 1
            device_tree.yview_scroll(units, "units")
            return "break"

        def select_from_ping(event: tk.Event) -> str:
            row = ping_tree.identify_row(event.y)
            if row and device_tree.exists(row):
                device_tree.selection_set(row)
                device_tree.focus(row)
            return "break"

        ping_tree.bind("<MouseWheel>", scroll_ping)
        ping_tree.bind("<Button-1>", select_from_ping)
        return device_tree, ping_tree

    def _build_devices_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12, style="Page.TFrame")
        self.notebook.add(tab, text="通訊設備")
        self._page_heading(tab, "通訊設備", "設定 Modbus TCP 目標、查看連線狀態與通訊統計")
        buttons = ttk.Frame(tab, style="PageHeader.TFrame")
        buttons.pack(fill="x", pady=(0, 6))
        for label, command in (("新增", self._add_device), ("編輯", self._edit_device), ("複製", self._copy_device), ("刪除", self._delete_device), ("切換啟用", self._toggle_devices)):
            ttk.Button(buttons, text=label, command=command).pack(side="left", padx=(0, 6))
        columns = ("enabled", "name", "host", "port", "scan", "merge", "status", "ok", "fail", "avg", "max", "error")
        headings = ("啟用", "設備", "IP / Hostname", "Port", "週期 ms", "合併", "狀態", "成功", "失敗", "平均 ms", "最大 ms", "最近錯誤")
        widths = (55, 140, 180, 65, 80, 75, 90, 70, 70, 85, 85, 260)
        self.device_tree, self.device_ping_tree = self._device_trees(tab, columns, headings, widths)
        self.device_tree.bind(
            "<Double-1>",
            lambda event: self._edit_device() if self.device_tree.identify_region(event.x, event.y) == "cell" else None,
            add="+",
        )

    def _build_points_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12, style="Page.TFrame")
        self.notebook.add(tab, text="點位管理")
        self._page_heading(tab, "點位管理", "依群組、關鍵字與品質快速檢視即時點位")
        actions = ttk.Frame(tab, style="PageHeader.TFrame")
        actions.pack(fill="x", pady=(0, 6))
        for label, command in (("新增", self._add_point), ("編輯", self._edit_point), ("複製並遞增", self._copy_point), ("刪除", self._delete_points), ("切換啟用", self._toggle_points)):
            ttk.Button(actions, text=label, command=command).pack(side="left", padx=(0, 6))

        filters = ttk.Frame(tab, style="PageHeader.TFrame")
        filters.pack(fill="x", pady=(0, 8))
        ttk.Label(filters, text="搜尋").pack(side="left", padx=(0, 5))
        self.search_var = tk.StringVar()
        search = ttk.Entry(filters, textvariable=self.search_var, width=28)
        search.pack(side="left")
        search.bind("<KeyRelease>", lambda _event: self._refresh_points())
        ttk.Label(filters, text="群組").pack(side="left", padx=(18, 5))
        self.group_var = tk.StringVar(value=ALL_GROUPS)
        self.group_combo = ttk.Combobox(filters, textvariable=self.group_var, state="readonly", width=24, values=(ALL_GROUPS,))
        self.group_combo.pack(side="left")
        self.group_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_points())
        ttk.Label(filters, text="品質").pack(side="left", padx=(18, 5))
        self.quality_var = tk.StringVar(value="全部")
        quality = ttk.Combobox(filters, textvariable=self.quality_var, state="readonly", width=16, values=("全部",) + tuple(item.value for item in Quality))
        quality.pack(side="left")
        quality.bind("<<ComboboxSelected>>", lambda _event: self._refresh_points())
        columns = ("enabled", "device", "group", "name", "reference", "raw", "fc", "unit_id", "type", "value", "eng_unit", "quality", "response", "updated", "error")
        headings = ("啟用", "設備", "群組", "點位", "文件地址", "通訊地址", "FC", "Unit", "型別", "工程值", "單位", "品質", "回應 ms", "更新時間", "錯誤")
        widths = (55, 120, 110, 160, 85, 85, 50, 55, 75, 115, 65, 110, 80, 160, 240)
        self.point_tree = self._tree(tab, columns, headings, widths)
        self._point_range_anchor: str | None = None
        self._point_drag_candidate: str | None = None
        self._point_drag_original_order: tuple[str, ...] = ()
        self._point_drag_start_y = 0
        self._point_drag_started = False
        self.point_tree.bind("<Button-1>", self._remember_point_range_anchor, add="+")
        self.point_tree.bind("<Shift-Button-1>", self._select_point_range)
        self.point_tree.bind("<ButtonPress-1>", self._point_drag_press, add="+")
        self.point_tree.bind("<B1-Motion>", self._point_drag_motion, add="+")
        self.point_tree.bind("<ButtonRelease-1>", self._point_drag_release, add="+")
        self.point_tree.bind(
            "<Double-1>",
            lambda event: self._edit_point() if self.point_tree.identify_region(event.x, event.y) == "cell" else None,
            add="+",
        )

    def _remember_point_range_anchor(self, event: tk.Event) -> None:
        if event.state & 0x0001:
            return
        row = self.point_tree.identify_row(event.y)
        if row:
            self._point_range_anchor = row

    def _select_point_range(self, event: tk.Event) -> str | None:
        target = self.point_tree.identify_row(event.y)
        if not target:
            return None
        self._select_point_range_to(target, additive=bool(event.state & 0x0004))
        return "break"

    def _select_point_range_to(self, target: str, *, additive: bool = False) -> None:
        rows = list(self.point_tree.get_children())
        if target not in rows:
            return
        anchor = self._point_range_anchor
        if anchor not in rows:
            focused = self.point_tree.focus()
            selected = self.point_tree.selection()
            anchor = focused if focused in rows else (selected[0] if selected else target)
            self._point_range_anchor = anchor
        start, end = sorted((rows.index(anchor), rows.index(target)))
        range_rows = rows[start : end + 1]
        if additive:
            self.point_tree.selection_add(*range_rows)
        else:
            self.point_tree.selection_set(*range_rows)
        self.point_tree.focus(target)
        self.point_tree.see(target)

    def _point_drag_press(self, event: tk.Event) -> None:
        self._point_drag_candidate = None
        self._point_drag_original_order = ()
        self._point_drag_started = False
        if event.state & (0x0001 | 0x0004):
            return
        if self.point_tree.identify_region(event.x, event.y) != "cell":
            return
        row = self.point_tree.identify_row(event.y)
        selected = self.point_tree.selection()
        if row and len(selected) <= 1:
            self._point_drag_candidate = row
            self._point_drag_original_order = tuple(self.point_tree.get_children())
            self._point_drag_start_y = event.y

    def _point_drag_motion(self, event: tk.Event) -> str | None:
        item = self._point_drag_candidate
        if item is None or not self.point_tree.exists(item) or self.point_tree.selection() != (item,):
            return None
        if not self._point_drag_started and abs(event.y - self._point_drag_start_y) < 4:
            return None
        self._point_drag_started = True
        self.point_tree.configure(cursor="fleur")

        height = self.point_tree.winfo_height()
        if event.y < 28:
            self.point_tree.yview_scroll(-1, "units")
        elif event.y > height - 28:
            self.point_tree.yview_scroll(1, "units")

        target = self.point_tree.identify_row(event.y)
        if target and target != item:
            target_index = self.point_tree.index(target)
            bbox = self.point_tree.bbox(target)
            if bbox and event.y >= bbox[1] + bbox[3] // 2:
                target_index += 1
            current_index = self.point_tree.index(item)
            if current_index < target_index:
                target_index -= 1
            self.point_tree.move(item, "", target_index)
            self.point_tree.see(item)
        elif not target and event.y > height - 28:
            self.point_tree.move(item, "", "end")
            self.point_tree.see(item)
        return "break"

    def _point_drag_release(self, _event: tk.Event) -> str | None:
        item = self._point_drag_candidate
        started = self._point_drag_started
        original_order = self._point_drag_original_order
        self._point_drag_candidate = None
        self._point_drag_original_order = ()
        self._point_drag_started = False
        self.point_tree.configure(cursor="")
        if item is None or not started:
            return None
        visible_order = tuple(self.point_tree.get_children())
        if visible_order != original_order and reorder_points_by_visible_order(self.project, visible_order):
            self.dirty = True
            self.status_var.set("點位順序已更新")
        self._refresh_points()
        if self.point_tree.exists(item):
            self.point_tree.selection_set(item)
            self.point_tree.focus(item)
            self.point_tree.see(item)
        self._point_range_anchor = item
        return "break"

    def _build_debug_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12, style="Page.TFrame")
        self.notebook.add(tab, text="通訊除錯")
        self._page_heading(tab, "通訊除錯", "檢視合併區塊、Request／Response Hex、回應時間與錯誤")
        tools = ttk.Frame(tab, style="PageHeader.TFrame")
        tools.pack(fill="x", pady=(0, 6))
        ttk.Button(tools, text="清除", command=self._clear_debug).pack(side="left", padx=(0, 6))
        ttk.Button(tools, text="匯出", command=self._export_debug).pack(side="left")
        self.debug_label = ttk.Label(tools, text="固定容量：0 / 2000")
        self.debug_label.pack(side="right")
        columns = ("time", "device", "tid", "unit", "fc", "start", "quantity", "elapsed", "retry", "error", "points", "request", "response")
        headings = ("時間", "設備", "TID", "Unit", "FC", "起始", "數量", "回應 ms", "重試", "Exception / Error", "區塊點位", "Request Hex", "Response Hex")
        widths = (105, 120, 65, 55, 45, 70, 55, 80, 50, 230, 200, 330, 330)
        self.debug_tree = self._tree(tab, columns, headings, widths)

    def _build_statusbar(self) -> None:
        frame = ttk.Frame(self, padding=(8, 3))
        frame.pack(fill="x")
        self.status_var = tk.StringVar(value="就緒")
        ttk.Label(frame, textvariable=self.status_var).pack(side="left")
        self.project_label = ttk.Label(frame, text="未命名專案")
        self.project_label.pack(side="right")

    def _mutating(self) -> None:
        if self.engine and self.engine.running:
            self._stop()
        self.dirty = True

    def _device_fields(self, device: Device) -> list[tuple[str, str, object, tuple[str, ...] | None]]:
        return [
            ("name", "設備名稱", device.name, None), ("host", "IP / Hostname", device.host, None),
            ("port", "TCP Port", device.port, None), ("connect_timeout", "連線逾時秒", device.connect_timeout, None),
            ("request_timeout", "請求逾時秒", device.request_timeout, None), ("retries", "單次重試", device.retries, None),
            ("scan", "掃描週期 ms", device.scan_interval_ms, None), ("merge", "合併模式", device.merge_mode, ("auto", "strict", "none")),
            ("max_block", "最大讀取區塊", device.max_read_block, None), ("gap", "允許空白位址", device.allowed_gap, None),
            ("group", "設備群組", device.group, None), ("enabled", "啟用", str(device.enabled).lower(), ("true", "false")),
            ("notes", "備註", device.notes, None),
        ]

    @staticmethod
    def _apply_device_values(device: Device, data: dict[str, str]) -> None:
        device.name, device.host = data["name"], data["host"]
        device.port = int(data["port"])
        device.connect_timeout, device.request_timeout = float(data["connect_timeout"]), float(data["request_timeout"])
        device.retries, device.scan_interval_ms = int(data["retries"]), int(data["scan"])
        device.merge_mode, device.max_read_block, device.allowed_gap = data["merge"], int(data["max_block"]), int(data["gap"])
        device.group, device.enabled, device.notes = data["group"], data["enabled"] == "true", data["notes"]

    def _add_device(self) -> None:
        device = Device(name=f"設備-{len(self.project.devices) + 1}")
        dialog = FormDialog(self, "新增設備", self._device_fields(device))
        if not dialog.result:
            return
        try:
            self._apply_device_values(device, dialog.result)
            errors = device.validate()
            if errors:
                raise ValueError("\n".join(errors))
            if any(item.name.casefold() == device.name.casefold() for item in self.project.devices):
                raise ValueError("設備名稱不得重複")
        except ValueError as exc:
            messagebox.showerror("設備設定錯誤", str(exc), parent=self)
            return
        self._mutating()
        self.project.devices.append(device)
        self._refresh_all()

    def _selected_device(self) -> Device | None:
        selection = self.device_tree.selection()
        return next((item for item in self.project.devices if selection and item.id == selection[0]), None)

    def _edit_device(self) -> None:
        device = self._selected_device()
        if not device:
            return
        draft = replace(device)
        dialog = FormDialog(self, "編輯設備", self._device_fields(draft))
        if not dialog.result:
            return
        try:
            self._apply_device_values(draft, dialog.result)
            errors = draft.validate()
            if errors:
                raise ValueError("\n".join(errors))
            if any(item.id != draft.id and item.name.casefold() == draft.name.casefold() for item in self.project.devices):
                raise ValueError("設備名稱不得重複")
        except ValueError as exc:
            messagebox.showerror("設備設定錯誤", str(exc), parent=self)
            return
        self._mutating()
        self.project.devices[self.project.devices.index(device)] = draft
        self._refresh_all()

    def _copy_device(self) -> None:
        device = self._selected_device()
        if not device:
            return
        self._mutating()
        self.project.devices.append(replace(device, id=new_id(), name=f"{device.name} - 複製"))
        self._refresh_all()

    def _delete_device(self) -> None:
        device = self._selected_device()
        if not device:
            return
        related = [point for point in self.project.points if point.device_id == device.id]
        if not messagebox.askyesno("刪除設備", f"設備「{device.name}」關聯 {len(related)} 個點位。\n刪除設備將一併刪除這些點位，是否繼續？", parent=self):
            return
        self._mutating()
        self.project.devices.remove(device)
        self.project.points = [point for point in self.project.points if point.device_id != device.id]
        self._refresh_all()

    def _toggle_devices(self) -> None:
        selected = set(self.device_tree.selection())
        if not selected:
            return
        self._mutating()
        for device in self.project.devices:
            if device.id in selected:
                device.enabled = not device.enabled
        self._refresh_all()

    def _add_point(self) -> None:
        if not self.project.devices:
            messagebox.showinfo("先建立設備", "新增點位前必須先建立設備。", parent=self)
            return
        point = Point(name=f"點位-{len(self.project.points) + 1}", device_id=self.project.devices[0].id)
        self._show_point_dialog(point, adding=True)

    def _selected_point(self) -> Point | None:
        selection = self.point_tree.selection()
        return next((item for item in self.project.points if selection and item.id == selection[0]), None)

    def _edit_point(self) -> None:
        point = self._selected_point()
        if point:
            self._show_point_dialog(point, adding=False)

    def _show_point_dialog(self, point: Point, *, adding: bool) -> None:
        dialog = PointDialog(self, self.project, point, adding=adding)
        draft = dialog.result
        if draft is None:
            return
        self._mutating()
        if adding:
            self.project.points.append(draft)
        else:
            self.project.points[self.project.points.index(point)] = draft
        self._refresh_all()

    def _copy_point(self) -> None:
        point = self._selected_point()
        if not point:
            return
        try:
            draft = create_incremented_copy(self.project, point)
        except ValueError as exc:
            messagebox.showerror("無法複製點位", str(exc), parent=self)
            return
        self._mutating()
        self.project.points.append(draft)
        self._refresh_all()
        if self.point_tree.exists(draft.id):
            self.point_tree.selection_set(draft.id)
            self.point_tree.focus(draft.id)
            self.point_tree.see(draft.id)

    def _delete_points(self) -> None:
        selected = set(self.point_tree.selection())
        if not selected or not messagebox.askyesno("刪除點位", f"確定刪除 {len(selected)} 個點位？", parent=self):
            return
        self._mutating()
        self.project.points = [point for point in self.project.points if point.id not in selected]
        self._refresh_all()

    def _toggle_points(self) -> None:
        selected = set(self.point_tree.selection())
        if not selected:
            return
        self._mutating()
        for point in self.project.points:
            if point.id in selected:
                point.enabled = not point.enabled
        self._refresh_all()

    def _start(self) -> None:
        if self.engine and self.engine.running:
            return
        errors = self.project.validate()
        if errors:
            messagebox.showerror("專案設定錯誤", "\n".join(errors[:20]), parent=self)
            return
        self.engine = PollingEngine(self.project)
        self.engine.start()
        self.status_var.set("通訊運行中")
        self._refresh_all()

    def _stop(self) -> None:
        if self.engine:
            self.status_var.set("正在停止通訊…")
            self.update_idletasks()
            self.engine.stop(timeout=5)
        self.status_var.set("通訊已停止")
        self._refresh_all()

    def _new_project(self) -> None:
        if not self._confirm_discard():
            return
        self._stop()
        self.project, self.project_path, self.engine, self.dirty = Project(), None, None, False
        self._refresh_all()

    def _open_project(self) -> None:
        if not self._confirm_discard():
            return
        selected = filedialog.askopenfilename(parent=self, title="開啟 KAFEI 專案", filetypes=(("KAFEI 專案", "*.kafei"), ("所有檔案", "*.*")))
        if not selected:
            return
        path = Path(selected)
        source = path
        recovery = recoverable_autosave(path)
        if recovery and messagebox.askyesno("發現復原資料", "發現較新的自動保存資料，是否載入復原版本？", parent=self):
            source = recovery
        try:
            project = load_project(source)
        except (OSError, ValueError) as exc:
            messagebox.showerror("無法開啟專案", str(exc), parent=self)
            return
        self._stop()
        self.project, self.project_path, self.engine = project, path, None
        self.dirty = source != path
        self._refresh_all()

    def _save(self) -> bool:
        if self.project_path is None:
            return self._save_as()
        try:
            self.project_path = save_project(self.project, self.project_path)
            self.dirty = False
            self.status_var.set(f"已保存：{self.project_path.name}")
            self._refresh_title()
            return True
        except (OSError, ValueError) as exc:
            messagebox.showerror("保存失敗", str(exc), parent=self)
            return False

    def _save_as(self) -> bool:
        selected = filedialog.asksaveasfilename(parent=self, title="保存 KAFEI 專案", defaultextension=".kafei", filetypes=(("KAFEI 專案", "*.kafei"),))
        if not selected:
            return False
        self.project_path = Path(selected)
        return self._save()

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        choice = messagebox.askyesnocancel("未保存變更", "專案有未保存變更，是否先保存？", parent=self)
        if choice is None:
            return False
        return self._save() if choice else True

    def _import_csv(self) -> None:
        selected = filedialog.askopenfilename(parent=self, title="匯入點位 CSV", filetypes=(("CSV", "*.csv"), ("所有檔案", "*.*")))
        if not selected:
            return
        try:
            result = import_points_csv(selected, self.project)
        except (OSError, UnicodeError, csv.Error) as exc:
            messagebox.showerror("CSV 讀取失敗", str(exc), parent=self)
            return
        lines = [f"新增：{result.added}", f"更新：{result.updated}", f"忽略：{result.ignored}", f"錯誤：{result.error_count}", f"警告：{result.warning_count}"]
        lines.extend(f"第 {item.row} 行 [{item.field}] {item.message}；建議：{item.suggestion}" for item in result.issues[:15])
        if not result.can_apply:
            messagebox.showerror("匯入驗證失敗（未套用任何變更）", "\n".join(lines), parent=self)
            return
        if not messagebox.askyesno("匯入預覽", "\n".join(lines) + "\n\n是否套用？", parent=self):
            return
        self._mutating()
        self.project = result.project
        self._refresh_all()

    def _save_template(self) -> None:
        selected = filedialog.asksaveasfilename(parent=self, title="保存 CSV 範本", defaultextension=".csv", filetypes=(("CSV", "*.csv"),))
        if selected:
            try:
                write_csv_template(selected)
            except OSError as exc:
                messagebox.showerror("保存失敗", str(exc), parent=self)

    def _export_latest(self) -> None:
        selected = filedialog.asksaveasfilename(parent=self, title="匯出最新結果", defaultextension=".csv", filetypes=(("CSV", "*.csv"),))
        if not selected:
            return
        try:
            export_latest_csv(selected, self.project, self.engine.states_snapshot() if self.engine else {})
        except OSError as exc:
            messagebox.showerror("匯出失敗", f"檔案可能被其他程式占用。\n{exc}", parent=self)

    def _export_debug(self) -> None:
        selected = filedialog.asksaveasfilename(parent=self, title="匯出除錯資料", defaultextension=".csv", filetypes=(("CSV", "*.csv"),))
        if not selected:
            return
        try:
            export_debug_csv(selected, self.engine.debug_snapshot() if self.engine else [])
        except OSError as exc:
            messagebox.showerror("匯出失敗", f"檔案可能被其他程式占用。\n{exc}", parent=self)

    def _clear_debug(self) -> None:
        if self.engine:
            self.engine.clear_debug()
        self._last_debug_size = -1
        self._refresh_debug()

    def _refresh_all(self) -> None:
        self._refresh_devices()
        self._refresh_group_filter()
        self._refresh_points()
        self._refresh_debug()
        original, merged, reduction = optimization_summary(self.project)
        self.optimization_label.config(text=f"原始 {original} → 合併 {merged}（減少 {reduction:.1f}%）")
        self._refresh_title()

    def _refresh_group_filter(self) -> None:
        values = group_filter_values(self.project.points)
        current = self.group_var.get()
        self.group_combo.configure(values=values)
        if current not in values:
            self.group_var.set(ALL_GROUPS)

    def _refresh_title(self) -> None:
        marker = " *" if self.dirty else ""
        name = self.project_path.name if self.project_path else self.project.name
        self.title(f"磨杯咖啡 MODBUS KAFEI v0.1.5 — {name}{marker}")
        self.project_label.config(text=str(self.project_path or name))

    def _refresh_devices(self) -> None:
        selected = set(self.device_tree.selection())
        view = self.device_tree.yview()
        self.device_tree.delete(*self.device_tree.get_children())
        self.device_ping_tree.delete(*self.device_ping_tree.get_children())
        stats = self.engine.stats_snapshot() if self.engine else {}
        for row_index, device in enumerate(self.project.devices):
            item = stats.get(device.id)
            status = "運行中" if self.engine and self.engine.running and device.enabled else ("停用" if not device.enabled else "停止")
            if item and item.consecutive_failures:
                status = "重連等待"
            values = (
                "是" if device.enabled else "否", device.name, device.host, device.port, device.scan_interval_ms, device.merge_mode,
                status, item.successful if item else 0, item.failed if item else 0,
                f"{item.average_response_ms:.1f}" if item else "—", f"{item.max_response_ms:.1f}" if item else "—", item.last_error if item else "",
            )
            tags = ["row-even" if row_index % 2 == 0 else "row-odd"]
            ping_text = ""
            ping_tags = list(tags)
            if self.engine and self.engine.running and device.enabled and item:
                if item.ping_reachable is True:
                    ping_text = "●"
                    ping_tags.append("ping-reachable")
                elif item.ping_reachable is False:
                    ping_text = "無回應"
                    ping_tags.append("ping-unreachable")
            if not device.enabled:
                tags.append("state-disabled")
            elif item and item.consecutive_failures:
                tags.append("state-error")
            elif self.engine and self.engine.running:
                tags.append("state-good")
            self.device_tree.insert("", "end", iid=device.id, values=values, tags=tuple(tags))
            self.device_ping_tree.insert("", "end", iid=device.id, values=(ping_text,), tags=tuple(ping_tags))
        for item in selected:
            if self.device_tree.exists(item):
                self.device_tree.selection_add(item)
        if view:
            self.device_tree.yview_moveto(view[0])

    def _refresh_points(self) -> None:
        if self._point_drag_candidate is not None:
            return
        selected = set(self.point_tree.selection())
        focused = self.point_tree.focus()
        view = self.point_tree.yview()
        self.point_tree.delete(*self.point_tree.get_children())
        states = self.engine.states_snapshot() if self.engine else {}
        devices = self.project.device_map()
        query = self.search_var.get().strip().casefold()
        group_filter = self.group_var.get()
        quality_filter = self.quality_var.get()
        displayed_index = 0
        for point in self.project.points:
            if not matches_group_filter(point.group_path, group_filter):
                continue
            state = states.get(point.id)
            quality = state.quality if state else (Quality.DISABLED if not point.enabled else Quality.STALE)
            searchable = " ".join((point.name, point.display_name, point.group_path, " ".join(point.tags), point.description, devices.get(point.device_id).name if point.device_id in devices else "")).casefold()
            if query and query not in searchable:
                continue
            if quality_filter != "全部" and quality.value != quality_filter:
                continue
            updated = state.updated_at.strftime("%Y-%m-%d %H:%M:%S") if state and state.updated_at else "—"
            response = f"{state.response_ms:.1f}" if state and state.response_ms is not None else "—"
            values = (
                "是" if point.enabled else "否", devices.get(point.device_id).name if point.device_id in devices else "?", point.group_path,
                point.display_name or point.name, point.document_address if point.document_address is not None else "—", point.raw_address, f"{point.function_code:02d}", point.unit_id,
                point.data_type, format_value(state.value, point.decimals) if state else "—", point.engineering_unit, quality.value, response, updated,
                state.last_error if state else "",
            )
            tags = ["row-even" if displayed_index % 2 == 0 else "row-odd"]
            if quality is Quality.GOOD:
                tags.append("state-good")
            elif quality is Quality.DISABLED:
                tags.append("state-disabled")
            elif quality is Quality.STALE:
                tags.append("state-warning")
            else:
                tags.append("state-error")
            self.point_tree.insert("", "end", iid=point.id, values=values, tags=tuple(tags))
            displayed_index += 1
        for item in selected:
            if self.point_tree.exists(item):
                self.point_tree.selection_add(item)
        if focused and self.point_tree.exists(focused):
            self.point_tree.focus(focused)
        if self._point_range_anchor and not self.point_tree.exists(self._point_range_anchor):
            self._point_range_anchor = None
        if view:
            self.point_tree.yview_moveto(view[0])

    def _refresh_debug(self) -> None:
        records = self.engine.debug_snapshot() if self.engine else []
        if len(records) == self._last_debug_size:
            return
        self._last_debug_size = len(records)
        self.debug_tree.delete(*self.debug_tree.get_children())
        for index, record in enumerate(records[-2000:]):
            tags = ["row-even" if index % 2 == 0 else "row-odd"]
            if record.error:
                tags.append("state-error")
            self.debug_tree.insert("", "end", iid=f"debug-{index}", values=(
                record.timestamp.strftime("%H:%M:%S.%f")[:-3], record.device_name, record.transaction_id or "—", record.unit_id,
                f"{record.function_code:02d}", record.start_address, record.quantity,
                f"{record.elapsed_ms:.1f}" if record.elapsed_ms is not None else "—", record.retry, record.error,
                "; ".join(record.point_names), record.request_hex, record.response_hex,
            ), tags=tuple(tags))
        self.debug_label.config(text=f"固定容量：{len(records)} / 2000（畫面最多 2000）")

    def _refresh_runtime(self) -> None:
        try:
            if self.engine:
                self._refresh_devices()
                self._refresh_points()
                self._refresh_debug()
        finally:
            self.after(self.REFRESH_MS, self._refresh_runtime)

    def _autosave_tick(self) -> None:
        try:
            if self.dirty and self.project_path:
                save_autosave(self.project, self.project_path)
                self.status_var.set(f"自動保存：{datetime.now().strftime('%H:%M:%S')}")
        except (OSError, ValueError) as exc:
            self.status_var.set(f"自動保存失敗：{exc}")
        finally:
            self.after(30_000, self._autosave_tick)

    def _close(self) -> None:
        if not self._confirm_discard():
            return
        self._stop()
        self.destroy()
