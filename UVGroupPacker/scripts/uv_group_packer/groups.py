"""UV groups stored in the scene as Maya objectSets of faces.

Each group is an objectSet named UVGP_<name> with extra attributes for its
display colour, packing weight, order, lock state and an optional custom
box (the region it always packs into, like UVPackmaster's group target boxes). All group sets live
under the root set UVGP_groups so they are easy to find in the Outliner.
A face belongs to at most one group: assigning faces to a group removes them
from the others.
"""
import re

import maya.cmds as cmds

ROOT_SET = "UVGP_groups"
PREFIX = "UVGP_"
BOX_ATTRS = ("uvgpBoxU0", "uvgpBoxV0", "uvgpBoxU1", "uvgpBoxV1")

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
    """Return group dicts in creation order: name, set, color, weight, locked, faces, box."""
    if not cmds.objExists(ROOT_SET):
        return []
    groups = []
    for s in cmds.sets(ROOT_SET, query=True) or []:
        if not s.startswith(PREFIX) or not cmds.attributeQuery("uvgpColor", node=s, exists=True):
            continue
        faces = cmds.sets(s, query=True) or []
        _ensure_box_attrs(s)
        box = None
        if cmds.getAttr(s + ".uvgpUseBox"):
            box = tuple(cmds.getAttr(s + "." + a) for a in BOX_ATTRS)
        padding = cmds.getAttr(s + ".uvgpPadding")
        rotation = cmds.getAttr(s + ".uvgpRotation")
        groups.append({
            "box": box,
            # Per-group overrides of the global packing options (None = global).
            "padding": padding if padding >= 0 else None,
            "rotation": rotation if rotation >= 0 else None,
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
        _ensure_box_attrs(set_name)
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


def _ensure_box_attrs(set_name):
    """Add attributes newer than v0.1: custom box and per-group option overrides."""
    if not cmds.attributeQuery("uvgpUseBox", node=set_name, exists=True):
        cmds.addAttr(set_name, longName="uvgpUseBox", attributeType="bool", defaultValue=False)
        for attr in BOX_ATTRS:
            cmds.addAttr(set_name, longName=attr, attributeType="double", defaultValue=0.0)
    if not cmds.attributeQuery("uvgpPadding", node=set_name, exists=True):
        cmds.addAttr(set_name, longName="uvgpPadding", attributeType="double", defaultValue=-1.0)
        cmds.addAttr(set_name, longName="uvgpRotation", attributeType="long", defaultValue=-1)


def set_overrides(name, padding=None, rotation=None):
    """Per-group shell padding (px) and rotation step (90/45/0); None uses the global option."""
    set_name = _set_name(name)
    _ensure_box_attrs(set_name)
    cmds.setAttr(set_name + ".uvgpPadding", -1.0 if padding is None else float(padding))
    cmds.setAttr(set_name + ".uvgpRotation", -1 if rotation is None else int(rotation))


def set_box(name, box):
    """Pin a group to a box (u_min, v_min, u_max, v_max), or None for automatic layout."""
    set_name = _set_name(name)
    _ensure_box_attrs(set_name)
    if box:
        u0, v0, u1, v1 = box
        box = (min(u0, u1), min(v0, v1), max(u0, u1), max(v0, v1))
        for attr, value in zip(BOX_ATTRS, box):
            cmds.setAttr(set_name + "." + attr, float(value))
    cmds.setAttr(set_name + ".uvgpUseBox", bool(box))


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


def groups_from_objects(items=None):
    """Create one group per mesh object, using the selection or every mesh in the scene."""
    items = items or cmds.ls(selection=True) or cmds.ls(type="mesh", noIntermediate=True)
    created = []
    for shape in sorted(_meshes_from(items)):
        transform = (cmds.listRelatives(shape, parent=True, fullPath=True) or [shape])[0]
        name = transform.split("|")[-1]
        created.append(create_group(name, faces=[transform]))
    return created


# ---------------------------------------------------------------- face tags
# Lock groups and align priorities are plain objectSets of faces, numbered and
# kept under their own root set. A face carries at most one tag of each kind.
LOCK_ROOT = "UVGP_lockGroups"
LOCK_PREFIX = "UVGPLock_"
PRIORITY_ROOT = "UVGP_priorities"
PRIORITY_PREFIX = "UVGPPrio_"


def _tag_sets(root, prefix):
    """{number: set name} of a tag kind."""
    if not cmds.objExists(root):
        return {}
    result = {}
    for s in cmds.sets(root, query=True) or []:
        if s.startswith(prefix) and s[len(prefix):].isdigit():
            result[int(s[len(prefix):])] = s
    return result


def _tag_faces(root, prefix):
    return {n: cmds.sets(s, query=True) or [] for n, s in _tag_sets(root, prefix).items()}


def _untag(root, prefix, faces):
    for s in _tag_sets(root, prefix).values():
        if cmds.sets(s, query=True):
            cmds.sets(faces, remove=s)
        if not cmds.sets(s, query=True):
            cmds.delete(s)


def _tag(root, prefix, number, faces):
    _untag(root, prefix, faces)
    if not cmds.objExists(root):
        cmds.sets(name=root, empty=True)
    set_name = prefix + str(number)
    if not cmds.objExists(set_name):
        set_name = cmds.sets(name=set_name, empty=True)
        cmds.sets(set_name, addElement=root)
    cmds.sets(faces, addElement=set_name)


def lock_groups():
    """{lock group number: faces}. Islands in one lock group keep their relative positions."""
    return _tag_faces(LOCK_ROOT, LOCK_PREFIX)


def new_lock_group(items):
    """Put the selected faces' islands in a new lock group; returns its number (0 = nothing)."""
    faces = to_faces(items)
    if not faces:
        return 0
    number = max(list(_tag_sets(LOCK_ROOT, LOCK_PREFIX)) or [0]) + 1
    _tag(LOCK_ROOT, LOCK_PREFIX, number, faces)
    return number


def clear_lock_group(items):
    faces = to_faces(items)
    if faces:
        _untag(LOCK_ROOT, LOCK_PREFIX, faces)


def priorities():
    """{priority: faces}. When stacking, islands go onto the highest-priority similar island."""
    return _tag_faces(PRIORITY_ROOT, PRIORITY_PREFIX)


def set_priority(items, value):
    """Give faces an align priority (0 clears it)."""
    faces = to_faces(items)
    if not faces:
        return
    if int(value) > 0:
        _tag(PRIORITY_ROOT, PRIORITY_PREFIX, int(value), faces)
    else:
        _untag(PRIORITY_ROOT, PRIORITY_PREFIX, faces)
