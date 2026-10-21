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
