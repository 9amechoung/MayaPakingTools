"""Aligning and stacking similar UV islands, mirroring UVPackmaster.

  Align To Unselected  every selected island goes onto the most similar
                       unselected island.
  Stack                similar islands among the selection are put on top of
                       each other, onto the one with the highest priority.
  Select Similar       selects the unselected islands similar to the selection.

Similarity modes:
  MODE_BORDER    compare outlines; works for islands with different topology.
  MODE_VERTEX    compare vertex positions of islands with the same number of
                 UVs (e.g. duplicated meshes); Correct Vertices then snaps
                 every UV exactly onto the target.
  MODE_TOPOLOGY  like MODE_VERTEX, but the face structure must match too.

Align priority (groups.set_priority) picks the target: islands go onto the
similar island with the highest priority; without priorities the biggest
island wins. Match 3D Axis keeps a world axis pointing the same way on stacked
islands, which matters for symmetric islands that fit in several orientations.
"""
import maya.cmds as cmds

from . import align_math
from . import groups as uvgroups
from . import islands as uvislands

MODE_BORDER = "border"
MODE_VERTEX = "vertex"
MODE_TOPOLOGY = "topology"

AXES = {"x": 0, "y": 1, "z": 2}


class AlignOptions(object):
    def __init__(self, mode=MODE_BORDER, threshold=0.1, allow_flip=False, adjust_scale=False,
                 correct_vertices=True, samples=64, match_axis=None):
        self.mode = mode
        # Largest accepted difference, relative to the target's size.
        self.threshold = max(0.0, float(threshold))
        self.allow_flip = bool(allow_flip)
        self.adjust_scale = bool(adjust_scale)
        self.correct_vertices = bool(correct_vertices)
        self.samples = max(8, int(samples))
        # None, or "x" / "y" / "z": keep this world axis consistent (Match 3D Axis).
        self.match_axis = match_axis


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
    _tag_priorities(selected + others)
    return selected, others


def _tag_priorities(island_list):
    lookup = uvislands.tag_lookup(uvgroups.priorities())
    for island in island_list:
        island["priority"] = uvislands.island_tag(island, lookup, default=0) if lookup else 0


def _border(island, options):
    if "_border" not in island:
        edges = align_math.border_edges(island["loops"])
        points = align_math.sample_border(island["coords"], edges, options.samples)
        island["_border"] = points
        island["_radius"] = align_math.radius(points)
        island["_elongation"] = align_math.elongation(points)
    return island["_border"]


def _axis(island, options):
    if not options.match_axis:
        return None
    if "_axis" not in island:
        island["_axis"] = uvislands.axis_direction(island, AXES[options.match_axis])
    return island["_axis"]


def _vertices(island):
    return [island["coords"][i] for i in island["uv_ids"]]


def _same_counts(a, b):
    return len(a["uv_ids"]) == len(b["uv_ids"]) and len(a["face_ids"]) == len(b["face_ids"])


def _comparable(a, b, options):
    if options.mode == MODE_VERTEX:
        return _same_counts(a, b)
    if options.mode == MODE_TOPOLOGY:
        return (_same_counts(a, b) and align_math.topology_signature(a["loops"])
                == align_math.topology_signature(b["loops"]))
    return True


def compare(island, target, options):
    """The match putting island onto target, or None when they can't be compared."""
    if not _comparable(island, target, options):
        return None
    if options.mode in (MODE_VERTEX, MODE_TOPOLOGY):
        return align_math.procrustes(_vertices(island), _vertices(target),
                                     options.allow_flip, options.adjust_scale)
    src, dst = _border(island, options), _border(target, options)
    # Cheap rejections before the rotation search.
    if abs(island["_elongation"] - target["_elongation"]) > 0.25:
        return None
    ratio = island["_radius"] / target["_radius"] if target["_radius"] > 0 else 0
    if not options.adjust_scale and not 0.7 < ratio < 1.4:
        return None
    return align_math.match_shapes(src, dst, options.allow_flip, options.adjust_scale,
                                   axis_src=_axis(island, options), axis_dst=_axis(target, options))


