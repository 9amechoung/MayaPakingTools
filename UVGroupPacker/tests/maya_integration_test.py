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


def uv_coords(node):
    coords = cmds.polyEditUV(node + ".map[*]", query=True)
    return list(zip(coords[0::2], coords[1::2]))


def max_uv_gap(a, b):
    return max(abs(p[0] - q[0]) + abs(p[1] - q[1]) for p, q in zip(uv_coords(a), uv_coords(b)))


def bbox_gap(a, b):
    ba = packer.uv_bbox(a + ".map[*]")
    bb = packer.uv_bbox(b + ".map[*]")
    return max(abs(x - y) for x, y in zip(ba, bb))


def test_align():
    from uv_group_packer import align

    cmds.file(new=True, force=True)
    # An L-shaped island (no symmetry), copies with moved UVs, and an unrelated cube.
    template = cmds.polyPlane(name="tpl", width=3, height=3, subdivisionsX=3, subdivisionsY=3)[0]
    # Keep the bottom row plus one face above it: an L whose mirror image is
    # not a rotation of itself.
    cmds.delete(template + ".f[4:8]")
    cmds.polyEditUV(template + ".map[*]", pivotU=0.5, pivotV=0.5, scaleU=0.4, scaleV=0.4)
    cmds.polyCube(name="other")

    def copy(name, angle, scale=1.0, flip=False, offset=(1.3, 0.2)):
        node = cmds.duplicate(template, name=name)[0]
        uvs = node + ".map[*]"
        cmds.polyEditUV(uvs, pivotU=0.5, pivotV=0.5, scaleU=-scale if flip else scale, scaleV=scale)
        cmds.polyEditUV(uvs, pivotU=0.5, pivotV=0.5, angle=angle)
        cmds.polyEditUV(uvs, uValue=offset[0], vValue=offset[1], relative=True)
        return node

    print("align to unselected (vertex position):")
    a = copy("rotA", 63)
    b = copy("rotB", -140, offset=(2.1, 1.4))
    cmds.select(a, b)
    result = align.align_to_unselected(align.AlignOptions(mode=align.MODE_VERTEX, threshold=0.01))
    check(result["aligned"] == 2, "both copies aligned: %s" % result)
    check(max_uv_gap(a, template) < 1e-5 and max_uv_gap(b, template) < 1e-5,
          "every UV sits exactly on the template")
    cmds.undo()
    check(max_uv_gap(a, template) > 0.1, "one undo reverts the align")
    cmds.delete(a, b)

    print("align to unselected (border shape):")
    small = copy("small", 37, scale=0.6)
    mirrored = copy("mirrored", 120, flip=True, offset=(-1.0, 0.4))
    cube_before = uv_coords("other")
    cmds.select(small, mirrored)
    result = align.align_to_unselected(align.AlignOptions(threshold=0.1))
    check(result["aligned"] == 0, "no match without Adjust Scale / Flipping: %s" % result)
    result = align.align_to_unselected(align.AlignOptions(threshold=0.1, adjust_scale=True,
                                                          allow_flip=True))
    check(result["aligned"] == 2, "scaled and mirrored copies aligned: %s" % result)
    check(bbox_gap(small, template) < 0.01 and bbox_gap(mirrored, template) < 0.01,
          "aligned copies cover the template (bbox gaps %.4f, %.4f)"
          % (bbox_gap(small, template), bbox_gap(mirrored, template)))
    check(uv_coords("other") == cube_before, "unrelated island untouched")


def plane(name, u_scale=0.3, v_scale=0.3, offset=(0.0, 0.0)):
    """A 2x2 plane whose UVs fill a small rectangle at offset."""
    node = cmds.polyPlane(name=name, width=2, height=2, subdivisionsX=2, subdivisionsY=2)[0]
    uvs = node + ".map[*]"
    cmds.polyEditUV(uvs, pivotU=0, pivotV=0, scaleU=u_scale, scaleV=v_scale)
    cmds.polyEditUV(uvs, uValue=offset[0], vValue=offset[1], relative=True)
    return node


def relative_error(nodes_before, nodes):
    """How far the nodes' UVs are from a rigid (similarity) move of their old UVs."""
    from uv_group_packer import align_math
    before = [p for n in nodes for p in nodes_before[n]]
    after = [p for n in nodes for p in uv_coords(n)]
    return align_math.procrustes(before, after, allow_flip=True, adjust_scale=True)["error"]


