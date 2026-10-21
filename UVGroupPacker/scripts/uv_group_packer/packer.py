"""Pack UV groups with Maya's built-in Unfold3D packer (u3dLayout).

Modes, mirroring UVPackmaster:
  MODE_SINGLE_TILE  every group gets its own rectangle inside one UV tile
                    (box area proportional to the group's surface area).
  MODE_TILES        every group gets its own whole UDIM tile.
  MODE_ATLAS        texture atlas: the tile is a grid of cells and every group
                    gets a block of cells.
  MODE_DYNAMIC      islands are packed at a fixed texel density into as many
                    tiles as they need.
  MODE_TILE_RANGE   islands are spread over a fixed number of tiles at the
                    highest texel density that fits.
A group with a custom box (see groups.set_box) always packs into that box.

Locking: islands that overlap (Lock Overlapping) or share a lock group keep
their relative positions. Such a unit is packed as one piece by letting a
temporary proxy quad (the unit's bounding box) take its place in u3dLayout and
then moving the unit the same way the proxy moved.
"""
import math
import time

import maya.cmds as cmds

from . import align_math
from . import groups as uvgroups
from . import islands as uvislands
from . import layout

MODE_SINGLE_TILE = "single_tile"
MODE_TILES = "tiles"
MODE_ATLAS = "atlas"
MODE_DYNAMIC = "dynamic"
MODE_TILE_RANGE = "tile_range"

GROUP_MODES = (MODE_SINGLE_TILE, MODE_TILES, MODE_ATLAS)
ISLAND_MODES = (MODE_DYNAMIC, MODE_TILE_RANGE)

LOCK_OFF = "off"
LOCK_ANY = "any"
LOCK_EXACT = "exact"

TD_INDEPENDENT = "independent"
TD_UNIFORM = "uniform"

# Share of a tile that u3dLayout usually manages to fill; used to guess how
# many tiles Dynamic Tiles needs before the first try.
FILL_RATIO = 0.6
MAX_DYNAMIC_TILES = 100

AREA_3D = "3d"
AREA_UV = "uv"

ROTATE_MODES = {0: (0, 90.0), 90: (1, 90.0), 45: (1, 45.0)}


class PackOptions(object):
    def __init__(self, mode=MODE_SINGLE_TILE, resolution=1024, shell_padding_px=4.0,
                 box_gap_px=8.0, rotate_step=90, area_source=AREA_3D,
                 tile_u=0, tile_v=0, tiles_per_row=10, split_shared_shells=True,
                 tile_count=4, fill_method=layout.FILL_SIMULTANEOUS, texel_density=0.0,
                 atlas_cols=3, atlas_rows=3, cells_per_group=1,
                 tile_margin_px=None, precision=1, pixel_perfect=False,
                 lock_overlapping=LOCK_OFF, search_time=0.0, td_policy=TD_INDEPENDENT):
        self.mode = mode
        self.split_shared_shells = bool(split_shared_shells)
        self.resolution = int(resolution)
        self.shell_padding_px = float(shell_padding_px)
        # Space between islands and the box/tile border; defaults to the shell padding.
        self.tile_margin_px = float(shell_padding_px if tile_margin_px is None else tile_margin_px)
        self.box_gap_px = float(box_gap_px)
        self.rotate_step = int(rotate_step)
        self.area_source = area_source
        self.tile_u = int(tile_u)
        self.tile_v = int(tile_v)
        self.tiles_per_row = max(1, int(tiles_per_row))
        self.tile_count = max(1, int(tile_count))
        self.fill_method = fill_method
        # px per scene unit; 0 keeps the islands' current average density.
        self.texel_density = max(0.0, float(texel_density))
        self.atlas_cols = max(1, int(atlas_cols))
        self.atlas_rows = max(1, int(atlas_rows))
        self.cells_per_group = max(1, int(cells_per_group))
        # Index into layout.PRECISION: 0 low .. 3 max.
        self.precision = max(0, min(int(precision), len(layout.PRECISION) - 1))
        self.mutations = None  # set by the heuristic search; None = from precision
        self.pixel_perfect = bool(pixel_perfect)
        self.lock_overlapping = lock_overlapping
        # Seconds the heuristic search may spend trying settings; 0 = off.
        self.search_time = max(0.0, float(search_time))
        self.td_policy = td_policy

    def copy(self, **changes):
        clone = PackOptions.__new__(PackOptions)
        clone.__dict__.update(self.__dict__)
        clone.__dict__.update(changes)
        return clone


