"""Pack UV groups with Maya's built-in Unfold3D packer (u3dLayout).

Modes, mirroring UVPackmaster 4:
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
"""
import maya.cmds as cmds

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
                 atlas_cols=3, atlas_rows=3, cells_per_group=1):
        self.mode = mode
        self.split_shared_shells = bool(split_shared_shells)
        self.resolution = int(resolution)
        self.shell_padding_px = float(shell_padding_px)
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
    edges = []
    for g in group_list:
        edges += cmds.polyListComponentConversion(g["faces"], fromFace=True, toEdge=True,
                                                  border=True) or []
    if edges:
        cmds.polyMapCut(edges)


def _fit_into_box(uvs, box):
    """Uniformly scale/move UVs so their bounding box sits inside box."""
    bbox = uv_bbox(uvs)
    if bbox is None or layout.contains(box, bbox):
        return
    scale, off_u, off_v = layout.fit_transform(bbox, box)
    cmds.polyEditUV(uvs, pivotU=0.0, pivotV=0.0, scaleU=scale, scaleV=scale)
    cmds.polyEditUV(uvs, uValue=off_u, vValue=off_v, relative=True)


def pack_group(faces, box, options):
    """Pack the UV shells of faces into box = (u_min, v_min, u_max, v_max)."""
    uvs = _to_uvs(faces)
    if not uvs:
        return
    width = max(box[2] - box[0], 1e-6)
    height = max(box[3] - box[1], 1e-6)
    # u3dLayout's resolution and padding are relative to the pack box, so scale
    # the pixel padding to keep it constant in final texture pixels.
    box_res = max(64, int(options.resolution * max(width, height)))
    padding = options.shell_padding_px / float(options.resolution) / max(width, height)
    rot_mode, rot_step = ROTATE_MODES.get(options.rotate_step, ROTATE_MODES[90])
    cmds.u3dLayout(uvs,
                   resolution=box_res,
                   preScaleMode=1,
                   preRotateMode=rot_mode,
                   rotateStep=rot_step,
                   rotateMin=0.0,
                   rotateMax=360.0,
                   shellSpacing=padding,
                   tileMargin=padding,
                   packBox=[box[0], box[2], box[1], box[3]])
    _fit_into_box(uvs, box)


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


def _uv_area(faces):
    areas = cmds.polyEvaluate(faces, uvFaceArea=True) if faces else 0.0
    return float(sum(areas)) if isinstance(areas, (list, tuple)) else float(areas or 0.0)


def _pack_tiles(island_list, count, options):
    """Spread islands over count tiles and pack each tile.

    Returns (tile boxes, bins of island indices, scale u3dLayout applied per tile).
    """
    pad = options.shell_padding_px / float(options.resolution)
    items = [(index, (i["bbox"][2] - i["bbox"][0] + pad) * (i["bbox"][3] - i["bbox"][1] + pad))
             for index, i in enumerate(island_list)]
    bins = layout.distribute(items, count, options.fill_method, capacity=FILL_RATIO)
    tiles = layout.tile_boxes(count, options.tile_u, options.tile_v, options.tiles_per_row)
    rot_mode, rot_step = ROTATE_MODES.get(options.rotate_step, ROTATE_MODES[90])
    scales = []
    for box, members in zip(tiles, bins):
        if not members:
            scales.append(None)
            continue
        uvs = [u for index in members for u in island_list[index]["uvs"]]
        faces = [f for index in members for f in island_list[index]["faces"]]
        before = _uv_area(faces)
        # preScaleMode 0 keeps the islands' relative sizes (their texel density
        # tiers); u3dLayout then scales the whole tile's content to fit.
        cmds.u3dLayout(uvs, resolution=options.resolution, preScaleMode=0,
                       preRotateMode=rot_mode, rotateStep=rot_step,
                       rotateMin=0.0, rotateMax=360.0,
                       shellSpacing=pad, tileMargin=pad,
                       packBox=[box[0], box[2], box[1], box[3]])
        after = _uv_area(faces)
        scales.append((after / before) ** 0.5 if before > 0 else 1.0)
    return tiles, bins, scales


def _refresh(island):
    """Re-read an island's UV bbox and area after it was moved."""
    return {"bbox": uv_bbox(island["uvs"]) or island["bbox"], "area_uv": _uv_area(island["faces"])}


def pack_islands(options, island_list=None):
    """Dynamic Tiles / Tile Range. Returns {UDIM number: box} for the used tiles."""
    _ensure_plugin()
    island_list = island_list if island_list is not None else island_targets()
    if not island_list:
        return {}
    dynamic = options.mode == MODE_DYNAMIC
    density = _target_density(island_list, options)
    count = (layout.tiles_needed(_area_at_density(island_list, options), FILL_RATIO)
             if dynamic else options.tile_count)

    selection = cmds.ls(selection=True)
    cmds.undoInfo(openChunk=True, chunkName="UVGroupPacker")
    try:
        while True:
            # Every try starts from the exact target density, so a tile that
            # had to shrink its islands shows up as a scale below 1.
            uvislands.set_density(island_list, density, options.resolution)
            tiles, bins, scales = _pack_tiles(island_list, count, options)
            used = [s for s in scales if s is not None]
            smallest = min(used) if used else 1.0
            # More tiles can't help once every island has a tile to itself:
            # an island bigger than a tile at this density gets scaled down.
            if (not dynamic or smallest >= 0.97 or count >= len(island_list)
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
                uvs = [u for index in members for u in island_list[index]["uvs"]]
                factor = common / scale
                cmds.polyEditUV(uvs, pivotU=box[0], pivotV=box[1], scaleU=factor, scaleV=factor)
            result[1001 + int(box[0]) + 10 * int(box[1])] = box
    finally:
        if selection:
            cmds.select(selection, replace=True)
        else:
            cmds.select(clear=True)
        cmds.undoInfo(closeChunk=True)
    return result


def pack(options=None):
    """Pack with whatever mode the options ask for. Returns {label: box}."""
    options = options or PackOptions()
    if options.mode in ISLAND_MODES:
        return pack_islands(options)
    return pack_groups(options)


def pack_groups(options=None, names=None):
    """Pack every unlocked group (or only the named ones). Returns {name: box}."""
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
        for g in targets:
            box = boxes.get(g["name"])
            if box:
                pack_group(g["faces"], box, options)
    finally:
        # Restore the selection inside the chunk so one undo reverts the whole pack.
        if selection:
            cmds.select(selection, replace=True)
        else:
            cmds.select(clear=True)
        cmds.undoInfo(closeChunk=True)
    return boxes
