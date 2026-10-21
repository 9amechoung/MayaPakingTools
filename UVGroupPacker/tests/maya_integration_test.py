"""End-to-end test inside Maya. Run with mayapy:

    "C:/Program Files/Autodesk/Maya2025/bin/mayapy.exe" tests/maya_integration_test.py
"""
import itertools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import maya.standalone  # noqa: E402

maya.standalone.initialize()

import maya.cmds as cmds  # noqa: E402

from uv_group_packer import groups as uvgroups  # noqa: E402
from uv_group_packer import islands as uvislands  # noqa: E402
from uv_group_packer import layout, packer  # noqa: E402


def make_material(name, color):
    shader = cmds.shadingNode("lambert", asShader=True, name=name)
    cmds.setAttr(shader + ".color", *color, type="double3")
    engine = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=name + "SG")
    cmds.connectAttr(shader + ".outColor", engine + ".surfaceShader")
    return engine


def build_scene():
    cmds.file(new=True, force=True)
    mask = make_material("MASK", (0, 0, 1))
    eye = make_material("EYE", (1, 1, 0))
    lens = make_material("LENS", (0, 1, 1))
    arr = make_material("ARR", (0, 1, 0))

    head = cmds.polySphere(name="head", radius=3, subdivisionsAxis=24, subdivisionsHeight=16)[0]
    cmds.sets(head, forceElement=mask)
    for i, x in enumerate((-1.2, 1.2)):
        e = cmds.polyCylinder(name="eye%d" % i, radius=0.8, height=0.3)[0]
        cmds.move(x, 0.5, 2.8, e)
        cmds.sets(e, forceElement=eye)
        lens_obj = cmds.polySphere(name="lens%d" % i, radius=0.6)[0]
        cmds.move(x, 0.5, 3.0, lens_obj)
        cmds.sets(lens_obj, forceElement=lens)
    pipe = cmds.polyPipe(name="hose", radius=0.4, height=4)[0]
    cmds.sets(pipe, forceElement=arr)
    # One mesh with two materials, to check per-face grouping.
    box = cmds.polyCube(name="box", width=2, height=2, depth=2)[0]
    cmds.sets(box, forceElement=mask)
    cmds.sets(box + ".f[0:1]", forceElement=arr)


def check(cond, message):
    if not cond:
        raise AssertionError(message)
    print("  ok  " + message)


def pack_and_verify(options):
    boxes = packer.pack_groups(options)
    groups = uvgroups.list_groups()
    check(set(boxes) == {g["name"] for g in groups}, "every group got a box (%s)" % options.mode)
    bboxes = {}
    for g in groups:
        bbox = packer.uv_bbox(cmds.polyListComponentConversion(g["faces"], toUV=True))
        bboxes[g["name"]] = bbox
        check(layout.contains(boxes[g["name"]], bbox, eps=1e-3),
              "%s UVs inside its box %s" % (g["name"], tuple(round(v, 3) for v in bbox)))
    for (na, a), (nb, b) in itertools.combinations(bboxes.items(), 2):
        sep = a[2] <= b[0] + 1e-3 or b[2] <= a[0] + 1e-3 or a[3] <= b[1] + 1e-3 or b[3] <= a[1] + 1e-3
        check(sep, "%s and %s do not overlap" % (na, nb))
    return boxes


def check_islands_in_tiles(tiles):
    """Every island sits inside one of the tiles; returns each island's texel density."""
    densities = []
    for island in packer.island_targets():
        bbox = packer.uv_bbox(island["uvs"])
        if not any(layout.contains(box, bbox, eps=1e-3) for box in tiles.values()):
            raise AssertionError("island %s outside the tiles: %s" % (island["faces"][0], bbox))
        densities.append(uvislands.texel_density(island["area3d"], island["area_uv"], 1024)
                         / island["weight"])
    print("  ok  %d islands inside their tiles" % len(densities))
    return densities


