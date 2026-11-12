"""Independent deformation algorithms; no FredoScale code or dependencies."""
from __future__ import annotations
import copy
import math
from dataclasses import dataclass, replace
from PySide6.QtGui import QVector3D, QMatrix4x4
from core.mesh import edge_flags, stamp_edge_flags
from core.texture import fit_uv_affine, affine_uv
from .common import xyz, finite, LIMIT, material_sides
def uv_at(face, points, tex=None):
    from core.texture import face_uv_axes
    gu, cu, gv, cv = face_uv_axes(tex or face.attrs['texture'], face.normal())
    return [(QVector3D.dotProduct(gu,p)+cu, QVector3D.dotProduct(gv,p)+cv) for p in points]



@dataclass
class Parameters:
    frame: object = None
    anchor: object = None
    mode: str = "scale"
    axis: int = 2
    factors: tuple = (1., 1., 1.)
    angle: float = 0.
    distance_cm: float = 0.
    lower: float = .25
    upper: float = .75
    taper_u: float = .5
    taper_v: float = .5
    center: bool = True
    orientation: tuple = (0., 0., 0.)
    subdivisions: int = 2
    target_cm: tuple | None = None
    fixed_axes: tuple = (True, False, True)
    border_cm: tuple | None = None
    border_bands_cm: tuple | None = None
    center_band_cm: tuple = (0., 0., 0.)
    preserve_shapes: bool = True


def orientation_frame(angles):
    frame = QMatrix4x4()
    frame.rotate(angles[2], 0, 0, 1)
    frame.rotate(angles[1], 0, 1, 0)
    frame.rotate(angles[0], 1, 0, 0)
    return frame


def dimensions_cm(points, orientation=(0., 0., 0.)):
    inverse, _ = orientation_frame(orientation).inverted()
    local = [xyz(inverse.map(p)) for p in points]
    return tuple((max(p[i] for p in local) - min(p[i] for p in local)) * 100 for i in range(3))


def detect_border_bands(local_points, boundary_levels=None):
    """Infer the outermost inner coordinate boundaries of an axis-aligned frame.

    No invented thickness when an axis has no pair of inner boundaries.
    Opposite ends can have different widths. Coordinates are in frame metres.
    """
    points = [xyz(p) for p in local_points]
    bands = []
    for i in range(3):
        values = sorted(p[i] for p in points)
        eps = max(1e-5, (values[-1]-values[0])*1e-6) if values else 1e-5
        if boundary_levels is not None and values:
            values = sorted([values[0],values[-1],*boundary_levels[i]])
        levels = []
        for v in values:
            if not levels or v-levels[-1] > eps:
                levels.append(v)
        bands.append(((levels[1]-levels[0])*100, (levels[-1]-levels[-2])*100)
                     if len(levels) >= 4 else (0., 0.))
        if len(levels) >= 4:
            # A moulded frame has several planes across each section. The
            # opening is the wide central gap, not the first profile step.
            span = levels[-1]-levels[0]
            mid = (levels[0]+levels[-1])/2
            opening = next(((a,b) for a,b in zip(levels,levels[1:])
                            if a < mid < b and b-a >= span*.25), None)
            if opening and opening[0] > levels[0]+eps and opening[1] < levels[-1]-eps:
                bands[-1] = ((opening[0]-levels[0])*100,(levels[-1]-opening[1])*100)
    return tuple(bands)


def detect_target_bands(targets, to_local):
    points = [to_local(v.position) for t in targets for v in t.vertices]
    coordinates = [xyz(p) for p in points]
    ext = [max(p[i] for p in coordinates)-min(p[i] for p in coordinates) for i in range(3)]
    eps = max(1e-5,max(ext)*1e-6)
    levels = [[],[],[]]
    for target in targets:
        for face in target.faces:
            vertices = [xyz(to_local(p)) for p in face.vertices]
            for i in range(3):
                if max(p[i] for p in vertices)-min(p[i] for p in vertices) <= eps:
                    levels[i].append(sum(p[i] for p in vertices)/len(vertices))
        if min(ext) <= eps:
            # Planar frames have no side walls: only exposed boundary edges
            # define the rim. Interior triangulation edges never define it.
            for edge in target.mesh.edges:
                if len(edge.faces)!=1 or edge.soft:
                    continue
                a,b=xyz(to_local(edge.a)),xyz(to_local(edge.b))
                for i in range(3):
                    if abs(a[i]-b[i])<=eps:
                        levels[i].append((a[i]+b[i])/2)
    return detect_border_bands(points,levels)


