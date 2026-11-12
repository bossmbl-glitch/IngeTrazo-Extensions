"""SKP / MAX 輕鬆匯入 — IngeTrazo 0.5.11. GPL-3.0-or-later."""
__version__ = "0.5.1"


def setup(app):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QToolBar, QStyle
    from .ui import ImportDialog
    from .drop import ViewportDrop
    dialogs = []

    def dialog():
        if not dialogs:
            dialogs.append(ImportDialog(app))
        return dialogs[0]

    def show():
        dlg = dialog()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    action = app.add_menu_action("SKP/MAX/CAD 匯入…", show, "Ctrl+Alt+I",
                                 tip="開啟匯入視窗，或直接將 SKP/MAX/DWG/DXF 拖進模型視窗")
    if action is not None:
        action.setIcon(app.window.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        toolbar = QToolBar("SKP/MAX/CAD 匯入", app.window)
        toolbar.setObjectName("it_model_import_toolbar")
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.addAction(action)
        app.window.addToolBar(toolbar)
        app.window._it_model_import_toolbar = toolbar
    app.window._it_model_import_dialogs = dialogs
    app.window._it_model_import_drop = ViewportDrop(app, dialog)
