import struct
import unittest

from kafei.codec import decode_point
from kafei.models import Device, Point, Project, protocol_address, reference_address


class AddressTests(unittest.TestCase):
    def test_reference_and_zero_based_are_explicit(self) -> None:
        self.assertEqual(protocol_address(3, "reference", 40001), 0)
        self.assertEqual(protocol_address(4, "reference", 30011), 10)
        self.assertEqual(protocol_address(1, "zero_based", 65535), 65535)
        self.assertEqual(reference_address(2, 0), 10001)

    def test_classic_reference_does_not_produce_invalid_80002(self) -> None:
        self.assertEqual(protocol_address(3, "reference", 49999), 9998)
        with self.assertRaisesRegex(ValueError, "Reference"):
            protocol_address(3, "reference", 50000)
        point = Point(function_code=3, address_mode="zero_based", address=40001)
        self.assertIsNone(point.document_address)

    def test_bad_address_mode_and_ranges_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "地址模式"):
            protocol_address(3, "", 40001)
        with self.assertRaises(ValueError):
            protocol_address(3, "reference", 40000)
        with self.assertRaises(ValueError):
            protocol_address(3, "zero_based", 65536)

    def test_project_rejects_invalid_point(self) -> None:
        device = Device(name="D")
        point = Point(name="P", device_id=device.id, function_code=3, data_type="FLOAT32", quantity=1)
        errors = Project(devices=[device], points=[point]).validate()
        self.assertTrue(any("占用位址數必須是 2" in error for error in errors))

    def test_point_rules_are_interlocked(self) -> None:
        device = Device(name="D", scan_interval_ms=750)
        invalid_fc01 = Point(name="P1", device_id=device.id, function_code=1, data_type="UINT16")
        self.assertTrue(any("FC01/FC02 只支援 BOOL" in item for item in invalid_fc01.validate({device.id: device})))
        missing_bit = Point(name="P2", device_id=device.id, function_code=3, data_type="BIT", bit_index=None)
        self.assertTrue(any("必須指定 Bit Index" in item for item in missing_bit.validate({device.id: device})))
        stale_bit = Point(name="P3", device_id=device.id, function_code=3, data_type="UINT16", bit_index=1)
        self.assertTrue(any("只有 FC03/FC04" in item for item in stale_bit.validate({device.id: device})))
        variable = Point(name="P4", device_id=device.id, function_code=3, data_type="ASCII", quantity=4)
        self.assertEqual(variable.validate({device.id: device}), [])
        variable.scan_interval_ms = 50
        self.assertEqual(variable.effective_interval(device), 750)


class CodecTests(unittest.TestCase):
    def _float_point(self, order: str) -> Point:
        return Point(device_id="D", data_type="FLOAT32", quantity=2, byte_order=order)

    def test_float32_all_orders(self) -> None:
        cases = {
            "ABCD": [0x3F80, 0x0000],
            "BADC": [0x803F, 0x0000],
            "CDAB": [0x0000, 0x3F80],
            "DCBA": [0x0000, 0x803F],
        }
        for order, registers in cases.items():
            with self.subTest(order=order):
                self.assertEqual(decode_point(self._float_point(order), registers), 1.0)

    def test_signed_scale_offset_and_rounding(self) -> None:
        point = Point(device_id="D", data_type="INT16", scale=0.1, offset=1, decimals=2)
        self.assertEqual(decode_point(point, [struct.unpack(">H", struct.pack(">h", -20))[0]]), -1.0)

    def test_bit_and_ascii(self) -> None:
        bit = Point(device_id="D", data_type="BIT", bit_index=3)
        self.assertTrue(decode_point(bit, [0b1000]))
        ascii_point = Point(device_id="D", data_type="ASCII", quantity=2)
        self.assertEqual(decode_point(ascii_point, [0x4B41, 0x4645]), "KAFE")
        register_bool = Point(device_id="D", data_type="BOOL")
        self.assertFalse(decode_point(register_bool, [0]))
        self.assertTrue(decode_point(register_bool, [1]))
        with self.assertRaisesRegex(ValueError, "只接受 0 或 1"):
            decode_point(register_bool, [2])

    def test_nan_is_conversion_error(self) -> None:
        point = self._float_point("ABCD")
        raw = struct.unpack(">2H", struct.pack(">f", float("nan")))
        with self.assertRaisesRegex(ValueError, "NaN"):
            decode_point(point, raw)


if __name__ == "__main__":
    unittest.main()
