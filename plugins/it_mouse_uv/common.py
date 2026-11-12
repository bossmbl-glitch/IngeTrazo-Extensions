"""Selection, independent component copies, and transactional undo."""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from PySide6.QtGui import QVector3D, QMatrix4x4
from core.mesh import Mesh, Face, Edge, Vertex, edge_flags, stamp_edge_flags, face_count
from core.group import Group, transformed_attrs, new_uid
from core.history import Command
from core.texture import face_uv_axes

LIMIT = 150000


def xyz(p):
    return (float(p.x()), float(p.y()), float(p.z()))


def finite(values):
    if not all(math.isfinite(float(x)) for x in values):
        raise ValueError("參數包含無效數值。")


def explicit_texture(tex, normal):
    result = copy.deepcopy(tex)
    if result.get("uvw"):
        return result
    u, uc, v, vc = face_uv_axes(result, normal)
    result["uvw"] = [*xyz(u), uc, *xyz(v), vc]
    return result


def clone_mesh(source, matrix=None, inherited=None):
    """Strict copy: never silently omit a face; preserve holes and edge flags."""
    matrix = matrix or QMatrix4x4()
    result = Mesh()
    for face in source.faces:
        attrs = copy.deepcopy(face.attrs)
        if inherited and not any(k in attrs for k in ("mat", "texture", "color")):
            attrs.update(copy.deepcopy(inherited))
        for side in (attrs, attrs.get("back", {})):
            if side.get("texture"):
                side["texture"] = explicit_texture(side["texture"], face.normal())
        attrs = transformed_attrs(attrs, matrix)
        if attrs.get("back", {}).get("texture"):
            attrs["back"] = transformed_attrs(attrs["back"], matrix)
        new = result.add_face([matrix.map(p) for p in face.vertices],
                              [[matrix.map(p) for p in h] for h in face.holes])
        new.attrs = attrs
        new.interior = face.interior
    for edge in source.edges:
        new = result.add_edge(matrix.map(edge.a), matrix.map(edge.b))
        stamp_edge_flags(new, edge_flags(edge))
    return result


def clone_tree(group, parent=None, inherited=None):
    if group.locked:
        raise ValueError("選取範圍含鎖定群組，請先解鎖。")
    matrix = parent or QMatrix4x4()
    if group.xform is not None:
        matrix = matrix * group.xform
    paint = group.material or inherited
    new = copy.copy(group)
    new.uid = new_uid()  # child copies must not collide with shared siblings' IDs
    new.mesh = clone_mesh(group.mesh, matrix, paint)
    new.children = [clone_tree(child, matrix, paint) for child in group.children]
    new.xform = QMatrix4x4() if new.children else None
    new.axes = QMatrix4x4(matrix)
    new.component = False
    return new


def walk(group):
    yield group
    for child in group.children:
        yield from walk(child)


@dataclass
class Target:
    mesh: Mesh
    faces: list
    vertices: set
    group: object = None


def selected_roots(scene):
    roots = [g for g in scene.groups if g in scene.selection]
    descendants = {c for g in roots for c in list(walk(g))[1:]}
    return [g for g in roots if g not in descendants]


