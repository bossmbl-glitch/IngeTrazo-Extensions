# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime hotfix for IngeTrazo 0.6.0: move independent flat faces onto faces.

Uses upstream's planar arrangement and snapshot history. Deliberate partial
vertex edits, nonplanar geometry and different edit contexts retain the native
Move implementation. It does not repair old documents on load.
"""
from functools import wraps

__version__ = '0.1.0'
PLUGIN_NAME = 'BUG_FIX｜共平面移動切面修正'
_TAG = '_lime_coplanar_move_fix'
_TOL = 1e-4


def _loops(face):
    return [face.loop, *face.hole_loops]


def _plan(command, scene):
    """Non-mutating: return a local planar rebuild only for whole flat islands."""
    from PySide6.QtGui import QVector3D as V
    from core.topology import _key
    from core.arrangement import plane_basis
    mesh = scene.mesh
    keys = {_key(p) for p in command.src}
    moving = {id(v) for v in mesh.vertices if _key(v.position) in keys}
    if not moving or command.delta.lengthSquared() < 1e-18:
        return None
    carried = []
    for face in mesh.faces:
        ids = {id(v) for lp in _loops(face) for v in lp}
        if not (ids & moving):
            continue
        if not ids.issubset(moving):
            return None  # preserve ordinary stretching/autofold and its guard
        carried.append(face)
    if not carried:
        return None
    # Moving a solid or folded surface is not this operation.
    normal = carried[0].normal()
    if normal.lengthSquared() < .5:
        return None
    origin = V(carried[0].vertices[0])
    if any(abs(V.dotProduct(v.position-origin,normal)) > _TOL
           for f in carried for lp in _loops(f) for v in lp):
        return None
    # Never let the special case carry loose edges/points not belonging to
    # the complete faces. Those need the original multi-selection semantics.
    face_ids = {id(v) for f in carried for lp in _loops(f) for v in lp}
    if moving != face_ids:
        return None
    if any(not e.faces for v in mesh.vertices if id(v) in moving for e in v.edges):
        return None
    landed = origin + command.delta
    u,w = plane_basis(normal)
    def box(points):
        pts = [(V.dotProduct(p-landed,u),V.dotProduct(p-landed,w)) for p in points]
        return min(p[0] for p in pts),min(p[1] for p in pts),max(p[0] for p in pts),max(p[1] for p in pts)
    boxes = [box([p+command.delta for p in f.vertices]) for f in carried]
    def overlaps(a,b):
        # A mere touch outside is not overlapping area; the native weld is fine.
        return min(a[2],b[2])-max(a[0],b[0]) > _TOL and min(a[3],b[3])-max(a[1],b[1]) > _TOL
    targets = []
    for face in mesh.faces:
        if face in carried:
            continue
        if any(abs(V.dotProduct(v.position-landed,normal)) > _TOL
               for lp in _loops(face) for v in lp):
            continue
        if any(overlaps(b,box(face.vertices)) for b in boxes):
            targets.append(face)
    if not targets:
        return None
    return carried, targets, landed, normal


def _apply(command, scene, plan):
    from PySide6.QtGui import QMatrix4x4
    from core.topology import _key
    from core.history import translate_points, RebuildPlaneFacesCommand, run_stitch
    from core.group import _remap_uvws
    carried, targets, origin, normal = plan
    mesh = scene.mesh
    command._before = mesh.capture_state()
    selection = set(scene.selection)
    try:
        translate_points(scene,{_key(p) for p in command.src},command.delta)
        if command.carry_textures:
            xf = QMatrix4x4(); xf.translate(command.delta)
            _remap_uvws(mesh,xf,carried)
        # Moved faces have paint precedence in overlapping cells. Arrangement
        # preserves every input border, including the intersection and holes;
        # it emits one face per covered cell, never two overlapping faces.
        before_faces = set(mesh.faces)
        rebuild = RebuildPlaneFacesCommand(origin,normal,faces=carried+targets)
        rebuild.take_snapshot = False
        rebuild.do(scene)
        new_faces = set(mesh.faces) - before_faces
        seeds = {_key(v.position) for f in new_faces
                 for lp in _loops(f) for v in lp}
        run_stitch(mesh,seeds,coplanar_merge=False,dedupe=False)
        command.welded = True
        command._after = mesh.capture_state()
        scene.selection.clear()
        scene.version += 1
    except Exception:
        mesh.restore_state(command._before)
        command._after = None
        scene.selection.clear();scene.selection.update(selection)
        scene.version += 1
        raise


def install_patch(command_class=None):
    if command_class is None:
        from core.history import MoveVerticesCommand
        command_class = MoveVerticesCommand
    original = command_class.do
    if getattr(original,_TAG,None) == __version__:
        return False
    @wraps(original)
    def do(self,scene):
        if self._after is not None:  # native snapshot redo; do not recut
            return original(self,scene)
        plan = _plan(self,scene)
        if plan is None:
            return original(self,scene)
        return _apply(self,scene,plan)
    setattr(do,_TAG,__version__)
    command_class.do = do
    return True


def setup(app):
    install_patch()
    def info():
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(app.window,PLUGIN_NAME,
            '試用版 v'+__version__+'\n適用 IngeTrazo 0.6.0。\n'
            '同一編輯層級內，獨立平面移動貼面時自動切面。\n'
            '支援內含、貼邊、跨框、部分重疊與完全重合。\n'
            '舊檔壞面不會自動修復；移除後須重新啟動。')
    app.add_menu_action(PLUGIN_NAME+'｜版本資訊',info,
                        tip='查看共平面移動切面修正版資訊')
