"""Corner normals in an auxiliary VBO; native materials/passes remain in charge.

The model and native position/UV buffers are unchanged. Only faces explicitly
marked by the smoothing tool opt in. Hard edges split the normal fan.
"""
import math
import re
import numpy as np
from PySide6.QtGui import QVector3D
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram
from .soften import normal, direction

KEY = '_it_smooth_normals'


def corner_normals(mesh):
    enabled = {f for f in mesh.faces if f.attrs.get(KEY)}
    if not enabled:
        return {}
    normals = {f: np.array(normal(f) or (0., 0., 1.)) for f in enabled}
    result = {}
    for vertex in {v for f in enabled for v in f.loop}:
        faces = {f for e in vertex.edges for f in e.faces if f in enabled}
        adjacency = {f: [] for f in faces}
        for edge in vertex.edges:
            if not edge.soft or len(edge.faces) != 2:
                continue
            a, b = edge.faces
            if a not in faces or b not in faces or a is b:
                continue
            sign = -1 if direction(a, edge) == direction(b, edge) else 1
            adjacency[a].append((b, sign)); adjacency[b].append((a, sign))
        while faces:
            seed = next(iter(faces)); fan = {seed: 1}; todo = [seed]
            while todo:
                face = todo.pop()
                for other, sign in adjacency[face]:
                    if other not in fan:
                        fan[other] = fan[face]*sign; todo.append(other)
            total = np.zeros(3)
            for face, sign in fan.items():
                loop = face.loop; i = loop.index(vertex)
                p = np.array(vertex.position.toTuple())
                a = np.array(loop[i-1].position.toTuple())-p
                b = np.array(loop[(i+1)%len(loop)].position.toTuple())-p
                den = np.linalg.norm(a)*np.linalg.norm(b)
                weight = math.acos(float(np.clip(a@b/den, -1, 1))) if den > 1e-20 else 0.
                total += normals[face]*sign*weight
            length = np.linalg.norm(total)
            total = total/length if length > 1e-12 else normals[seed]
            for face, sign in fan.items():
                result[face, vertex] = total*sign
            faces.difference_update(fan)
    return result


def shader_sources(vertex, fragment):
    assert 'layout(location = 6)' in vertex and 'float matte(vec3 n)' in fragment
    vertex = vertex.replace('void main() {', '''
layout(location = 7) in vec3 a_it_normal;
layout(location = 8) in vec3 a_it_base;
out vec3 v_it_normal;
out vec3 v_it_base;
void main() {
    v_it_normal = a_it_normal;
    v_it_base = a_it_base;
''', 1)
    fragment = fragment.replace('void main() {', '''
in vec3 v_it_normal;
in vec3 v_it_base;
void main() {
    bool it_smooth = dot(v_it_normal, v_it_normal) > 0.25;
    float it_shade = it_smooth ? matte(normalize(v_it_normal)) : u_shade;
''', 1)
    fragment = fragment.replace('texel.rgb * u_shade', 'texel.rgb * it_shade')
    fragment = fragment.replace('vec4(v_color, u_opacity)',
                                'vec4(it_smooth ? v_it_base * it_shade : v_color, u_opacity)')
    fragment = fragment.replace('if (u_reshade == 1 &&', 'if (!it_smooth && u_reshade == 1 &&')
    fragment = fragment.replace('float ndl = dot(nrm, u_sun_dir);', '''
        if (it_smooth) {
            vec3 sn = normalize(v_it_normal);
            nrm = dot(sn, nrm) < 0.0 ? -sn : sn;
        }
        float ndl = dot(nrm, u_sun_dir);''')
    return vertex, fragment


def install_serialization():
    from formats import igz
    if getattr(igz, '_it_smooth_io', False):
        return
    write, read = igz._face_attrs_json, igz._face_attrs_from_json
    def encode(face):
        result = write(face)
        if face.attrs.get(KEY):
            result[KEY] = True
        return result
    def decode(raw):
        result = read(raw)
        if raw.get(KEY):
            result = result or {}; result[KEY] = True
        return result
    igz._face_attrs_json, igz._face_attrs_from_json = encode, decode
    igz._it_smooth_io = True


def triangle_key(points):
    # Pick chunks reconstruct corners as v0 + edge. Cancellation near zero
    # differs by ~1e-17 from draw vertices; exact keys dropped entire facets.
    # 0.1 micrometre keys are far below the host's ~0.1 mm weld tolerance.
    return tuple(sorted(tuple(round(float(x),7) for x in p) for p in np.asarray(points, np.float32)))


