"""Flatten selected imported geometry; IngeTrazo 0.5.11, GPL-3.0-or-later."""
from __future__ import annotations

import copy
import math

from PySide6.QtGui import QMatrix4x4, QVector3D
from core.group import Group, transformed_attrs
from core.history import Command, CommandRejected
from core.mesh import Mesh, Vertex, Edge, Face


def point_key(p):
    return (p.x(), p.y(), p.z())


def edge_key(a, b):
    return tuple(sorted((point_key(a), point_key(b))))


def walk(group):
    yield group
    for child in group.children or ():
        yield from walk(child)


def clone_mesh(source, matrix):
    """Independent topology and paint; retain layers, holes and edge flags."""
    result = Mesh()
    # Build in the original coordinates first. Flattening is a separate step
    # so a singular projection never reaches the texture transform routine.
    for f in source.faces:
        nf = result.add_face(f.vertices, f.holes or None)
        nf.attrs = copy.deepcopy(f.attrs)
        nf.interior = f.interior
    for e in source.edges:
        ne = result.add_edge(e.a, e.b)
        for attr in ("soft", "hidden", "layer", "curve"):
            setattr(ne, attr, getattr(e, attr))
    for v in list(result.vertices):
        p = matrix.map(v.position)
        if not all(math.isfinite(x) for x in point_key(p)):
            raise ValueError("圖面包含無效座標，無法壓平。")
        result.place_vertex(v, p)
    for f in result.faces:
        f.attrs = transformed_attrs(f.attrs, matrix)
    return result


def flatten_mesh(mesh, vertices):
    """Set only target Z to zero; weld exact coincidences, preserving XY."""
    targets = set(vertices)
    changed = [v for v in targets if v.position.z() != 0.0]
    if not changed:
        return 0
    affected = {f for v in changed for e in v.edges for f in e.faces}
    for v in changed:
        p = v.position
        if not all(math.isfinite(x) for x in point_key(p)):
            raise ValueError("圖面包含無效座標，無法壓平。")
        mesh.place_vertex(v, QVector3D(p.x(), p.y(), 0.0))

    # The native tolerance weld can move XY by 0.1 mm. Use its topological
    # primitive only for EXACT coordinate matches, limited to target points.
    touched = {point_key(v.position) for v in changed}
    flags = {}
    for e in mesh.edges:
        key = edge_key(e.a, e.b)
        flags.setdefault(key, (e.soft, e.hidden, e.layer, e.curve))
    buckets = {}
    for v in list(mesh.vertices):
        key = point_key(v.position)
        if key in touched:
            buckets.setdefault(key, []).append(v)
    for vertices_at_point in buckets.values():
        keep = vertices_at_point[0]
        for duplicate in vertices_at_point[1:]:
            affected.update(duplicate.faces())
            mesh._weld_into(keep, duplicate)
    for f in list(mesh.faces):
        if f in affected and f.area() <= 1e-12:
            mesh.remove_face(f)
    for e in mesh.edges:
        values = flags.get(edge_key(e.a, e.b))
        if values is not None:
            e.soft, e.hidden, e.layer, e.curve = values

    # Remove duplicate projected surfaces only when a changed face is involved.
    # Include holes in the key: an opening must never be replaced by a solid face.
    def ring_key(loop):
        return frozenset(frozenset((id(v), id(loop[(i + 1) % len(loop)])))
                         for i, v in enumerate(loop))
    seen = {}
    for f in list(mesh.faces):
        key = (ring_key(f.loop), frozenset(ring_key(h) for h in f.hole_loops))
        previous = seen.get(key)
        if previous is not None and (f in affected or previous in affected):
            mesh.remove_face(f)
        else:
            seen[key] = f
    return len(changed)


def flattened_tree(group, parent=None):
    parent = QMatrix4x4() if parent is None else parent
    world = parent * group.xform if group.xform is not None else parent
    new = copy.copy(group)
    new.mesh = clone_mesh(group.mesh, world)
    count = flatten_mesh(new.mesh, new.mesh.vertices)
    new.children = []
    for child in group.children or ():
        flat_child, n = flattened_tree(child, world)
        new.children.append(flat_child)
        count += n
    # All resulting meshes are world-space. Keep each container, with an
    # invertible identity matrix, rather than leaving a zero-scale instance.
    new.xform = QMatrix4x4() if new.children else None
    new.axes = None
    new.component = False
    new.billboard = False
    return new, count