def mapper_for_targets(targets, params):
    if params.mode=='fixed_border' and params.border_cm is None and params.border_bands_cm is None:
        frame=params.frame if params.frame is not None else orientation_frame(params.orientation)
        inverse,_=frame.inverted()
        params=replace(params,border_bands_cm=detect_target_bands(targets,inverse.map))
    if params.mode=='fixed_border' and params.preserve_shapes:
        from .preserve_shape import profile_intervals,resize_mapper
        frame=params.frame if params.frame is not None else orientation_frame(params.orientation)
        inverse,_=frame.inverted()
        bands=params.border_bands_cm
        if bands is None:bands=tuple((v,v) for v in params.border_cm)
        finite([*params.factors,*params.center_band_cm,*(v for pair in bands for v in pair)])
        if params.target_cm is not None:finite(params.target_cm)
        if any(v<0 for pair in bands for v in pair) or any(v<0 for v in params.center_band_cm):
            raise ValueError('保留厚度不能是負數。')
        return resize_mapper([v.position for t in targets for v in t.vertices],params,frame,bands,
                             profile_intervals(targets,inverse.map))
    return make_mapper([v.position for t in targets for v in t.vertices],params)


def fixed_border_mapper(points, params):
    """Piecewise affine resize; fixed end bands and optional central mullion."""
    finite([*params.orientation, *params.center_band_cm])
    frame = params.frame if params.frame is not None else orientation_frame(params.orientation)
    inverse, ok = frame.inverted()
    local = [xyz(inverse.map(p)) for p in points]
    if not ok or not local:
        raise ValueError("沒有可調整尺寸的頂點。")
    lo = [min(p[i] for p in local) for i in range(3)]
    hi = [max(p[i] for p in local) for i in range(3)]
    spans = [hi[i] - lo[i] for i in range(3)]
    bands = params.border_bands_cm
    if bands is None:
        bands = (tuple((v,v) for v in params.border_cm) if params.border_cm is not None
                 else detect_border_bands([QVector3D(*p) for p in local]))
    finite(v for pair in bands for v in pair)
    targets = list(params.target_cm) if params.target_cm is not None else [spans[i]*100*params.factors[i] for i in range(3)]
    if not any(params.fixed_axes):
        raise ValueError("請至少勾選一個要調整的方向。")
    knots, planes = {}, []
    for i in range(3):
        if not params.fixed_axes[i]:
            continue
        finite([targets[i]])
        span, length = spans[i], targets[i] / 100.
        left, right = (v/100. for v in bands[i])
        middle = params.center_band_cm[i]/100.
        if left < 0 or right < 0 or middle < 0 or length <= 0:
            raise ValueError("目標尺寸須大於零，保留厚度不能是負數。")
        if abs(length-span) <= 1e-7:
            continue
        reserved = left+right+middle
        if span-reserved <= 1e-6 or length-reserved <= 1e-6:
            axis_name = 'XYZ'[i]
            raise ValueError(f"{axis_name} 原尺寸與目標尺寸都須大於兩端邊框＋中央保留厚度（{reserved*100:g} cm）。")
        start = lo[i] - (length-span)/2 if params.center else lo[i]
        if params.anchor is not None:
            anchor = xyz(inverse.map(params.anchor))[i]
            start = anchor + (lo[i]-anchor)*length/span
        if middle and (left >= (span-middle)/2 or right >= (span-middle)/2
                       or left >= (length-middle)/2 or right >= (length-middle)/2):
            raise ValueError("中央保留區與兩端保留區重疊，請增加目標尺寸或縮小保留區。")
        old = [lo[i], lo[i]+left]
        new = [start, start+left]
        if middle > 0:
            old += [(lo[i]+hi[i]-middle)/2, (lo[i]+hi[i]+middle)/2]
            new += [start+(length-middle)/2, start+(length+middle)/2]
        old += [hi[i]-right, hi[i]]
        new += [start+length-right, start+length]
        pairs = []
        for a,b in zip(old,new):
            if not pairs or a-pairs[-1][0] > 1e-9:
                pairs.append((a,b))
        knots[i] = pairs
        planes.extend((i,a) for a,b in pairs[1:-1])

    def mapped(point):
        p = list(xyz(inverse.map(point)))
        for i, pairs in knots.items():
            x = p[i]
            segment = next(((a,b,c,d) for (a,b),(c,d) in zip(pairs,pairs[1:]) if x <= c+1e-8),
                           (*pairs[-2], *pairs[-1]))
            a,b,c,d = segment
            p[i] = b + (x-a)*(d-b)/(c-a)
        return frame.map(QVector3D(*p))
    mapped.cut_planes = planes
    mapped.to_local = inverse.map
    mapped.to_world = frame.map
    mapped.changed = bool(knots)
    return mapped