class SmoothRenderer:
    def __init__(self, vp):
        self.vp = vp
        self.buffers = {}
        self.registry_key = None
        self.registry = {}
        self.enabled_cache = {}
        self.normal_cache = {}
        self.route_state = None

    def sync_routes(self):
        """An edited mesh is omitted from the host's placements epoch.

        Smoothing changes whether that mesh is drawn by the instanced pass.
        Without invalidating BOTH lists, turning it off skips the static pass
        while the cached instance pool still excludes it: the part disappears.
        """
        vp = self.vp
        scene = vp.scene
        state = (id(scene.edit_group), id(scene.mesh), self.enabled(scene.mesh))
        if state == self.route_state:
            return
        self.route_state = state
        for name in ('_inst_pool', '_static_groups_memo', '_elig_memo',
                     '_edge_groups_cache', '_frame_cache'):
            setattr(vp, name, None)
        vp._forget_frame_records()
        vp._edges_version = -1
        vp._edges_content_key = None

    def enabled(self, mesh):
        key = (getattr(mesh, '_mut_serial', None), self.vp.scene.content_version)
        hit = self.enabled_cache.get(id(mesh))
        if hit is None or hit[0] is not mesh or hit[1] != key:
            hit = (mesh, key, any(f.attrs.get(KEY) for f in mesh.faces))
            self.enabled_cache[id(mesh)] = hit
        return hit[2]

    def mesh_normals(self, mesh):
        key = (getattr(mesh, '_mut_serial', None), self.vp.scene.content_version)
        hit = self.normal_cache.get(id(mesh))
        if hit is None or hit[0] is not mesh or hit[1] != key:
            hit = (mesh, key, corner_normals(mesh))
            self.normal_cache[id(mesh)] = hit
        return hit[2]

    def build_registry(self):
        vp = self.vp
        key = (vp.scene.content_version, id(vp.scene.mesh))
        if self.registry_key == key:
            return self.registry
        from core.materials import effective_attrs
        from views.viewport import _paint_of
        registry = {}
        used_meshes = set()
        def add(mesh, triangles, entities, matrix=None, paint=None):
            used_meshes.add(id(mesh))
            corners = self.mesh_normals(mesh)
            linear = (np.array(matrix.data(), dtype=float).reshape(4,4).T[:3,:3]
                      if matrix is not None else np.eye(3))
            try:
                transform = np.linalg.inv(linear).T
            except np.linalg.LinAlgError:
                return
            per_face = {}
            for points, face in zip(triangles, entities):
                if not face.attrs.get(KEY):
                    continue
                if face not in per_face:
                    positions = np.array([v.position.toTuple() for v in face.loop])
                    if matrix is not None:
                        m = np.array(matrix.data(), dtype=float).reshape(4,4).T
                        positions = positions @ m[:3,:3].T + m[:3,3]
                    ns = np.array([transform @ corners[face, v] for v in face.loop])
                    ns /= np.maximum(np.linalg.norm(ns, axis=1)[:,None], 1e-20)
                    attrs = effective_attrs(face.attrs, paint)
                    front = tuple(attrs.get('color') or vp.DEFAULT_FACE_COLOR)
                    back = attrs.get('back')
                    back_color = tuple(back.get('color') or front) if isinstance(back, dict) else front
                    per_face[face] = positions, ns, front, back_color
                positions, ns, front, back = per_face[face]
                # Native triangulation uses existing polygon vertices.
                indices = np.linalg.norm(np.asarray(points)[:,None,:]-positions[None,:,:], axis=2).argmin(axis=1)
                registry.setdefault(triangle_key(points), []).append((np.asarray(points, np.float32), ns[indices], front, back))
        loose = vp.scene.loose_mesh
        if self.enabled(loose):
            triangles, entities = [], []
            for f in loose.faces:
                if f.attrs.get(KEY):
                    for tri in vp._tris_of(f):
                        triangles.append([p.toTuple() for p in tri]); entities.append(f)
            add(loose, triangles, entities, paint=_paint_of(vp, vp.scene.edit_group))
        for group in vp._placements():
            if not self.enabled(group.mesh) or getattr(group, 'billboard', False):
                continue
            ch = vp._group_chunk(group)
            if ch['v0'] is None:
                continue
            triangles = np.stack((ch['v0'], ch['v0']+ch['e1'], ch['v0']+ch['e2']), axis=1)
            add(group.mesh, triangles, [ch['faces'][i] for i in ch['tri_ent']],
                getattr(group, 'xform', None), _paint_of(vp, group))
        self.registry, self.registry_key = registry, key
        self.normal_cache = {k:v for k,v in self.normal_cache.items() if k in used_meshes}
        self.enabled_cache = {k:v for k,v in self.enabled_cache.items() if k in used_meshes}
        return registry

    def auxiliary(self, raw, stride, registry=None):
        registry = self.build_registry() if registry is None else registry
        a = np.frombuffer(raw, np.float32).reshape(-1, stride)
        output = np.zeros((len(a),6), np.float32)
        # Padding at the tail is never drawn; parts may end on a vertex boundary.
        for start in range(0, len(a)-2, 3):
            points = a[start:start+3,:3]
            candidates = registry.get(triangle_key(points))
            if not candidates:
                continue
            best = None
            for positions, ns, front, back in candidates:
                order = np.linalg.norm(points[:,None,:]-positions[None,:,:], axis=2).argmin(axis=1)
                if stride == 6:
                    # Choose the correct side's unshaded material colour, without
                    # inheriting the host's quantised flat-lighting bands.
                    flat = np.cross(points[1]-points[0], points[2]-points[0])
                    colors = [(np.linalg.norm(np.array(self.vp._shaded_color(base,QVector3D(*flat)))-a[start,3:6]),base)
                              for base in (front,back)]
                    score, base = min(colors, key=lambda item:item[0])
                else:
                    score, base = 0., front
                if best is None or score < best[0]:
                    best = score, ns[order], base
            output[start:start+3,:3] = best[1]
            output[start:start+3,3:] = best[2]
        return output.tobytes()

    def upload(self, slot, parts):
        if slot not in ('faces', 'tex'):
            return
        vp = self.vp
        stride = 6 if slot == 'faces' else 5
        registry = self.build_registry()
        vao = vp._faces_vao if slot == 'faces' else vp._tex_faces_vao
        vao.bind()
        if not registry:
            vp._program.disableAttributeArray(7); vp._program.disableAttributeArray(8)
            vao.release()
            return
        vbo = self.buffers.get(slot)
        if vbo is None:
            vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            vbo.setUsagePattern(QOpenGLBuffer.DynamicDraw); vbo.create()
            self.buffers[slot] = vbo
        aux = [self.auxiliary(raw, stride, registry) for raw in parts]
        # Reuse the host's incremental upload and size accounting.
        self.original_upload(vbo, 'it_normals_'+slot, aux, empty=72)
        vbo.bind()
        vp._program.enableAttributeArray(7); vp._program.setAttributeBuffer(7, 0x1406, 0, 3, 24)
        vp._program.enableAttributeArray(8); vp._program.setAttributeBuffer(8, 0x1406, 12, 3, 24)
        vbo.release(); vao.release()


