# SPDX-License-Identifier: GPL-3.0-or-later
"""Soften Edges 1.0.0 for IngeTrazo 0.5.11.

Install this single file in Extensions > Open plugins folder and restart.
Geometry stays intact. Edge flags and per-face smoothing are undoable.
The optional viewport adapter smooths opaque colour faces; textured and
transparent faces retain the host's shading. It never changes materials.
"""
from __future__ import annotations

import math
import logging
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QMatrix4x4, QVector3D
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSlider, QDoubleSpinBox, QCheckBox, QPushButton, QMessageBox)
from core.mesh import Edge, Face
from core.group import Group, transformed_mesh
from core.history import Command

VERSION = "1.0.0"
SMOOTH = "soften_edges_smooth"
LOG = logging.getLogger("ingetrazo.plugins.soften_edges")
_MISSING = object()


def touch(mesh):
    mesh._chunk_dirty = True
    mesh._attrs_dirty = True
    mesh._mut_serial = getattr(mesh, "_mut_serial", 0) + 1
    mesh.__dict__.pop("_face_geom_memo", None)


def edge_angle(edge, normals):
    """Oriented face angle; open/non-manifold/degenerate edges are protected."""
    if len(edge.faces) != 2:
        return None
    a, b = edge.faces
    for f in (a, b):
        if f not in normals:
            normals[f] = f.normal()
    na, nb = normals[a], normals[b]
    if na.lengthSquared() < 1e-18 or nb.lengthSquared() < 1e-18:
        return None
    dot = QVector3D.dotProduct(na.normalized(), nb.normalized())
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


def edge_plan(mesh, edges, angle, coplanar, smooth, unsoften=False):
    if not math.isfinite(angle) or not 0 <= angle <= 180:
        raise ValueError("角度必須介於 0–180 度。")
    changes, normals, affected = [], {}, set()
    for e in edges:
        deg = edge_angle(e, normals)
        if deg is None:
            continue
        value = (not unsoften and deg <= angle + 1e-6
                 and (coplanar or deg > 1e-4))
        if e.soft != value:
            changes.append((e, e.soft, value))
        affected.update(e.faces)
    flags = []
    for f in affected:
        old = f.attrs.get(SMOOTH, _MISSING)
        new = bool(smooth and not unsoften)
        if old is _MISSING or old != new:
            flags.append((f, old, new))
    return changes, flags


def group_tree(group, skip_locked=True):
    if skip_locked and getattr(group, "locked", False):
        return
    yield group
    for child in group.children or ():
        yield from group_tree(child, skip_locked)


class SoftenCommand(Command):
    """Prepare everything before mutation; swap only private group meshes.

    Unselected component instances keep their prototype. Children, transforms,
    layer assignments, materials, and entity coordinates remain unchanged.
    """
    keeps_paint = True

    def __init__(self, scene, angle=20.0, coplanar=False, smooth=True,
                 unsoften=False):
        self.swaps, self.edits, self.face_flags, self.meshes = [], [], [], []
        self.changed_edges = 0
        self.textured_faces = 0
        selected = set(scene.selection)
        groups = [g for g in selected if isinstance(g, Group)
                  and not getattr(g, "locked", False)]
        seen = set()
        for root in groups:
            for g in group_tree(root):
                if id(g) in seen:
                    continue
                seen.add(id(g))
                # Detached copy preserves nested group identity and transforms.
                private = transformed_mesh(g.mesh, QMatrix4x4())
                ed, fa = edge_plan(private, private.edges, angle, coplanar,
                                   smooth, unsoften)
                if not ed and not fa:
                    continue
                for e, _, new in ed:
                    e.soft = new
                for f, _, new in fa:
                    f.attrs[SMOOTH] = new
                touch(private)
                self.swaps.append((g, g.mesh, private))
                self.changed_edges += len(ed)
                self.textured_faces += sum(bool(f.attrs.get("texture"))
                                           for f in private.faces)
        mesh = scene.mesh
        selected_faces = {s for s in selected if isinstance(s, Face)}
        selected_edges = {s for s in selected if isinstance(s, Edge)}
        # Face selection never changes the outer boundary of the selection.
        edges = [e for e in mesh.edges if e in selected_edges or
                 (len(e.faces) == 2 and all(f in selected_faces for f in e.faces))]
        if edges:
            self.edits, self.face_flags = edge_plan(
                mesh, edges, angle, coplanar, smooth, unsoften)
            self.meshes.append(mesh)
            self.changed_edges += len(self.edits)
            self.textured_faces += sum(bool(f.attrs.get("texture"))
                                       for f in selected_faces)

    @property
    def has_changes(self):
        return bool(self.swaps or self.edits or self.face_flags)

    def _set(self, scene, forward):
        for g, before, after in self.swaps:
            g.mesh = after if forward else before
            touch(g.mesh)
        for e, before, after in self.edits:
            e.soft = after if forward else before
        for f, before, after in self.face_flags:
            value = after if forward else before
            if value is _MISSING:
                f.attrs.pop(SMOOTH, None)
            else:
                f.attrs[SMOOTH] = value
        for m in self.meshes:
            touch(m)
        scene.version += 1

    def do(self, scene):
        self._set(scene, True)

    def undo(self, scene):
        self._set(scene, False)