def make_mapper(points, params):
    if params.mode == "fixed_border":
        return fixed_border_mapper(points, params)
    finite([*params.factors, params.angle, params.distance_cm, params.lower,
            params.upper, params.taper_u, params.taper_v, *params.orientation])
    if params.axis not in (0, 1, 2):
        raise ValueError("變形軸必須是 X、Y 或 Z。")
    frame = QMatrix4x4()
    frame.rotate(params.orientation[2], 0, 0, 1)
    frame.rotate(params.orientation[1], 0, 1, 0)
    frame.rotate(params.orientation[0], 1, 0, 0)
    inverse, ok = frame.inverted()
    local = [xyz(inverse.map(p)) for p in points]
    if not ok or not local:
        raise ValueError("沒有可變形的頂點。")
    lo = [min(p[i] for p in local) for i in range(3)]
    hi = [max(p[i] for p in local) for i in range(3)]
    center = [(a + b) / 2 for a, b in zip(lo, hi)]
    pivot = center if params.center else lo
    axis = params.axis
    a, b = ((1, 2), (2, 0), (0, 1))[axis]
    height = hi[axis] - lo[axis]
    nonlinear = params.mode in ("stretch", "taper", "twist", "bend")
    if nonlinear and height < 1e-7:
        raise ValueError("變形軸沒有足夠長度，請換一個軸。")
    factors = params.factors
    if params.target_cm is not None:
        finite(params.target_cm)
        if any(x <= 0 for x in params.target_cm):
            raise ValueError("目標尺寸必須大於零。")
        if any(hi[i] - lo[i] < 1e-7 for i in range(3)):
            raise ValueError("零厚度幾何請使用倍率縮放，不能指定三軸尺寸。")
        factors = tuple(params.target_cm[i] / 100. / (hi[i] - lo[i]) for i in range(3))
    if any(f <= 1e-6 for f in factors):
        raise ValueError("縮放倍率必須為正數。")
    if params.taper_u <= 0 or params.taper_v <= 0:
        raise ValueError("錐化末端倍率必須大於零，避免壓扁或翻面。")
    if params.mode == "stretch" and not (0 <= params.lower < params.upper <= 1):
        raise ValueError("拉伸區間必須滿足 0 ≤ 起點 < 終點 ≤ 100%。")
    delta = params.distance_cm / 100.
    band = (params.upper - params.lower) * height
    if params.mode == "stretch" and delta <= -band + 1e-7:
        raise ValueError("壓縮量會使拉伸區間反轉，請縮小數值。")
    angle = math.radians(params.angle)
    if params.mode == "shear" and abs(params.angle) >= 85:
        raise ValueError("剪切角度須小於 85°。")
    if params.mode == "bend" and abs(angle) > math.pi * 1.95:
        raise ValueError("彎曲角度須小於 351°，避免首尾重疊。")
    radius = height / angle if abs(angle) > 1e-10 and height else 0.
    if params.mode == "bend" and radius and abs(radius) <= (hi[a] - lo[a]) / 2 + 1e-7:
        raise ValueError("彎曲半徑小於截面半寬，會產生折返。請減小角度。")

    def mapped(point):
        p = list(xyz(inverse.map(point)))
        q = list(p)
        t = (p[axis] - lo[axis]) / height if height else 0.
        if params.mode == "scale":
            q = [pivot[i] + (p[i] - pivot[i]) * factors[i] for i in range(3)]
        elif params.mode == "rotate":
            c, s = math.cos(angle), math.sin(angle)
            x, y = p[a] - pivot[a], p[b] - pivot[b]
            q[a], q[b] = pivot[a] + x * c - y * s, pivot[b] + x * s + y * c
        elif params.mode == "shear":
            q[a] += math.tan(angle) * (p[axis] - pivot[axis])
        elif params.mode == "stretch":
            w = max(0., min(1., (t - params.lower) / (params.upper - params.lower)))
            q[axis] += delta * w
        elif params.mode == "taper":
            q[a] = center[a] + (p[a] - center[a]) * (1 + t * (params.taper_u - 1))
            q[b] = center[b] + (p[b] - center[b]) * (1 + t * (params.taper_v - 1))
        elif params.mode == "twist":
            c, s = math.cos(angle * t), math.sin(angle * t)
            x, y = p[a] - center[a], p[b] - center[b]
            q[a], q[b] = center[a] + x * c - y * s, center[b] + x * s + y * c
        elif params.mode == "bend":
            if radius:
                r = radius - (p[a] - center[a])
                q[a] = center[a] + radius - r * math.cos(angle * t)
                q[axis] = lo[axis] + r * math.sin(angle * t)
        else:
            raise ValueError("未知變形模式。")
        finite(q)
        return frame.map(QVector3D(*q))
    return mapped


