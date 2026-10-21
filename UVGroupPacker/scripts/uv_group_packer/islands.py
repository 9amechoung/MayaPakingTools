"""UV islands (shells) and texel density.

Shells, areas and UV bounds are read with OpenMaya so large meshes stay fast;
UVs are only edited through maya.cmds so every change is undoable.
Texel density is in texture pixels per scene unit (px/cm with Maya's default
units) for a texture of the given resolution.
"""
import math

import maya.api.OpenMaya as om
import maya.cmds as cmds


def _ranges(indices):
    """Turn sorted ints into (start, end) runs: [0, 1, 2, 5] -> [(0, 2), (5, 5)]."""
    runs = []
    for i in sorted(indices):
        if runs and i == runs[-1][1] + 1:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    return runs


def _components(node, kind, indices):
    return ["%s.%s[%d:%d]" % (node, kind, a, b) if a != b else "%s.%s[%d]" % (node, kind, a)
            for a, b in _ranges(indices)]


def _faces_by_mesh(faces):
    """{mesh dag path: [dag, set of face ids]} for face components or whole objects."""
    sel = om.MSelectionList()
    for item in faces:
        try:
            sel.add(item)
        except RuntimeError:
            continue
    meshes = {}
    for i in range(sel.length()):
        dag, comp = sel.getComponent(i)
        try:
            dag.extendToShape()
        except RuntimeError:
            continue
        if not dag.hasFn(om.MFn.kMesh):
            continue
        entry = meshes.setdefault(dag.fullPathName(), [dag, set()])
        if comp.isNull():
            entry[1].update(range(om.MFnMesh(dag).numPolygons))
        elif comp.apiType() == om.MFn.kMeshPolygonComponent:
            entry[1].update(om.MFnSingleIndexedComponent(comp).getElements())
    return meshes


def shells(faces, weight=1.0):
    """Split faces into UV islands.

    Returns a list of dicts:
      faces, uvs   compact component strings for maya.cmds
      node         mesh shape name used in those strings
      face_ids, uv_ids, loops (per-face UV ids), coords ({uv id: (u, v)})
      area3d       world surface area
      area_uv      UV area
      bbox         (u_min, v_min, u_max, v_max)
      weight       texel density multiplier carried along for packing
    Faces without UVs are skipped.
    """
    result = []
    for path, (dag, face_ids) in _faces_by_mesh(faces).items():
        mesh = om.MFnMesh(dag)
        if not mesh.numUVs():
            continue
        _, shell_ids = mesh.getUvShellsIds()
        us, vs = mesh.getUVs()
        node = dag.partialPathName()
        it = om.MItMeshPolygon(dag)
        islands = {}
        for face in sorted(face_ids):
            it.setIndex(face)
            if not it.hasUVs():
                continue
            uv_ids = [it.getUVIndex(k) for k in range(it.polygonVertexCount())]
            data = islands.setdefault(shell_ids[uv_ids[0]],
                                      {"faces": [], "uvs": set(), "loops": [],
                                       "area3d": 0.0, "area_uv": 0.0})
            data["faces"].append(face)
            data["uvs"].update(uv_ids)
            data["loops"].append(uv_ids)
            data["area3d"] += it.getArea(om.MSpace.kWorld)
            data["area_uv"] += it.getUVArea()
        for data in islands.values():
            uv_ids = sorted(data["uvs"])
            iu = [us[i] for i in uv_ids]
            iv = [vs[i] for i in uv_ids]
            result.append({
                "mesh": path,
                "node": node,
                "face_ids": data["faces"],
                "uv_ids": uv_ids,
                "loops": data["loops"],
                "coords": dict(zip(uv_ids, zip(iu, iv))),
                "faces": _components(node, "f", data["faces"]),
                "uvs": _components(node, "map", data["uvs"]),
                "area3d": data["area3d"],
                "area_uv": data["area_uv"],
                "bbox": (min(iu), min(iv), max(iu), max(iv)),
                "weight": weight,
            })
    return result


def texel_density(area3d, area_uv, resolution):
    """Pixels per scene unit for a texture of this resolution."""
    if area3d <= 0:
        return 0.0
    return math.sqrt(max(area_uv, 0.0) / area3d) * resolution