def find_match(island, templates, options):
    """The target for island: among similar templates, the highest priority, then the
    closest. Returns (template, match), or (None, closest match or None)."""
    closest, hits = None, []
    for template in templates:
        m = compare(island, template, options)
        if m is None:
            continue
        if closest is None or m["error"] < closest["error"]:
            closest = m
        if m["error"] <= options.threshold:
            hits.append((template.get("priority", 0), -m["error"], id(template), template, m))
    if not hits:
        return None, closest
    _, _, _, template, m = max(hits, key=lambda h: h[:3])
    return template, m


def _put_on(island, template, m, options):
    uvislands.apply_match(island["uvs"], m)
    if (options.mode in (MODE_VERTEX, MODE_TOPOLOGY) and options.correct_vertices
            and _same_counts(island, template)):
        # Move every UV exactly onto its counterpart.
        for src_id, dst_id in zip(island["uv_ids"], template["uv_ids"]):
            u, v = template["coords"][dst_id]
            cmds.polyEditUV("%s.map[%d]" % (island["node"], src_id), uValue=u, vValue=v,
                            relative=False)


class _Chunk(object):
    """Undo chunk that also restores the selection, so one undo reverts everything."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.selection = cmds.ls(selection=True)
        cmds.undoInfo(openChunk=True, chunkName=self.name)

    def __exit__(self, *exc):
        if self.selection:
            cmds.select(self.selection, replace=True)
        else:
            cmds.select(clear=True)
        cmds.undoInfo(closeChunk=True)


def align_to_unselected(options=None, selection=None):
    """Align every selected island to its most similar unselected island.

    Returns {"aligned": n, "skipped": n, "errors": [closest error per skipped island]}.
    """
    options = options or AlignOptions()
    selected, templates = split_islands(selection)
    result = {"aligned": 0, "skipped": 0, "errors": []}
    if not selected or not templates:
        result["skipped"] = len(selected)
        return result
    with _Chunk("UVGroupPacker Align"):
        for island in selected:
            template, m = find_match(island, templates, options)
            if template is None:
                result["skipped"] += 1
                result["errors"].append(m["error"] if m else None)
                continue
            _put_on(island, template, m, options)
            result["aligned"] += 1
    return result


def stack(options=None, selection=None):
    """Put similar islands among the selection on top of each other.

    Each stack goes onto its highest-priority island (the biggest one when
    priorities tie). Returns {"stacks": n, "moved": n, "single": n}.
    """
    options = options or AlignOptions()
    island_list = split_islands(selection)[0]
    order = sorted(island_list, key=lambda i: (-i["priority"], -i["area3d"]))
    result = {"stacks": 0, "moved": 0, "single": 0}
    done = set()
    with _Chunk("UVGroupPacker Stack"):
        for target in order:
            if id(target) in done:
                continue
            done.add(id(target))
            moved = 0
            for island in order:
                if id(island) in done:
                    continue
                m = compare(island, target, options)
                if m is not None and m["error"] <= options.threshold:
                    _put_on(island, target, m, options)
                    done.add(id(island))
                    moved += 1
            if moved:
                result["stacks"] += 1
                result["moved"] += moved
            else:
                result["single"] += 1
    return result


def select_similar(options=None, selection=None):
    """Add the unselected islands that are similar to a selected one to the selection.

    Returns the number of islands added.
    """
    options = options or AlignOptions()
    references, candidates = split_islands(selection)
    found = []
    for island in candidates:
        for ref in references:
            m = compare(island, ref, options)
            if m is not None and m["error"] <= options.threshold:
                found.append(island)
                break
    if found:
        cmds.select([f for i in found for f in i["faces"]], add=True)
    return len(found)
