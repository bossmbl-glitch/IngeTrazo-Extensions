"""Native planar/box/cylinder/sphere/quad UV tools and strict OBJ round trips."""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path
from PySide6.QtGui import QVector3D
from core.texture import face_uv_axes, fit_uv_affine, affine_uv
from .common import xyz, bounds, finite, LIMIT


def texture(face):
    tex = face.attrs.get("texture")
    if not tex or not tex.get("path"):
        raise ValueError("請先為所有選取的面套用圖片材質，或按「套用檢查格」。")
    return tex


def uv_at(face, points, tex=None):
    gu, cu, gv, cv = face_uv_axes(tex or texture(face), face.normal())
    return [(QVector3D.dotProduct(gu, p) + cu,
             QVector3D.dotProduct(gv, p) + cv) for p in points]


def set_uv(face, points, uv):
    finite(c for pair in uv for c in pair)
    mapping = fit_uv_affine(points, uv)
    if mapping is None:
        raise ValueError("面積過小或頂點共線，無法建立 UV。")
    # Polygon UVs must really be affine. IngeTrazo renders an affine map per face.
    fitted = affine_uv(mapping, points)
    if any(abs(a - b) > 2e-4 for x, y in zip(fitted, uv) for a, b in zip(x, y)):
        raise ValueError("這個面的 UV 需要三角化；請先按「三角化選取面」。")
    face.attrs = {**face.attrs, "texture": {**texture(face), "uvw": mapping}}


def replace_faces(target, records):
    """Records are (positions, attrs, interior); preserve existing boundary edges."""
    mesh = target.mesh
    old_edges = {e for f in target.faces for lp in (f.loop, *f.hole_loops)
                 for v in lp for e in v.edges if f in e.faces}
    for face in list(target.faces):
        mesh.remove_face(face)
    new_faces = []
    for points, attrs, interior in records:
        new = mesh.add_face(points)
        new.attrs = copy.deepcopy(attrs)
        new.interior = interior
        new_faces.append(new)
    for edge in old_edges:
        if not edge.faces:
            mesh.remove_edge(edge)
    target.faces = new_faces
    target.vertices = {v for f in new_faces for v in f.loop}


def triangulate(targets):
    plans = []
    for target in targets:
        records = [(list(tri), face.attrs, face.interior)
                   for face in target.faces for tri in face.triangulate()]
        if len(records) > LIMIT:
            raise ValueError("三角化後超過 150,000 面，請縮小選取範圍。")
        plans.append((target, records))
    for target, records in plans:
        replace_faces(target, records)


def assign_texture(targets, path):
    from PySide6.QtGui import QImage
    if QImage(str(path)).isNull():
        raise ValueError("圖片無法讀取。")
    for target in targets:
        for face in target.faces:
            attrs = dict(face.attrs)
            attrs.pop("mat", None)
            attrs["color"] = (1.0, 1.0, 1.0)
            attrs["texture"] = {"path": str(path), "sw": 1.0, "sh": 1.0}
            face.attrs = attrs


