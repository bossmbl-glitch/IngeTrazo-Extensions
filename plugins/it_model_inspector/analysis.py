"""Read-only geometry diagnostics. Workers receive numeric snapshots, never live meshes."""
from dataclasses import dataclass, field
from collections import defaultdict
from itertools import product
import math
import numpy as np


@dataclass
class Snapshot:
    name: str
    points: np.ndarray
    edges: list  # (vertex index, vertex index, hidden, soft)
    faces: list  # face -> [outer ring, inner rings...]
    radial: list  # actual edge incidence, face indices


@dataclass
class Issue:
    code: str
    level: str
    name: str
    detail: str
    segments: list
    points: list

    def record(self):
        return dict(type=self.code, level=self.level, object=self.name,
                    detail=self.detail, coordinates_m=self.points,
                    segments_m=self.segments)


@dataclass
class Result:
    issues: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    meshes: int = 0
    faces: int = 0
    edges: int = 0
    complete: bool = True


def snapshot(mesh, matrix, name):
    vertices = list(mesh.vertices)
    vi = {v: i for i, v in enumerate(vertices)}
    faces = list(mesh.faces)
    fi = {f: i for i, f in enumerate(faces)}
    pts = np.asarray([v.position.toTuple() for v in vertices], dtype=float).reshape((-1, 3))
    if matrix is not None and len(pts):
        from core.group import np_affine
        rot, trans = np_affine(matrix)
        pts = pts @ rot.T + trans
    return Snapshot(name, pts,
                    [(vi[e.v0], vi[e.v1], bool(e.hidden), bool(e.soft)) for e in mesh.edges],
                    [[[vi[v] for v in ring] for ring in [f.loop, *f.hole_loops]] for f in faces],
                    [[fi.get(f, -1) for f in e.faces] for e in mesh.edges])


def scene_sources(scene, selection=None):
    """Only explicitly selected groups/components; never widen to the scene or loose mesh."""
    ctx = scene.edit_group
    roots = list(scene.groups if ctx is None else ctx.children)
    chosen = set(scene.selection if selection is None else selection)
    roots = [g for g in roots if g in chosen]
    if not roots:
        raise ValueError('請先選取要檢查的模型群組或元件。若是散置的面與線，請先組成群組，再選取檢查。')
    def walk(group, matrix, path, ancestors):
        if id(group) in ancestors:
            raise ValueError('模型群組包含循環參照，無法安全走訪。')
        if (getattr(group, 'ext', None) or {}).get('it_lighting_guide'):
            return
        local = group.xform
        world = matrix if local is None else (local if matrix is None else matrix * local)
        path = path + '/' + group.name
        if group.mesh.edges or group.mesh.faces:
            yield group.mesh, world, path
        for child in group.children:
            yield from walk(child, world, path, ancestors | {id(group)})
    for i, g in enumerate(roots):
        yield from walk(g, None, f'#{i+1}', set())


def normal(points):
    if len(points) < 3:
        return np.zeros(3), 0.
    p = points - points[0]
    n = np.sum(np.cross(p, np.roll(p, -1, axis=0)), axis=0)
    length = float(np.linalg.norm(n))
    return n / length if length > 1e-20 else np.zeros(3), length / 2


def ring_key(ring, pts, quantum):
    # Rotation- and winding-independent, retaining topology and hole boundaries.
    seq = [tuple(round(float(x) / quantum) for x in pts[v]) for v in ring]
    if not seq:
        return ()
    def canon(s):
        least = min(s)
        return min(tuple(s[i:] + s[:i]) for i, x in enumerate(s) if x == least)
    return min(canon(seq), canon(list(reversed(seq))))


class EdgeTree:
    """AABB tree for local near-miss/T-junction searches; avoids all-pairs scans."""
    def __init__(self, a, b, ids=None):
        self.a, self.b = a, b
        self.ids = np.arange(len(a)) if ids is None else ids
        self.lo = np.minimum(a[self.ids], b[self.ids]).min(axis=0)
        self.hi = np.maximum(a[self.ids], b[self.ids]).max(axis=0)
        self.children = []
        if len(self.ids) > 24:
            axis = int(np.argmax(self.hi - self.lo))
            order = self.ids[np.argsort((a[self.ids, axis] + b[self.ids, axis]) / 2)]
            n = len(order) // 2
            self.children = [EdgeTree(a, b, order[:n]), EdgeTree(a, b, order[n:])]

    def query(self, point, tol):
        if np.any(point < self.lo - tol) or np.any(point > self.hi + tol):
            return
        if self.children:
            for child in self.children:
                yield from child.query(point, tol)
        else:
            yield from self.ids


