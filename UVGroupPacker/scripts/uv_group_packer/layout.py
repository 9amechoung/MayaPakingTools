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


# ---------------------------------------------------------------- tiles
# Offsets for moving a box to a neighbouring tile, named like a compass.
DIRECTIONS = {
    "NW": (-1, 1), "N": (0, 1), "NE": (1, 1),
    "W": (-1, 0), "E": (1, 0),
    "SW": (-1, -1), "S": (0, -1), "SE": (1, -1),
}


def move_box(box, direction):
    """Shift box by one whole tile in a compass direction ('N', 'SE', ...)."""
    du, dv = DIRECTIONS[direction]
    return (box[0] + du, box[1] + dv, box[2] + du, box[3] + dv)


def tile_boxes(count, start_u=0, start_v=0, per_row=10, margin=0.0):
    """Boxes of count UDIM tiles, filled left to right then upwards."""
    per_row = max(1, int(per_row))
    boxes = []
    for index in range(max(0, int(count))):
        u = start_u + index % per_row
        v = start_v + index // per_row
        boxes.append((u + margin, v + margin, u + 1 - margin, v + 1 - margin))
    return boxes


FILL_SIMULTANEOUS = "simultaneous"
FILL_SEQUENTIAL = "sequential"


def distribute(items, count, method=FILL_SIMULTANEOUS, capacity=None):
    """Split (key, area) items into count bins; returns a list of key lists.

    simultaneous: every item goes to the currently lightest bin, so all tiles
                  end up about equally full.
    sequential:   fill the first bin up to capacity, then the next one, like
                  pages of a book. Items that fit nowhere go to the lightest bin.
    """
    count = max(1, int(count))
    bins = [[] for _ in range(count)]
    sums = [0.0] * count
    ordered = sorted(items, key=lambda it: (-it[1], str(it[0])))
    if capacity is None:
        capacity = sum(a for _, a in ordered) / count
    for key, area in ordered:
        index = None
        if method == FILL_SEQUENTIAL:
            index = next((i for i in range(count) if sums[i] + area <= capacity + 1e-12), None)
        if index is None:
            index = min(range(count), key=lambda i: sums[i])
        bins[index].append(key)
        sums[index] += area
    return bins


def tiles_needed(total_area, fill_ratio=0.7):
    """How many unit tiles a set of islands with this UV area should need."""
    if total_area <= 0:
        return 1
    count = total_area / max(fill_ratio, 1e-6)
    return max(1, int(count) + (0 if count == int(count) else 1))