def projection(targets, mode, axis=2, tile_cm=100., angle=0., view_basis=None):
    finite([tile_cm, angle])
    if tile_cm <= 0:
        raise ValueError("貼圖尺寸必須大於零。")
    if not any(t.faces for t in targets):
        raise ValueError("選取內容沒有面。")
    for t in targets:
        for f in t.faces:
            texture(f)
    lo, hi = bounds(targets)
    center = [(a + b) / 2 for a, b in zip(lo, hi)]
    a, b = ((1, 2), (2, 0), (0, 1))[axis]
    size = tile_cm / 100.0
    height = hi[axis] - lo[axis]
    if mode == "quad":
        plans = []
        for target in targets:
            records = []
            for face in target.faces:
                if len(face.loop) != 4 or face.hole_loops:
                    raise ValueError("四邊面貼圖只能用於沒有孔洞的四邊面。")
                corners = {tuple(round(c, 6) for c in xyz(p)): val for p, val in
                           zip(face.vertices, [(0,0),(1,0),(1,1),(0,1)])}
                for tri in face.triangulate():
                    values = [corners[tuple(round(c, 6) for c in xyz(p))] for p in tri]
                    mapping = fit_uv_affine(tri, values)
                    if mapping is None:
                        raise ValueError("四邊面含退化三角面。")
                    attrs = copy.deepcopy(face.attrs)
                    attrs["texture"] = {**texture(face), "uvw": mapping}
                    records.append((list(tri), attrs, face.interior))
            plans.append((target, records))
        for target, records in plans:
            replace_faces(target, records)
        return
    if mode in ("cylinder", "sphere"):
        if height < 1e-8:
            raise ValueError("投影軸方向沒有高度，請改選 X／Y／Z 軸。")
        triangulate(targets)
    phase = math.radians(angle)
    for target in targets:
        for face in target.faces:
            pts = face.vertices
            if mode == "box":
                n = xyz(face.normal())
                k = max(range(3), key=lambda i: abs(n[i]))
                aa, bb = ((1, 2), (2, 0), (0, 1))[k]
                sign = 1 if n[k] >= 0 else -1
                values = [(xyz(p)[aa] / size * sign, xyz(p)[bb] / size) for p in pts]
            elif mode == "view":
                if view_basis is None:
                    raise ValueError("缺少相機座標。")
                u, v = view_basis
                values = [(QVector3D.dotProduct(p, u) / size,
                           QVector3D.dotProduct(p, v) / size) for p in pts]
            elif mode in ("cylinder", "sphere"):
                values, poles = [], []
                for p in pts:
                    q = [c - d for c, d in zip(xyz(p), center)]
                    u = ((math.atan2(q[b], q[a]) - phase) / (2 * math.pi)) % 1
                    if mode == "cylinder":
                        v = (xyz(p)[axis] - lo[axis]) / height
                    else:
                        radius = math.sqrt(sum(c * c for c in q))
                        v = math.acos(max(-1., min(1., q[axis] / max(radius, 1e-12)))) / math.pi
                    values.append([u, v])
                    poles.append(math.hypot(q[a], q[b]) < 1e-8)
                nonpoles = [uv[0] for uv, pole in zip(values, poles) if not pole]
                if nonpoles and max(nonpoles) - min(nonpoles) > .5:
                    for uv in values:
                        if uv[0] < .5:
                            uv[0] += 1
                available = [uv[0] for uv, pole in zip(values, poles) if not pole]
                for uv, pole in zip(values, poles):
                    if pole:
                        uv[0] = sum(available) / max(1, len(available))
            else:
                values = [(xyz(p)[a] / size, xyz(p)[b] / size) for p in pts]
            set_uv(face, pts, values)


def transform(targets, du=0., dv=0., su=1., sv=1., degrees=0., pivot=(.5, .5)):
    finite([du, dv, su, sv, degrees, *pivot])
    if abs(su) < 1e-8 or abs(sv) < 1e-8:
        raise ValueError("UV 縮放倍率不能為零。")
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    for target in targets:
        for face in target.faces:
            # Compose directly with affine rows; holes and arbitrary ngons remain intact.
            gu, cu, gv, cv = face_uv_axes(texture(face), face.normal())
            rowu = [*xyz(gu), cu - pivot[0]]
            rowv = [*xyz(gv), cv - pivot[1]]
            newu = [su * c * u - sv * s * v for u, v in zip(rowu, rowv)]
            newv = [su * s * u + sv * c * v for u, v in zip(rowu, rowv)]
            newu[3] += pivot[0] + du
            newv[3] += pivot[1] + dv
            face.attrs = {**face.attrs, "texture": {**texture(face), "uvw": newu + newv}}


def signature(face):
    return [[[round(c, 6) for c in xyz(p)] for p in loop]
            for loop in (face.vertices, *face.holes)]


