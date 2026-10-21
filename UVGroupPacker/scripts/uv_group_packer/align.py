"""Align To Unselected: put selected UV islands on top of similar unselected ones.

Mirrors UVPackmaster 4's aligning mode of the same name. For every selected
island the most similar unselected island in the scene is found and the
selected island is moved, rotated (and optionally mirrored and scaled) onto it,
so repeated parts end up stacked on one template.

Similarity modes:
  MODE_BORDER  compare outlines; works for islands with different topology.
  MODE_VERTEX  islands with the same topology (e.g. duplicated meshes) are
               matched vertex by vertex; Correct Vertices then snaps every
               UV exactly onto the template.
"""
import maya.cmds as cmds

from . import align_math
from . import groups as uvgroups
from . import islands as uvislands

MODE_BORDER = "border"
MODE_VERTEX = "vertex"


class AlignOptions(object):
    def __init__(self, mode=MODE_BORDER, threshold=0.1, allow_flip=False, adjust_scale=False,
                 correct_vertices=True, samples=64):
        self.mode = mode
        # Largest accepted difference, relative to the template's size.
        self.threshold = max(0.0, float(threshold))
        self.allow_flip = bool(allow_flip)
        self.adjust_scale = bool(adjust_scale)
        self.correct_vertices = bool(correct_vertices)
        self.samples = max(8, int(samples))


def split_islands(selection=None):
    """(selected islands, unselected islands) over every mesh in the scene.

    An island counts as selected when any of its faces is selected.
    """
    faces = uvgroups.to_faces(selection if selection is not None else cmds.ls(selection=True))
    picked = {path: ids for path, (_, ids) in uvislands._faces_by_mesh(faces).items()}
    selected, others = [], []
    for island in uvislands.shells(cmds.ls(type="mesh", noIntermediate=True) or []):
        ids = picked.get(island["mesh"])
        (selected if ids and ids.intersection(island["face_ids"]) else others).append(island)
    return selected, others


def _border(island, options):
    if "_border" not in island:
        edges = align_math.border_edges(island["loops"])
        points = align_math.sample_border(island["coords"], edges, options.samples)
        island["_border"] = points
        island["_radius"] = align_math.radius(points)
        island["_elongation"] = align_math.elongation(points)
    return island["_border"]


def _vertices(island):
    return [island["coords"][i] for i in island["uv_ids"]]


def _same_topology(a, b):
    return len(a["uv_ids"]) == len(b["uv_ids"]) and len(a["face_ids"]) == len(b["face_ids"])


def find_match(island, templates, options):
    """Return (template, match) with the lowest error, or (None, best match)."""
    best_template, best = None, None
    if options.mode == MODE_VERTEX:
        for template in templates:
            if not _same_topology(island, template):
                continue
            m = align_math.procrustes(_vertices(island), _vertices(template),
                                      options.allow_flip, options.adjust_scale)
            if best is None or m["error"] < best["error"]:
                best_template, best = template, m
    else:
        src = _border(island, options)
        for template in templates:
            dst = _border(template, options)
            # Cheap rejections before the rotation search.
            if abs(island["_elongation"] - template["_elongation"]) > 0.25:
                continue
            ratio = island["_radius"] / template["_radius"] if template["_radius"] > 0 else 0
            if not options.adjust_scale and not 0.7 < ratio < 1.4:
                continue
            m = align_math.match_shapes(src, dst, options.allow_flip, options.adjust_scale)
            if best is None or m["error"] < best["error"]:
                best_template, best = template, m
    if best is None or best["error"] > options.threshold:
        return None, best
    return best_template, best


def _transform(island, m):
    """Apply a match to the island's UVs with undoable polyEditUV calls."""
    uvs = island["uvs"]
    cu, cv = m["src_c"]
    s = m["scale"]
    if m["flip"] or abs(s - 1.0) > 1e-9:
        cmds.polyEditUV(uvs, pivotU=cu, pivotV=cv, scaleU=-s if m["flip"] else s, scaleV=s)
    if abs(m["angle"]) > 1e-9:
        cmds.polyEditUV(uvs, pivotU=cu, pivotV=cv, angle=m["angle"])
    cmds.polyEditUV(uvs, uValue=m["dst_c"][0] - cu, vValue=m["dst_c"][1] - cv, relative=True)


def _snap(island, template):
    """Move every UV exactly onto its counterpart (same topology only)."""
    for src_id, dst_id in zip(island["uv_ids"], template["uv_ids"]):
        u, v = template["coords"][dst_id]
        cmds.polyEditUV("%s.map[%d]" % (island["node"], src_id), uValue=u, vValue=v,
                        relative=False)


def align_to_unselected(options=None, selection=None):
    """Align every selected island to its most similar unselected island.

    Returns {"aligned": n, "skipped": n, "errors": [best error per skipped island]}.
    """
    options = options or AlignOptions()
    selected, templates = split_islands(selection)
    result = {"aligned": 0, "skipped": 0, "errors": []}
    if not selected or not templates:
        result["skipped"] = len(selected)
        return result

    current = cmds.ls(selection=True)
    cmds.undoInfo(openChunk=True, chunkName="UVGroupPacker Align")
    try:
        for island in selected:
            template, m = find_match(island, templates, options)
            if template is None:
                result["skipped"] += 1
                result["errors"].append(m["error"] if m else None)
                continue
            _transform(island, m)
            if (options.mode == MODE_VERTEX and options.correct_vertices
                    and _same_topology(island, template)):
                _snap(island, template)
            result["aligned"] += 1
    finally:
        if current:
            cmds.select(current, replace=True)
        else:
            cmds.select(clear=True)
        cmds.undoInfo(closeChunk=True)
    return result
