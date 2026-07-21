from __future__ import annotations

import argparse
import ctypes
from pathlib import Path
import tempfile

from .codec import decode_point
from .merge import build_read_blocks
from .models import Device, Point, Project
from .persistence import load_project, save_project


def headless_smoke() -> int:
    device = Device(name="Smoke Device", host="127.0.0.1")
    point = Point(name="Smoke Point", device_id=device.id, function_code=3, address_mode="reference", address=40001, data_type="UINT16")
    project = Project(name="Smoke", devices=[device], points=[point])
    if project.validate():
        return 2
    if point.raw_address != 0 or decode_point(point, [123]) != 123:
        return 3
    if len(build_read_blocks(project)) != 1:
        return 4
    with tempfile.TemporaryDirectory(prefix="kafei-smoke-") as directory:
        path = save_project(project, Path(directory) / "中文測試.kafei")
        loaded = load_project(path)
        if loaded.points[0].document_address != 40001:
            return 5
    print("KAFEI headless smoke: PASS")
    return 0


def _enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def _set_windows_app_identity() -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Kafei.Modbus.Validator.0.1.5")
    except (AttributeError, OSError):
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MODBUS KAFEI")
    parser.add_argument("--headless-smoke", action="store_true", help="run non-UI smoke checks")
    parser.add_argument("--ui-smoke", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.headless_smoke:
        return headless_smoke()
    _enable_dpi_awareness()
    _set_windows_app_identity()
    from .ui import KafeiApp

    app = KafeiApp()
    if args.ui_smoke:
        app.after(500, app.destroy)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