def save_uv(targets, filename):
    rows = []
    for ti, target in enumerate(targets):
        for face in target.faces:
            tex = texture(face)
            gu, cu, gv, cv = face_uv_axes(tex, face.normal())
            mapping = list(tex["uvw"]) if tex.get("uvw") else [*xyz(gu), cu, *xyz(gv), cv]
            rows.append({"target": ti, "shape": signature(face), "uvw": mapping})
    Path(filename).write_text(json.dumps({"format": "it-uv-1", "faces": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


def load_uv(targets, filename):
    path = Path(filename)
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("UV 檔案過大。")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    expected = [(ti, f) for ti, t in enumerate(targets) for f in t.faces]
    rows = data.get("faces", [])
    if data.get("format") != "it-uv-1" or len(rows) != len(expected):
        raise ValueError("UV 檔案與選取面數不符。")
    for row, (ti, face) in zip(rows, expected):
        if row.get("target") != ti or row.get("shape") != signature(face):
            raise ValueError("選取範圍、面順序或幾何已改變，無法安全載回 UV。")
        values = row.get("uvw", [])
        if len(values) != 8:
            raise ValueError("UV 資料不完整。")
        finite(values)
        texture(face)
    for row, (_, face) in zip(rows, expected):
        face.attrs = {**face.attrs, "texture": {**texture(face), "uvw": list(row["uvw"])}}


def tri_key(points):
    return tuple(sorted(tuple(round(c, 6) for c in xyz(p)) for p in points))


def export_obj(targets, filename):
    """Geometry/UV only. The bridge never exports textures or arbitrary files."""
    lines = ["# IngeTrazo UV bridge; metres; do not move/reorder/merge geometry", "# UV editing only; no material images are exported"]
    index = 1
    keys = set()
    for t in targets:
        for face in t.faces:
            for tri in face.triangulate():
                key = tri_key(tri)
                if key in keys:
                    raise ValueError("選取中有重疊三角面，請分開匯出以避免 UV 配對歧義。")
                keys.add(key)
                coords = uv_at(face, tri)
                lines.append(f"o ituv_{index}")
                lines.extend("v " + " ".join(f"{c:.9g}" for c in xyz(p)) for p in tri)
                lines.extend(f"vt {u:.12g} {v:.12g}" for u, v in coords)
                lines.append("f " + " ".join(f"{i}/{i}" for i in range(index, index + 3)))
                index += 3
    Path(filename).write_text("\n".join(lines) + "\n", encoding="utf-8")


def import_obj(targets, filename):
    path = Path(filename)
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("OBJ 檔案過大。")
    verts, coords, triangles = [], [], {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        if fields[0] == "v":
            p = list(map(float, fields[1:4]))
            if len(p) != 3:
                raise ValueError("OBJ 頂點資料不完整。")
            finite(p)
            verts.append(QVector3D(*p))
        elif fields[0] == "vt":
            uv = tuple(map(float, fields[1:3]))
            if len(uv) != 2:
                raise ValueError("OBJ UV 資料不完整。")
            finite(uv)
            coords.append(uv)
        elif fields[0] == "f":
            if len(fields) != 4:
                raise ValueError("OBJ 必須保留匯出時的三角面；請勿合併或重新拓樸。")
            pp, uv = [], []
            for token in fields[1:]:
                item = token.split("/")
                if len(item) < 2 or not item[1]:
                    raise ValueError("OBJ 的面缺少 UV 索引。")
                vi, ui = int(item[0]), int(item[1])
                if vi == 0 or ui == 0:
                    raise ValueError("OBJ 索引不可為零。")
                pp.append(verts[vi - 1 if vi > 0 else vi])
                uv.append(coords[ui - 1 if ui > 0 else ui])
            key = tri_key(pp)
            if key in triangles:
                raise ValueError("OBJ 中存在重疊面，UV 配對不明確。")
            triangles[key] = (pp, uv)
    plans, used = [], set()
    for t in targets:
        records = []
        for face in t.faces:
            for tri in face.triangulate():
                key = tri_key(tri)
                if key not in triangles or key in used:
                    raise ValueError("OBJ 與選取的模型不符。請勿改變座標、單位或拓樸。")
                used.add(key)
                pp, values = triangles[key]
                mapping = fit_uv_affine(pp, values)
                if mapping is None:
                    raise ValueError("OBJ 中有退化面。")
                attrs = copy.deepcopy(face.attrs)
                attrs["texture"] = {**texture(face), "uvw": mapping}
                records.append((list(tri), attrs, face.interior))
        plans.append((t, records))
    if len(used) != len(triangles):
        raise ValueError("OBJ 含額外幾何；未套用 UV。")
    for t, records in plans:
        replace_faces(t, records)
