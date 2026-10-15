"""Tick labels must describe the grid positions at financial price precision."""
import math
import struct
import unittest
import zlib

from backend.app.modules.sub_agent.png_plot import _axis_scale, line_chart


class ChartAxisTests(unittest.TestCase):
    def test_real_price_range_has_accurate_nonmisleading_ticks(self):
        lo, hi, ticks = _axis_scale([9.0, 9.16, 9.18, 9.48])
        self.assertEqual(ticks, ['8.952', '9.096', '9.24', '9.384', '9.528'])
        self.assertLess(lo, 9.0)
        self.assertGreater(hi, 9.48)

    def test_small_constant_and_large_values_keep_distinct_accurate_labels(self):
        cases = ([1e-9, 1.2e-9], [0, 0], [9.0, 9.0], [1e12, 1e12],
                 [1e12, 1e12 + 4], [-1e12, 1e12], [-.0002, -.0001])
        for values in cases:
            with self.subTest(values=values):
                lo, hi, labels = _axis_scale(values)
                self.assertLess(lo, min(values))
                self.assertGreater(hi, max(values))
                actual = [float(label) for label in labels]
                self.assertEqual(actual, sorted(set(actual)))
                step = (hi - lo) / 4
                for index, value in enumerate(actual):
                    self.assertLessEqual(abs(value - (lo + step * index)), step / 1000)
                self.assertNotIn('-0', labels)
        lo, hi, _ = _axis_scale([1e-9, 1.2e-9])
        self.assertLess(hi - lo, 1e-9)

    def test_png_is_decodable_for_each_supported_scale(self):
        for values in ([9, 9.48], [1e-9, 1.2e-9], [0, 0], [1e12, 1e12 + 4], [-1e12, 1e12]):
            with self.subTest(values=values):
                png = line_chart(['FIRST', 'LAST'], values)
                self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
                compressed, offset = b'', 8
                while offset < len(png):
                    length = struct.unpack('>I', png[offset:offset + 4])[0]
                    kind = png[offset + 4:offset + 8]
                    payload = png[offset + 8:offset + 8 + length]
                    checksum = struct.unpack('>I', png[offset + 8 + length:offset + 12 + length])[0]
                    self.assertEqual(zlib.crc32(kind + payload), checksum)
                    if kind == b'IDAT':
                        compressed += payload
                    offset += 12 + length
                raw = zlib.decompress(compressed)
                self.assertEqual(len(raw), 320 * (640 * 3 + 1))
                self.assertTrue(any(value != 255 for value in raw))

    def test_nonfinite_or_unrepresentable_ranges_fail_clearly(self):
        for values in ([1, math.inf], [1, math.nan], [-1e308, 1e308]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                line_chart(['A', 'B'], values)


if __name__ == '__main__':
    unittest.main()