# ---------------------------------------------------------------- atlas
def atlas_boxes(names, cols, rows, cells_per_group=1, tile=(0.0, 0.0, 1.0, 1.0), gap=0.0):
    """Give each group a block of cells in a cols x rows grid (a texture atlas).

    Cells are taken from the top-left, row by row. A group that needs more
    cells than a row has gets a block several rows high. Groups that no longer
    fit in the grid are left out of the result.
    Returns {name: box}.
    """
    cols, rows = max(1, int(cols)), max(1, int(rows))
    cells = max(1, int(cells_per_group))
    width = min(cells, cols)
    height = -(-cells // cols)
    used = [[False] * cols for _ in range(rows)]
    cell_u = (tile[2] - tile[0]) / float(cols)
    cell_v = (tile[3] - tile[1]) / float(rows)
    half = gap * 0.5

    def free(r, c):
        if r + height > rows or c + width > cols:
            return False
        return all(not used[rr][cc] for rr in range(r, r + height) for cc in range(c, c + width))

    boxes = {}
    for name in names:
        spot = next(((r, c) for r in range(rows) for c in range(cols) if free(r, c)), None)
        if spot is None:
            break
        r, c = spot
        for rr in range(r, r + height):
            for cc in range(c, c + width):
                used[rr][cc] = True
        # Row 0 is the top of the tile.
        u0 = tile[0] + c * cell_u
        v1 = tile[3] - r * cell_v
        boxes[name] = (u0 + half, v1 - height * cell_v + half,
                       u0 + width * cell_u - half, v1 - half)
    return boxes


# ---------------------------------------------------------------- overlap
# A "shape" here is {"bbox": (u0, v0, u1, v1), "points": [(u, v)...],
# "tris": [((u, v), (u, v), (u, v))...], "edges": [((u, v), (u, v))...]}.
def fan_triangles(loops, coords):
    """Triangles of polygon loops (lists of UV ids), fanned from each first corner."""
    tris = []
    for loop in loops:
        for k in range(1, len(loop) - 1):
            tris.append((coords[loop[0]], coords[loop[k]], coords[loop[k + 1]]))
    return tris


def _side(p, a, b):
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _in_triangle(p, tri, eps=1e-12):
    a, b, c = tri
    d1, d2, d3 = _side(p, a, b), _side(p, b, c), _side(p, c, a)
    has_neg = d1 < -eps or d2 < -eps or d3 < -eps
    has_pos = d1 > eps or d2 > eps or d3 > eps
    return not (has_neg and has_pos)


def _segments_cross(p1, p2, q1, q2):
    d1, d2 = _side(q1, p1, p2), _side(q2, p1, p2)
    d3, d4 = _side(p1, q1, q2), _side(p2, q1, q2)
    return d1 * d2 < 0 and d3 * d4 < 0


def _boxes_touch(a, b, eps=0.0):
    return a[0] <= b[2] + eps and b[0] <= a[2] + eps and a[1] <= b[3] + eps and b[1] <= a[3] + eps


def shapes_overlap(a, b, exact=False, tolerance=0.01):
    """Do two UV islands overlap?

    exact: only when they cover the same space (same bbox and area, within
           tolerance of their size), i.e. islands stacked on top of each other.
    otherwise: when any part overlaps.
    """
    if exact:
        size = max(a["bbox"][2] - a["bbox"][0], a["bbox"][3] - a["bbox"][1],
                   b["bbox"][2] - b["bbox"][0], b["bbox"][3] - b["bbox"][1], 1e-9)
        return all(abs(x - y) <= tolerance * size for x, y in zip(a["bbox"], b["bbox"]))
    if not _boxes_touch(a["bbox"], b["bbox"]):
        return False
    for first, second in ((a, b), (b, a)):
        box = second["bbox"]
        for p in first["points"]:
            if box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]:
                if any(_in_triangle(p, tri) for tri in second["tris"]):
                    return True
    # Crossing without any corner inside the other island (like a "+").
    return any(_segments_cross(p1, p2, q1, q2) for p1, p2 in a["edges"] for q1, q2 in b["edges"])


def overlapping_pairs(shapes, exact=False, tolerance=0.01):
    """Index pairs of overlapping shapes, with a sweep over bbox U so it stays fast."""
    order = sorted(range(len(shapes)), key=lambda i: shapes[i]["bbox"][0])
    pairs = []
    for pos, i in enumerate(order):
        bi = shapes[i]["bbox"]
        reach = bi[2] + tolerance * max(bi[2] - bi[0], bi[3] - bi[1])
        for j in order[pos + 1:]:
            if shapes[j]["bbox"][0] > reach:
                break
            if shapes_overlap(shapes[i], shapes[j], exact, tolerance):
                pairs.append((i, j))
    return pairs


def clusters(count, pairs):
    """Connected groups of indices (union-find); singletons included."""
    parent = list(range(count))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in pairs:
        ri, rj = root(i), root(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)
    groups = {}
    for i in range(count):
        groups.setdefault(root(i), []).append(i)
    return sorted(groups.values())


# ---------------------------------------------------------------- precision
def pixel_snap_offset(bbox, resolution):
    """Move that puts a bbox's lower-left corner on the texture's pixel grid."""
    res = float(resolution)
    return (round(bbox[0] * res) / res - bbox[0], round(bbox[1] * res) / res - bbox[1])


# (u3dLayout resolution factor, mutations) per precision level: low .. max.
# Measured: doubling the resolution costs ~3x the time and gains the most;
# extra mutations cost ~2x for a smaller gain.
PRECISION = ((0.5, 1), (1.0, 1), (2.0, 1), (2.0, 2))


def search_candidates(precision):
    """(precision level, mutations) a heuristic search tries, cheapest first."""
    start = max(0, min(int(precision), len(PRECISION) - 1))
    result = []
    for level in range(start, len(PRECISION)):
        base = PRECISION[level][1]
        result += [(level, base), (level, base * 2)]
    return result
