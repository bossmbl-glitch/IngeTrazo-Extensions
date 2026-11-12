"""Independent 固定邊框縮放 plugin for IngeTrazo 0.5.11."""
__version__ = '0.6.1'

def setup(app):
    from .smooth_render import install
    install(app.viewport)
    from PySide6.QtCore import Qt, QSize
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QToolBar
    from views.icons import tool_icon
    from .mouse_tool import FrameScaleTool
    from .ui import DeformDialog
    dialogs=[]
    def advanced():
        tool.on_cancel(app.viewport)
        if not dialogs:
            dialogs.append(DeformDialog(app))
        dialogs[0].show()
        dialogs[0].raise_()
        dialogs[0].activateWindow()
    tool=FrameScaleTool(app,advanced)
    from .soften_ui import SoftenPanel
    soften = SoftenPanel(app)
    app.add_context_menu(soften.context)
    app.window._it_soften_panel = soften
    tool.soften_panel = soften
    key='it_frame_scale'
    app.window._tools[key]=tool
    action=QAction(tool_icon('scale'),'固定邊框縮放',app.window)
    action.setCheckable(True)
    action.setToolTip(tool.description)
    action.triggered.connect(lambda:app.window._activate_tool(key))
    app.window._tool_actions[key]=action
    app.window._tool_group.addAction(action)
    menu=app.add_menu('固定邊框縮放')
    menu.addAction(action)
    menu.addAction('進階設定…',advanced)
    menu.addAction('柔化／平滑邊線…',soften.open)
    bar=QToolBar('固定邊框縮放',app.window)
    bar.setObjectName('it_frame_scale_toolbar')
    bar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    bar.setIconSize(QSize(24,24))
    bar.addAction(action)
    app.window.addToolBar(bar)
    menu.addSeparator()
    menu.addAction(bar.toggleViewAction())
    setattr(app.window,'_it_frame_scale_ui',(menu,bar,tool,action,dialogs))
