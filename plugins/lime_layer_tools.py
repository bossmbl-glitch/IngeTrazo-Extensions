# SPDX-License-Identifier: GPL-3.0-or-later
"""萊姆圖層工具 v0.1.0 — IngeTrazo 0.6.0."""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout,
                              QLabel, QMessageBox, QPushButton, QToolBar,
                              QVBoxLayout)
from core import layers as L
from core.group import Group
from core.history import AssignLayerCommand, PurgeUnusedCommand

VERSION = "0.1.0"


def assignment_targets(selection, recursive=False):
    """Deduplicate shared meshes and overlapping parent/child selections."""
    result, seen, groups, meshes = [], set(), set(), set()

    def add(entity):
        if id(entity) not in seen:
            seen.add(id(entity))
            result.append(entity)

    def descend(group):
        if id(group) in groups:
            return
        groups.add(id(group))
        add(group)
        mesh = group.mesh
        if id(mesh) not in meshes:
            meshes.add(id(mesh))
            for entity in list(mesh.faces) + list(mesh.edges):
                add(entity)
        for child in group.children or ():
            descend(child)

    for entity in L.taggable(selection):
        if recursive and isinstance(entity, Group):
            descend(entity)
        else:
            add(entity)
    return result


class EmptyLayersCommand(PurgeUnusedCommand):
    def __init__(self):
        super().__init__(layers=True, materials=False)

    def _survey(self, scene):
        super()._survey(scene)
        # The native purge excludes survey meshes from its used-layer query.
        # Keep their tag too, and keep the current drawing layer usable.
        protected = {L.DEFAULT_LAYER, L.active_layer(scene)}
        photo = getattr(scene, "photo_mesh", None)
        if photo is not None:
            protected.add(L.layer_of(photo))
        self._layers = [(i, ly) for i, ly in self._layers
                        if ly.name not in protected]


def _icon(purge=False):
    pix = QPixmap(32, 32)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#526879"), 2))
    painter.setBrush(QColor("#deebf5"))
    for y in (16, 10, 4):
        painter.drawRoundedRect(3, y, 22, 10, 2, 2)
    painter.setPen(QPen(QColor("#c43b36" if purge else "#1b8a61"), 3))
    if purge:
        painter.drawLine(21, 20, 29, 28)
        painter.drawLine(29, 20, 21, 28)
    else:
        painter.drawLine(18, 25, 29, 25)
        painter.drawLine(25, 21, 29, 25)
        painter.drawLine(25, 29, 29, 25)
    painter.end()
    return QIcon(pix)


class LayerToolsDialog(QDialog):
    def __init__(self, app):
        super().__init__(app.window)
        self.app = app
        self.setWindowTitle("萊姆圖層工具 v" + VERSION)
        self.setWindowFlag(Qt.Tool, True)
        self.setModal(False)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(QLabel("先在模型選取物件，再指定目標圖層。"))
        row = QHBoxLayout()
        row.addWidget(QLabel("目標圖層"))
        self.target = QComboBox()
        self.target.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.target.setMinimumContentsLength(18)
        row.addWidget(self.target, 1)
        layout.addLayout(row)
        self.recursive = QCheckBox("包含群組內部物件（含巢狀群組、面與線）")
        layout.addWidget(self.recursive)
        note = QLabel("勾選後，元件共用的內部資料會同步影響其他實例。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.assign_button = QPushButton(_icon(), "選取物件歸層")
        self.assign_button.setMinimumHeight(40)
        self.assign_button.setAutoDefault(False)
        self.assign_button.clicked.connect(self.assign)
        layout.addWidget(self.assign_button)
        self.purge_button = QPushButton(_icon(True), "清除空圖層")
        self.purge_button.setMinimumHeight(40)
        self.purge_button.setAutoDefault(False)
        self.purge_button.clicked.connect(self.purge)
        layout.addWidget(self.purge_button)
        self.status = QLabel("兩項操作皆可用 Ctrl+Z 復原。")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        self._scene = None
        self.refresh()

    def refresh(self):
        scene = self.app.scene
        if self._scene is not None and self._scene is not scene:
            self.status.setText("已切換文件，請確認目標圖層。")
        previous = (self.target.currentText() if self._scene is scene
                    else L.active_layer(scene))
        self._scene = scene
        names = [ly.name for ly in scene.layers]
        if [self.target.itemText(i) for i in range(self.target.count())] != names:
            self.target.blockSignals(True)
            self.target.clear()
            self.target.addItems(names)
            self.target.blockSignals(False)
        index = self.target.findText(previous)
        if index < 0:
            index = self.target.findText(L.active_layer(scene))
        self.target.setCurrentIndex(max(0, index) if names else -1)

    def show_tools(self):
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()

    def _execute(self, command):
        vp = self.app.viewport
        vp.history.execute(command)
        if vp.history.last_error or vp.history.last_rejected:
            self.status.setText("操作未完成：" + str(
                vp.history.last_error or vp.history.last_rejected))
            return False
        # At the configured history limit the length may stay unchanged.
        if not vp.history.undo_stack or vp.history.undo_stack[-1] is not command:
            self.status.setText("操作未完成，未建立復原紀錄。")
            return False
        vp.notify_scene_changed()
        vp.update()
        self.refresh()
        return True

    def assign(self):
        self.refresh()
        scene = self.app.scene
        name = self.target.currentText()
        if not name or scene.layer(name) is None:
            self.status.setText("請先選擇有效的目標圖層。")
            return
        targets = assignment_targets(scene.selection, self.recursive.isChecked())
        if not targets:
            self.status.setText("請先在模型選取物件或群組，再按「選取物件歸層」。")
            return
        changed = [e for e in targets if L.layer_of(e) != name]
        if not changed:
            self.status.setText("選取的物件已全部位於「" + name + "」。")
            return
        if self._execute(AssignLayerCommand(changed, name)):
            self.status.setText(f"已將 {len(changed)} 個圖層項目歸入「{name}」。可 Ctrl+Z 復原。")

    def purge(self):
        command = EmptyLayersCommand()
        count, _ = command.counts(self.app.scene)
        if not count:
            self.status.setText("沒有可清除的空圖層。預設、目前使用及場景保留的圖層會保留。")
            self.show_tools()
            return
        if self._execute(command):
            self.status.setText(f"已清除 {count} 個空圖層。可 Ctrl+Z 復原。")
            self.show_tools()


def setup(app):
    if getattr(app.window, "_lime_layer_tools", None) is not None:
        return
    dialog = LayerToolsDialog(app)
    app.window._lime_layer_tools = dialog
    app.on_document_changed(dialog.refresh)
    app.add_menu_action("萊姆圖層工具…", dialog.show_tools)
    toolbar = QToolBar("萊姆圖層工具", app.window)
    toolbar.setObjectName("lime_layer_tools_toolbar")
    toolbar.setIconSize(QSize(28, 28))
    assign = QAction(_icon(), "選取物件歸層", toolbar)
    assign.setToolTip("開啟圖層工具，指定目標圖層並將選取物件歸層")
    assign.triggered.connect(dialog.show_tools)
    purge = QAction(_icon(True), "清除空圖層", toolbar)
    purge.setToolTip("清除沒有物件使用的圖層（可復原）")
    purge.triggered.connect(dialog.purge)
    toolbar.addAction(assign)
    toolbar.addAction(purge)
    app.window.addToolBar(toolbar)
    restore = getattr(app, "restore_toolbar_layout", None)
    if callable(restore):
        restore()
