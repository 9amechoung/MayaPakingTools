"""UV groups stored in the scene as Maya objectSets of faces.

Each group is an objectSet named UVGP_<name> with extra attributes for its
display colour, packing weight, order and lock state. All group sets live
under the root set UVGP_groups so they are easy to find in the Outliner.
A face belongs to at most one group: assigning faces to a group removes them
from the others.
"""
import re

import maya.cmds as cmds

ROOT_SET = "UVGP_groups"
PREFIX = "UVGP_"

DEFAULT_COLORS = [
    (0.10, 0.20, 1.00),  # blue
    (1.00, 0.90, 0.10),  # yellow
    (0.10, 0.90, 1.00),  # cyan
    (0.20, 0.90, 0.20),  # green
    (1.00, 0.50, 0.10),  # orange
    (0.90, 0.20, 0.90),  # magenta
    (1.00, 0.20, 0.20),  # red
    (0.60, 0.40, 1.00),  # purple
]


def _sanitize(name):
    name = re.sub(r"[^0-9A-Za-z_]", "_", name.strip()) or "group"
    if name[0].isdigit():
        name = "_" + name
    return name


def _set_name(name):
    return PREFIX + name


def _ensure_root():
    if not cmds.objExists(ROOT_SET):
        cmds.sets(name=ROOT_SET, empty=True)
    return ROOT_SET


def to_faces(items):
    """Convert any selection (objects, faces, UVs, edges...) to face components."""
    if not items:
        return []
    faces = cmds.polyListComponentConversion(items, toFace=True) or []
    return [f for f in faces if ".f[" in f]


_RANGE = re.compile(r"\.f\[(\d+)(?::(\d+))?\]$")


def count_faces(faces):
    """Count faces in compact component strings like 'mesh.f[0:99]' without flattening."""
    total = 0
    for f in faces:
        m = _RANGE.search(f)
        if m:
            total += int(m.group(2) or m.group(1)) - int(m.group(1)) + 1
    return total


def selected_faces():
    return to_faces(cmds.ls(selection=True))


def list_groups():
    """Return group dicts in creation order: name, set, color, weight, locked, faces."""
    if not cmds.objExists(ROOT_SET):
        return []
    groups = []
    for s in cmds.sets(ROOT_SET, query=True) or []:
        if not s.startswith(PREFIX) or not cmds.attributeQuery("uvgpColor", node=s, exists=True):
            continue
        faces = cmds.sets(s, query=True) or []
        groups.append({
            "name": s[len(PREFIX):],
            "set": s,
            "color": tuple(cmds.getAttr(s + ".uvgpColor")[0]),
            "weight": cmds.getAttr(s + ".uvgpWeight"),
            "locked": cmds.getAttr(s + ".uvgpLocked"),
            "order": cmds.getAttr(s + ".uvgpOrder"),
            "faces": faces,
            "count": count_faces(faces),
        })
    groups.sort(key=lambda g: g["order"])
    return groups


def create_group(name, color=None, faces=None):
    """Create a group (or reuse an existing one) and optionally assign faces."""
    name = _sanitize(name)
    set_name = _set_name(name)
    if not cmds.objExists(set_name):
        existing = list_groups()
        if color is None:
            color = DEFAULT_COLORS[len(existing) % len(DEFAULT_COLORS)]
        order = max([g["order"] for g in existing] or [-1]) + 1
        set_name = cmds.sets(name=set_name, empty=True)
        cmds.addAttr(set_name, longName="uvgpColor", attributeType="float3", usedAsColor=True)
        for axis in "RGB":
            cmds.addAttr(set_name, longName="uvgpColor" + axis, attributeType="float",
                         parent="uvgpColor")
        cmds.addAttr(set_name, longName="uvgpWeight", attributeType="double",
                     defaultValue=1.0, minValue=0.0)
        cmds.addAttr(set_name, longName="uvgpOrder", attributeType="long", defaultValue=order)
        cmds.addAttr(set_name, longName="uvgpLocked", attributeType="bool", defaultValue=False)
        cmds.setAttr(set_name + ".uvgpColor", *color, type="float3")
        cmds.sets(set_name, addElement=_ensure_root())
    if faces:
        assign(name, faces)
    return name


def assign(name, items):
    """Move the given faces (any component/object selection) into a group."""
    faces = to_faces(items)
    if not faces:
        return 0
    target = _set_name(name)
    for g in list_groups():
        if g["set"] != target and g["faces"]:
            cmds.sets(faces, remove=g["set"])
    cmds.sets(faces, addElement=target)
    return count_faces(faces)


def unassign(items):
    faces = to_faces(items)
    if not faces:
        return
    for g in list_groups():
        if g["faces"]:
            cmds.sets(faces, remove=g["set"])


def delete_group(name):
    set_name = _set_name(name)
    if cmds.objExists(set_name):
        cmds.delete(set_name)


def rename_group(old, new):
    new = _sanitize(new)
    if new == old or cmds.objExists(_set_name(new)):
        return old
    cmds.rename(_set_name(old), _set_name(new))
    return new


def set_color(name, color):
    cmds.setAttr(_set_name(name) + ".uvgpColor", *color, type="float3")


def set_weight(name, weight):
    cmds.setAttr(_set_name(name) + ".uvgpWeight", max(0.0, float(weight)))


def set_locked(name, locked):
    cmds.setAttr(_set_name(name) + ".uvgpLocked", bool(locked))


def group_faces(name):
    set_name = _set_name(name)
    if not cmds.objExists(set_name):
        return []
    return cmds.sets(set_name, query=True) or []


def select_group(name, add=False):
    faces = group_faces(name)
    if faces:
        cmds.select(faces, add=add)
    elif not add:
        cmds.select(clear=True)


def _meshes_from(items):
    shapes = cmds.ls([i.split(".")[0] for i in items], dag=True, type="mesh",
                     long=True, noIntermediate=True) or []
    return set(shapes)


def groups_from_materials(items=None):
    """Create one group per material, using the selection or every mesh in the scene."""
    items = items or cmds.ls(selection=True) or cmds.ls(type="mesh", noIntermediate=True)
    meshes = _meshes_from(items)
    if not meshes:
        return []

    engines = sorted(set(cmds.listConnections(list(meshes), type="shadingEngine") or []))
    created = []
    for engine in engines:
        members = cmds.sets(engine, query=True) or []
        # Keep only members that belong to the meshes we were asked about.
        members = [m for m in members if _meshes_from([m]) & meshes]
        faces = to_faces(members)
        if not faces:
            continue
        material = (cmds.listConnections(engine + ".surfaceShader") or [engine])[0]
        created.append(create_group(material, color=_material_color(material), faces=faces))
    return created


def _material_color(material):
    """The material's base colour, so groups match the viewport.

    None for unknown or grey materials (default lambert grey would make every
    group look the same), which falls back to the default palette.
    """
    for attr in ("baseColor", "color"):
        if cmds.attributeQuery(attr, node=material, exists=True):
            value = cmds.getAttr(material + "." + attr)
            if value and isinstance(value[0], tuple) and len(value[0]) == 3:
                if max(value[0]) - min(value[0]) < 0.05:
                    return None
                return value[0]
    return None
