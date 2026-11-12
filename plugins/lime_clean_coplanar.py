# SPDX-License-Identifier: MIT
"""One-click, conservative coplanar seam cleanup for IngeTrazo 0.6.0.

Version 1.0.2. No geometry is hidden, decimated, exploded or moved.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from math import sqrt

from core.group import Group
from core.mesh import Edge, Face
from core.history import Command, _dirty_group_chunks
from core.layers import DEFAULT_LAYER

VERSION = '1.0.2'


def _face_attrs(face):
    # Explicit default layer and an absent tag mean exactly the same thing.
    attrs = dict(face.attrs)
    if not attrs.get('layer') or attrs.get('layer') == DEFAULT_LAYER:
        attrs.pop('layer', None)
    return attrs


def _xyz(p):
    return (float(p.x()), float(p.y()), float(p.z()))


def _dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def _normal(face):
    # Double precision Newell vector, including large-coordinate scenes.
    origin = _xyz(face.loop[0].position)
    pts = [tuple(a-b for a, b in zip(_xyz(v.position), origin))
           for v in face.loop]
    n = [0., 0., 0.]
    for a, b in zip(pts, pts[1:] + pts[:1]):
        n[0] += (a[1]-b[1]) * (a[2]+b[2])
        n[1] += (a[2]-b[2]) * (a[0]+b[0])
        n[2] += (a[0]-b[0]) * (a[1]+b[1])
    length = sqrt(_dot(n, n))
    return tuple(x/length for x in n) if length > 1e-15 else None


def _same_plane(seed, candidate, normal):
    other = _normal(candidate)
    if other is None or _dot(normal, other) < 1. - 1e-10:
        return False  # Creases, curves and reversed faces stay intact.
    origin = _xyz(seed.loop[0].position)
    for loop in (candidate.loop, *candidate.hole_loops):
        for vertex in loop:
            delta = tuple(a-b for a, b in zip(_xyz(vertex.position), origin))
            if abs(_dot(normal, delta)) > 1e-6:  # metres, not an angle slider
                return False
    return True


def _boundary(faces):
    counts = defaultdict(int)
    for face in faces:
        for loop in (face.loop, *face.hole_loops):
            for a, b in zip(loop, loop[1:] + loop[:1]):
                counts[frozenset((_xyz(a.position), _xyz(b.position)))] += 1
    return {key for key, count in counts.items() if count == 1}


def _area(faces):
    from core.bim import face_net_area
    return sum(face_net_area(face) for face in faces)


def _regions(mesh, scene, face_scope=None, edge_scope=None, ignored=()):
    ignored = set(ignored)
    allowed = {f for f in mesh.faces
               if (face_scope is None or f in face_scope)
               and f not in ignored
               and scene.entity_visible(f) and not scene.entity_locked(f)
               and not f.interior}
    links = defaultdict(list)
    removable = set()
    incident = defaultdict(list)
    for edge in mesh.edges:
        neighbors = [f for f in edge.faces if f not in ignored]
        for face in neighbors:
            incident[face].append(edge)
        if len(neighbors) != 2 or (edge_scope is not None and edge not in edge_scope):
            continue
        a, b = neighbors
        if a is b or a not in allowed or b not in allowed:
            continue
        if _face_attrs(a) != _face_attrs(b):
            continue  # Includes colour, back face, material, UV, layer, BIM.
        if (getattr(edge, 'layer', None) not in (None, '', DEFAULT_LAYER, a.attrs.get('layer'))
                or scene.entity_locked(edge)):
            continue
        n = _normal(a)
        if n is None or not _same_plane(a, b, n):
            continue
        removable.add(edge)
        links[a].append(b)
        links[b].append(a)

    seen = set()
    result = []
    order = {face: i for i, face in enumerate(mesh.faces)}
    for seed in mesh.faces:
        if seed not in links or seed in seen:
            continue
        normal = _normal(seed)
        region, todo = set(), [seed]
        while todo:
            face = todo.pop()
            if face in seen or not _same_plane(seed, face, normal):
                continue
            seen.add(face)
            region.add(face)
            todo.extend(links[face])
        if len(region) < 2:
            continue
        # Alternate paths must not dissolve a protected seam or T-junction.
        nearby = {e for face in region for e in incident[face]}
        if any(sum(f in region for f in e.faces) >= 2 and e not in removable
               for e in nearby):
            continue
        result.append(sorted(region, key=order.__getitem__))
    return result


def _opposite_sides(a, b, edge):
    normal = _normal(a)
    p, q = _xyz(edge.v0.position), _xyz(edge.v1.position)
    direction = tuple(y-x for x, y in zip(p, q))
    transverse = (normal[1]*direction[2]-normal[2]*direction[1],
                  normal[2]*direction[0]-normal[0]*direction[2],
                  normal[0]*direction[1]-normal[1]*direction[0])
    scale = sqrt(_dot(transverse, transverse))
    if scale <= 1e-12:
        return False
    def side(face):
        values = [_dot(transverse, tuple(x-y for x, y in zip(_xyz(v.position), p))) / scale
                  for v in face.loop]
        signs = {1 if x > 1e-6 else -1 for x in values if abs(x) > 1e-6}
        return next(iter(signs)) if len(signs) == 1 else 0
    return side(a) * side(b) == -1


def _partitions(mesh, scene, face_scope=None, edge_scope=None):
    """Infer buried join sheets from unambiguous planar exterior continuations.

    Only three geometric sheets at an edge give evidence: two continue the
    exterior on opposite sides, the third is a potential buried partition.
    Shared partition junctions are validated together, never guessed locally.
    """
    by_shape = defaultdict(list)
    incident = defaultdict(set)
    face_key = {}
    for face in mesh.faces:
        shape = frozenset(frozenset((_xyz(a.position), _xyz(b.position)))
                          for loop in (face.loop, *face.hole_loops)
                          for a, b in zip(loop, loop[1:] + loop[:1]))
        by_shape[shape].append(face)
        face_key[face] = shape
    for edge in mesh.edges:
        for face in set(edge.faces):
            incident[face_key[face]].add(edge)

    suspects, exterior = set(), set()
    for edge in mesh.edges:
        shapes = list(dict.fromkeys(face_key[f] for f in edge.faces))
        if len(shapes) != 3:
            continue
        pairs = []
        for i, ka in enumerate(shapes):
            for kb in shapes[i+1:]:
                # An exterior sheet must not be a coincident doubled surface.
                if len(by_shape[ka]) != 1 or len(by_shape[kb]) != 1:
                    continue
                a, b = by_shape[ka][0], by_shape[kb][0]
                n = _normal(a)
                if (n and _same_plane(a,b,n) and _face_attrs(a)==_face_attrs(b)
                        and _opposite_sides(a,b,edge)):
                    pairs.append((ka,kb))
        if len(pairs) == 1:
            ka,kb = pairs[0]
            exterior.update((ka,kb))
            suspects.update(k for k in shapes if k not in (ka,kb))

    candidates = suspects - exterior
    candidates = {k for k in candidates
                  if len(by_shape[k]) <= 2 and not by_shape[k][0].hole_loops
                  and all((face_scope is None or f in face_scope)
                          and scene.entity_visible(f) and not scene.entity_locked(f)
                          and _face_attrs(f)==_face_attrs(by_shape[k][0])
                          for f in by_shape[k])}

    # If rejecting one join sheet invalidates a neighbor, reject that neighbor
    # too. This leaves complete original topology at every unresolved junction.
    changed = True
    while changed:
        rejected = set()
        removed = {f for k in candidates for f in by_shape[k]}
        for key in candidates:
            face = by_shape[key][0]
            n = _normal(face)
            edges = incident[key]
            if n is None or len(edges) != len(face.loop):
                rejected.add(key)
                continue
            for edge in edges:
                outside = list(dict.fromkeys(f for f in edge.faces if f not in removed))
                if (len(outside) != 2 or scene.entity_locked(edge)
                        or (edge_scope is not None and edge not in edge_scope)
                        or any(not scene.entity_visible(f) or scene.entity_locked(f)
                               or _face_attrs(f) != _face_attrs(face)
                               or (face_scope is not None and f not in face_scope)
                               for f in outside)
                        or getattr(edge,'layer',None) not in
                           (None,'',DEFAULT_LAYER,face.attrs.get('layer'))):
                    rejected.add(key)
                    break
                a,b = outside
                an = _normal(a)
                bn = _normal(b)
                if an is None or bn is None:
                    rejected.add(key)
                    break
                if _same_plane(a,b,an):
                    if not _opposite_sides(a,b,edge) or abs(_dot(n,an)) > 1.-1e-8:
                        rejected.add(key)
                        break
                else:
                    # Two buried joins can meet at the model's real L corner.
                    removed_sheets = {face_key[f] for f in edge.faces if f in removed}
                    if len(removed_sheets)<2 or abs(_dot(an,bn))>1.-1e-8:
                        rejected.add(key)
                        break
        changed = bool(rejected)
        candidates.difference_update(rejected)
    return [f for f in mesh.faces if face_key[f] in candidates]


def _selection_jobs(scene):
    """Only selected containers/geometry. Shared unselected instances are skipped."""
    selected = {e for e in scene.selection if isinstance(e, (Group, Face, Edge))}
    requested, blocked = {}, set()

    def visit(group, inherited=False):
        active = inherited or group in selected
        active = (active and scene.entity_visible(group)
                  and not scene.entity_locked(group))
        if active:
            requested[id(group.mesh)] = (group.mesh, None, None)
        elif group is not scene.edit_group:
            blocked.add(id(group.mesh))
        for child in group.children or ():
            visit(child, active)

    for group in scene.groups:
        visit(group)
    skipped = len(set(requested) & blocked)
    jobs = [job for key, job in requested.items() if key not in blocked]
    faces = {f for f in scene.mesh.faces if f in selected}
    edges = {e for e in scene.mesh.edges if e in selected}
    if faces or edges:
        # One selected face is enough: target only seams incident to that face,
        # without spreading the cleanup throughout unrelated loose geometry.
        target_edges = edges | {e for e in scene.mesh.edges if any(f in faces for f in e.faces)}
        scope = faces | {f for e in target_edges for f in e.faces}
        if id(scene.mesh) not in blocked:
            if id(scene.mesh) not in requested:
                jobs.append((scene.mesh, scope, target_edges))
        else:
            skipped += 1
    # A component open for editing shares changes back on exit; require making
    # it unique first so a cleanup never silently changes unselected instances.
    if any(level.get('definition') is not None
           for level in getattr(scene, '_edit_stack', ())):
        return [], max(1, skipped)
    return jobs, skipped


class CleanCoplanarCommand(Command):
    def __init__(self, plans, partitions=None):
        self.plans = plans
        self.partitions = partitions or {}
        self.partitions_removed = 0
        self.before = None
        self.after = None
        self.previous_selection = None
        self.result_selection = None
        self.edges_removed = 0
        self.faces_removed = 0
        self.regions_merged = 0

    def _touch(self, scene):
        _dirty_group_chunks(scene)
        scene.version += 1

    def do(self, scene):
        if self.after is not None:
            for (mesh, _), state in zip(self.plans, self.after):
                mesh.restore_state(state)
            scene.selection.clear()
            scene.selection.update(self.result_selection)
            self._touch(scene)
            return
        self.before = [mesh.capture_state() for mesh, _ in self.plans]
        self.previous_selection = set(scene.selection)
        selected = set(scene.selection)
        try:
            for mesh, regions in self.plans:
                edges_before, faces_before = len(mesh.edges), len(mesh.faces)
                for partition in self.partitions.get(id(mesh), ()):
                    mesh.remove_face(partition)
                    self.partitions_removed += 1
                for region in regions:
                    boundary, area = _boundary(region), _area(region)
                    if area <= 1e-12:
                        continue
                    attrs = deepcopy(region[0].attrs)
                    merged = mesh.dissolve_coplanar_region(region)
                    if merged is None:
                        continue  # Ambiguous outline: leave this region as is.
                    merged.attrs = attrs
                    # A hole or outside contour must never be filled/changed.
                    if (_boundary([merged]) != boundary
                            or abs(_area([merged])-area) > max(1e-9, area*1e-6)):
                        raise ValueError('清理後外框或面積不一致，已還原全部操作。')
                    self.regions_merged += 1
                    if any(f in selected for f in region):
                        selected.add(merged)
                self.edges_removed += edges_before - len(mesh.edges)
                self.faces_removed += faces_before - len(mesh.faces)
            alive = set(scene.mesh.edges) | set(scene.mesh.faces)
            self.result_selection = {e for e in selected
                                     if not isinstance(e, (Edge, Face)) or e in alive}
            scene.selection.clear()
            scene.selection.update(self.result_selection)
            self.after = [mesh.capture_state() for mesh, _ in self.plans]
        except Exception:
            # History snapshots only its active mesh. Roll back every other
            # selected group too, before allowing History to log the failure.
            for (mesh, _), state in zip(self.plans, self.before):
                mesh.restore_state(state)
            scene.selection.clear()
            scene.selection.update(self.previous_selection)
            raise
        self._touch(scene)

    def undo(self, scene):
        for (mesh, _), state in zip(self.plans, self.before):
            mesh.restore_state(state)
        scene.selection.clear()
        scene.selection.update(self.previous_selection)
        self._touch(scene)


def clean(viewport):
    scene = viewport.scene
    if not scene.selection:
        message = '尚未選取模型。請先選取群組、元件、面或共面分隔線，再按清理。'
        viewport.flash_status(message, 5000)
        return message
    jobs, skipped = _selection_jobs(scene)
    plans = []
    partitions = {}
    for mesh, face_scope, edge_scope in jobs:
        internal = _partitions(mesh, scene, face_scope, edge_scope)
        regions = _regions(mesh, scene, face_scope, edge_scope, ignored=internal)
        if regions or internal:
            plans.append((mesh, regions))
            partitions[id(mesh)] = internal
    if not plans:
        message = '沒有可安全移除的共面線。'
        if skipped:
            message += ' 共用元件請先設為獨立元件，再整個選取清理。'
        viewport.flash_status(message, 6000)
        return message
    command = CleanCoplanarCommand(plans, partitions)
    viewport.history.execute(command)
    if getattr(viewport.history, 'last_error', None):
        message = '清理失敗，已還原。' + viewport.history.last_error
        viewport.flash_status(message, 7000)
        return message
    viewport.notify_scene_changed()
    message = f'清理完成：移除 {command.edges_removed} 條共面線，減少 {command.faces_removed} 個面。Ctrl+Z 可復原。'
    if command.partitions_removed:
        message += f' 已移除 {command.partitions_removed} 個封閉接合內面。'
    if skipped:
        message += f' 已略過 {skipped} 個共用網格。'
    viewport.flash_status(message, 7000)
    return message


def _diagnostics(viewport):
    scene = viewport.scene
    jobs, skipped = _selection_jobs(scene)
    meshes = [m for m, _, _ in jobs]
    lines = [f'套件版本：{VERSION}', f'選取項目：{len(scene.selection)}',
             f'可處理網格：{len(meshes)}', f'略過共用網格：{skipped}',
             f'面數：{sum(len(m.faces) for m in meshes)}',
             f'線數：{sum(len(m.edges) for m in meshes)}']
    pairs = crease = attrs = junctions = 0
    for mesh in meshes:
        for edge in mesh.edges:
            if len(edge.faces) > 2:
                junctions += 1
            if len(edge.faces) == 2 and edge.faces[0] is not edge.faces[1]:
                a, b = edge.faces
                n = _normal(a)
                if n is None or not _same_plane(a, b, n):
                    crease += 1
                elif _face_attrs(a) != _face_attrs(b):
                    attrs += 1
                else:
                    pairs += 1
    lines += [f'同平面且同屬性的相鄰面交界：{pairs}',
              f'材質／貼圖／其他面屬性不同的共面交界：{attrs}',
              f'轉角或非共面交界：{crease}', f'三面以上交界：{junctions}']
    lines.append(f'可確認的封閉接合內面：{sum(len(_partitions(m, scene, f, e)) for m, f, e in jobs)}')
    return '\n'.join(lines)


def _run_with_report(app):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox
    import traceback
    viewport = app.viewport
    try:
        # Capture before cleanup so counts describe why an operation ran/skipped.
        details = _diagnostics(viewport)
        message = clean(viewport)
    except Exception:
        message = '清理未能完成。請將「詳細資料」內容提供給我，以便修正。'
        details = traceback.format_exc()
    old = getattr(app.window, '_lime_cleanup_report', None)
    if old is not None:
        old.close()
    report = QMessageBox(app.window)
    report.setWindowTitle(f'模型清理 v{VERSION} — 執行結果')
    report.setIcon(QMessageBox.Information)
    report.setText(message)
    report.setDetailedText(details)
    report.setStandardButtons(QMessageBox.Ok)
    report.setWindowModality(Qt.NonModal)
    report.setAttribute(Qt.WA_DeleteOnClose, False)
    app.window._lime_cleanup_report = report
    report.show()


def setup(app):
    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtWidgets import QToolBar
    action = app.add_menu_action('一鍵清理共面線 v1.0.2', lambda: _run_with_report(app),
                                 tip='清理選取模型內的共面分隔線，保留外框、轉角與材質交界。')
    if action is None:
        return
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
    <rect x="4" y="4" width="56" height="56" rx="12" fill="#e8f3f4"/>
    <path d="M17 20H35V45H17Z" fill="#fff" stroke="#27556b" stroke-width="3"/>
    <path d="M17 33H35M26 20V45" stroke="#92adb7" stroke-dasharray="3 3" stroke-width="2"/>
    <path d="M32 40L49 18" stroke="#e99c35" stroke-width="6" stroke-linecap="round"/>
    <path d="M27 39L36 46L28 55L17 47Z" fill="#208f86"/>
    </svg>'''
    pixmap = QPixmap()
    pixmap.loadFromData(QByteArray(svg), 'SVG')
    action.setIcon(QIcon(pixmap))
    action.setAutoRepeat(False)
    toolbar = QToolBar('模型清理', app.window)
    toolbar.setObjectName('lime_clean_coplanar_toolbar')
    toolbar.addAction(action)
    app.window.addToolBar(toolbar)
