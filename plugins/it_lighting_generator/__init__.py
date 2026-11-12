"""燈具生成器 — IngeTrazo 0.5.11 / 0.6.0. GPL-3.0-or-later."""
__version__ = '1.0.2'


def setup(app):
    from PySide6.QtCore import Qt, QSize, QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QToolBar
    from pathlib import Path
    from .ui import LightingDialog
    dialogs = []

    def show():
        if not dialogs:
            dialogs.append(LightingDialog(app))
        dialogs[0].show()
        dialogs[0].raise_()
        dialogs[0].activateWindow()

    action = app.add_menu_action('燈具生成器…', show, 'Ctrl+Alt+L',
                                 tip='21 款原模型燈具：單顆、連續放置與矩陣排列')
    if action is not None:
        action.setIcon(QIcon(str(Path(__file__).parent/'previews'/'QA1_N1.png')))
        toolbar = QToolBar('燈具生成器', app.window)
        toolbar.setObjectName('it_lighting_generator_toolbar')
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        toolbar.setIconSize(QSize(28,28))
        toolbar.addAction(action)
        app.window.addToolBar(toolbar)
        app.window._it_lighting_toolbar = toolbar
        toolbar.show()
        # Packaged IngeTrazo loads plugins AFTER the main window is shown,
        # then restores its saved toolbar layout. Reveal this bar after that.
        QTimer.singleShot(0, toolbar.show)
    app.window._it_lighting_dialogs = dialogs

    def update_selected():
        from PySide6.QtWidgets import QMessageBox
        from .engine import UpdateLightingCommand
        vp=app.viewport
        try:
            cmd=UpdateLightingCommand(list(vp.scene.selection))
            if not cmd.items:
                QMessageBox.information(app.window,'更新燈具','請先選取由燈具生成器放置的燈具或排列組。')
                return
            vp.history.execute(cmd)
            if vp.history.last_error:
                raise RuntimeError(vp.history.last_error)
            vp.notify_scene_changed()
            vp.flash_status(f'已更新 {len(cmd.items)} 組燈具，位置不變；Ctrl+Z 可復原。',5000)
        except Exception as exc:
            QMessageBox.critical(app.window,'更新燈具失敗',str(exc))

    app.add_menu_action('更新選取燈具（模型與定位框）',update_selected,
                        tip='將已選取的舊版燈具更新為目前模型庫；保留位置、角度與排列，可復原')
