"""Unit tests for the Maya-free layout module. Run: python -m unittest discover tests"""
import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from uv_group_packer import layout  # noqa: E402


def area(box):
    return (box[2] - box[0]) * (box[3] - box[1])


def overlaps(a, b, eps=1e-9):
    return a[0] < b[2] - eps and b[0] < a[2] - eps and a[1] < b[3] - eps and b[1] < a[3] - eps


class ComputeBoxesTest(unittest.TestCase):
    def test_single_group_fills_tile(self):
        boxes = layout.compute_boxes({"a": 3.0})
        self.assertEqual(boxes, {"a": (0.0, 0.0, 1.0, 1.0)})

    def test_areas_proportional_to_weights(self):
        weights = {"mask": 5.0, "eye": 2.0, "lens": 2.0, "arr": 1.0, "head": 0.5}
        boxes = layout.compute_boxes(weights)
        total = sum(weights.values())
        for name, w in weights.items():
            self.assertAlmostEqual(area(boxes[name]), w / total, places=9)

    def test_boxes_tile_without_overlap(self):
        weights = {str(i): float(i + 1) for i in range(9)}
        boxes = layout.compute_boxes(weights)
        self.assertAlmostEqual(sum(area(b) for b in boxes.values()), 1.0, places=9)
        for a, b in itertools.combinations(boxes.values(), 2):
            self.assertFalse(overlaps(a, b))
        for b in boxes.values():
            self.assertTrue(layout.contains((0, 0, 1, 1), b))

    def test_gap_and_tile_offset(self):
        boxes = layout.compute_boxes({"a": 1.0, "b": 1.0}, tile=(2, 0, 3, 1), gap=0.02)
        for b in boxes.values():
            self.assertTrue(layout.contains((2.01, 0.01, 2.99, 0.99), b, eps=1e-9))
        a, b = sorted(boxes.values())
        self.assertAlmostEqual(b[0] - a[2], 0.02)

    def test_zero_weights_skipped(self):
        self.assertEqual(layout.compute_boxes({"a": 0.0}), {})
        self.assertEqual(set(layout.compute_boxes({"a": 0.0, "b": 1.0})), {"b"})


class FitTransformTest(unittest.TestCase):
    def test_fit_keeps_aspect_and_centres(self):
        src = (0.0, 0.0, 2.0, 1.0)
        dst = (0.5, 0.5, 1.0, 1.0)
        scale, ou, ov = layout.fit_transform(src, dst)
        self.assertAlmostEqual(scale, 0.25)
        new = (src[0] * scale + ou, src[1] * scale + ov, src[2] * scale + ou, src[3] * scale + ov)
        self.assertTrue(layout.contains(dst, new))
        self.assertAlmostEqual((new[1] + new[3]) / 2, 0.75)


if __name__ == "__main__":
    unittest.main()