def _ensure_plugin():
    if not cmds.pluginInfo("Unfold3D", query=True, loaded=True):
        cmds.loadPlugin("Unfold3D", quiet=True)


def _to_uvs(faces):
    return cmds.polyListComponentConversion(faces, toUV=True) or []


def uv_bbox(uvs):
    """Return (u_min, v_min, u_max, v_max) of the given UVs, or None."""
    # polyEvaluate(boundingBox2d=True) reports the whole mesh for a partial
    # component list, so read the coordinates directly.
    coords = cmds.polyEditUV(uvs, query=True) if uvs else None
    if not coords:
        return None
    us, vs = coords[0::2], coords[1::2]
    return (min(us), min(vs), max(us), max(vs))


def group_area(faces, source=AREA_3D):
    if not faces:
        return 0.0
    flag = {"worldFaceArea": True} if source == AREA_3D else {"uvFaceArea": True}
    areas = cmds.polyEvaluate(faces, **flag)
    if isinstance(areas, (list, tuple)):
        return float(sum(areas))
    return float(areas or 0.0)


def _uv_area(faces):
    areas = cmds.polyEvaluate(faces, uvFaceArea=True) if faces else 0.0
    return float(sum(areas)) if isinstance(areas, (list, tuple)) else float(areas or 0.0)


def compute_group_boxes(group_list, options):
    """Return {group name: box} for the given group dicts and options.

    Groups with a custom box keep it; the others share the automatic layout.
    """
    boxes = {g["name"]: g["box"] for g in group_list if g.get("box")}
    auto = [g for g in group_list if not g.get("box")]
    tile = (options.tile_u, options.tile_v, options.tile_u + 1.0, options.tile_v + 1.0)
    gap = options.box_gap_px / float(options.resolution)

    if options.mode == MODE_TILES:
        tiles = layout.tile_boxes(len(auto), options.tile_u, options.tile_v,
                                  options.tiles_per_row, margin=gap * 0.5)
        boxes.update((g["name"], box) for g, box in zip(auto, tiles))
    elif options.mode == MODE_ATLAS:
        boxes.update(layout.atlas_boxes([g["name"] for g in auto], options.atlas_cols,
                                        options.atlas_rows, options.cells_per_group,
                                        tile=tile, gap=gap))
    else:
        weights = {g["name"]: group_area(g["faces"], options.area_source) * g["weight"]
                   for g in auto}
        boxes.update(layout.compute_boxes(weights, tile=tile, gap=gap))
    return boxes


def island_targets():
    """Islands to pack in the island modes.

    The selection if there is one, otherwise every unlocked group (each island
    carries its group's weight as a texel density multiplier, so heavier groups
    get more pixels: texel density tiers), otherwise every mesh in the scene.
    """
    selection = uvgroups.selected_faces()
    if selection:
        return uvislands.shells(selection)
    group_list = [g for g in uvgroups.list_groups() if g["faces"] and not g["locked"]]
    if group_list:
        result = []
        for g in group_list:
            result += uvislands.shells(g["faces"], weight=g["weight"] or 1.0)
        return result
    return uvislands.shells(cmds.ls(type="mesh", noIntermediate=True) or [])


def estimate_tile_boxes(options, island_list=None):
    """Tiles an island mode would use, for previews (no UVs are changed).

    Dynamic Tiles guesses the count from the islands' area at the target
    texel density; the real pack may add a tile or two.
    """
    count = options.tile_count
    if options.mode == MODE_DYNAMIC:
        island_list = island_list if island_list is not None else island_targets()
        count = layout.tiles_needed(_area_at_density(island_list, options), FILL_RATIO)
    return layout.tile_boxes(count, options.tile_u, options.tile_v, options.tiles_per_row)


