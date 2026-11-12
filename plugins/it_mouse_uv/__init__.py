"""Independent UV 貼圖 plugin for IngeTrazo 0.5.11."""
__version__ = '0.3.0'

def setup(app):
    from PySide6.QtCore import Qt, QSize
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QToolBar
    from views.icons import tool_icon
    from .mouse_tool import MouseUVTool
    from .ui import UVDialog
    dialogs=[]
    def advanced():
        tool.commit(app.viewport)
        if not dialogs:
            dialogs.append(UVDialog(app))
        dialogs[0].show()
        dialogs[0].raise_()
        dialogs[0].activateWindow()
    tool=MouseUVTool(app,advanced)
    key='it_mouse_uv'
    app.window._tools[key]=tool
    action=QAction(tool_icon('texture_position'),'UV 貼圖',app.window)
    action.setCheckable(True)
    action.setToolTip(tool.description)
    action.triggered.connect(lambda:app.window._activate_tool(key))
    app.window._tool_actions[key]=action
    app.window._tool_group.addAction(action)
    menu=app.add_menu('UV 貼圖')
    menu.addAction(action)
    menu.addAction('進階設定…',advanced)
    bar=QToolBar('UV 貼圖',app.window)
    bar.setObjectName('it_mouse_uv_toolbar')
    bar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    bar.setIconSize(QSize(24,24))
    bar.addAction(action)
    app.window.addToolBar(bar)
    menu.addSeparator()
    menu.addAction(bar.toggleViewAction())
    setattr(app.window,'_it_mouse_uv_ui',(menu,bar,tool,action,dialogs))