def test_packing_extras():
    print("lock overlapping (exact):")
    cmds.file(new=True, force=True)
    a = plane("stackA", offset=(0.2, 0.2))
    b = cmds.duplicate(a, name="stackB")[0]
    plane("loose", offset=(0.25, 0.25))
    cmds.select(clear=True)
    options = packer.PackOptions(mode=packer.MODE_TILE_RANGE, tile_count=1, shell_padding_px=8)
    packer.pack_islands(options.copy(lock_overlapping=packer.LOCK_EXACT))
    check(max_uv_gap(a, b) < 1e-4, "identical stacked islands stay stacked")
    check(bbox_gap(a, "loose") > 0.05, "a partly overlapping island is not locked in exact mode")
    cmds.undo()
    packer.pack_islands(options)
    check(bbox_gap(a, b) > 0.05, "without locking the stack is pulled apart")
    cmds.undo()

    print("lock overlapping (any part):")
    before = {n: uv_coords(n) for n in (a, b, "loose")}
    packer.pack_islands(options.copy(lock_overlapping=packer.LOCK_ANY))
    check(relative_error(before, [a, b, "loose"]) < 1e-4, "overlapping islands keep their layout")
    cmds.undo()

    print("lock groups:")
    cmds.file(new=True, force=True)
    left = plane("left", offset=(0.0, 0.0))
    right = plane("right", offset=(0.5, 0.1))
    plane("free", offset=(0.1, 0.6))
    number = uvgroups.new_lock_group([left, right])
    check(number == 1 and len(uvgroups.lock_groups()) == 1, "lock group created")
    before = {n: uv_coords(n) for n in (left, right)}
    cmds.select(clear=True)
    packer.pack_islands(options)
    check(relative_error(before, [left, right]) < 1e-4, "lock group keeps relative positions")
    uvgroups.clear_lock_group([left, right])
    check(not uvgroups.lock_groups(), "lock group cleared")

    print("margins, pixel perfect:")
    packer.pack_islands(options.copy(tile_margin_px=64, resolution=1024, pixel_perfect=True))
    bbox = packer.uv_bbox(cmds.ls("*.map[*]"))
    check(bbox[0] >= 64 / 1024.0 - 2e-3 and bbox[2] <= 1 - 64 / 1024.0 + 2e-3,
          "tile margin keeps islands 64px from the border %s" % (tuple(round(x, 4) for x in bbox),))
    for node in (left, right, "free"):
        u0 = packer.uv_bbox(node + ".map[*]")[0] * 1024
        check(abs(u0 - round(u0)) < 1e-3, "%s starts on a pixel (%.4f px)" % (node, u0))

    print("groups from objects, overrides, uniform texel density:")
    created = uvgroups.groups_from_objects()
    check(sorted(created) == ["free", "left", "right"], "one group per object: %s" % created)
    uvgroups.set_overrides("left", padding=16, rotation=0)
    g = [x for x in uvgroups.list_groups() if x["name"] == "left"][0]
    check(g["padding"] == 16 and g["rotation"] == 0, "per-group overrides stored")
    cmds.scale(3, 3, 3, "free")  # bigger in 3D: independent boxes give it less density
    uvgroups.set_weight("right", 2.0)
    packer.pack_groups(packer.PackOptions(td_policy=packer.TD_UNIFORM))
    dens = {x["name"]: uvislands.faces_density(x["faces"], 1024) / x["weight"]
            for x in uvgroups.list_groups()}
    check(max(dens.values()) / min(dens.values()) < 1.02,
          "uniform texel density across groups (weight 2 counted): %s"
          % {k: round(v, 1) for k, v in dens.items()})

    print("heuristic search:")
    result, tries = packer.search(packer.PackOptions(search_time=1.0, precision=1))
    check(result and len(tries) > 1, "search tried %d settings: %s"
          % (len(tries), [round(t, 4) for t in tries]))
    covered = packer._coverage(packer.PackOptions())
    check(covered >= max(tries) * 0.99, "final pack is as good as the best try")


def test_stacking():
    from uv_group_packer import align

    print("stack with priority:")
    cmds.file(new=True, force=True)
    template = cmds.polyPlane(name="tpl", width=3, height=3, subdivisionsX=3, subdivisionsY=3)[0]
    cmds.delete(template + ".f[4:8]")
    cmds.polyEditUV(template + ".map[*]", pivotU=0.5, pivotV=0.5, scaleU=0.3, scaleV=0.3)
    copies = []
    for i, angle in enumerate((40, 150, 260)):
        node = cmds.duplicate(template, name="copy%d" % i)[0]
        cmds.polyEditUV(node + ".map[*]", pivotU=0.5, pivotV=0.5, angle=angle)
        cmds.polyEditUV(node + ".map[*]", uValue=1.2 + i, vValue=0.3, relative=True)
        copies.append(node)
    cmds.polyCube(name="other")
    uvgroups.set_priority(copies[1], 5)
    target_before = uv_coords(copies[1])
    cmds.select(copies + [template])
    result = align.stack(align.AlignOptions(mode=align.MODE_TOPOLOGY, threshold=0.01))
    check(result["stacks"] == 1 and result["moved"] == 3, "one stack of four: %s" % result)
    check(uv_coords(copies[1]) == target_before, "the priority island stays put")
    check(all(max_uv_gap(n, copies[1]) < 1e-5 for n in copies + [template]),
          "everything stacked onto the priority island")
    cmds.undo()

    print("select similar:")
    cmds.select(copies[0])
    added = align.select_similar(align.AlignOptions(threshold=0.1))
    selected = set(cmds.ls(cmds.ls(selection=True, objectsOnly=True), long=False))
    check(added == 3, "three similar islands found (%d)" % added)
    check("otherShape" not in selected and "other" not in selected, "the cube is not selected")

    print("match 3D axis:")
    cmds.file(new=True, force=True)
    rect = cmds.polyPlane(name="rect", width=4, height=1, subdivisionsX=4, subdivisionsY=1)[0]
    cmds.polyEditUV(rect + ".map[*]", pivotU=0, pivotV=0, scaleU=0.5, scaleV=0.125)
    turned = cmds.duplicate(rect, name="turned")[0]
    cmds.polyEditUV(turned + ".map[*]", pivotU=0.25, pivotV=0.0625, angle=180)
    cmds.polyEditUV(turned + ".map[*]", uValue=1.5, vValue=0.5, relative=True)
    cmds.select(turned)
    align.align_to_unselected(align.AlignOptions(threshold=0.05, match_axis="x"))
    check(max_uv_gap(turned, rect) < 1e-3,
          "symmetric island lands in the orientation matching 3D X (gap %.5f)"
          % max_uv_gap(turned, rect))


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

    test_align()
    test_packing_extras()
    test_stacking()

    print("ALL PASSED")


if __name__ == "__main__":
    try:
        main()
    finally:
        maya.standalone.uninitialize()
