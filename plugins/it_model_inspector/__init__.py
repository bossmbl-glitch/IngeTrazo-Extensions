"""IngeTrazo 模型檢查器. GPL-3.0-or-later."""
__version__ = '1.2.0'


def setup(app):
    from pathlib import Path
    from PySide6.QtCore import Qt, QSize, QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QToolBar
    from .ui import InspectorDialog
    dialog = InspectorDialog(app)
    def show():
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
    action = app.add_menu_action('檢查與修正選取模型…', show, 'Ctrl+Alt+I',
                                 tip='只檢查選取模型；可勾選自動修正，支援 Ctrl+Z 復原')
    action.setIcon(QIcon(str(Path(__file__).with_name('icon.svg'))))
    bar = QToolBar('模型檢查器', app.window)
    bar.setObjectName('it_model_inspector_toolbar')
    bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    bar.setIconSize(QSize(28, 28))
    bar.addAction(action)
    app.window.addToolBar(bar)
    app.window._it_model_inspector = dialog
    app.window._it_model_inspector_toolbar = bar
    bar.show()
    QTimer.singleShot(0, bar.show)

