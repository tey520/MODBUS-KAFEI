import unittest

from kafei.models import Device, Point, Project
from kafei.point_ops import (
    ALL_GROUPS,
    create_incremented_copy,
    group_filter_values,
    matches_group_filter,
    reorder_points_by_visible_order,
)


class GroupFilterTests(unittest.TestCase):
    def test_group_values_include_hierarchy_parents(self) -> None:
        points = [
            Point(group_path="PM-M3MSB1/HL"),
            Point(group_path="PM-M3MSB1/DP/L10.1"),
            Point(group_path="PM-M3MSB2/Main Feed"),
        ]
        values = group_filter_values(points)
        self.assertEqual(values[0], ALL_GROUPS)
        self.assertIn("PM-M3MSB1", values)
        self.assertIn("PM-M3MSB1/DP", values)
        self.assertIn("PM-M3MSB1/DP/L10.1", values)

    def test_parent_group_matches_descendants_only(self) -> None:
        self.assertTrue(matches_group_filter("PM-M3MSB1/DP/L10.1", "PM-M3MSB1"))
        self.assertTrue(matches_group_filter("PM-M3MSB1/HL", "PM-M3MSB1/HL"))
        self.assertFalse(matches_group_filter("PM-M3MSB10/HL", "PM-M3MSB1"))
        self.assertTrue(matches_group_filter("anything", ALL_GROUPS))


class CopyPointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.device = Device(name="D")

    def test_copy_skips_overlap_and_uses_unique_names(self) -> None:
        source = Point(
            name="Current",
            display_name="電流",
            device_id=self.device.id,
            address=10,
            quantity=2,
            data_type="FLOAT32",
        )
        occupied = Point(name="Occupied", device_id=self.device.id, address=12, quantity=2, data_type="FLOAT32")
        project = Project(devices=[self.device], points=[source, occupied])
        first = create_incremented_copy(project, source)
        self.assertEqual((first.name, first.display_name, first.address), ("Current - 複製 1", "電流 - 複製 1", 14))
        project.points.append(first)
        second = create_incremented_copy(project, first)
        self.assertEqual((second.name, second.display_name, second.address), ("Current - 複製 2", "電流 - 複製 2", 16))
        self.assertNotEqual(first.id, second.id)

    def test_reference_address_increments_by_span(self) -> None:
        source = Point(
            name="Power",
            device_id=self.device.id,
            address_mode="reference",
            address=40001,
            quantity=2,
            data_type="FLOAT32",
        )
        copied = create_incremented_copy(Project(devices=[self.device], points=[source]), source)
        self.assertEqual((copied.address, copied.raw_address), (40003, 2))

    def test_copy_stops_at_address_limit(self) -> None:
        source = Point(name="Last", device_id=self.device.id, address=65535)
        with self.assertRaisesRegex(ValueError, "65535"):
            create_incremented_copy(Project(devices=[self.device], points=[source]), source)


class ReorderPointTests(unittest.TestCase):
    def test_reorders_all_points(self) -> None:
        points = [Point(name=name) for name in ("A", "B", "C")]
        project = Project(points=points)
        changed = reorder_points_by_visible_order(project, [points[2].id, points[0].id, points[1].id])
        self.assertTrue(changed)
        self.assertEqual([point.name for point in project.points], ["C", "A", "B"])

    def test_filtered_reorder_preserves_hidden_slots(self) -> None:
        points = [Point(name=name) for name in ("A", "hidden-1", "B", "hidden-2", "C")]
        project = Project(points=points)
        changed = reorder_points_by_visible_order(project, [points[4].id, points[0].id, points[2].id])
        self.assertTrue(changed)
        self.assertEqual([point.name for point in project.points], ["C", "hidden-1", "A", "hidden-2", "B"])

    def test_no_change_and_invalid_order(self) -> None:
        points = [Point(name="A"), Point(name="B")]
        project = Project(points=points)
        self.assertFalse(reorder_points_by_visible_order(project, [points[0].id, points[1].id]))
        with self.assertRaisesRegex(ValueError, "重複"):
            reorder_points_by_visible_order(project, [points[0].id, points[0].id])
        with self.assertRaisesRegex(ValueError, "找不到"):
            reorder_points_by_visible_order(project, ["missing"])


if __name__ == "__main__":
    unittest.main()