def install_serialization():
    """Keep the extra checkbox flag through the host's explicit attrs codec."""
    import formats.igz as igz
    # Host explicitly encodes face attributes in both JSON and binary modes.
    if getattr(igz, "_soften_edges_codec", False):
        return
    encode, decode = igz._face_attrs_json, igz._face_attrs_from_json

    def pack(face):
        attrs = face.attrs
        out = encode(face)
        if SMOOTH in attrs:
            out = dict(out or {})
            out[SMOOTH] = bool(attrs[SMOOTH])
        return out

    def unpack(raw):
        out = decode(raw)
        if SMOOTH in raw:
            out = dict(out or {})
            out[SMOOTH] = bool(raw[SMOOTH])
        return out

    igz._face_attrs_json = pack
    igz._face_attrs_from_json = unpack
    igz._soften_edges_codec = True


def install_export_normals():
    from formats.meshexport import SmoothNormals
    if getattr(SmoothNormals, "_soften_edges_hook", False):
        return
    original = SmoothNormals.corners

    def corners(self, face):
        if face.attrs.get(SMOOTH) is False:
            return {}
        return original(self, face)

    SmoothNormals.corners = corners
    SmoothNormals._soften_edges_hook = True


def _position(p):
    return tuple(p.toTuple())


def _position_key(p):
    # Qt and the renderer's NumPy matrix path differ by float32 roundoff.
    return tuple(round(float(x), 5) for x in p)


def color_triangle_table(viewport):
    """World-space face corners, independent of triangulation diagonals."""
    from formats.meshexport import SmoothNormals, world_faces_painted
    smoother = SmoothNormals()
    table = {"vertices": {}, "faces": {}}
    light = viewport._LIGHT
    for face, attrs in world_faces_painted(viewport.scene):
        if not face.attrs.get(SMOOTH):
            continue
        if attrs.get("texture") or float(attrs.get("opacity", 1.0)) < 0.999:
            continue
        smooth = smoother.corners(face)
        if not smooth:
            continue
        flat = face.normal()
        base = tuple(attrs.get("color") or viewport.DEFAULT_FACE_COLOR)
        original = viewport._shaded_color(base, flat)
        fid = len(table["faces"])
        colors = {}
        for loop in (face.loop, *face.hole_loops):
            for v in loop:
                p = v.position
                key = _position_key(_position(p))
                n = smooth.get(_position(p), flat).normalized()
                shade = 0.62 + 0.38 * abs(QVector3D.dotProduct(n, light))
                colors[key] = [min(1.0, c * shade) for c in base]
                table["vertices"].setdefault(key, set()).add(fid)
        table["faces"][fid] = (original, colors)
    return table


def rewrite_colors(raw, table):
    import numpy as np
    if not raw or len(raw) % 72:
        return raw
    source = np.frombuffer(raw, np.float32).reshape(-1, 3, 6)
    out = None
    for i, tri in enumerate(source):
        keys = [_position_key(p) for p in tri[:, :3]]
        candidates = [table['vertices'].get(k, set()) for k in keys]
        common = set.intersection(*candidates)
        matches = []
        for fid in common:
            old, colors = table['faces'][fid]
            # Rotated instances re-quantize the host's old diffuse factors.
            if np.max(np.abs(np.asarray(old) - tri[0, 3:])) < 0.04:
                matches.append([colors[k] for k in keys])
        if not matches or any(not np.allclose(matches[0], m) for m in matches[1:]):
            continue
        if out is None:
            out = source.copy()
        out[i, :, 3:] = matches[0]
    return raw if out is None else out.tobytes()