def average_density(island_list, resolution):
    """Area-weighted texel density of several islands."""
    return texel_density(sum(i["area3d"] for i in island_list),
                         sum(i["area_uv"] for i in island_list), resolution)


def faces_density(faces, resolution):
    return average_density(shells(faces), resolution)


def scale_island(island, factor):
    """Uniformly scale an island around its bbox centre and keep its dict in sync."""
    if abs(factor - 1.0) < 1e-9:
        return
    u0, v0, u1, v1 = island["bbox"]
    cu, cv = (u0 + u1) * 0.5, (v0 + v1) * 0.5
    cmds.polyEditUV(island["uvs"], pivotU=cu, pivotV=cv, scaleU=factor, scaleV=factor)
    island["bbox"] = (cu + (u0 - cu) * factor, cv + (v0 - cv) * factor,
                      cu + (u1 - cu) * factor, cv + (v1 - cv) * factor)
    island["area_uv"] *= factor * factor


def set_density(island_list, density, resolution, use_weight=True):
    """Scale every island so its texel density is density (times its weight)."""
    for island in island_list:
        current = texel_density(island["area3d"], island["area_uv"], resolution)
        if current <= 0:
            continue
        target = density * (island["weight"] if use_weight else 1.0)
        scale_island(island, target / current)
    return len(island_list)


def apply_match(uvs, m):
    """Apply an align_math match (similarity transform) with undoable polyEditUV calls."""
    cu, cv = m["src_c"]
    s = m["scale"]
    if m["flip"] or abs(s - 1.0) > 1e-9:
        cmds.polyEditUV(uvs, pivotU=cu, pivotV=cv, scaleU=-s if m["flip"] else s, scaleV=s)
    if abs(m["angle"]) > 1e-9:
        cmds.polyEditUV(uvs, pivotU=cu, pivotV=cv, angle=m["angle"])
    cmds.polyEditUV(uvs, uValue=m["dst_c"][0] - cu, vValue=m["dst_c"][1] - cv, relative=True)


def overlap_shape(island):
    """The island as a layout.shapes_overlap shape (bbox, points, triangles, border edges)."""
    from . import align_math, layout
    coords = island["coords"]
    return {"bbox": island["bbox"], "points": list(coords.values()),
            "tris": layout.fan_triangles(island["loops"], coords),
            "edges": [(coords[a], coords[b]) for a, b in align_math.border_edges(island["loops"])]}


def axis_direction(island, axis):
    """UV direction in which world X/Y/Z (axis 0/1/2) grows across the island, or None.

    Used by Match 3D Axis to keep stacked islands oriented the same way in 3D.
    """
    sel = om.MSelectionList()
    sel.add(island["mesh"])
    it = om.MItMeshPolygon(sel.getDagPath(0))
    gx = gy = 0.0
    for face in island["face_ids"]:
        it.setIndex(face)
        points = it.getPoints(om.MSpace.kWorld)
        us, vs = it.getUVs()
        for k in range(1, len(points) - 1):
            du1, dv1 = us[k] - us[0], vs[k] - vs[0]
            du2, dv2 = us[k + 1] - us[0], vs[k + 1] - vs[0]
            det = du1 * dv2 - du2 * dv1
            if abs(det) < 1e-14:
                continue
            d1 = points[k][axis] - points[0][axis]
            d2 = points[k + 1][axis] - points[0][axis]
            # Gradient of the world coordinate over UV, weighted by UV area.
            weight = abs(det) / det
            gx += (d1 * dv2 - d2 * dv1) * weight
            gy += (du1 * d2 - du2 * d1) * weight
    length = math.hypot(gx, gy)
    return (gx / length, gy / length) if length > 1e-12 else None


def tag_lookup(tag_faces):
    """{tag: {mesh path: face ids}} from {tag: face components} (lock groups, priorities)."""
    return {tag: {path: ids for path, (_, ids) in _faces_by_mesh(faces).items()}
            for tag, faces in tag_faces.items()}


def island_tag(island, lookup, default=None):
    """The highest tag whose faces touch the island, or default."""
    faces = set(island["face_ids"])
    hits = [tag for tag, meshes in lookup.items() if faces & meshes.get(island["mesh"], set())]
    return max(hits) if hits else default