def split_shared_shells(group_list):
    """Cut UV seams along group borders so no UV shell belongs to two groups.

    A shell that spans two groups would otherwise be moved by both packs.
    """
    by_mesh = {}
    for g in group_list:
        for edge in cmds.polyListComponentConversion(g["faces"], fromFace=True, toEdge=True,
                                                     border=True) or []:
            by_mesh.setdefault(edge.split(".")[0], []).append(edge)
    # polyMapCut refuses edges of more than one object at a time.
    for edges in by_mesh.values():
        cmds.polyMapCut(edges)


# ---------------------------------------------------------------- locking
def build_units(island_list, options):
    """Group island indices into units that must keep their relative positions."""
    pairs = []
    if options.lock_overlapping in (LOCK_ANY, LOCK_EXACT):
        shapes = [uvislands.overlap_shape(i) for i in island_list]
        pairs += layout.overlapping_pairs(shapes, exact=options.lock_overlapping == LOCK_EXACT)
    locks = uvgroups.lock_groups()
    if locks:
        lookup = uvislands.tag_lookup(locks)
        by_lock = {}
        for index, island in enumerate(island_list):
            tag = uvislands.island_tag(island, lookup)
            if tag is not None:
                by_lock.setdefault(tag, []).append(index)
        for members in by_lock.values():
            pairs += [(members[0], other) for other in members[1:]]
    return layout.clusters(len(island_list), pairs)


def _make_proxy(island_list, unit):
    """A one-quad mesh whose UVs are the unit's bbox and whose 3D/UV ratio matches it."""
    uvs = [u for i in unit for u in island_list[i]["uvs"]]
    u0, v0, u1, v1 = uv_bbox(uvs)
    width, height = max(u1 - u0, 1e-6), max(v1 - v0, 1e-6)
    area3d = sum(island_list[i]["area3d"] for i in unit)
    area_uv = sum(island_list[i]["area_uv"] for i in unit)
    k = math.sqrt(area3d / area_uv) if area_uv > 0 and area3d > 0 else 1.0
    proxy = cmds.polyPlane(name="uvgpProxy#", width=width * k, height=height * k,
                           subdivisionsX=1, subdivisionsY=1, constructionHistory=False)[0]
    corners = proxy + ".map[0:3]"
    cmds.polyEditUV(corners, pivotU=0.0, pivotV=0.0, scaleU=width, scaleV=height)
    cmds.polyEditUV(corners, uValue=u0, vValue=v0, relative=True)
    return proxy, uvs


def _corners(proxy):
    coords = cmds.polyEditUV(proxy + ".map[0:3]", query=True)
    return list(zip(coords[0::2], coords[1::2]))


def _layout(island_list, units, box, options, pre_scale, padding_px=None, rotate_step=None):
    """Run u3dLayout on the given units inside box.

    pre_scale: u3dLayout preScaleMode (1 = by 3D area, 0 = keep UV sizes).
    """
    padding_px = options.shell_padding_px if padding_px is None else padding_px
    rotate_step = options.rotate_step if rotate_step is None else rotate_step
    singles = [u for unit in units if len(unit) == 1 for u in island_list[unit[0]]["uvs"]]
    proxies = []
    for unit in units:
        if len(unit) > 1:
            proxy, uvs = _make_proxy(island_list, unit)
            proxies.append((proxy, uvs, _corners(proxy)))
    targets = singles + [p + ".map[0:3]" for p, _, _ in proxies]
    if not targets:
        return

    size = max(box[2] - box[0], box[3] - box[1], 1e-6)
    factor, mutations = layout.PRECISION[options.precision]
    # u3dLayout's resolution and spacing are relative to the pack box, so scale
    # the pixel values to keep them constant in final texture pixels.
    res = float(options.resolution)
    rot_mode, rot_step = ROTATE_MODES.get(rotate_step, ROTATE_MODES[90])
    try:
        cmds.u3dLayout(targets,
                       resolution=max(64, int(res * factor * size)),
                       mutations=options.mutations or mutations,
                       preScaleMode=pre_scale,
                       preRotateMode=rot_mode,
                       rotateStep=rot_step,
                       rotateMin=0.0,
                       rotateMax=360.0,
                       shellSpacing=padding_px / res / size,
                       tileMargin=options.tile_margin_px / res / size,
                       packBox=[box[0], box[2], box[1], box[3]])
        for proxy, uvs, before in proxies:
            match = align_math.procrustes(before, _corners(proxy), allow_flip=True,
                                          adjust_scale=True)
            uvislands.apply_match(uvs, match)
    finally:
        if proxies:
            cmds.delete([p for p, _, _ in proxies])


