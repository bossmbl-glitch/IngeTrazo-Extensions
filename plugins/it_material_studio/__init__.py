"""Material Studio: named colours and image textures, centimetres and mouse control."""
__version__='0.5.1'

def setup(app):
    from PySide6.QtCore import Qt,QTimer,QSize
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QToolBar,QScrollArea
    from tools.base import Tool
    from views.icons import tool_icon
    from .ui import MaterialPanel
    from .engine import apply,from_attrs,paint_selection
    panel=MaterialPanel(app)
    scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(panel)
    dock=app.add_panel('材質編輯器',scroll,start_hidden=True)
    class MaterialBrush(Tool):
        name='材質油漆桶';icon='paint';uses_snap=False
        description='B 油漆桶；點選取範圍可一次全部上色；按住 Alt 點擊吸取，放開恢復上色。'
        def on_activate(self,viewport):viewport.flash_status(self.description,6000)
        def on_deactivate(self,viewport):viewport.set_hover(None)
        def on_click(self,ctx):
            vp=ctx.viewport;x,y=ctx.screen.x(),ctx.screen.y()
            if ctx.modifiers&Qt.AltModifier or panel._sample_once:
                panel._sample_once=False
                face,group=vp.pick_face_any(x,y)
                if face is not None:
                    from core.group import effective_material
                    from core.materials import effective_attrs
                    panel.set_recipe(from_attrs(effective_attrs(face.attrs,effective_material(group)),face.normal()))
                    panel._material_source='model'
                    panel.tabs.setCurrentIndex(1)
                    app.show_panel(dock);panel.message('已吸取材質；直接調色可更新所有同名面，放開 Alt 再點模型可上色。')
                return
            entity=vp.pick_group(x,y) or vp.pick_face(x,y)
            if entity is not None:
                try:
                    selected=paint_selection(app.scene)
                    batch=entity in selected
                    targets=selected if batch else [entity]
                    panel.end_color()
                    count=apply(app,panel.recipe(),targets,both=panel.both.isChecked())
                    if not batch:app.scene.select([entity])
                    vp.notify_scene_changed()
                    panel._loaded_name=panel.recipe()['name']
                    panel.message(f'已塗上 {count} 個面；調整面板可繼續編輯。Ctrl+Z 可復原。')
                except (ValueError,OSError) as exc:panel.message(str(exc),True)
        def on_cancel(self,viewport):pass
        def on_key(self,viewport,key,modifiers):
            if key==Qt.Key_Escape:app.window._activate_tool('select');return True
            return False
    key='it_material_studio_paint';tool=MaterialBrush();app.window._tools[key]=tool
    brush_action=QAction(tool_icon('paint'),'材質油漆桶',app.window);brush_action.setCheckable(True)
    brush_action.setObjectName('it_material_studio_bucket')
    brush_action.triggered.connect(lambda:app.window._activate_tool(key))
    app.window._tool_actions[key]=brush_action;app.window._tool_group.addAction(brush_action)
    action=QAction(tool_icon('paint'),'材質編輯器',app.window)
    def open_panel():
        app.show_panel(dock)
        if app.scene.selection:panel.read_selection()
    action.triggered.connect(open_panel)
    menu=app.add_menu('材質編輯器');menu.addAction(action);menu.addAction(brush_action)
    bar=QToolBar('材質編輯器',app.window);bar.setObjectName('it_material_studio_toolbar')
    bar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon);bar.setIconSize(QSize(24,24));bar.addAction(action)
    app.window.addToolBar(bar)
    from .position import ModelTexturePosition
    position=ModelTexturePosition(app,panel)
    app.window._it_material_position=position
    position.rotate_actions={}
    for degrees in (90,180):
        rotate=QAction(f'貼圖 {degrees}°',app.window)
        rotate.setToolTip(f'將選取的多個面之貼圖旋轉 {degrees}°；Ctrl+Z 一次復原')
        rotate.triggered.connect(lambda checked=False,d=degrees:position.rotate_selected(d))
        bar.addAction(rotate);menu.addAction(rotate);position.rotate_actions[degrees]=rotate
    def context(menu,selection):
        position.add_menu(menu,selection)
        if selection:
            def edit():app.show_panel(dock);panel.read_selection()
            menu.addAction('編輯選取材質…',lambda:QTimer.singleShot(0,edit))
    app.add_context_menu(context)
    app.window._it_material_studio_ui=(panel,dock,action,tool,bar,context)
    from .shortcuts import install_bucket_key
    QTimer.singleShot(0,lambda:install_bucket_key(app.window,brush_action))
