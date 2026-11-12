# SPDX-License-Identifier: MIT
"""Alt+Q selection isolation for IngeTrazo 0.6.0. Version 1.0.0."""
from core.group import Group
from core.mesh import Edge, Face
from core.history import HideCommand, _is_hidden
from core.outliner import parents, walk


def _all_entities(scene):
    """Live identities only: deleted/replaced geometry must never be restored."""
    yield from scene.loose_mesh.faces
    yield from scene.loose_mesh.edges
    for group, _, _ in walk(scene.groups):
        yield group
        yield from group.mesh.faces
        yield from group.mesh.edges
    # Editing a transformed component can use a temporary world-space mesh.
    yield from scene.mesh.faces
    yield from scene.mesh.edges


def _targets(scene, selection):
    selected_groups = {id(e) for e in selection if isinstance(e, Group)}
    selected_geometry = {id(e) for e in selection if isinstance(e, (Face, Edge))}
    up = parents(scene.groups)
    holders = set()
    anchors = [e for e in selection if isinstance(e, Group)]
    if selected_geometry and scene.edit_group is not None:
        anchors.append(scene.edit_group)
        holders.add(id(scene.edit_group))
    for group in anchors:
        parent = up.get(id(group))
        while parent is not None:
            holders.add(id(parent))
            parent = up.get(id(parent))

    targets = []
    seen = set()

    def add(entity):
        if id(entity) not in seen and not _is_hidden(entity):
            targets.append(entity)
            seen.add(id(entity))

    def mesh_targets(mesh):
        for face in mesh.faces:
            if id(face) not in selected_geometry:
                add(face)
        for edge in mesh.edges:
            # A selected face keeps all its boundary/hole edges visible.
            if (id(edge) not in selected_geometry
                    and not any(id(f) in selected_geometry for f in edge.faces)):
                add(edge)

    def visit(group):
        if id(group) in selected_groups:
            return  # Keep the whole selected container, including all children.
        if id(group) not in holders:
            add(group)
            return
        mesh_targets(group.mesh)
        for child in group.children or ():
            visit(child)

    mesh_targets(scene.loose_mesh)
    for group in scene.groups:
        visit(group)
    return targets


def _execute(viewport, targets, hidden):
    viewport.history.execute(HideCommand(targets, hidden=hidden))
    error = getattr(viewport.history, "last_error", None)
    rejected = getattr(viewport.history, "last_rejected", None)
    if error or rejected:
        viewport.flash_status("Alt+Q 執行失敗：" + str(error or rejected), 5000)
        return False
    viewport.notify_scene_changed()
    return True


def toggle(viewport):
    scene = viewport.scene
    token = (id(scene), id(scene.loose_mesh))
    state = getattr(viewport, "_lime_altq_state", None)
    if state and state[0] == token:
        live_ids = {id(e) for e in _all_entities(scene)}
        targets = [e for e in state[1] if id(e) in live_ids and _is_hidden(e)]
        if targets:
            if _execute(viewport, targets, False):
                viewport._lime_altq_state = None
                viewport.flash_status("已顯示 Alt+Q 隱藏的模型。", 3000)
            return
        # Isolation was undone, removed, or already restored by another command.
    viewport._lime_altq_state = None
    selection = [e for e in scene.selection
                 if isinstance(e, (Group, Face, Edge))
                 and not _is_hidden(e) and scene.entity_visible(e)]
    if not selection:
        viewport.flash_status("請先選取要保留顯示的群組、元件、面或線。", 3500)
        return
    targets = _targets(scene, selection)
    if not targets:
        viewport.flash_status("沒有其他可隱藏的模型。", 3000)
        return
    if _execute(viewport, targets, True):
        viewport._lime_altq_state = (token, targets)
        viewport.flash_status("已隱藏選取以外的模型；再按 Alt+Q 恢復顯示。", 4000)


def setup(app):
    from PySide6.QtGui import QKeySequence

    # The stock command keeps its menu entry; only its conflicting key is freed.
    stock = getattr(app.window, "_act_hide_rest", None)
    key = QKeySequence("Alt+Q")
    if stock is not None and stock.shortcut() == key:
        stock.setShortcut(QKeySequence())
        getattr(app.window, "_ext_taken_keys", set()).discard(key.toString())
    action = app.add_menu_action(
        "選取隔離／恢復顯示（Alt+Q）", lambda: toggle(app.viewport),
        "Alt+Q", tip="隱藏選取之外的模型；再按一次顯示剛才隱藏的模型。")
    if action is not None:
        action.setAutoRepeat(False)