def install_viewport(viewport):
    """Instance-only adapter, no edits to IngeTrazo's installed files."""
    import types
    if getattr(viewport, "_soften_edges_installed", False):
        return True
    if not hasattr(viewport, "_upload_vbo"):
        return False
    original_upload = viewport._upload_vbo
    original_eligible = getattr(viewport, "_instanced_eligible", None)
    state = {"version": None, "table": {}, "parts": {}, "mesh_flags": {}}

    def has_smooth(mesh):
        # No eager materialization of untouched deferred imported meshes.
        src = mesh.__dict__.get("_deferred_source")
        if src is not None:
            return any(a.get(SMOOTH) for a in src.get("meta", {}).get("face_attrs", []) if a)
        key = (id(mesh), getattr(mesh, "_mut_serial", 0), viewport.scene.version)
        if key not in state["mesh_flags"]:
            if len(state["mesh_flags"]) > 4096:
                state["mesh_flags"].clear()
            state["mesh_flags"][key] = any(f.attrs.get(SMOOTH) for f in mesh.faces)
        return state["mesh_flags"][key]

    def eligible(self, group):
        if has_smooth(group.mesh):
            return False
        return original_eligible(group)

    def upload(self, vbo, slot, parts, empty=24):
        if slot == "faces":
            key = (id(self.scene), self.scene.version, id(self.scene.mesh))
            try:
                if key != state["version"]:
                    # Avoid a walk over all model faces unless at least one
                    # mesh is marked by this plugin.
                    meshes = [self.scene.loose_mesh]
                    for root in self.scene.groups:
                        meshes.extend(g.mesh for g in group_tree(root, False))
                    state["table"] = (color_triangle_table(self)
                        if any(has_smooth(m) for m in meshes) else {})
                    state["parts"].clear()
                    state["version"] = key
                if state["table"]:
                    changed = []
                    for raw in parts:
                        hit = state["parts"].get(id(raw))
                        if hit is None or hit[0] is not raw:
                            hit = (raw, rewrite_colors(raw, state["table"]))
                            state["parts"][id(raw)] = hit
                        changed.append(hit[1])
                    parts = changed
            except Exception:
                # Geometry softening remains usable if a host update changes
                # a private renderer API. The panel reports this explicitly.
                self._soften_edges_render_error = True
                LOG.exception("Smooth viewport adapter failed; using host shading")
                state["version"] = key
                state["table"] = {}
        return original_upload(vbo, slot, parts, empty=empty)

    viewport._upload_vbo = types.MethodType(upload, viewport)
    if original_eligible:
        viewport._instanced_eligible = types.MethodType(eligible, viewport)
    viewport._soften_edges_installed = True
    return True


class SoftenPanel(QWidget):
    def __init__(self, viewport):
        super().__init__()
        self.viewport = viewport
        self.setMinimumWidth(265)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("法線之間的角度："))
        row = QHBoxLayout()
        row.addStretch()
        self.angle = QDoubleSpinBox()
        self.angle.setRange(0, 180)
        self.angle.setDecimals(1)
        self.angle.setSuffix(" 度")
        self.angle.setValue(20.0)
        row.addWidget(self.angle)
        row.addStretch()
        layout.addLayout(row)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1800)
        self.slider.setValue(200)
        self.slider.valueChanged.connect(lambda v: self.angle.setValue(v / 10))
        self.angle.valueChanged.connect(lambda v: self.slider.setValue(round(v * 10)))
        layout.addWidget(self.slider)
        self.smooth = QCheckBox("平滑法線")
        self.smooth.setChecked(True)
        self.smooth.setToolTip("平滑不透明純色表面。貼圖／透明表面的視窗光影仍使用主程式；匯出可帶平滑法線。")
        self.coplanar = QCheckBox("柔化共面")
        layout.addWidget(self.smooth)
        layout.addWidget(self.coplanar)
        self.status = QLabel("先選取模型、群組，或相鄰的面／邊線。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.apply_button = QPushButton("套用至選取項目")
        self.apply_button.clicked.connect(lambda: self.apply(False))
        buttons.addWidget(self.apply_button)
        reset = QPushButton("取消柔化")
        reset.clicked.connect(lambda: self.apply(True))
        buttons.addWidget(reset)
        layout.addLayout(buttons)
        help_text = QLabel("Ctrl+Z 復原。邊界與非流形邊線保留。\n平滑法線：視窗目前支援不透明純色表面；貼圖仍可柔化邊線。")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        layout.addStretch()

    def apply(self, unsoften=False):
        vp = self.viewport
        if not vp.scene.selection:
            self.status.setText("尚未選取項目；請先選取要柔化的模型。")
            return
        try:
            command = SoftenCommand(vp.scene, self.angle.value(),
                self.coplanar.isChecked(), self.smooth.isChecked(), unsoften)
            if not command.has_changes:
                self.status.setText("沒有需要變更的邊線。請確認選取範圍；鎖定群組與開放邊界不會變更。")
                return
            vp.history.execute(command)
            if getattr(vp.history, "last_error", None):
                raise RuntimeError(vp.history.last_error)
            # Drop disk-loaded/stale chunks: edge changes can preserve counts.
            for name in ("_group_chunks", "_instance_chunks", "_proto_chunks"):
                cache = getattr(vp, name, None)
                if isinstance(cache, dict):
                    cache.clear()
            vp.notify_scene_changed()
            self.status.setText(f"已更新 {command.changed_edges:,} 條邊線。Ctrl+Z 可復原。")
        except Exception as exc:
            LOG.exception("Soften edges failed")
            QMessageBox.warning(self, "柔化邊線", str(exc))


def setup(app):
    install_serialization()
    install_export_normals()
    supported = install_viewport(app.viewport)
    panel = SoftenPanel(app.viewport)
    if not supported:
        panel.status.setText("此版本可柔化邊線；平滑法線僅套用至匯出。")
    dock = app.add_panel("柔化邊線", panel, name="soften_edges")
    app.add_menu_action("柔化邊線…", lambda: app.show_panel(dock),
                        tip="依角度柔化選取模型的邊線，保留外輪廓，可復原。")

    def context(menu, selection):
        if selection:
            menu.addAction("柔化邊線…", lambda: QTimer.singleShot(
                0, lambda: app.show_panel(dock)))
    app.add_context_menu(context)
