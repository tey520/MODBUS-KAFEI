import unittest

from kafei.merge import build_read_blocks, optimization_summary
from kafei.models import Device, Point, Project


class MergeTests(unittest.TestCase):
    def test_contiguous_and_allowed_gap_are_merged(self) -> None:
        device = Device(name="D", allowed_gap=1, max_read_block=125)
        points = [
            Point(name="A", device_id=device.id, address=0),
            Point(name="B", device_id=device.id, address=2),
            Point(name="C", device_id=device.id, address=3),
        ]
        project = Project(devices=[device], points=points)
        blocks = build_read_blocks(project)
        self.assertEqual(len(blocks), 1)
        self.assertEqual((blocks[0].start_address, blocks[0].quantity), (0, 4))
        original, merged, reduction = optimization_summary(project, blocks)
        self.assertEqual((original, merged), (3, 1))
        self.assertAlmostEqual(reduction, 200 / 3)

    def test_different_unit_or_fc_never_merge(self) -> None:
        device = Device(name="D")
        points = [
            Point(name="A", device_id=device.id, address=0, unit_id=1, function_code=3),
            Point(name="B", device_id=device.id, address=1, unit_id=2, function_code=3),
            Point(name="C", device_id=device.id, address=2, unit_id=1, function_code=4),
        ]
        self.assertEqual(len(build_read_blocks(Project(devices=[device], points=points))), 3)

    def test_point_scan_override_is_ignored_and_device_period_is_used(self) -> None:
        device = Device(name="D", scan_interval_ms=1000)
        points = [
            Point(name="A", device_id=device.id, address=0, scan_interval_ms=100),
            Point(name="B", device_id=device.id, address=1, scan_interval_ms=2000),
        ]
        blocks = build_read_blocks(Project(devices=[device], points=points))
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].interval_ms, 1000)

    def test_block_limit_does_not_split_multi_register_point(self) -> None:
        device = Device(name="D", max_read_block=4)
        points = [
            Point(name="A", device_id=device.id, address=0, quantity=2, data_type="UINT32"),
            Point(name="B", device_id=device.id, address=3, quantity=2, data_type="UINT32"),
        ]
        blocks = build_read_blocks(Project(devices=[device], points=points))
        self.assertEqual([(block.start_address, block.quantity) for block in blocks], [(0, 2), (3, 2)])

    def test_protocol_limits_split_register_blocks(self) -> None:
        device = Device(name="D", max_read_block=125)
        points = [Point(name=f"P{i}", device_id=device.id, address=i) for i in range(130)]
        blocks = build_read_blocks(Project(devices=[device], points=points))
        self.assertEqual([block.quantity for block in blocks], [125, 5])

    def test_5000_points_50_devices_plan(self) -> None:
        devices = [Device(name=f"D{i}") for i in range(50)]
        points = [Point(name=f"P{d}-{i}", device_id=device.id, address=i) for d, device in enumerate(devices) for i in range(100)]
        project = Project(devices=devices, points=points)
        self.assertEqual(len(project.points), 5000)
        self.assertEqual(len(build_read_blocks(project)), 50)


if __name__ == "__main__":
    unittest.main()
