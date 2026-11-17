# SPDX-License-Identifier: GPL-3.0-or-later
"""IngeTrazo -> background Blender -> official D5 LiveSync."""
__version__ = "0.9.11"


def setup(app):
    """Create the toolbar immediately; load the sync panel only on demand."""
    from PySide6.QtCore import Qt, QTimer, QSettings
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QToolBar, QStyle, QMessageBox
    import logging
    import os
    from pathlib import Path

    win = app.window
    existing = getattr(win, "_ingetrazo_d5_toolbar", None)
    if existing is not None:
        existing.show()
        return
    log = logging.getLogger("ingetrazo.d5_livesync")
    log.setLevel(logging.INFO)
    if not log.handlers:
        try:
            directory = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "IngeTrazoD5Bridge"
            directory.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(directory / "plugin.log", encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            log.addHandler(handler)
        except OSError:
            pass

    toolbar = QToolBar("D5 自動同步", win)
    toolbar.setObjectName("ingetrazo_d5_livesync_toolbar")
    toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    toolbar.setStyleSheet("QToolButton:checked { background-color: #eda13b; color: #201500; font-weight: bold; }")
    toolbar.setMovable(True)
    toolbar.setFloatable(True)
    state = {"panel": None, "dock": None}
    actions = {}

    def refresh_actions():
        panel = state["panel"]
        active = bool(panel and panel.process and panel.process.poll() is None)
        actions["start"].setEnabled(not active)
        actions["now"].setEnabled(bool(panel and panel.running))
        actions["stop"].setEnabled(active)
        paused = panel.schedule.paused if panel else QSettings("IngeTrazo", "D5LiveSyncBridge").value("paused", False, type=bool)
        actions["pause"].setChecked(paused)
        actions["resume"].setEnabled(paused)

    def get_panel():
        if state["panel"] is None:
            from .panel import SyncPanel
            panel = SyncPanel(app)
            dock = app.add_panel("D5 自動同步", panel, start_hidden=True)
            state.update(panel=panel, dock=dock)
            win._ingetrazo_d5_livesync = panel
            panel.state_changed.connect(refresh_actions)
            refresh_actions()
        return state["panel"]

    def invoke(command):
        try:
            panel = get_panel()
            if command in ("start", "settings"):
                app.show_panel(state["dock"])
            if command == "start":
                panel.start()
            elif command == "layers":
                panel.open_layers()
            elif command == "now":
                panel.sync_now()
            elif command == "pause":
                panel.set_paused(True)
            elif command == "resume":
                panel.set_paused(False)
            elif command == "stop":
                panel.stop()
            refresh_actions()
        except Exception as exc:
            log.exception("Toolbar action failed: %s", command)
            QMessageBox.warning(win, "D5 自動同步", "無法執行：" + str(exc))

    for key, title, icon, tip in (
        ("start", "D5", QStyle.StandardPixmap.SP_MediaPlay, "啟動背景 Blender 並將目前模型自動同步到 D5"),
        ("pause", "暫停", QStyle.StandardPixmap.SP_MediaPause, "暫停／繼續自動更新；已提交的版本會完成，連線與模型關聯保留"),
        ("resume", "繼續", QStyle.StandardPixmap.SP_MediaPlay, "恢復自動同步，合併傳送暫停期間的最新修改"),
        ("now", "更新", QStyle.StandardPixmap.SP_BrowserReload, "立即傳送最新修改；上一版未完成時會等待"),
        ("stop", "停止", QStyle.StandardPixmap.SP_MediaStop, "停止此模型的背景同步，保留 D5 場景"),
        ("layers", "圖層", QStyle.StandardPixmap.SP_DirIcon, "依最外層群組圖層設定正常同步、暫時不更新或排除同步"),
        ("settings", "設定", QStyle.StandardPixmap.SP_FileDialogDetailedView, "開啟同步設定、連線狀態與紀錄"),
    ):
        callback = lambda command=key: invoke(command)
        action = app.add_menu_action(title, callback, tip=tip)
        if action is None:
            action = QAction(title, win)
            action.triggered.connect(lambda checked=False, fn=callback: fn())
        action.setObjectName("ingetrazo_d5_" + key)
        if key == "pause":
            action.setCheckable(True)
        action.setIcon(win.style().standardIcon(icon))
        action.setToolTip(tip)
        toolbar.addAction(action)
        actions[key] = action
    win.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
    win._ingetrazo_d5_toolbar = toolbar
    win._ingetrazo_d5_toolbar_actions = actions
    refresh_actions()
    # Host restores saved layouts after plugin setup. Show this new toolbar
    # after that pass so an old layout cannot leave the new controls hidden.
    QTimer.singleShot(0, toolbar.show)
    from .d5_light_ui import setup_lighting
    setup_lighting(app, log, toolbar)
    from .compact_ui import compact_toolbar
    compact_toolbar(toolbar)
    # The host restores layouts after setup; reassert this toolbar's explicit
    # icon/button sizes after that pass, including existing light buttons.
    QTimer.singleShot(0, lambda: compact_toolbar(toolbar))
    log.info("Toolbar ready; version=%s; source=%s", __version__, __file__)