def main():
    # Undo is off by default in mayapy.
    cmds.undoInfo(state=True, infinity=True)
    build_scene()
    cmds.select(clear=True)
    created = uvgroups.groups_from_materials()
    check(sorted(created) == ["ARR", "EYE", "LENS", "MASK"], "groups from materials: %s" % created)
    check(uvgroups.list_groups()[-1]["count"] > 0, "groups have faces")
    arr_faces = cmds.ls(uvgroups.group_faces("ARR"), flatten=True)
    check("box.f[0]" in arr_faces, "per-face material assignment respected")

    # Manual reassignment moves faces out of their old group.
    uvgroups.assign("EYE", ["box.f[2]"])
    check("box.f[2]" not in cmds.ls(uvgroups.group_faces("MASK"), flatten=True),
          "assign removes faces from the previous group")
    check("box.f[2]" in cmds.ls(uvgroups.group_faces("EYE"), flatten=True), "assign adds faces")

    print("single tile:")
    pack_and_verify(packer.PackOptions(mode=packer.MODE_SINGLE_TILE))

    print("UDIM tiles:")
    boxes = pack_and_verify(packer.PackOptions(mode=packer.MODE_TILES, tile_u=1))
    check(all(b[0] >= 1.0 for b in boxes.values()), "tiles start at U=1, one per group")

    print("texture atlas:")
    boxes = pack_and_verify(packer.PackOptions(mode=packer.MODE_ATLAS, atlas_cols=2, atlas_rows=2))
    check(all(abs((b[2] - b[0]) - 0.5) < 0.02 for b in boxes.values()), "atlas cells are 1/2 tile wide")

    print("custom box:")
    uvgroups.set_box("EYE", (2.0, 0.0, 2.5, 0.5))
    check(uvgroups.list_groups()[1]["box"] == (2.0, 0.0, 2.5, 0.5), "custom box stored on the group")
    boxes = pack_and_verify(packer.PackOptions(mode=packer.MODE_SINGLE_TILE))
    check(boxes["EYE"] == (2.0, 0.0, 2.5, 0.5), "group with a custom box packs into it")
    uvgroups.set_box("EYE", None)
    check(uvgroups.list_groups()[1]["box"] is None, "custom box cleared")

    print("tile range:")
    cmds.select(clear=True)
    island_list = packer.island_targets()
    tiles = packer.pack_islands(packer.PackOptions(mode=packer.MODE_TILE_RANGE, tile_count=3,
                                                   tiles_per_row=3, tile_u=1))
    check(sorted(tiles) == [1002, 1003, 1004], "tile range fills tiles 1002-1004: %s" % sorted(tiles))
    densities = check_islands_in_tiles(tiles)
    check(max(densities) / min(densities) < 1.02,
          "one texel density in every tile (%.1f..%.1f)" % (min(densities), max(densities)))

    print("dynamic tiles:")
    # The density tile range just reached in 3 tiles needs more than one tile.
    density = min(densities)
    tiles = packer.pack_islands(packer.PackOptions(mode=packer.MODE_DYNAMIC, texel_density=density,
                                                   tiles_per_row=4))
    check(1 < len(tiles) < 8, "dynamic tiles added tiles as needed: %s" % sorted(tiles))
    densities = check_islands_in_tiles(tiles)
    check(all(abs(d / density - 1.0) < 0.02 for d in densities),
          "every island at the requested %.1f px/unit" % density)

    print("texel density tiers:")
    uvgroups.set_weight("LENS", 2.0)
    tiles = packer.pack_islands(packer.PackOptions(mode=packer.MODE_TILE_RANGE, tile_count=2))
    lens = uvislands.faces_density(uvgroups.group_faces("LENS"), 1024)
    arr = uvislands.faces_density(uvgroups.group_faces("ARR"), 1024)
    check(abs(lens / arr - 2.0) < 0.05, "weight 2 group gets 2x texel density (%.2f)" % (lens / arr))
    uvgroups.set_weight("LENS", 1.0)

    print("lock:")
    uvgroups.set_locked("MASK", True)
    before = packer.uv_bbox(cmds.polyListComponentConversion(uvgroups.group_faces("MASK"), toUV=True))
    boxes = packer.pack_groups(packer.PackOptions(mode=packer.MODE_SINGLE_TILE))
    after = packer.uv_bbox(cmds.polyListComponentConversion(uvgroups.group_faces("MASK"), toUV=True))
    check("MASK" not in boxes and before == after, "locked group is not moved")

    print("undo:")
    cmds.undo()
    eye_bbox = packer.uv_bbox(cmds.polyListComponentConversion(uvgroups.group_faces("EYE"), toUV=True))
    check(eye_bbox[0] >= 1.0, "one undo restores the previous pack")

    print("ALL PASSED")


if __name__ == "__main__":
    try:
        main()
    finally:
        maya.standalone.uninitialize()
