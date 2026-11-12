# SPDX-License-Identifier: GPL-3.0-or-later
"""萊姆圖層底色 v0.1.0 — IngeTrazo 0.6.0 / PySide6.

Install this .py in Extensions > Open plugins folder and restart.
Uses the existing name-column delegate and context menu. No geometry edits.
"""
from __future__ import annotations

from functools import wraps
from types import MethodType

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QBrush, QColor, QPalette
from PySide6.QtWidgets import QColorDialog, QMessageBox, QStyle

VERSION = "0.1.0"
KEY = "lime_layer_background"
DEFAULT_COLOR = "#fff36a"


def _colors(app):
    raw = app.document_data({})
    values = raw.get("colors", {}) if isinstance(raw, dict) else {}
    if not isinstance(values, dict):
        return {}
    return {name: QColor(value).name() for name, value in values.items()
            if isinstance(name, str) and isinstance(value, str)
            and QColor(value).isValid()}


def _install_rename_hook():
    # Core renames are not undo steps; move our independent metadata as part
    # of that same rename. Do not mutate layer colours or material attributes.
    from core import layers
    original = layers.rename_layer
    if getattr(original, "_lime_background", False):
        return

    @wraps(original)
    def rename(scene, old_name, new_name):
        result = original(scene, old_name, new_name)
        if result:
            raw = (getattr(scene, "plugin_data", {}) or {}).get(KEY)
            values = raw.get("colors") if isinstance(raw, dict) else None
            if isinstance(values, dict) and old_name in values:
                values[new_name] = values.pop(old_name)
        return result

    rename._lime_background = True
    layers.rename_layer = rename


def setup(app):
    """Called once by IngeTrazo's extension loader."""
    # The filename is the persistent document-data namespace.
    if app.key != KEY:
        raise RuntimeError("請保留套件檔名 lime_layer_background.py")
    dock = getattr(app.window, "layers_dock", None)
    panel = getattr(dock, "panel", None)
    if panel is None:
        raise RuntimeError("找不到原生圖層面板；此版本適用 IngeTrazo 0.6.0。")
    if getattr(panel, "_lime_background_controller", None) is not None:
        return
    from views.layers_panel import COL_NAME, KIND_LAYER
    for attr in ("tree", "fill_menu", "selected_layers", "_targets", "_key"):
        if not hasattr(panel, attr):
            raise RuntimeError("圖層面板介面不相容：" + attr)
    base = type(panel.tree.itemDelegateForColumn(COL_NAME))
    if base is type(None):
        raise RuntimeError("找不到名稱欄繪製器。")

    class TintDelegate(base):
        def initStyleOption(self, option, index):
            super().initStyleOption(option, index)
            identity = index.data(Qt.UserRole)
            if not identity or identity[0] != KIND_LAYER:
                return
            value = controller.colors.get(identity[1])
            if value:
                color = QColor(value)
                option.backgroundBrush = QBrush(color)
                # Ensure custom dark colours remain legible in either theme.
                light = (0.2126 * color.redF() + 0.7152 * color.greenF()
                         + 0.0722 * color.blueF())
                ink = QColor("#111111" if light > 0.55 else "#ffffff")
                option.palette.setColor(QPalette.Text, ink)

        def paint(self, painter, option, index):
            super().paint(painter, option, index)
            identity = index.data(Qt.UserRole)
            value = (controller.colors.get(identity[1]) if identity
                     and identity[0] == KIND_LAYER else None)
            # Keep Qt's native selection readable; a bottom stripe retains
            # the assigned colour while a row is selected.
            if value and option.state & QStyle.State_Selected:
                r = option.rect
                painter.fillRect(r.left() + 4, r.bottom() - 2,
                                 max(0, r.width() - 8), 3, QColor(value))

    class Controller:
        def __init__(self):
            self.colors = _colors(app)
            self.original_menu = panel.fill_menu
            self.delegate = TintDelegate(panel.tree)
            panel.tree.setItemDelegateForColumn(COL_NAME, self.delegate)

        def refresh(self):
            self.colors = _colors(app)
            panel.tree.viewport().update()

        def apply(self, names, value):
            live = {ly.name for ly in app.scene.layers}
            names = [name for name in names if name in live]
            if not names:
                QMessageBox.information(app.window, "圖層名稱底色",
                                        "請先在圖層面板選取一個或多個圖層。")
                return
            colors = _colors(app)
            before = dict(colors)
            for name in names:
                if value is None:
                    colors.pop(name, None)
                else:
                    colors[name] = QColor(value).name()
            if colors != before:
                app.set_document_data({"version": 1, "colors": colors})
            self.refresh()

        def choose(self, names):
            if not names:
                self.apply([], None)
                return
            initial = self.colors.get(names[0], DEFAULT_COLOR)
            color = QColorDialog.getColor(QColor(initial), app.window,
                                          "設定圖層名稱底色")
            if color.isValid():
                self.apply(names, color.name())

        def fill(self, menu, item):
            self.original_menu(menu, item)
            if item is None or panel._key(item)[0] != KIND_LAYER:
                return
            # Match the native menu's right-click / multi-selection semantics.
            names = list(panel._targets(item))
            menu.addSeparator()
            menu.addAction("設定名稱底色…", lambda: QTimer.singleShot(
                0, lambda: self.choose(names)))
            menu.addAction("標記淡黃色", lambda: self.apply(names, DEFAULT_COLOR))
            clear = menu.addAction("清除名稱底色", lambda: self.apply(names, None))
            clear.setEnabled(any(name in self.colors for name in names))

    controller = Controller()
    panel._lime_background_controller = controller
    panel.fill_menu = MethodType(lambda _panel, menu, item:
                                controller.fill(menu, item), panel)
    _install_rename_hook()
    app.on_document_changed(controller.refresh)
    app.add_menu_action("圖層名稱底色…", lambda: controller.choose(
        list(panel.selected_layers())))
