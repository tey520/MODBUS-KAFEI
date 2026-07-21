from pathlib import Path
import sys
import time
import tracemalloc


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kafei.merge import build_read_blocks
from kafei.models import Device, Point, Project


def main() -> int:
    tracemalloc.start()
    started = time.perf_counter()
    devices = [Device(name=f"Device-{index:02d}", host="127.0.0.1") for index in range(50)]
    points = [
        Point(name=f"Point-{device_index:02d}-{point_index:03d}", device_id=device.id, address=point_index)
        for device_index, device in enumerate(devices)
        for point_index in range(100)
    ]
    project = Project(name="50x5000 load smoke", devices=devices, points=points)
    errors = project.validate()
    blocks = build_read_blocks(project)
    elapsed_ms = (time.perf_counter() - started) * 1000
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"devices={len(devices)} points={len(points)} blocks={len(blocks)} errors={len(errors)}")
    print(f"elapsed_ms={elapsed_ms:.1f} peak_python_mib={peak / 1024 / 1024:.2f}")
    if errors or len(blocks) != 50:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