def _fit_into_box(uvs, box):
    """Uniformly scale/move UVs so their bounding box sits inside box."""
    bbox = uv_bbox(uvs)
    if bbox is None or layout.contains(box, bbox):
        return
    scale, off_u, off_v = layout.fit_transform(bbox, box)
    cmds.polyEditUV(uvs, pivotU=0.0, pivotV=0.0, scaleU=scale, scaleV=scale)
    cmds.polyEditUV(uvs, uValue=off_u, vValue=off_v, relative=True)


def pixel_snap(faces, resolution):
    """Move every island so its lower-left corner sits on the pixel grid."""
    for island in uvislands.shells(faces):
        du, dv = layout.pixel_snap_offset(uv_bbox(island["uvs"]), resolution)
        if abs(du) > 1e-12 or abs(dv) > 1e-12:
            cmds.polyEditUV(island["uvs"], uValue=du, vValue=dv, relative=True)


def pack_group(faces, box, options, padding_px=None, rotate_step=None):
    """Pack the UV shells of faces into box = (u_min, v_min, u_max, v_max)."""
    island_list = uvislands.shells(faces)
    if not island_list:
        return
    units = build_units(island_list, options)
    _layout(island_list, units, box, options, pre_scale=1,
            padding_px=padding_px, rotate_step=rotate_step)
    _fit_into_box(_to_uvs(faces), box)


# ---------------------------------------------------------------- island modes
def _target_density(island_list, options):
    return options.texel_density or uvislands.average_density(island_list, options.resolution)


def _area_at_density(island_list, options):
    """UV area (bbox + padding) the islands take at the target texel density."""
    if not island_list:
        return 0.0
    density = _target_density(island_list, options)
    pad = options.shell_padding_px / float(options.resolution)
    total = 0.0
    for island in island_list:
        current = uvislands.texel_density(island["area3d"], island["area_uv"], options.resolution)
        factor = density * island["weight"] / current if current > 0 else 1.0
        u0, v0, u1, v1 = island["bbox"]
        total += ((u1 - u0) * factor + pad) * ((v1 - v0) * factor + pad)
    return total


def _refresh(island):
    """Re-read an island's UV bbox and area after it was moved."""
    return {"bbox": uv_bbox(island["uvs"]) or island["bbox"], "area_uv": _uv_area(island["faces"])}


def _set_unit_density(island_list, units, density, resolution):
    """Scale islands to the density; a locked unit scales as a whole to keep its layout."""
    for unit in units:
        members = [island_list[i] for i in unit]
        if len(members) == 1:
            uvislands.set_density(members, density, resolution)
            continue
        current = uvislands.average_density(members, resolution)
        if current <= 0:
            continue
        total3d = max(sum(m["area3d"] for m in members), 1e-12)
        weight = sum(m["weight"] * m["area3d"] for m in members) / total3d
        factor = density * weight / current
        uvs = [u for m in members for u in m["uvs"]]
        u0, v0, u1, v1 = uv_bbox(uvs)
        cmds.polyEditUV(uvs, pivotU=(u0 + u1) * 0.5, pivotV=(v0 + v1) * 0.5,
                        scaleU=factor, scaleV=factor)
        for m in members:
            m.update(_refresh(m))


