import json
from pathlib import Path
import tempfile
import unittest

from kafei.csvio import import_point_rows
from kafei.models import Device, Point, Project
from kafei.persistence import load_project, save_project


HEADERS = ("device_name", "device_host", "point_name", "unit_id", "function_code", "address_mode", "address", "data_type")


class CsvTransactionTests(unittest.TestCase):
    def test_missing_address_mode_is_error_and_base_is_unchanged(self) -> None:
        base = Project(name="Base")
        row = {"device_name": "D", "device_host": "127.0.0.1", "point_name": "P", "unit_id": "1", "function_code": "3", "address": "0", "data_type": "UINT16"}
        result = import_point_rows([row], HEADERS, base)
        self.assertFalse(result.can_apply)
        self.assertEqual(base.devices, [])
        self.assertEqual(base.points, [])
        self.assertEqual(result.project.points, [])

    def test_valid_reference_import(self) -> None:
        base = Project(name="Base")
        row = {"device_name": "D", "device_host": "127.0.0.1", "point_name": "P", "unit_id": "1", "function_code": "3", "address_mode": "reference", "address": "40001", "data_type": "FLOAT32"}
        result = import_point_rows([row], HEADERS, base)
        self.assertTrue(result.can_apply)
        self.assertEqual((result.added, len(result.project.devices), len(result.project.points)), (1, 1, 1))
        self.assertEqual(result.project.points[0].quantity, 2)
        self.assertEqual(result.project.points[0].raw_address, 0)

    def test_bad_row_does_not_modify_original_even_after_good_row(self) -> None:
        rows = [
            {"device_name": "D", "device_host": "127.0.0.1", "point_name": "Good", "unit_id": "1", "function_code": "3", "address_mode": "zero_based", "address": "0", "data_type": "UINT16"},
            {"device_name": "D", "device_host": "127.0.0.1", "point_name": "Bad", "unit_id": "x", "function_code": "3", "address_mode": "zero_based", "address": "1", "data_type": "UINT16"},
        ]
        base = Project(name="Base")
        result = import_point_rows(rows, HEADERS, base)
        self.assertFalse(result.can_apply)
        self.assertEqual((len(base.devices), len(base.points)), (0, 0))

    def test_defaults_tags_and_device_scan_policy(self) -> None:
        headers = HEADERS + ("tags", "scan_ms")
        rows = [
            {
                "device_name": "D",
                "device_host": "127.0.0.1",
                "point_name": "Coil",
                "unit_id": "1",
                "function_code": "1",
                "address_mode": "zero_based",
                "address": "0",
                "data_type": "BOOL",
                "tags": "狀態, 重要",
                "scan_ms": "50",
            },
            {
                "device_name": "D",
                "point_name": "Register",
                "unit_id": "1",
                "function_code": "3",
                "address_mode": "zero_based",
                "address": "1",
                "data_type": "UINT16",
                "tags": "電壓;舊格式",
            },
        ]
        result = import_point_rows(rows, headers, Project())
        self.assertTrue(result.can_apply)
        self.assertEqual(result.project.points[0].decimals, 0)
        self.assertEqual(result.project.points[1].decimals, 2)
        self.assertEqual(result.project.points[0].tags, ["狀態", "重要"])
        self.assertEqual(result.project.points[1].tags, ["電壓", "舊格式"])
        self.assertIsNone(result.project.points[0].scan_interval_ms)
        self.assertTrue(any(issue.level == "INFO" and issue.field == "scan_ms" for issue in result.issues))


class PersistenceTests(unittest.TestCase):
    def test_round_trip_unicode_and_backup(self) -> None:
        device = Device(name="咖啡機", notes="中文")
        project = Project(name="磨杯", devices=[device], points=[Point(name="溫度", device_id=device.id)])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "中文專案.kafei"
            save_project(project, path)
            project.points[0].description = "第二版"
            save_project(project, path)
            loaded = load_project(path)
            self.assertEqual(loaded.points[0].description, "第二版")
            self.assertEqual(len(list((path.parent / ".kafei-backups").glob("*.kafei"))), 1)

    def test_future_version_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "future.kafei"
            path.write_text(json.dumps({"schema_version": 999, "name": "future"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "高於"):
                load_project(path)


if __name__ == "__main__":
    unittest.main()
