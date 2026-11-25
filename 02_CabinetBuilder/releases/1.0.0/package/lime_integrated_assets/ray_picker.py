"""Pixel tools must work even when the host cannot intersect its drawing plane."""
from PySide6.QtCore import QObject,QEvent,Qt,QTimer
from tools.base import Tool


class RayPicker(Tool):
    uses_snap=False
    name='點選表面'
    def __init__(self,viewport,choose,cancel,name='點選表面',consume_hover=False,hover_interval_ms=0):
        self.viewport=viewport;self.choose=choose;self.cancel=cancel;self.name=name;self.filter=None;self.consume_hover=consume_hover;self.hover_interval_ms=hover_interval_ms;self.timer=None;self.pending=None
    def on_activate(self,vp):
        tool=self
        self.timer=QTimer(vp);self.timer.setSingleShot(True)
        def flush():
            sample=tool.pending;tool.pending=None
            if sample is not None and vp.active_tool is tool and getattr(vp,'nav_mode',None) is None:tool.choose(*sample,False)
        self.timer.timeout.connect(flush)
        class Filter(QObject):
            def eventFilter(self,obj,event):
                if vp.active_tool is not tool:return False
                if event.type() in (QEvent.MouseMove,QEvent.MouseButtonPress) and (getattr(vp,'nav_mode',None) is not None or event.buttons() not in (Qt.NoButton,Qt.LeftButton)):
                    tool.timer.stop();tool.pending=None
                if event.type()==QEvent.MouseMove and event.buttons()==Qt.NoButton and getattr(vp,'nav_mode',None) is None:
                    if tool.hover_interval_ms:
                        tool.pending=(event.position().x(),event.position().y())
                        if not tool.timer.isActive():tool.timer.start(tool.hover_interval_ms)
                    else:tool.choose(event.position().x(),event.position().y(),False)
                    return tool.consume_hover
                if event.type()==QEvent.MouseButtonPress and event.button()==Qt.LeftButton and getattr(vp,'nav_mode',None) is None:
                    tool.timer.stop();tool.pending=None
                    tool.choose(event.position().x(),event.position().y(),True);return True
                return False
        self.filter=Filter(vp);vp.installEventFilter(self.filter)
    def on_deactivate(self,vp):
        self.pending=None
        if self.timer is not None:self.timer.stop();self.timer.deleteLater();self.timer=None
        if self.filter is not None:vp.removeEventFilter(self.filter);self.filter.deleteLater();self.filter=None
        self.cancel()
    def claims_key(self,key,mods):return key==Qt.Key_Escape or (key==Qt.Key_Backspace and callable(getattr(self,'undo_placement',None)))
    def on_key(self,vp,key,mods):
        if key==Qt.Key_Escape:self.cancel();return True
        if key==Qt.Key_Backspace and callable(getattr(self,'undo_placement',None)):self.undo_placement();return True
        return False
    def on_cancel(self,vp):self.cancel()


def install_front_picker(api,window_class):
    def start_front_pick(window):
        def start():
            window.check();window.ensure_target()
            if window._conversion_source is None:raise ValueError('請先讀取選取量體。')
            window.stop_front_pick(False)
            window._front_pick_old=window.vp.active_tool
            window._front_pick_saved=(window.volume_front.currentData(),{k:window.fields[k].value() for k in ('width','height','depth')},api.QMatrix4x4(window.rigid))
            def choose(x,y,commit):
                try:
                    window.check();window.ensure_target()
                    origin,direction=window.vp._pixel_to_ray(x,y)
                    if origin is None or direction is None:return False
                    face=api.volume_front_from_ray(window._conversion_data,api.xyz(origin),api.xyz(direction))
                    if face is None:
                        if commit:window.message('請點選已讀取量體的表面。',True)
                        return False
                    # The validated source is a cuboid. Its own exact ray hit is
                    # authoritative; native pick_group can return a nested proxy.
                    window.volume_front.setCurrentIndex(window.volume_front.findData(face))
                    if commit:window.stop_front_pick(True);window.message('已確認正面；可預覽後轉換量體。')
                    return True
                except Exception as exc:
                    window.message(str(exc),True);window.stop_front_pick(False);return False
            window._front_pick_tool=RayPicker(window.vp,choose,lambda:window.stop_front_pick(False),'直接選取量體正面')
            window.vp.set_active_tool(window._front_pick_tool);window.vp.setFocus()
            return '直接點橘色預覽或量體上的面以確認正面；Esc 取消。'
        window.run(start)
    window_class.start_front_pick=start_front_pick
