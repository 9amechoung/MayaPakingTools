"""Show each group's colour on the model in the 3D viewport (UVPackmaster's
"show group assignment").

The colours go into a separate colour set, UVGP_groups, so the mesh's own
colour sets are untouched. Everything the display adds is recorded on the
UVGP_groups root set and removed again by hide(): on meshes with construction
history that means deleting exactly the createColorSet/polyColorPerVertex
nodes it made, which Maya rewires around, so later edits (like a pack) stay.
Showing and hiding are kept out of the undo queue.
"""
import json

import maya.cmds as cmds

from . import groups as uvgroups

COLOR_SET = "UVGP_groups"
RECORD_ATTR = "uvgpDisplay"
UNGROUPED = (0.35, 0.35, 0.35)


def _record():
    if not cmds.objExists(uvgroups.ROOT_SET) or \
            not cmds.attributeQuery(RECORD_ATTR, node=uvgroups.ROOT_SET, exists=True):
        return None
    text = cmds.getAttr(uvgroups.ROOT_SET + "." + RECORD_ATTR)
    return json.loads(text) if text else None


def _save_record(record):
    root = uvgroups._ensure_root()
    if not cmds.attributeQuery(RECORD_ATTR, node=root, exists=True):
        cmds.addAttr(root, longName=RECORD_ATTR, dataType="string")
    cmds.setAttr(root + "." + RECORD_ATTR, json.dumps(record) if record else "", type="string")


def is_shown():
    return bool(_record())


class _NoUndo(object):
    def __enter__(self):
        self.state = cmds.undoInfo(query=True, stateWithoutFlush=True)
        cmds.undoInfo(stateWithoutFlush=False)

    def __exit__(self, *exc):
        cmds.undoInfo(stateWithoutFlush=self.state)


def _shape(node):
    if cmds.nodeType(node) == "mesh":
        return cmds.ls(node, long=True)[0]
    return (cmds.listRelatives(node, shapes=True, noIntermediate=True, fullPath=True) or [node])[0]


def show():
    """Paint every group's faces in its colour (other faces of those meshes grey)."""
    group_list = [g for g in uvgroups.list_groups() if g["faces"]]
    with _NoUndo():
        _hide()
        if not group_list:
            return 0
        by_mesh = {}
        for g in group_list:
            for face in g["faces"]:
                by_mesh.setdefault(_shape(face.split(".")[0]), []).append((g["color"], face))
        record = {"meshes": []}
        for shape, painted in by_mesh.items():
            before = set(cmds.listHistory(shape) or [])
            entry = {"shape": shape,
                     "current": (cmds.polyColorSet(shape, query=True, currentColorSet=True)
                                 or [None])[0],
                     "display": cmds.getAttr(shape + ".displayColors")}
            cmds.polyColorSet(shape, create=True, colorSet=COLOR_SET, representation="RGB")
            cmds.polyColorSet(shape, currentColorSet=True, colorSet=COLOR_SET)
            cmds.polyColorPerVertex(shape + ".f[*]", rgb=UNGROUPED, colorDisplayOption=True)
            for color, face in painted:
                cmds.polyColorPerVertex(face, rgb=tuple(color), colorDisplayOption=True)
            entry["nodes"] = sorted(set(cmds.listHistory(shape) or []) - before)
            record["meshes"].append(entry)
        _save_record(record)
    return len(group_list)


def _hide():
    record = _record()
    if not record:
        return
    for entry in record["meshes"]:
        shape = entry["shape"]
        if not cmds.objExists(shape):
            continue
        nodes = [n for n in entry["nodes"] if cmds.objExists(n)]
        if nodes:
            cmds.delete(nodes)
        if COLOR_SET in (cmds.polyColorSet(shape, query=True, allColorSets=True) or []):
            cmds.polyColorSet(shape, delete=True, colorSet=COLOR_SET)
        if entry["current"]:
            cmds.polyColorSet(shape, currentColorSet=True, colorSet=entry["current"])
        cmds.setAttr(shape + ".displayColors", entry["display"])
    _save_record(None)


def hide():
    """Remove the group colours and everything showing them added."""
    with _NoUndo():
        _hide()


def refresh():
    """Repaint after groups changed, if the colours are on."""
    if is_shown():
        show()
