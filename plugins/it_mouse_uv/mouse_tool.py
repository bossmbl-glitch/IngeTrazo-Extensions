"""Select in the viewport, right-click for mapping, drag native texture pins."""
from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QVector3D
from PySide6.QtWidgets import QMenu, QInputDialog, QFileDialog
from tools.texture_position import TexturePositionTool
from core.mesh import Face
from .common import execute
from . import uv


class MouseUVTool(TexturePositionTool):
    name='UV 貼圖'
    shortcut=None
    description='點選面或群組，右鍵選擇 UV 投影；拖曳貼圖釘移動、旋轉及縮放。'

    def __init__(self, app, advanced):
        super().__init__()
        self.app, self.advanced=app,advanced
        self.axis=2
        self.tile_cm=100.

    def on_activate(self,viewport):
        super().on_activate(viewport)
        viewport.flash_status('UV：點選面或群組 → 右鍵選投影；有貼圖的面可直接拖曳。',7000)
        self.begin_selected(viewport)

    def begin_selected(self,viewport):
        faces=[f for f in viewport.scene.selection if isinstance(f,Face)]
        if len(faces)==1 and len(viewport.scene.selection)==1 and viewport.scene.entity_selectable(faces[0]):
            if self.begin(viewport,faces[0]):
                viewport.flash_status('拖曳貼圖移動；紅點移動、綠點旋轉／縮放、藍點拉伸；右鍵選 UV 方式，Enter 套用。',7000)
                return True
        return False

    def _pick(self,viewport,x,y):
        group=viewport.pick_group(x,y)
        face=viewport.pick_face(x,y)
        entity=group or face
        if entity is not None:
            if entity not in viewport.scene.selection:
                viewport.scene.select([entity])
            viewport.update()
        return entity

    def on_click(self,ctx):
        if self.active:
            return super().on_click(ctx)
        self._pick(ctx.viewport,ctx.screen.x(),ctx.screen.y())
        if not self.begin_selected(ctx.viewport):
            ctx.viewport.flash_status('已選取。右鍵選 UV 投影或載入貼圖；雙擊群組可進入後調整單面。',6000)

    def on_double_click(self,ctx):
        self.commit(ctx.viewport)
        selector=self.app.window._tools.get('select')
        if selector is not None:
            selector.on_double_click(ctx)

    def _exit(self,viewport):
        self._leave(viewport)
        viewport.update()
        viewport.flash_status('UV 工具：點下一個面或右鍵選投影。',4000)

    def commit(self,viewport):
        if not self.active:
            return
        if self.changed():
            tex=self.result_texture()
            if tex is not None:
                viewport.history.execute(self.side_command(self.face,self.side,tex))
                viewport.notify_scene_changed()
        self._exit(viewport)

    def on_deactivate(self,viewport):
        self.commit(viewport)
        self._leave(viewport)

    def run(self,callback):
        try:
            self.commit(self.app.viewport)
            callback()
            self.app.viewport.flash_status('UV 已套用，Ctrl+Z 可復原。',4000)
        except Exception as error:
            self.app.viewport.flash_status('未套用：'+str(error),7000)

    def project(self,mode):
        def apply():
            camera=self.app.viewport.camera
            forward=camera.forward()
            right=QVector3D.crossProduct(forward,camera.up_vector()).normalized()
            up=QVector3D.crossProduct(right,forward).normalized()
            execute(self.app,lambda ts:uv.projection(ts,mode,self.axis,self.tile_cm,0.,(right,up)),label='UV 投影')
        self.run(apply)

    def adjust(self,degrees=0.,su=1.,sv=1.):
        if self.active:
            if degrees:
                self.rotate(degrees)
            if su<0:
                self.flip(True)
            if sv<0:
                self.flip(False)
            self.app.viewport.update()
        else:
            self.run(lambda:execute(self.app,lambda ts:uv.transform(ts,su=su,sv=sv,degrees=degrees),label='UV 調整'))

    def image(self):
        self.commit(self.app.viewport)
        filename,_=QFileDialog.getOpenFileName(self.app.window,'選擇貼圖圖片','','圖片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if filename:
            self.run(lambda:execute(self.app,lambda ts:uv.assign_texture(ts,filename),label='載入貼圖'))
            self.begin_selected(self.app.viewport)

    def set_size(self):
        size,ok=QInputDialog.getDouble(self.app.window,'貼圖尺寸','每張貼圖覆蓋尺寸（cm）',self.tile_cm,.01,100000.,2)
        if ok:
            self.tile_cm=size

    def build_menu(self,viewport):
        menu=QMenu(viewport)
        if self.active:
            menu.addAction('套用拖曳結果（Enter）',lambda:self.commit(viewport))
            menu.addAction('取消拖曳結果',lambda:self._exit(viewport))
            menu.addAction('還原目前貼圖位置',lambda:(self.reset(),viewport.update()))
        else:
            menu.addAction('用滑鼠移動／縮放貼圖',lambda:self.begin_selected(viewport))
        menu.addSeparator()
        projection=menu.addMenu('UV 投影方式')
        for label,mode in [('平面投影','planar'),('盒狀投影','box'),('圓柱投影','cylinder'),('球面投影','sphere'),('四邊面鋪滿','quad'),('依目前視角投影','view')]:
            projection.addAction(label,lambda checked=False,mode=mode:self.project(mode))
        axis=menu.addMenu('投影方向')
        for i,label in enumerate(['X 軸／YZ 平面','Y 軸／XZ 平面','Z 軸／XY 平面']):
            action=axis.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.axis==i)
            action.triggered.connect(lambda checked=False,i=i:setattr(self,'axis',i))
        menu.addAction(f'貼圖尺寸：{self.tile_cm:g} cm…',lambda:QTimer.singleShot(0,self.set_size))
        rotate=menu.addMenu('旋轉／鏡射')
        rotate.addAction('旋轉 90°',lambda:self.adjust(degrees=90))
        rotate.addAction('旋轉 -90°',lambda:self.adjust(degrees=-90))
        rotate.addAction('水平鏡射',lambda:self.adjust(su=-1))
        rotate.addAction('垂直鏡射',lambda:self.adjust(sv=-1))
        menu.addSeparator()
        menu.addAction('載入貼圖圖片…',lambda:QTimer.singleShot(0,self.image))
        menu.addAction('套用檢查格',lambda:self.run(lambda:execute(self.app,lambda ts:uv.assign_texture(ts,Path(__file__).with_name('checker.png')),label='UV 檢查格')))
        menu.addAction('進階 UV／匯入匯出…',lambda:QTimer.singleShot(0,self.advanced))
        menu.addAction('結束工具',lambda:self.app.window._activate_tool('select'))
        return menu

    def context_menu(self,viewport,global_pos):
        if not self.active:
            pos=viewport.mapFromGlobal(global_pos)
            self._pick(viewport,pos.x(),pos.y())
        self.build_menu(viewport).exec(global_pos)
        return True
