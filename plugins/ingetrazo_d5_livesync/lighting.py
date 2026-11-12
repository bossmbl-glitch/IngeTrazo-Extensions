# SPDX-License-Identifier: GPL-3.0-or-later
"""Toolbar for IngeTrazo's native, undoable Render-panel lights."""
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor, QPen, QPolygonF
from PySide6.QtWidgets import QToolBar, QStyle, QScrollArea, QMessageBox


def light_icon(filename):
    icon = QIcon(str(Path(__file__).parent / "icons" / filename))
    if not icon.pixmap(24, 24).isNull():
        return icon
    # Some frozen Qt distributions omit the SVG image plugin.
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#d89b16"), 1.8))
    painter.setBrush(QColor("#ffe298"))
    if filename == "light_point.svg":
        import math
        painter.drawEllipse(QPointF(12, 12), 4, 4)
        for index in range(8):
            angle = index * math.pi / 4
            painter.drawLine(QPointF(12 + 7 * math.cos(angle), 12 + 7 * math.sin(angle)),
                             QPointF(12 + 10 * math.cos(angle), 12 + 10 * math.sin(angle)))
    else:
        painter.drawPolygon(QPolygonF([QPointF(9, 8), QPointF(3, 21), QPointF(21, 21), QPointF(15, 8)]))
        painter.fillRect(8, 3, 8, 5, QColor("#617589"))
    painter.end()
    return QIcon(pixmap)


def setup_lighting(app, log):
    win = app.window
    previous = getattr(win, "_ingetrazo_lighting_toolbar", None)
    if previous is not None:
        previous.show()
        return
    bar = QToolBar("燈光", win)
    bar.setObjectName("ingetrazo_lighting_toolbar")
    bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    bar.setMovable(True)
    bar.setFloatable(True)
    actions = {}
    state = {"panel": None, "dock": None}

    def bind_panel(create=False):
        dock = win.extension_panels().get("extension_render_blender")
        if dock is None and create:
            # Use the host's own plugin, not a separate copy of its lighting
            # data, picking overlays or history implementation.
            import sys
            from core.paths import app_root
            from core.extensions import _import_by_path
            from views.extension_api import ExtensionApp
            module = sys.modules.get("ingetrazo_plugin_render_blender")
            if module is None:
                source = Path(app_root()) / "plugins" / "render_blender.py"
                if not source.is_file():
                    raise RuntimeError("找不到 IngeTrazo 內建的 Render 燈光插件")
                module = _import_by_path("render_blender", source)
            module.setup(ExtensionApp(win, "render_blender"))
            dock = win.extension_panels().get("extension_render_blender")
        if dock is None:
            return None
        panel = dock.widget()
        if not all(hasattr(panel, name) for name in
                   ("_lights", "_begin_pick", "_delete_light", "selected_index")):
            raise RuntimeError("目前 IngeTrazo 的燈光 API 與此插件不相容")
        if state["panel"] is not panel:
            state.update(panel=panel, dock=dock)
            panel._lights.currentRowChanged.connect(refresh)
            panel._lights.itemChanged.connect(refresh)
        return panel

    def selected_light(panel):
        if panel is None:
            return None
        index = panel.selected_index()
        raw = panel.app.document_data({}) or {}
        lights = raw.get("lights", []) if isinstance(raw, dict) else []
        return lights[index] if index is not None and 0 <= index < len(lights) else None

    def refresh(*_args):
        panel = bind_panel()
        light = selected_light(panel)
        for key in ("move", "toggle", "delete"):
            actions[key].setEnabled(light is not None)
        actions["aim"].setEnabled(bool(light and light.get("kind") == "spot"))
        actions["toggle"].setText("關閉燈光" if light and light.get("on", True) else "開啟燈光")
        if light is None:
            actions["toggle"].setText("燈光開關")

    def show_lights(panel):
        from views.fold_section import FoldSection
        app.show_panel(state["dock"])
        section = panel._lights.parentWidget()
        while section is not None and not isinstance(section, FoldSection):
            section = section.parentWidget()
        if section is not None:
            section.set_open(True)
        scroll = panel.findChild(QScrollArea)
        if scroll is not None:
            QTimer.singleShot(0, lambda: scroll.ensureWidgetVisible(panel._lights, 0, 20))

    def invoke(command):
        try:
            panel = bind_panel(create=True)
            if panel is None:
                raise RuntimeError("無法開啟內建燈光面板")
            show_lights(panel)
            if command in ("point", "spot"):
                panel._begin_pick("new", command)
            elif command in ("move", "aim"):
                if selected_light(panel) is not None:
                    panel._begin_pick(command)
            elif command == "delete":
                panel._delete_light()
            elif command == "toggle":
                index = panel.selected_index()
                if index is not None:
                    item = panel._lights.item(index)
                    item.setCheckState(Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked
                                       else Qt.CheckState.Checked)
            refresh()
        except Exception as exc:
            log.exception("Lighting toolbar action failed: %s", command)
            QMessageBox.warning(win, "燈光工具列", str(exc))

    for key, title, icon, tip in (
        ("point", "點光源", "light_point.svg", "在模型上點一下放置點光源；Esc 取消"),
        ("spot", "聚光燈", "light_spot.svg", "在模型上點一下放置聚光燈；預設向下照射"),
        ("move", "移動燈光", QStyle.StandardPixmap.SP_ArrowRight, "先在視窗或燈光清單選燈，再點新的位置"),
        ("aim", "瞄準目標", QStyle.StandardPixmap.SP_ArrowDown, "選取聚光燈後，在模型上點選照射目標"),
        ("toggle", "燈光開關", QStyle.StandardPixmap.SP_DialogApplyButton, "開啟或關閉所選燈光；可復原"),
        ("delete", "刪除燈光", QStyle.StandardPixmap.SP_TrashIcon, "刪除所選燈光；Ctrl+Z 可復原"),
        ("settings", "燈光設定", QStyle.StandardPixmap.SP_FileDialogDetailedView, "開啟原生燈光清單，調整功率、色溫、角度與方向"),
    ):
        callback = lambda cmd=key: invoke(cmd)
        action = app.add_menu_action(title, callback, tip=tip)
        if action is None:
            action = QAction(title, win)
            action.triggered.connect(lambda checked=False, fn=callback: fn())
        action.setObjectName("ingetrazo_light_" + key)
        action.setToolTip(tip)
        action.setIcon(light_icon(icon) if isinstance(icon, str)
                       else win.style().standardIcon(icon))
        bar.addAction(action)
        actions[key] = action
    win.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
    win.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
    win._ingetrazo_lighting_toolbar = bar
    win._ingetrazo_lighting_actions = actions
    app.on_document_changed(refresh)
    refresh()
    QTimer.singleShot(0, bar.show)
    log.info("Native lighting toolbar ready")
