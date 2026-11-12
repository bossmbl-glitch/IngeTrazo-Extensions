"""一鍵壓平 — imported plans to world Z=0. GPL-3.0-or-later."""
__version__ = "1.0.0"


def setup(app):
    from PySide6.QtCore import Qt, QSize, QTimer
    from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor, QPen
    from PySide6.QtWidgets import QToolBar, QMessageBox
    from .engine import FlattenPlanCommand

    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#36bce8"), 3))
    painter.drawLine(4, 26, 28, 26)
    painter.setPen(QPen(QColor("#f6c85f"), 3))
    for x, y in ((8, 5), (16, 9), (24, 3)):
        painter.drawLine(x, y, x, 20)
        painter.drawLine(x, 20, x - 3, 16)
        painter.drawLine(x, 20, x + 3, 16)
    painter.end()

    action = QAction(QIcon(pixmap), "一鍵壓平", app.window)
    action.setObjectName("it_flatten_plan_action")
    action.setToolTip("將選取圖面的高度統一到 Z＝0，保留 X、Y 位置。Ctrl＋Z 可復原。")

    def run():
        viewport = app.viewport
        command = FlattenPlanCommand()
        viewport.history.execute(command)
        message = (getattr(viewport.history, "last_rejected", None)
                   or getattr(viewport.history, "last_error", None))
        if message:
            viewport.flash_status(message, 7000)
            if getattr(viewport.history, "last_error", None):
                QMessageBox.warning(app.window, "一鍵壓平", "壓平失敗，已復原本次修改。\n" + message)
            return
        viewport.notify_scene_changed()
        viewport.flash_status(f"已壓平 {command.changed_vertices:,} 個頂點到 Z＝0；Ctrl＋Z 可復原。", 6000)

    action.triggered.connect(run)
    menu = app.add_menu("圖面壓平")
    menu.addAction(action)
    toolbar = QToolBar("圖面壓平", app.window)
    toolbar.setObjectName("it_flatten_plan_toolbar")
    toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    toolbar.setIconSize(QSize(24, 24))
    toolbar.addAction(action)
    app.window.addToolBar(toolbar)
    menu.addSeparator()
    menu.addAction(toolbar.toggleViewAction())

    def context_menu(menu, selection):
        if selection:
            item = menu.addAction(action.icon(), "一鍵壓平（Z＝0）")
            item.triggered.connect(lambda: QTimer.singleShot(0, run))

    app.add_context_menu(context_menu)
    app.window._it_flatten_plan_ui = (menu, toolbar, action, run, context_menu)