_FIELDS = ("mesh", "children", "xform", "axes", "component", "billboard")


def group_state(group):
    return {key: getattr(group, key) for key in _FIELDS}


def restore_group(group, state):
    for key, value in state.items():
        setattr(group, key, value)


def selection_targets(scene):
    if scene.edit_group is not None:
        raise CommandRejected("請先退出群組編輯，再選取整個圖面後壓平。")
    groups = []
    vertices = set()
    loose_edges, loose_faces = set(scene.mesh.edges), set(scene.mesh.faces)
    for entity in scene.selection:
        if isinstance(entity, Group) and entity in scene.groups:
            for node in walk(entity):
                if getattr(node, "locked", False) or scene._layer_state(node)[1]:
                    raise CommandRejected("選取圖面內有鎖定的群組或圖層，請先解鎖。")
                if any(scene._layer_state(e)[1] for e in node.mesh.edges):
                    raise CommandRejected("選取圖面內有鎖定的圖層，請先解鎖。")
                if any(scene._layer_state(f)[1] for f in node.mesh.faces):
                    raise CommandRejected("選取圖面內有鎖定的圖層，請先解鎖。")
            groups.append(entity)
        elif isinstance(entity, Edge) and entity in loose_edges:
            vertices.update((entity.v0, entity.v1))
        elif isinstance(entity, Face) and entity in loose_faces:
            vertices.update(entity.loop)
            for hole in entity.hole_loops:
                vertices.update(hole)
        elif isinstance(entity, Vertex) and entity in scene.mesh.vertices:
            vertices.add(entity)
    if not groups and not vertices:
        raise CommandRejected("請先選取要壓平的圖面群組、線條或面。")
    # Connected geometry follows a shared vertex. Honour locks on it too.
    for v in vertices:
        if any(scene._layer_state(e)[1] for e in v.edges):
            raise CommandRejected("選取線條連接到鎖定的圖層，請先解鎖。")
        if any(scene._layer_state(f)[1] for f in v.faces()):
            raise CommandRejected("選取圖面連接到鎖定的圖層，請先解鎖。")
    return groups, vertices


class FlattenPlanCommand(Command):
    """One transaction with independent group meshes and identity-safe undo."""
    def __init__(self):
        self.prepared = False
        self.changed_vertices = 0
        self.group_changes = []
        self.before_mesh = self.after_mesh = None

    def do(self, scene):
        if self.prepared:
            self._apply(scene, after=True)
            return
        groups, vertices = selection_targets(scene)
        self.before_selection = set(scene.selection)
        # Prepare every group off-document. Failure here cannot alter a model.
        changes = []
        count = 0
        for g in groups:
            new, n = flattened_tree(g)
            if n:
                changes.append((g, group_state(g), group_state(new)))
                count += n
        self.group_changes = changes
        if vertices:
            self.before_mesh = scene.mesh.capture_state()
        try:
            if vertices:
                count += flatten_mesh(scene.mesh, vertices)
                self.after_mesh = scene.mesh.capture_state()
            if not count:
                raise CommandRejected("選取的圖面已經位於 Z＝0，無需壓平。")
            for g, before, after in changes:
                restore_group(g, after)
            live = set(scene.mesh.edges) | set(scene.mesh.faces) | set(scene.mesh.vertices) | set(scene.groups)
            self.after_selection = {e for e in self.before_selection if e in live}
            scene.selection.clear()
            scene.selection.update(self.after_selection)
            self.changed_vertices = count
            self.prepared = True
            scene.version += 1
        except BaseException:
            if self.before_mesh is not None:
                scene.mesh.restore_state(self.before_mesh)
            for g, before, after in changes:
                restore_group(g, before)
            scene.selection.clear()
            scene.selection.update(self.before_selection)
            raise

    def _apply(self, scene, after):
        snap = self.after_mesh if after else self.before_mesh
        if snap is not None:
            scene.mesh.restore_state(snap)
        for g, before_state, after_state in self.group_changes:
            restore_group(g, after_state if after else before_state)
        scene.selection.clear()
        scene.selection.update(self.after_selection if after else self.before_selection)
        scene.version += 1

    def undo(self, scene):
        self._apply(scene, after=False)
