"""Native viewport grips with piecewise, fixed-thickness geometry resizing."""
from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtGui import QVector3D, QMatrix4x4, QVector4D
from PySide6.QtWidgets import QMenu, QInputDialog
from tools.scale import ScaleTool, _Grip
from .common import get_targets, xyz, EditCommand, execute
from . import deform


class FrameScaleTool(ScaleTool):
    name = '固定邊框縮放'
    shortcut = None
    icon = 'scale'
    description = '直接拖曳面中心點：只伸縮中間直段，兩端整組形狀與尺寸保持不變。'

    def __init__(self, app, advanced):
        super().__init__()
        self.app, self.advanced = app, advanced
        settings = QSettings()
        self.border = None
        self._bands = ((0.,0.),)*3
        self.middle = [float(settings.value('it_frame_scale/middle_'+a, 0.0)) for a in 'xyz']
        self._preview_command = None
        self._points = []
        self._eligible = ()
        self._error = ''

    @property
    def dragging(self):
        return self._grip is not None

    def on_activate(self, viewport):
        super().on_activate(viewport)
        viewport.flash_status(('無法選取：'+self._error) if self._error else '點物件 → 拖曳面中心點；一次調整一個方向，Esc 取消。', 7000)

    def _selection_bounds(self, viewport):
        self._pick_frame(viewport)
        try:
            targets = get_targets(viewport.scene, connected=True, detached=True)
        except ValueError as error:
            message = str(error) if viewport.scene.selection else ''
            if message and message != self._error:
                viewport.flash_status('無法選取：'+message, 7000)
            self._error = message
            return None, None
        self._error = ''
        local = [self._to_local(v.position) for t in targets for v in t.vertices]
        self._bands = deform.detect_target_bands(targets,self._to_local) if self.border is None else ((self.border,self.border),)*3
        points = [xyz(p) for p in local]
        return (QVector3D(*[min(p[i] for p in points) for i in range(3)]),
                QVector3D(*[max(p[i] for p in points) for i in range(3)]))

    def _refresh_box(self, viewport):
        previous = self._box_version
        super()._refresh_box(viewport)
        if self._grip is not None or self._lo is None or previous == self._box_version:
            return
        ext = self._extents()
        self._eligible = tuple(i for i in range(3) if ext[i] > 1e-6)
        self._grips = []
        for axis in self._eligible:
            for side in (0.,1.):
                p = [.5,.5,.5]
                p[axis] = side
                self._grips.append(_Grip(tuple(p),(axis,)))

    def _active_axes(self):
        return len(self._eligible)

    def _capture_originals(self, viewport):
        targets = get_targets(viewport.scene, connected=True, detached=True)
        self._points = [QVector3D(v.position) for t in targets for v in t.vertices]
        self._preview_command = None

    def _parameters(self, factors):
        ext = self._extents()
        limited = []
        for i, f in enumerate(factors):
            left,right = self._bands[i]
            reserved = (2*max(left,right)+self.middle[i] if self.middle[i] else left+right)
            minimum = (reserved+.05)/(ext[i]*100) if ext[i] > 1e-8 else 1.
            active = self._grip is not None and i in self._grip.mask and i in self._eligible
            limited.append(max(minimum, f) if active and abs(f-1)>1e-7 else 1.)
        frame = QMatrix4x4()
        if self._axes is not None:
            for i,a in enumerate(self._axes):
                frame.setColumn(i, QVector4D(a, 0.))
            frame.setColumn(3, QVector4D(self._orig, 1.))
        params = deform.Parameters(mode='fixed_border', factors=tuple(limited),
            fixed_axes=tuple(i in self._eligible for i in range(3)),
            border_bands_cm=self._bands, center_band_cm=tuple(self.middle),
            frame=frame, anchor=QVector3D(self._anchor), center=self.about_center)
        return params

    def _apply_preview(self, viewport, factors):
        params = self._parameters(factors)
        if all(abs(a-b)<1e-7 for a,b in zip(params.factors,self._factors)):
            return
        self._revert_preview(viewport)
        if all(abs(f-1)<1e-7 for f in params.factors):
            return
        command = EditCommand(viewport.scene, lambda ts: deform.apply(ts,params), True, self.name)
        try:
            command.do(viewport.scene)
            self._preview_command = command
            self._factors = params.factors
            self._error = ''
        except Exception as error:
            self._error = str(error)
            viewport.flash_status('無法縮放：'+self._error, 5000)

    def _revert_preview(self, viewport):
        if self._preview_command is not None:
            self._preview_command.undo(viewport.scene)
            self._preview_command = None
        self._factors = (1.,1.,1.)

    def _commit(self, viewport, factors):
        params = self._parameters(factors)
        self._revert_preview(viewport)
        try:
            if any(abs(f-1)>1e-7 for f in params.factors):
                execute(self.app,lambda ts:deform.apply(ts,params),True,self.name)
        except Exception as error:
            viewport.flash_status('未套用：'+str(error), 6000)
        finally:
            self._reset()
            self._last = None
            self._refresh_box(viewport)
            viewport.update()

    def _grab(self, viewport, grip, screen_xy):
        try:
            self._error=''
            super()._grab(viewport,grip,screen_xy)
        except Exception as error:
            self.on_cancel(viewport)
            viewport.flash_status(str(error),5000)

    def on_release(self,viewport):
        if self._grip is not None and self._moved and self._error and self._preview_command is None:
            error=self._error;self.on_cancel(viewport)
            viewport.flash_status('未套用：'+error,7000);return
        super().on_release(viewport)

    def on_click(self, ctx):
        vp=ctx.viewport
        if self._grip is not None:
            return super().on_click(ctx)
        self._refresh_box(vp)
        if self._lo is not None and self._grip_under(vp,ctx.screen.x(),ctx.screen.y()) is not None:
            return super().on_click(ctx)
        picked=vp.pick_group(ctx.screen.x(),ctx.screen.y()) or vp.pick_face(ctx.screen.x(),ctx.screen.y()) or vp.pick_edge(ctx.screen.x(),ctx.screen.y())
        if picked is not None:
            vp.scene.select([picked])
            self._box_version=-1
            self._refresh_box(vp)
            vp.update()

    def on_cancel(self, viewport):
        self._revert_preview(viewport)
        self._reset()
        self._last=None
        self._refresh_box(viewport)
        viewport.update()

    def on_key(self, viewport, key, modifiers):
        if key == Qt.Key_Shift:
            self.uniform = False
            return True
        if modifiers & Qt.ControlModifier and key in (Qt.Key_Z,Qt.Key_Y,Qt.Key_S):
            self.on_cancel(viewport)
            return False
        if key in (Qt.Key_Enter,Qt.Key_Return) and self._grip is not None:
            self._commit(viewport,self._factors)
            return True
        return super().on_key(viewport,key,modifiers)

    def value_label(self):
        if self._lo is None:
            return None
        sizes=[e*f*100 for e,f in zip(self._extents(),self._factors)]
        pos=self._grip_pos(self._grip) if self._grip is not None else self._to_world(self._hi)
        return (' × '.join(f'{x:.1f}' for x in sizes)+' cm',pos)

    def vcb_caption(self):
        return '倍率／尺寸（例如 150cm）'

    def set_thickness(self, axis=None):
        self.on_cancel(self.app.viewport)
        current=(self.border if self.border is not None else max(v for pair in self._bands for v in pair)) if axis is None else self.middle[axis]
        label='邊框厚度' if axis is None else 'XYZ'[axis]+' 中央保留厚度（0 = 不保留）'
        value,ok=QInputDialog.getDouble(self.app.window,label,label+'（cm）',current,0.,100000.,2)
        if ok:
            if axis is None:
                self.border=value
            else:
                self.middle[axis]=value
                QSettings().setValue('it_frame_scale/middle_'+'xyz'[axis],value)
            self._box_version=-1
            self._refresh_box(self.app.viewport)
            self.app.viewport.update()

    def build_menu(self, viewport):
        menu=QMenu(viewport)
        if hasattr(self, 'soften_panel'):
            self.soften_panel.context(menu, list(viewport.scene.selection))
            menu.addSeparator()
        auto=menu.addAction('依模型自動保留邊框')
        auto.setCheckable(True)
        auto.setChecked(self.border is None)
        def automatic():
            self.on_cancel(viewport)
            self.border=None
            self._box_version=-1
            self._refresh_box(viewport)
            viewport.update()
        auto.triggered.connect(automatic)
        menu.addAction('手動設定保留厚度…',lambda:QTimer.singleShot(0,self.set_thickness))
        mid=menu.addMenu('保留中央立柱／橫檔')
        for i,a in enumerate('XYZ'):
            mid.addAction(f'{a}：{self.middle[i]:g} cm…',lambda checked=False,i=i:QTimer.singleShot(0,lambda:self.set_thickness(i)))
        center=menu.addAction('以中心縮放')
        center.setCheckable(True)
        center.setChecked(self.about_center)
        center.triggered.connect(lambda:self._toggle_center(viewport))
        menu.addSeparator()
        menu.addAction('進階變形設定…',lambda:QTimer.singleShot(0,self.advanced))
        menu.addAction('取消目前拖曳',lambda:self.on_cancel(viewport))
        menu.addAction('結束工具',lambda:self.app.window._activate_tool('select'))
        return menu

    def repair_round(self):
        from .round_section import repair
        self.on_cancel(self.app.viewport)
        results=[]
        try:
            execute(self.app,lambda targets:results.extend(repair(targets)),True,'圓柱截面修成正圓')
            self._box_version=-1;self._refresh_box(self.app.viewport)
            self.app.viewport.flash_status(f'已將 {len(results)} 個圓柱截面修成正圓，保持較小直徑與長度。Ctrl+Z 可復原。',8000)
        except ValueError as error:self.app.viewport.flash_status(str(error),8000)
        return results

    def context_menu(self, viewport, global_pos):
        self.build_menu(viewport).exec(global_pos)
        return True
