"""Pack UV groups with Maya's built-in Unfold3D packer (u3dLayout).

Two modes, mirroring UVPackmaster's group modes:
  MODE_SINGLE_TILE  every group gets its own rectangle inside one UV tile
                    (box area proportional to the group's surface area).
  MODE_TILES        every group gets its own whole UDIM tile.
"""
import maya.cmds as cmds

from . import groups as uvgroups
from . import layout

MODE_SINGLE_TILE = "single_tile"
MODE_TILES = "tiles"

AREA_3D = "3d"
AREA_UV = "uv"

ROTATE_MODES = {0: (0, 90.0), 90: (1, 90.0), 45: (1, 45.0)}


class PackOptions(object):
    def __init__(self, mode=MODE_SINGLE_TILE, resolution=1024, shell_padding_px=4.0,
                 box_gap_px=8.0, rotate_step=90, area_source=AREA_3D,
                 tile_u=0, tile_v=0, tiles_per_row=10, split_shared_shells=True):
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
    """Return {group name: box} for the given group dicts and options."""
    if options.mode == MODE_TILES:
        boxes = {}
        gap = options.box_gap_px / float(options.resolution) * 0.5
        for index, g in enumerate(group_list):
            u = options.tile_u + index % options.tiles_per_row
            v = options.tile_v + index // options.tiles_per_row
            boxes[g["name"]] = (u + gap, v + gap, u + 1 - gap, v + 1 - gap)
        return boxes

    weights = {g["name"]: group_area(g["faces"], options.area_source) * g["weight"]
               for g in group_list}
    tile = (options.tile_u, options.tile_v, options.tile_u + 1.0, options.tile_v + 1.0)
    gap = options.box_gap_px / float(options.resolution)
    return layout.compute_boxes(weights, tile=tile, gap=gap)


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