def install(vp):
    install_serialization()
    if hasattr(vp, '_it_smooth_renderer'):
        return vp._it_smooth_renderer
    from views.viewport import SHADER_DIR
    renderer = SmoothRenderer(vp)
    vertex, fragment = shader_sources((SHADER_DIR/'basic.vert').read_text(encoding='utf-8'), (SHADER_DIR/'basic.frag').read_text(encoding='utf-8'))
    def compile_program():
        program = QOpenGLShaderProgram(vp)
        if not (program.addShaderFromSourceCode(QOpenGLShader.Vertex, vertex)
                and program.addShaderFromSourceCode(QOpenGLShader.Fragment, fragment)
                and program.link()):
            raise RuntimeError('平滑法線著色器編譯失敗：'+program.log())
        return program
    renderer.original_upload = vp._upload_vbo
    def upload(vbo, slot, parts, empty=24):
        result = renderer.original_upload(vbo, slot, parts, empty)
        renderer.upload(slot, parts)
        return result
    old_eligible = vp._instanced_eligible
    old_sync = vp._sync_edges
    def sync_edges():
        renderer.sync_routes()
        return old_sync()
    def eligible(group):
        return False if renderer.enabled(group.mesh) else old_eligible(group)
    old_forget = vp._forget_context_objects
    def forget_context():
        renderer.buffers.clear()
        for key in ('it_normals_faces', 'it_normals_tex'):
            getattr(vp, '_vbo_parts', {}).pop(key, None)
        old_forget()
    # Existing windows have already compiled their program before extensions.
    # Attribute locations 0..6 are unchanged, so their VAOs remain valid.
    old_program = getattr(vp, '_program', None)
    if old_program is not None:
        vp.makeCurrent()
        program = compile_program()
        names = re.findall(r'uniform\s+\w+\s+(\w+)\s*;', vertex+'\n'+fragment)
        locations = {old_program.uniformLocation(n): program.uniformLocation(n) for n in names}
        for attr, value in list(vp.__dict__.items()):
            if attr.startswith('_loc_') and not attr.startswith('_loc_d_') and attr not in ('_loc_pos','_loc_uv','_loc_vcolor','_loc_inst'):
                if isinstance(value, int) and value in locations:
                    setattr(vp, attr, locations[value])
        vp._program = program
        program.bind(); program.setUniformValue(program.uniformLocation('u_light_fixed'), vp._LIGHT)
        program.release(); old_program.deleteLater()
        vp.doneCurrent()
    vp._compile_program = compile_program
    vp._upload_vbo = upload
    vp._instanced_eligible = eligible
    vp._sync_edges = sync_edges
    vp._forget_context_objects = forget_context
    vp._it_smooth_renderer = renderer
    vp._edges_version = -1
    vp._edges_content_key = None
    vp.scene.version += 1
    vp.update()
    return renderer