def subdivide(tri, depth):
    if depth == 0:
        yield tri
        return
    a, b, c = tri
    ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
    for part in ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)):
        yield from subdivide(part, depth - 1)


def _cut_polygon(poly, axis, value):
    """Split a convex polygon at an exact local plane, retaining boundary vertices."""
    eps = 1e-7
    coordinates = [xyz(p)[axis] for p in poly]
    if min(coordinates) >= value-eps or max(coordinates) <= value+eps:
        return [poly]

    def intersection(a, b):
        if xyz(a) > xyz(b):
            a,b = b,a
        aa,bb = xyz(a)[axis],xyz(b)[axis]
        if abs(aa-value)<=eps:
            return a
        if abs(bb-value)<=eps:
            return b
        q = list(xyz(a + (b-a)*((value-aa)/(bb-aa))))
        q[axis] = value
        return QVector3D(*q)

    result = []
    for side in (-1, 1):
        out = []
        for a,b in zip(poly, poly[1:]+poly[:1]):
            ina = side*(xyz(a)[axis]-value) >= -eps
            inb = side*(xyz(b)[axis]-value) >= -eps
            if ina:
                out.append(a)
            if ina != inb:
                out.append(intersection(a,b))
        cleaned = []
        for p in out:
            if not cleaned or (p-cleaned[-1]).lengthSquared() > 1e-18:
                cleaned.append(p)
        if len(cleaned)>1 and (cleaned[0]-cleaned[-1]).lengthSquared() < 1e-18:
            cleaned.pop()
        if len(cleaned) >= 3:
            result.append(cleaned)
    return result


def _cut_edge(a, b, mapper):
    aa, bb = xyz(mapper.to_local(a)), xyz(mapper.to_local(b))
    stops = [0.,1.]
    for axis,value in mapper.cut_planes:
        # Match polygon classification: an edge lying numerically on a plane
        # must not be split by an arbitrary ratio of two rounding errors.
        da,db = aa[axis]-value,bb[axis]-value
        if not ((da < -1e-7 and db > 1e-7) or (db < -1e-7 and da > 1e-7)):
            continue
        d = bb[axis]-aa[axis]
        if abs(d)>1e-12:
            t = (value-aa[axis])/d
            if 1e-8 < t < 1-1e-8:
                stops.append(t)
    stops.sort()
    unique = [stops[0]]
    for t in stops[1:]:
        if t-unique[-1]>1e-8:
            unique.append(t)
    return [(mapper(a+(b-a)*s),mapper(a+(b-a)*t)) for s,t in zip(unique,unique[1:])]