class Stopped(Exception):
    pass


def scan(snapshots, tolerance=.0005, sharp_degrees=30., max_issues=3000,
         cancelled=lambda: False, progress=lambda *a: None):
    result = Result()
    duplicates = {}
    def check():
        if cancelled():
            raise Stopped('已取消；以下為部分結果。')
        if len(result.issues) >= max_issues:
            raise Stopped(f'已達 {max_issues} 筆上限；請縮小範圍後再檢查，以下為部分結果。')
    def add(s, code, detail, segments=(), points=(), level='待確認'):
        check()
        segs = [[list(map(float, a)), list(map(float, b))] for a, b in segments]
        pp = [list(map(float, p)) for p in points]
        if not pp:
            pp = [p for seg in segs for p in seg]
        result.issues.append(Issue(code, level, s.name, detail, segs, pp))
    try:
        for num, s in enumerate(snapshots):
            check()
            progress(num, len(snapshots), s.name)
            p = s.points
            result.meshes += 1
            result.faces += len(s.faces)
            result.edges += len(s.edges)
            if not np.isfinite(p).all():
                add(s, '無效座標', '存在 NaN 或無限值座標；此網格其餘檢查已略過。', level='錯誤')
                result.complete = False
                continue
            edge_map = {tuple(sorted((a, b))): i for i, (a, b, _, _) in enumerate(s.edges)}
            incidence = defaultdict(list)
            normals = []
            directions = defaultdict(list)
            for fidx, rings in enumerate(s.faces):
                check()
                outer = rings[0] if rings else []
                all_ids = [v for ring in rings for v in ring]
                segs = [(p[a], p[b]) for r in rings for a, b in zip(r, r[1:] + r[:1])]
                n, area = normal(p[outer])
                normals.append(n)
                if len(outer) < 3 or area < 1e-12:
                    add(s, '退化面', '面積近零或頂點不足，可能造成三角化／顯示異常。', segs, p[all_ids], '錯誤')
                elif all_ids:
                    deviations = np.abs((p[all_ids] - p[outer[0]]) @ n)
                    if deviations.max() > tolerance:
                        add(s, '非共平面', f'同一面的頂點偏離平面 {deviations.max()*1000:.3f} mm；可能出現斜面或破圖。', segs, level='錯誤')
                for ring in rings:
                    if len(set(ring)) != len(ring):
                        add(s, '重複頂點', '面的同一環重複經過頂點。', segs, level='錯誤')
                    for a, b in zip(ring, ring[1:] + ring[:1]):
                        key = tuple(sorted((a, b)))
                        incidence[key].append(fidx)
                        directions[key].append(a < b)
                        if key not in edge_map:
                            add(s, '缺少邊線', '面的邊界沒有對應拓樸邊線。', [(p[a], p[b])], level='錯誤')
                    # Project using a best-fitting plane even for bow-tie polygons (zero Newell normal).
                    if 4 <= len(ring) <= 256:
                        q3 = p[ring] - p[ring[0]]
                        _, _, basis = np.linalg.svd(q3, full_matrices=False)
                        q = q3 @ basis[:2].T
                        crossed = False
                        def cross(a, b): return a[0]*b[1] - a[1]*b[0]
                        for i in range(len(q)):
                            if i % 32 == 0: check()
                            a, b = q[i], q[(i+1) % len(q)]
                            for j in range(i+2, len(q)):
                                if i == 0 and j == len(q)-1: continue
                                c, d = q[j], q[(j+1) % len(q)]
                                if (cross(b-a, c-a)*cross(b-a, d-a) < -1e-20 and
                                        cross(d-c, a-c)*cross(d-c, b-c) < -1e-20):
                                    crossed = True
                                    break
                            if crossed: break
                        if crossed:
                            add(s, '面邊界自交', '同一面的非相鄰邊互相穿越，可能形成錯誤三角面。', segs, level='錯誤')
                    elif len(ring) > 256:
                        note = '超過 256 點的環未做自交檢查；其他檢查照常執行。'
                        if note not in result.notes: result.notes.append(note)
                if outer:
                    # Use a tighter tolerance for duplicates, not the gap threshold.
                    quantum = min(tolerance, 1e-5)
                    key = (ring_key(outer, p, quantum), tuple(sorted(ring_key(r, p, quantum) for r in rings[1:])))
                    if key in duplicates:
                        add(s, '重複重疊面', f'與「{duplicates[key]}」有相同輪廓（含內孔），可能閃爍或遮擋。', segs)
                    else:
                        duplicates[key] = s.name + f' / 面 {fidx+1}'
            boundary, loose = [], []
            for eidx, (a, b, hidden, soft) in enumerate(s.edges):
                if eidx % 256 == 0: check()
                seg = [(p[a], p[b])]
                key = tuple(sorted((a, b)))
                fs = incidence.get(key, [])
                length = np.linalg.norm(p[b]-p[a])
                if length < 1e-8:
                    add(s, '零長度邊', '邊線兩端重合。', seg, level='錯誤')
                if sorted(fs) != sorted(s.radial[eidx]):
                    add(s, '拓樸關聯異常', '邊線記錄的相鄰面與實際面邊界不一致。', seg, level='錯誤')
                if len(fs) == 1: boundary.append(eidx)
                if not fs: loose.append(eidx)
                if len(fs) > 2:
                    add(s, '多面共邊', f'{len(fs)} 個面共用一條邊；牆面交接可正常，實體外殼需確認。', seg)
                if len(fs) == 2:
                    dot = float(np.clip(np.dot(normals[fs[0]], normals[fs[1]]), -1, 1))
                    angle = math.degrees(math.acos(abs(dot)))
                    if (hidden or soft) and angle >= sharp_degrees:
                        add(s, '折角線被隱藏', f'相鄰面夾角 {angle:.1f}°，邊線設定為隱藏／柔化；可能是該有線卻看不到。', seg)
                    if len(directions[key]) == 2 and directions[key][0] == directions[key][1]:
                        add(s, '面方向不一致', '相鄰面沿共邊使用同一方向；可能有反面，也可能是刻意的雙面結構。', seg)
            # One item per connected boundary, instead of thousands of individual edges.
            for ids, code in [(boundary, '開放邊界／疑似破洞'), (loose, '孤立線／未成面')]:
                adjacency = defaultdict(list)
                for i in ids:
                    a, b, _, _ = s.edges[i]
                    adjacency[a].append(i); adjacency[b].append(i)
                unseen = set(ids)
                while unseen:
                    check()
                    todo = [unseen.pop()]; component = []
                    degree = defaultdict(int)
                    while todo:
                        i = todo.pop(); component.append(i)
                        for v in s.edges[i][:2]:
                            degree[v] += 1
                            for j in adjacency[v]:
                                if j in unseen: unseen.remove(j); todo.append(j)
                    closed = all(n == 2 for n in degree.values())
                    description = ('封閉環' if closed else '開放鏈／分岔邊界') + f'，共 {len(component)} 條邊。'
                    description += ('可能缺面；門窗、薄片與刻意開口也會出現。' if code.startswith('開放') else '尚未連到任何面；可能是輪廓、定位線或未完成的面。')
                    add(s, code, description, [(p[s.edges[i][0]], p[s.edges[i][1]]) for i in component])
            candidates = boundary + loose
            if candidates:
                ends = sorted({v for i in candidates for v in s.edges[i][:2]})
                grid = defaultdict(list)
                for v in ends:
                    check()
                    cell = tuple(np.floor(p[v]/tolerance).astype(np.int64))
                    for offset in product((-1, 0, 1), repeat=3):
                        for u in grid[tuple(cell[k]+offset[k] for k in range(3))]:
                            dist = np.linalg.norm(p[v]-p[u])
                            if dist <= tolerance and tuple(sorted((v, u))) not in edge_map:
                                add(s, '端點未接合', f'兩個不同頂點相距 {dist*1000:.3f} mm，可能是細縫。', [(p[v], p[u])])
                    grid[cell].append(v)
                ea = np.asarray([p[s.edges[i][0]] for i in candidates])
                eb = np.asarray([p[s.edges[i][1]] for i in candidates])
                tree = EdgeTree(ea, eb)
                for v in ends:
                    check()
                    for idx in tree.query(p[v], tolerance):
                        a, b = s.edges[candidates[idx]][:2]
                        if v in (a, b): continue
                        ab = p[b]-p[a]; ll = float(ab @ ab)
                        if ll < 1e-16: continue
                        t = float((p[v]-p[a]) @ ab / ll)
                        if not (0 < t < 1): continue
                        foot = p[a] + t*ab
                        if min(np.linalg.norm(foot-p[a]), np.linalg.norm(foot-p[b])) <= tolerance: continue
                        dist = np.linalg.norm(p[v]-foot)
                        if dist <= tolerance:
                            add(s, '邊線未分割接合', f'頂點靠近另一條邊的中段（{dist*1000:.3f} mm），但沒有共用頂點。', [(p[a], p[b]), (p[v], foot)])
    except Stopped as exc:
        result.complete = False
        result.notes.append(str(exc))
    return result
