"""Pure-python box layout: split one UV tile into rectangles, one per group.

No Maya imports here so the logic can be unit-tested outside Maya.
A box is a tuple (u_min, v_min, u_max, v_max).
"""


def _partition(items):
    """Split weight-sorted items into two lists whose weight sums are close.

    Greedy: walk the list (largest first) and add each item to the lighter side.
    """
    left, right = [], []
    left_sum = right_sum = 0.0
    for item in items:
        if left_sum <= right_sum:
            left.append(item)
            left_sum += item[1]
        else:
            right.append(item)
            right_sum += item[1]
    return left, left_sum, right, right_sum


def _split(items, box, out):
    if len(items) == 1:
        out[items[0][0]] = box
        return

    left, left_sum, right, right_sum = _partition(items)
    total = left_sum + right_sum
    ratio = left_sum / total if total > 0 else 0.5

    u0, v0, u1, v1 = box
    # Cut across the longer side so boxes stay as square as possible.
    if (u1 - u0) >= (v1 - v0):
        cut = u0 + (u1 - u0) * ratio
        _split(left, (u0, v0, cut, v1), out)
        _split(right, (cut, v0, u1, v1), out)
    else:
        # Fill top-down so the first (largest) group sits at the top like a page.
        cut = v1 - (v1 - v0) * ratio
        _split(left, (u0, cut, u1, v1), out)
        _split(right, (u0, v0, u1, cut), out)


def compute_boxes(weights, tile=(0.0, 0.0, 1.0, 1.0), gap=0.0):
    """Return {name: box} with box areas proportional to the given weights.

    weights: {name: weight}. Groups with weight <= 0 are skipped.
    tile:    the region to fill, (u_min, v_min, u_max, v_max).
    gap:     empty space kept between neighbouring boxes and the tile border.
    """
    items = sorted(((n, float(w)) for n, w in weights.items() if w > 0),
                   key=lambda it: (-it[1], it[0]))
    if not items:
        return {}

    raw = {}
    _split(items, tile, raw)

    half = gap * 0.5
    boxes = {}
    for name, (u0, v0, u1, v1) in raw.items():
        boxes[name] = (u0 + half, v0 + half,
                       max(u0 + half, u1 - half), max(v0 + half, v1 - half))
    return boxes


def fit_transform(src, dst):
    """Uniform scale + translate that fits bbox src inside box dst, centred.

    Returns (scale, offset_u, offset_v) such that new = old * scale + offset.
    """
    su = src[2] - src[0]
    sv = src[3] - src[1]
    du = dst[2] - dst[0]
    dv = dst[3] - dst[1]
    candidates = [s for s in (du / su if su > 0 else None,
                              dv / sv if sv > 0 else None) if s is not None]
    scale = min(candidates) if candidates else 1.0
    off_u = dst[0] + (du - su * scale) * 0.5 - src[0] * scale
    off_v = dst[1] + (dv - sv * scale) * 0.5 - src[1] * scale
    return scale, off_u, off_v


def contains(outer, inner, eps=1e-4):
    return (inner[0] >= outer[0] - eps and inner[1] >= outer[1] - eps and
            inner[2] <= outer[2] + eps and inner[3] <= outer[3] + eps)