def _convex_face_pieces(face):
    """Keep convex cells intact, especially their collinear seam vertices.

    Triangulating a prior cut cell can omit collinear boundary vertices and
    introduce T junctions on the neighbouring cell during a second resize.
    """
    points = face.vertices
    normal = face.normal()
    if not face.hole_loops and all(
        QVector3D.dotProduct(QVector3D.crossProduct(b-a,c-b),normal) >= -1e-10
        for a,b,c in zip(points,points[1:]+points[:1],points[2:]+points[:2])):
        return [points]
    boundary = [p for loop in (face.vertices,*face.holes) for p in loop]
    pieces = []
    for triangle in face.triangulate():
        ring = []
        triangle = list(triangle)
        for a,b in zip(triangle,triangle[1:]+triangle[:1]):
            ring.append(a)
            direction = b-a
            length2 = direction.lengthSquared()
            if length2 < 1e-18:
                continue
            candidates = []
            for p in boundary:
                t = QVector3D.dotProduct(p-a,direction)/length2
                if 1e-7 < t < 1-1e-7 and (p-(a+direction*t)).lengthSquared()<1e-12:
                    candidates.append((t,p))
            for t,p in sorted(candidates,key=lambda item:item[0]):
                if (p-ring[-1]).lengthSquared()>1e-18:
                    ring.append(p)
        pieces.append(ring)
    return pieces


def apply_fixed_border(targets, params):
    mapper = mapper_for_targets(targets, params)
    if not mapper.changed:
        raise ValueError("目標尺寸和目前尺寸相同；沒有修改模型。")
    plans, total = [], 0
    for target in targets:
        mesh = target.mesh
        edges = [e for e in mesh.edges if e.v0 in target.vertices or e.v1 in target.vertices]
        selected_faces = set(target.faces)
        if any(f not in selected_faces for e in edges for f in e.faces):
            raise ValueError("選取範圍不完整，請選取完整群組或相連物件。")
        was_closed = bool(target.faces) and all(len(e.faces)==2 for e in edges)
        records = []
        for face in target.faces:
            for polygon in _convex_face_pieces(face):
                pieces = [[mapper.to_local(p) for p in polygon]]
                for axis,value in mapper.cut_planes:
                    pieces = [part for piece in pieces for part in _cut_polygon(piece,axis,value)]
                for piece in pieces:
                    original = [mapper.to_world(p) for p in piece]
                    moved = [mapper(p) for p in original]
                    records.append((moved,remap_attrs(face,original,moved),face.interior,face.area()<1e-12,id(face)))
                    total += 1
                    if total > LIMIT:
                        raise ValueError("固定邊框切片超過 150,000 面，請縮小選取範圍。")
        segments = [(a,b,edge_flags(e)) for e in edges for a,b in _cut_edge(e.a,e.b,mapper)]
        plans.append((target,edges,was_closed,records,segments))
    for target,edges,was_closed,records,segments in plans:
        mesh = target.mesh
        for face in list(target.faces):
            mesh.remove_face(face)
        for edge in edges:
            mesh.remove_edge(edge)
        mesh.prune_orphan_vertices()
        faces = []
        provenance = {}
        for points,attrs,interior,already_degenerate,source_id in records:
            # The host welds at ~0.1 mm. Two clipping intersections can therefore
            # resolve to the same vertex even when their float coordinates differ.
            # Remove consecutive duplicates by host identity, before creating edges.
            ring = []
            for point in points:
                vertex = mesh.vertex(point)
                if not ring or ring[-1] is not vertex:
                    ring.append(vertex)
            if len(ring)>1 and ring[0] is ring[-1]:
                ring.pop()
            if len(ring)<3:
                continue
            face = mesh.add_face([v.position for v in ring])
            face.attrs,face.interior = attrs,interior
            if face.area()<1e-12 and not already_degenerate:
                raise ValueError("固定邊框縮放產生過小的面，已取消。")
            faces.append(face)
            provenance[face] = source_id
        generated = {e for f in faces for v in f.loop for e in v.edges}
        from .soften import normal, edge_cosine
        normals = {f: normal(f) for f in faces}
        for edge in generated:
            edge.soft = True
            if (len(edge.faces) == 2
                    and provenance.get(edge.faces[0]) == provenance.get(edge.faces[1])):
                cosine = edge_cosine(edge, normals)
                # A coplanar cut inside ONE original face is never an outline.
                # Actual bends and source edges retain their original flags below.
                edge.hidden = cosine is not None and cosine > 1.-1e-8
        vertices = {v for f in faces for v in f.loop}
        for a,b,flags in segments:
            if mesh.vertex(a) is mesh.vertex(b):
                continue
            edge = mesh.add_edge(a,b)
            stamp_edge_flags(edge,flags)
            vertices.update((edge.v0,edge.v1))
            generated.add(edge)
        if was_closed and any(len(e.faces)!=2 for e in generated):
            raise ValueError("固定邊框縮放的封閉性檢查失敗，已整筆取消。")
        target.faces,target.vertices = faces,vertices
        mesh.prune_orphan_vertices()