def _unit_area(island_list, unit, pad):
    boxes = [island_list[i]["bbox"] for i in unit]
    u0, v0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    u1, v1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    return (u1 - u0 + pad) * (v1 - v0 + pad)


def _pack_tiles(island_list, units, count, options, progress=None):
    """Spread units over count tiles and pack each tile.

    Returns (tile boxes, bins of unit indices, scale u3dLayout applied per tile).
    """
    pad = options.shell_padding_px / float(options.resolution)
    items = [(index, _unit_area(island_list, unit, pad)) for index, unit in enumerate(units)]
    bins = layout.distribute(items, count, options.fill_method, capacity=FILL_RATIO)
    tiles = layout.tile_boxes(count, options.tile_u, options.tile_v, options.tiles_per_row)
    scales = []
    for box, members in zip(tiles, bins):
        if not members:
            scales.append(None)
            continue
        faces = [f for m in members for i in units[m] for f in island_list[i]["faces"]]
        before = _uv_area(faces)
        # preScaleMode 0 keeps the islands' relative sizes (their texel density
        # tiers); u3dLayout then scales the whole tile's content to fit.
        _layout(island_list, [units[m] for m in members], box, options, pre_scale=0)
        after = _uv_area(faces)
        scales.append((after / before) ** 0.5 if before > 0 else 1.0)
        if progress:
            progress(len(scales), count, 1001 + int(box[0]) + 10 * int(box[1]))
    return tiles, bins, scales


def pack_islands(options, island_list=None, progress=None):
    """Dynamic Tiles / Tile Range. Returns {UDIM number: box} for the used tiles.

    progress(done, total, label) is called after every tile, e.g. to redraw a preview.
    """
    _ensure_plugin()
    island_list = island_list if island_list is not None else island_targets()
    if not island_list:
        return {}
    dynamic = options.mode == MODE_DYNAMIC
    density = _target_density(island_list, options)
    units = build_units(island_list, options)
    count = (layout.tiles_needed(_area_at_density(island_list, options), FILL_RATIO)
             if dynamic else options.tile_count)

    selection = cmds.ls(selection=True)
    cmds.undoInfo(openChunk=True, chunkName="UVGroupPacker")
    try:
        while True:
            # Every try starts from the exact target density, so a tile that
            # had to shrink its islands shows up as a scale below 1.
            _set_unit_density(island_list, units, density, options.resolution)
            tiles, bins, scales = _pack_tiles(island_list, units, count, options, progress)
            used = [s for s in scales if s is not None]
            smallest = min(used) if used else 1.0
            # More tiles can't help once every unit has a tile to itself:
            # a unit bigger than a tile at this density gets scaled down.
            if (not dynamic or smallest >= 0.97 or count >= len(units)
                    or count >= MAX_DYNAMIC_TILES):
                break
            count += 1
            island_list = [dict(i, **_refresh(i)) for i in island_list]

        # One texel density for every tile: Dynamic Tiles keeps the target
        # (unless it never fit), Tile Range uses the largest that fits everywhere.
        common = min(smallest, 1.0) if dynamic else smallest
        result = {}
        for box, members, scale in zip(tiles, bins, scales):
            if scale is None:
                continue
            if scale > common + 1e-6:
                uvs = [u for m in members for i in units[m] for u in island_list[i]["uvs"]]
                factor = common / scale
                cmds.polyEditUV(uvs, pivotU=box[0], pivotV=box[1], scaleU=factor, scaleV=factor)
            result[1001 + int(box[0]) + 10 * int(box[1])] = box
        if options.pixel_perfect:
            pixel_snap([f for i in island_list for f in i["faces"]], options.resolution)
    finally:
        if selection:
            cmds.select(selection, replace=True)
        else:
            cmds.select(clear=True)
        cmds.undoInfo(closeChunk=True)
    return result


