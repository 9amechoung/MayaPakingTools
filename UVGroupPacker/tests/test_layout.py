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


class TileTest(unittest.TestCase):
    def test_tile_boxes_wrap_rows(self):
        boxes = layout.tile_boxes(5, start_u=1, start_v=0, per_row=2)
        self.assertEqual([b[:2] for b in boxes], [(1, 0), (2, 0), (1, 1), (2, 1), (1, 2)])

    def test_move_box(self):
        self.assertEqual(layout.move_box((0.1, 0.2, 0.5, 0.6), "NE"), (1.1, 1.2, 1.5, 1.6))
        self.assertEqual(layout.move_box((1, 1, 2, 2), "SW"), (0, 0, 1, 1))

    def test_tiles_needed(self):
        self.assertEqual(layout.tiles_needed(0.0), 1)
        self.assertEqual(layout.tiles_needed(1.4, fill_ratio=0.7), 2)
        self.assertEqual(layout.tiles_needed(1.5, fill_ratio=0.7), 3)


class DistributeTest(unittest.TestCase):
    items = [("a", 4.0), ("b", 3.0), ("c", 2.0), ("d", 2.0), ("e", 1.0)]

    def test_simultaneous_balances(self):
        bins = layout.distribute(self.items, 2)
        sums = [sum(dict(self.items)[k] for k in b) for b in bins]
        self.assertEqual(sorted(sums), [6.0, 6.0])

    def test_sequential_fills_first_tile(self):
        bins = layout.distribute(self.items, 3, layout.FILL_SEQUENTIAL, capacity=6.0)
        self.assertEqual(bins[0], ["a", "c"])
        self.assertEqual(sorted(k for b in bins for k in b), list("abcde"))

    def test_every_item_placed_once(self):
        bins = layout.distribute(self.items, 4, layout.FILL_SEQUENTIAL)
        self.assertEqual(sorted(k for b in bins for k in b), list("abcde"))


class AtlasTest(unittest.TestCase):
    def test_grid_cells_from_top_left(self):
        boxes = layout.atlas_boxes(["a", "b", "c", "d"], 3, 3)
        for got, want in zip(boxes["a"], (0.0, 2 / 3.0, 1 / 3.0, 1.0)):
            self.assertAlmostEqual(got, want)
        self.assertAlmostEqual(boxes["d"][0], 0.0)
        self.assertAlmostEqual(boxes["d"][3], 2 / 3.0)

    def test_two_cells_per_group_no_overlap(self):
        boxes = layout.atlas_boxes(list("abcde"), 3, 3, cells_per_group=2)
        # 3 columns fit one 2-wide block per row, so only 3 groups fit.
        self.assertEqual(sorted(boxes), ["a", "b", "c"])
        self.assertAlmostEqual(area(boxes["a"]), 2 / 9.0)
        for a, b in itertools.combinations(boxes.values(), 2):
            self.assertFalse(overlaps(a, b))

    def test_block_taller_than_one_row(self):
        boxes = layout.atlas_boxes(["a", "b"], 2, 4, cells_per_group=3, tile=(1, 0, 2, 1), gap=0.02)
        self.assertEqual(set(boxes), {"a", "b"})
        for b in boxes.values():
            self.assertTrue(layout.contains((1, 0, 2, 1), b))
            self.assertAlmostEqual(b[3] - b[1], 0.5 - 0.02)


def square_shape(u, v, size=1.0):
    coords = {0: (u, v), 1: (u + size, v), 2: (u + size, v + size), 3: (u, v + size)}
    loops = [[0, 1, 2, 3]]
    return {"bbox": (u, v, u + size, v + size), "points": list(coords.values()),
            "tris": layout.fan_triangles(loops, coords),
            "edges": [(coords[i], coords[(i + 1) % 4]) for i in range(4)]}


def bar_shape(u0, v0, u1, v1):
    coords = {0: (u0, v0), 1: (u1, v0), 2: (u1, v1), 3: (u0, v1)}
    return {"bbox": (u0, v0, u1, v1), "points": list(coords.values()),
            "tris": layout.fan_triangles([[0, 1, 2, 3]], coords),
            "edges": [(coords[i], coords[(i + 1) % 4]) for i in range(4)]}


class OverlapTest(unittest.TestCase):
    def test_any_part(self):
        self.assertTrue(layout.shapes_overlap(square_shape(0, 0), square_shape(0.5, 0.5)))
        self.assertFalse(layout.shapes_overlap(square_shape(0, 0), square_shape(1.2, 0)))

    def test_plus_shape_crossing(self):
        # No corner of either bar lies inside the other.
        self.assertTrue(layout.shapes_overlap(bar_shape(0, 0.4, 1, 0.6), bar_shape(0.4, 0, 0.6, 1)))

    def test_exact_needs_same_place(self):
        self.assertTrue(layout.shapes_overlap(square_shape(0, 0), square_shape(0.001, 0), exact=True))
        self.assertFalse(layout.shapes_overlap(square_shape(0, 0), square_shape(0.5, 0.5), exact=True))

    def test_pairs_and_clusters(self):
        shapes = [square_shape(0, 0), square_shape(0.5, 0), square_shape(5, 5), square_shape(1.2, 0)]
        pairs = layout.overlapping_pairs(shapes)
        self.assertEqual(sorted(pairs), [(0, 1), (1, 3)])
        self.assertEqual(layout.clusters(4, pairs), [[0, 1, 3], [2]])


class PrecisionTest(unittest.TestCase):
    def test_pixel_snap(self):
        du, dv = layout.pixel_snap_offset((0.10049, 0.5, 0.2, 0.6), 1000)
        self.assertAlmostEqual(0.10049 + du, 0.100)
        self.assertAlmostEqual(dv, 0.0)

    def test_search_candidates_start_at_precision(self):
        cands = layout.search_candidates(2)
        self.assertEqual(cands[0], (2, layout.PRECISION[2][1]))
        self.assertEqual(len(cands), len(set(cands)))
        self.assertTrue(all(level >= 2 for level, _ in cands))


if __name__ == "__main__":
    unittest.main()