def remap_attrs(face, old_points, new_points):
    attrs = copy.deepcopy(face.attrs)
    for side in material_sides(attrs):
        tex = side.get("texture")
        if tex:
            values = uv_at(face, old_points, tex)
            mapping = fit_uv_affine(new_points, values)
            if mapping is None:
                raise ValueError("變形產生退化面，已取消。")
            fitted = affine_uv(mapping, new_points)
            if any(abs(a - b) > 1e-3 for u, v in zip(values, fitted) for a, b in zip(u, v)):
                raise ValueError("面 UV 無法保持，請增加細分或減少變形量。")
            side["texture"] = {**tex, "uvw": mapping}
    return attrs


def apply(targets, params):
    if params.mode == "fixed_border":
        return apply_fixed_border(targets, params)
    nonlinear = params.mode in ("stretch", "taper", "twist", "bend")
    if not 0 <= params.subdivisions <= 4:
        raise ValueError("細分層級必須介於 0 與 4。")
    points = [v.position for t in targets for v in t.vertices]
    mapper = make_mapper(points, params)
    triangle_count = sum(sum(len(f.loop) + sum(map(len, f.hole_loops)) + 2 * len(f.hole_loops) - 2
                             for f in t.faces) for t in targets)
    if nonlinear and triangle_count * (4 ** params.subdivisions) > LIMIT:
        raise ValueError("細分後超過 150,000 面。請降低細分層級或分批操作。")
    for target in targets:
        mesh = target.mesh
        # All faces connected to the selection are in scope; no hanging seam vertices.
        face_set = set(target.faces)
        connected_edges = [e for e in mesh.edges if e.v0 in target.vertices or e.v1 in target.vertices]
        was_closed = bool(target.faces) and all(len(e.faces) == 2 for e in connected_edges)
        if not nonlinear:
            newattrs = [(f, remap_attrs(f, f.vertices, [mapper(p) for p in f.vertices])) for f in target.faces]
            updates = [(v, mapper(v.position)) for v in target.vertices]
            for v, p in updates:
                mesh.place_vertex(v, p)
            for face, attrs in newattrs:
                face.attrs = attrs
        else:
            records = []
            for face in target.faces:
                for tri in face.triangulate():
                    for piece in subdivide(tri, params.subdivisions):
                        moved = [mapper(p) for p in piece]
                        records.append((moved, remap_attrs(face, piece, moved), face.interior))
            # Recreate original boundary and loose edges as matching subdivided segments.
            segments = []
            count = 2 ** params.subdivisions
            for edge in connected_edges:
                for i in range(count):
                    start = edge.a + (edge.b - edge.a) * (i / count)
                    end = edge.a + (edge.b - edge.a) * ((i + 1) / count)
                    segments.append((mapper(start), mapper(end), edge_flags(edge)))
            for f in list(target.faces):
                mesh.remove_face(f)
            for e in connected_edges:
                if e.faces:
                    raise ValueError("選取範圍仍與未選取面共用邊，無法安全細分。")
                mesh.remove_edge(e)
            mesh.prune_orphan_vertices()
            newfaces = []
            for pts, attrs, interior in records:
                new = mesh.add_face(pts)
                new.attrs, new.interior = attrs, interior
                newfaces.append(new)
            # New tessellation edges are smooth; source edges retain their properties.
            newface_set = set(newfaces)
            for f in newfaces:
                for v in f.loop:
                    for e in v.edges:
                        if e.faces and all(ff in newface_set for ff in e.faces):
                            e.soft = True
            for a, b, flags in segments:
                stamp_edge_flags(mesh.add_edge(a, b), flags)
            target.faces = newfaces
            target.vertices = {v for f in newfaces for v in f.loop}
            for e in mesh.edges:
                if not e.faces:
                    target.vertices.update((e.v0, e.v1))
        if any(f.area() < 1e-12 for f in target.faces):
            raise ValueError("變形產生零面積面，已取消。")
        if was_closed:
            touched = {e for f in target.faces for v in f.loop for e in v.edges}
            if any(len(e.faces) != 2 for e in touched):
                raise ValueError("變形後封閉性檢查未通過，已取消。")
