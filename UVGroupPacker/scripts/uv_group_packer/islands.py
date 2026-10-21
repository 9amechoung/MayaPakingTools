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
