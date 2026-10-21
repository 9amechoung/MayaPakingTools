"""Pure-python 2D shape matching for Align To Unselected.

No Maya imports here so the logic can be unit-tested outside Maya.
Points are (u, v) tuples. A match is a dict describing the similarity
transform that puts a source island on a template island:

    p' = scale * R(angle) * F * (p - src_c) + dst_c

where F mirrors U when flip is True. error is the remaining distance divided
by the template's size (RMS radius), so 0 is a perfect fit and 0.1 means the
shapes are about 10 % apart.
"""
import math


# ---------------------------------------------------------------- borders
def border_edges(loops):
    """UV edges used by only one face: the island outline.

    loops: per-face lists of UV ids, in winding order.
    """
    count = {}
    for loop in loops:
        for i, a in enumerate(loop):
            b = loop[(i + 1) % len(loop)]
            if a != b:
                key = (a, b) if a < b else (b, a)
                count[key] = count.get(key, 0) + 1
    return [e for e, n in count.items() if n == 1]


def sample_border(coords, edges, count=64):
    """Points spread evenly along the border edges, independent of tessellation.

    coords: {uv id: (u, v)}.
    """
    lengths = [math.hypot(coords[b][0] - coords[a][0], coords[b][1] - coords[a][1])
               for a, b in edges]
    total = sum(lengths)
    if total <= 0:
        return [coords[a] for a, _ in edges] or list(coords.values())
    step = total / float(count)
    points = []
    carry = 0.0
    for (a, b), length in zip(edges, lengths):
        if length <= 0:
            continue
        pa, pb = coords[a], coords[b]
        t = carry
        while t < length:
            f = t / length
            points.append((pa[0] + (pb[0] - pa[0]) * f, pa[1] + (pb[1] - pa[1]) * f))
            t += step
        carry = t - length
    return points


# ---------------------------------------------------------------- descriptors
def centroid(points):
    n = float(len(points))
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def radius(points, c=None):
    """RMS distance to the centroid: a size measure that ignores rotation."""
    cu, cv = c or centroid(points)
    return math.sqrt(sum((p[0] - cu) ** 2 + (p[1] - cv) ** 2 for p in points) / len(points))


def elongation(points, c=None):
    """Ratio of the small to the large principal spread (1 = round, 0 = a line)."""
    cu, cv = c or centroid(points)
    sxx = sum((p[0] - cu) ** 2 for p in points)
    syy = sum((p[1] - cv) ** 2 for p in points)
    sxy = sum((p[0] - cu) * (p[1] - cv) for p in points)
    half_trace = (sxx + syy) * 0.5
    root = math.sqrt(max(half_trace ** 2 - (sxx * syy - sxy * sxy), 0.0))
    big, small = half_trace + root, half_trace - root
    return math.sqrt(max(small, 0.0) / big) if big > 0 else 1.0


# ---------------------------------------------------------------- transforms
def apply(match, p):
    s, a = match["scale"], math.radians(match["angle"])
    x, y = p[0] - match["src_c"][0], p[1] - match["src_c"][1]
    if match["flip"]:
        x = -x
    ca, sa = math.cos(a), math.sin(a)
    return (s * (ca * x - sa * y) + match["dst_c"][0],
            s * (sa * x + ca * y) + match["dst_c"][1])


def _nearest_mean(a, b):
    total = 0.0
    for pa in a:
        best = min((pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2 for pb in b)
        total += math.sqrt(best)
    return total / len(a)


def chamfer(a, b):
    """Symmetric mean nearest-point distance between two point sets."""
    return (_nearest_mean(a, b) + _nearest_mean(b, a)) * 0.5


def _subsample(points, count):
    if len(points) <= count:
        return points
    step = len(points) / float(count)
    return [points[int(i * step)] for i in range(count)]


def match_shapes(src, dst, allow_flip=False, adjust_scale=False, coarse_steps=72):
    """Best similarity transform of point set src onto dst, without correspondence.

    Tries every rotation in coarse steps (and mirrored, if allowed), then
    refines the best angle. Without adjust_scale the size is kept, so islands
    of a different size end up with a large error.
    """
    src_c, dst_c = centroid(src), centroid(dst)
    dst_r = radius(dst, dst_c)
    src_r = radius(src, src_c)
    scale = dst_r / src_r if adjust_scale and src_r > 0 else 1.0
    coarse_src, coarse_dst = _subsample(src, 24), _subsample(dst, 24)

    def error(points_src, points_dst, angle, flip):
        m = {"scale": scale, "angle": angle, "flip": flip, "src_c": src_c, "dst_c": dst_c}
        return chamfer([apply(m, p) for p in points_src], points_dst)

    best = None
    for flip in ((False, True) if allow_flip else (False,)):
        for k in range(coarse_steps):
            angle = k * 360.0 / coarse_steps
            e = error(coarse_src, coarse_dst, angle, flip)
            if best is None or e < best[0]:
                best = (e, angle, flip)

    _, angle, flip = best
    step = 360.0 / coarse_steps
    while step > 0.05:
        step *= 0.5
        candidates = [angle - step, angle, angle + step]
        angle = min(candidates, key=lambda a: error(src, dst, a, flip))
    e = error(src, dst, angle, flip)
    return {"scale": scale, "angle": angle % 360.0, "flip": flip, "src_c": src_c,
            "dst_c": dst_c, "error": e / dst_r if dst_r > 0 else e}


def procrustes(src, dst, allow_flip=False, adjust_scale=False):
    """Best similarity transform when src[i] corresponds to dst[i] (same topology)."""
    src_c, dst_c = centroid(src), centroid(dst)
    dst_r = radius(dst, dst_c)
    best = None
    for flip in ((False, True) if allow_flip else (False,)):
        a = [((-1 if flip else 1) * (p[0] - src_c[0]), p[1] - src_c[1]) for p in src]
        b = [(p[0] - dst_c[0], p[1] - dst_c[1]) for p in dst]
        dot = sum(x[0] * y[0] + x[1] * y[1] for x, y in zip(a, b))
        cross = sum(x[0] * y[1] - x[1] * y[0] for x, y in zip(a, b))
        angle = math.degrees(math.atan2(cross, dot))
        norm = sum(x[0] ** 2 + x[1] ** 2 for x in a)
        scale = math.hypot(dot, cross) / norm if adjust_scale and norm > 0 else 1.0
        m = {"scale": scale, "angle": angle % 360.0, "flip": flip, "src_c": src_c, "dst_c": dst_c}
        rms = math.sqrt(sum((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2
                            for q, p in zip((apply(m, p) for p in src), dst)) / len(src))
        m["error"] = rms / dst_r if dst_r > 0 else rms
        if best is None or m["error"] < best["error"]:
            best = m
    return best