# ---------------------------------------------------------------- group modes
def _uniform_density(targets, boxes, resolution):
    """Shrink groups so all of them share the lowest texel density (times their weight)."""
    densities = {}
    for g in targets:
        if g["name"] in boxes:
            density = uvislands.faces_density(g["faces"], resolution)
            densities[g["name"]] = density / (g["weight"] or 1.0)
    if not densities:
        return
    common = min(densities.values())
    for g in targets:
        density = densities.get(g["name"])
        if density and density > common * 1.0001:
            box = boxes[g["name"]]
            factor = common / density
            cmds.polyEditUV(_to_uvs(g["faces"]), pivotU=box[0], pivotV=box[1],
                            scaleU=factor, scaleV=factor)


def pack_groups(options=None, names=None, progress=None):
    """Pack every unlocked group (or only the named ones). Returns {name: box}.

    progress(done, total, group name) is called after every group.
    """
    options = options or PackOptions()
    _ensure_plugin()

    all_groups = [g for g in uvgroups.list_groups() if g["faces"]]
    targets = [g for g in all_groups if not g["locked"]]
    if names:
        targets = [g for g in targets if g["name"] in names]
    if not targets:
        return {}

    selection = cmds.ls(selection=True)
    cmds.undoInfo(openChunk=True, chunkName="UVGroupPacker")
    try:
        if options.split_shared_shells:
            split_shared_shells(all_groups)
        boxes = compute_group_boxes(targets, options)
        for index, g in enumerate(targets):
            box = boxes.get(g["name"])
            if box:
                pack_group(g["faces"], box, options, padding_px=g.get("padding"),
                           rotate_step=g.get("rotation"))
            if progress:
                progress(index + 1, len(targets), g["name"])
        if options.td_policy == TD_UNIFORM:
            _uniform_density(targets, boxes, options.resolution)
        if options.pixel_perfect:
            pixel_snap([f for g in targets if g["name"] in boxes for f in g["faces"]],
                       options.resolution)
    finally:
        # Restore the selection inside the chunk so one undo reverts the whole pack.
        if selection:
            cmds.select(selection, replace=True)
        else:
            cmds.select(clear=True)
        cmds.undoInfo(closeChunk=True)
    return boxes


# ---------------------------------------------------------------- search
def _pack_once(options, progress=None):
    if options.mode in ISLAND_MODES:
        return pack_islands(options, progress=progress)
    return pack_groups(options, progress=progress)


def _coverage(options):
    """UV area the packed islands cover; bigger means a tighter pack."""
    if options.mode in ISLAND_MODES:
        faces = [f for i in island_targets() for f in i["faces"]]
    else:
        faces = [f for g in uvgroups.list_groups() if g["faces"] and not g["locked"]
                 for f in g["faces"]]
    return _uv_area(faces)


def search(options, report=None, progress=None):
    """Heuristic search: try precision settings for up to options.search_time
    seconds, undo every try and finish with the best one.

    Returns (result of the final pack, coverage of every try). Every try is
    reverted with undo, so without undo this is a plain pack.
    """
    if not cmds.undoInfo(query=True, state=True):
        return _pack_once(options), []
    if options.mode in GROUP_MODES and options.split_shared_shells:
        # Cut seams once, so every try packs the same shells.
        split_shared_shells([g for g in uvgroups.list_groups() if g["faces"]])
        options = options.copy(split_shared_shells=False)
    deadline = time.time() + options.search_time
    tries = []
    for level, mutations in layout.search_candidates(options.precision):
        if tries and time.time() >= deadline:
            break
        trial = options.copy(precision=level, mutations=mutations)
        _pack_once(trial)
        score = _coverage(trial)
        cmds.undo()
        tries.append((score, trial))
        if report:
            report(len(tries), score)
    best = max(tries, key=lambda t: t[0])[1]
    return _pack_once(best, progress), [t[0] for t in tries]


def pack(options=None, progress=None):
    """Pack with whatever mode the options ask for. Returns {label: box}."""
    options = options or PackOptions()
    if options.search_time > 0:
        return search(options, progress=progress)[0]
    return _pack_once(options, progress)