def get_targets(scene, connected=False, detached=False):
    """Groups plus current-context faces. Deforms expand loose connected parts."""
    targets = []
    roots = selected_roots(scene)
    if sum(face_count(g.mesh) for root in roots for g in walk(root)) > LIMIT:
        raise ValueError("選取範圍超過 150,000 面，請分批操作。")
    for root in roots:
        if not scene.entity_selectable(root):
            raise ValueError("群組已鎖定或不可編輯。")
        tree = clone_tree(root) if detached else root
        for g in walk(tree):
            if g.locked:
                raise ValueError("選取範圍含鎖定群組，請先解鎖。")
            targets.append(Target(g.mesh, list(g.mesh.faces), set(g.mesh.vertices), g))
    mesh = scene.mesh
    selected_faces = [f for f in mesh.faces if f in scene.selection]
    vertices = {v for f in selected_faces for lp in (f.loop, *f.hole_loops) for v in lp}
    if connected:
        for edge in mesh.edges:
            if edge in scene.selection:
                vertices.update((edge.v0, edge.v1))
        vertices.update(v for v in mesh.vertices if v in scene.selection)
        todo = list(vertices)
        while todo:
            v = todo.pop()
            for edge in v.edges:
                other = edge.other(v)
                if other not in vertices:
                    vertices.add(other)
                    todo.append(other)
        selected_faces = [f for f in mesh.faces if any(v in vertices for v in f.loop)]
    if vertices or selected_faces:
        if any(not scene.entity_selectable(f) for f in selected_faces):
            raise ValueError("連通幾何含鎖定或隱藏的面；請解除鎖定／顯示後再操作。")
        targets.append(Target(mesh, selected_faces, vertices))
    if not targets or not any(t.vertices for t in targets):
        raise ValueError("請先選取面或群組。變形也可選取邊，會包含整個相連物件。")
    if sum(len(t.faces) for t in targets) > LIMIT:
        raise ValueError("選取範圍超過 150,000 面，請分批操作。")
    return targets


FIELDS = ("mesh", "children", "xform", "axes", "component")


class EditCommand(Command):
    """Keep original group references intact; undo restores exact mesh identities."""
    def __init__(self, scene, operation, connected=False, label="UV 與變形"):
        self.operation = operation
        self.connected = connected
        self.label = label
        self.original_selection = set(scene.selection)
        self.roots = selected_roots(scene)
        self.before = self.after = None

    def capture(self, scene):
        return (scene.mesh.capture_state(),
                [(g, tuple(getattr(g, k) for k in FIELDS)) for g in self.roots],
                set(scene.selection))

    def restore(self, scene, state):
        mesh, groups, selection = state
        scene.mesh.restore_state(mesh)
        for group, values in groups:
            for key, value in zip(FIELDS, values):
                setattr(group, key, value)
        scene.selection.clear()
        scene.selection.update(selection)
        scene.version += 1

    def do(self, scene):
        if self.after is not None:
            self.restore(scene, self.after)
            return
        self.before = self.capture(scene)
        try:
            # Copy the entire selected subtree before touching any original mesh.
            copies = [(root, clone_tree(root)) for root in self.roots]
            for root, new in copies:
                for key in FIELDS:
                    setattr(root, key, getattr(new, key))
            targets = get_targets(scene, self.connected)
            self.operation(targets)
            for target in targets:
                target.mesh._chunk_dirty = True
                target.mesh._mut_serial += 1
            valid = set(scene.mesh.faces) | set(scene.mesh.edges) | set(scene.mesh.vertices) | set(scene.groups)
            scene.selection.intersection_update(valid)
            # Keep the resulting loose faces selected after triangulation.
            for target in targets:
                if target.group is None:
                    scene.selection.update(f for f in target.faces if f in scene.mesh.faces)
            scene.version += 1
            self.after = self.capture(scene)
        except BaseException:
            self.restore(scene, self.before)
            raise

    def undo(self, scene):
        self.restore(scene, self.before)


def execute(app, operation, connected=False, label="UV 與變形"):
    checked = get_targets(app.scene, connected, detached=True)
    if not connected and not any(t.faces for t in checked):
        raise ValueError("UV 工具需要選取至少一個面。")
    cmd = EditCommand(app.scene, operation, connected, label)
    app.viewport.history.execute(cmd)
    error = app.viewport.history.last_error or app.viewport.history.last_rejected
    if error:
        raise ValueError(error)
    app.viewport.notify_scene_changed()


def bounds(targets):
    points = [xyz(v.position) for t in targets for v in t.vertices]
    return ([min(p[i] for p in points) for i in range(3)],
            [max(p[i] for p in points) for i in range(3)])
