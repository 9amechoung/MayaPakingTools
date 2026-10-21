"""Unit tests for the Maya-free shape matching. Run: python -m unittest discover tests"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from uv_group_packer import align_math  # noqa: E402

# An "L" outline: no rotational or mirror symmetry, so the answer is unique.
L_SHAPE = [(0, 0), (3, 0), (3, 1), (1, 1), (1, 2), (0, 2)]


def outline(corners, per_edge=12):
    points = []
    for i, a in enumerate(corners):
        b = corners[(i + 1) % len(corners)]
        for k in range(per_edge):
            f = k / float(per_edge)
            points.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    return points


def moved(points, angle, scale=1.0, flip=False, offset=(5.0, -2.0)):
    a = math.radians(angle)
    result = []
    for x, y in points:
        if flip:
            x = -x
        result.append((scale * (x * math.cos(a) - y * math.sin(a)) + offset[0],
                       scale * (x * math.sin(a) + y * math.cos(a)) + offset[1]))
    return result


def max_gap(a, b):
    return max(math.hypot(p[0] - q[0], p[1] - q[1]) for p, q in zip(a, b))


class BorderTest(unittest.TestCase):
    def test_border_of_two_quads(self):
        loops = [[0, 1, 4, 3], [1, 2, 5, 4]]
        edges = align_math.border_edges(loops)
        self.assertEqual(len(edges), 6)
        self.assertNotIn((1, 4), edges)

    def test_sampling_is_even(self):
        coords = {0: (0, 0), 1: (4, 0), 2: (4, 1), 3: (0, 1)}
        points = align_math.sample_border(coords, [(0, 1), (1, 2), (2, 3), (3, 0)], count=10)
        self.assertEqual(len(points), 10)


class MatchShapesTest(unittest.TestCase):
    def test_finds_rotation(self):
        src = outline(L_SHAPE)
        dst = moved(src, 70)
        m = align_math.match_shapes(src, dst)
        self.assertLess(m["error"], 0.01)
        self.assertAlmostEqual(m["angle"], 70, delta=0.5)
        self.assertFalse(m["flip"])

    def test_scale_only_with_adjust_scale(self):
        src = outline(L_SHAPE)
        dst = moved(src, 200, scale=0.5)
        self.assertGreater(align_math.match_shapes(src, dst)["error"], 0.1)
        m = align_math.match_shapes(src, dst, adjust_scale=True)
        self.assertLess(m["error"], 0.01)
        self.assertAlmostEqual(m["scale"], 0.5, places=3)

    def test_mirror_only_with_flipping(self):
        src = outline(L_SHAPE)
        dst = moved(src, 30, flip=True)
        self.assertGreater(align_math.match_shapes(src, dst)["error"], 0.05)
        m = align_math.match_shapes(src, dst, allow_flip=True)
        self.assertTrue(m["flip"])
        self.assertLess(m["error"], 0.01)

    def test_different_shape_has_large_error(self):
        square = outline([(0, 0), (2, 0), (2, 2), (0, 2)])
        m = align_math.match_shapes(outline(L_SHAPE), square, adjust_scale=True)
        self.assertGreater(m["error"], 0.05)


class ProcrustesTest(unittest.TestCase):
    def test_exact_transform(self):
        src = [(0, 0), (3, 0), (3, 1), (1, 1), (1, 2), (0, 2)]
        dst = moved(src, -40, scale=2.0, flip=True)
        m = align_math.procrustes(src, dst, allow_flip=True, adjust_scale=True)
        self.assertLess(m["error"], 1e-9)
        self.assertLess(max_gap([align_math.apply(m, p) for p in src], dst), 1e-9)

    def test_scale_kept_without_adjust(self):
        src = [(0, 0), (3, 0), (3, 1), (0, 1)]
        m = align_math.procrustes(src, moved(src, 10, scale=2.0))
        self.assertEqual(m["scale"], 1.0)
        self.assertGreater(m["error"], 0.1)


if __name__ == "__main__":
    unittest.main()
