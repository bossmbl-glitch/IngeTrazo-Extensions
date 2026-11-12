"""Angle-based edge softening. No vertices, faces or UVs are changed."""
import math
from .common import EditCommand, get_targets


def normal(face):
    # Double precision, relative coordinates: tiny imported faces must not
    # acquire QVector3D.normalized()'s zero-vector fallback.
    p = [v.position.toTuple() for v in face.loop]
    if len(p) < 3:
        return None
    origin = p[0]
    n = [0., 0., 0.]
    for a, b in zip(p[1:], p[2:]):
        a = [a[i]-origin[i] for i in range(3)]
        b = [b[i]-origin[i] for i in range(3)]
        n[0] += a[1]*b[2]-a[2]*b[1]
        n[1] += a[2]*b[0]-a[0]*b[2]
        n[2] += a[0]*b[1]-a[1]*b[0]
    length = math.sqrt(sum(x*x for x in n))
    return tuple(x/length for x in n) if length > 1e-18 else None


def direction(face, edge):
    for loop in (face.loop, *face.hole_loops):
        for a, b in zip(loop, loop[1:]+loop[:1]):
            if a is edge.v0 and b is edge.v1:
                return 1
            if a is edge.v1 and b is edge.v0:
                return -1
    return 0


def edge_cosine(edge, normals):
    if len(edge.faces) != 2 or edge.faces[0] is edge.faces[1]:
        return None
    a, b = edge.faces
    na, nb = normals.get(a), normals.get(b)
    if na is None or nb is None:
        return None
    dot = sum(x*y for x, y in zip(na, nb))
    # Imported back-facing polygons must not turn a shallow seam into 170°.
    if direction(a, edge) == direction(b, edge):
        dot = -dot
    return max(-1., min(1., dot))


def apply(targets, angle=20., coplanar=True, smooth=None):
    angle = float(angle)
    if not math.isfinite(angle) or not 0 <= angle <= 180:
        raise ValueError('角度必須介於 0° 與 180°。')
    threshold = math.cos(math.radians(angle))
    changed = softened = 0
    for target in targets:
        faces = set(target.faces)
        if smooth is not None:
            for face in faces:
                if smooth:
                    face.attrs['_it_smooth_normals'] = True
                else:
                    face.attrs.pop('_it_smooth_normals', None)
        normals = {f: normal(f) for f in faces}
        edges = {e for v in target.vertices for e in v.edges}
        for edge in edges:
            # Explicitly hidden edges (including new resize cuts) stay hidden.
            # Open boundaries and edges bordering unselected faces stay intact.
            if edge.hidden or len(edge.faces) != 2 or any(f not in faces for f in edge.faces):
                continue
            cosine = edge_cosine(edge, normals)
            if cosine is None:
                continue
            flat = cosine > 1.-1e-10
            value = coplanar if flat else angle > 0 and cosine >= threshold-1e-10
            changed += edge.soft != value
            edge.soft = bool(value)
            softened += value
    return changed, softened


class SoftenCommand(EditCommand):
    def __init__(self, scene, angle, coplanar, smooth=True):
        self.angle, self.coplanar, self.smooth = angle, coplanar, smooth
        self.count = (0, 0)
        super().__init__(scene, self.run, False, '柔化／平滑邊線')

    def run(self, targets):
        self.count = apply(targets, self.angle, self.coplanar, self.smooth)

    def amend(self, scene, angle, coplanar, smooth=True):
        # Only the last command of this live slider gesture may be amended.
        self.angle, self.coplanar, self.smooth = angle, coplanar, smooth
        targets = get_targets(scene)
        self.run(targets)
        for target in targets:
            target.mesh._chunk_dirty = True
            target.mesh._mut_serial += 1
        scene.version += 1
        self.after = self.capture(scene)
