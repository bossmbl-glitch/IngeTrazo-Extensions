# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct mouse translation of plugin lights; one document edit per release."""
import math
from PySide6.QtCore import QObject,QEvent,Qt,QPointF
from PySide6.QtGui import QVector3D,QColor,QPen


class LightDrag(QObject):
    def __init__(self,panel):
        super().__init__(panel)
        self.panel=panel;self.viewport=panel.app.viewport;self.state=None;self.eat_release=False
        self.viewport.installEventFilter(self)

    def available(self):
        if getattr(self.panel,'use_proxies',False):return False
        vp=self.viewport
        tool=getattr(vp,'active_tool',None)
        pending=self.panel.pending
        return (getattr(vp,'nav_mode',None) is None and
                (type(tool).__name__ in ('SelectTool','MoveTool') or
                 bool(pending and pending['command']=='move')))

    def handles(self,light):
        vp=self.viewport;pos=QVector3D(*light['pos']);a=self.panel.pixel(vp,light['pos'])
        if a is None:return []
        _,normal=vp._pixel_to_ray(vp.width()/2,vp.height()/2)
        if normal is None:return []
        p=vp._ray_hit_plane(a,(pos,normal));q=vp._ray_hit_plane((a[0]+60,a[1]),(pos,normal))
        if p is None or q is None:return []
        length=(q-p).length();result=[]
        for i,(label,color) in enumerate((('X','#ee5d5d'),('Y','#66cc78'),('Z','#559dff'))):
            axis=QVector3D(1 if i==0 else 0,1 if i==1 else 0,1 if i==2 else 0)
            end=pos+axis*length;b=self.panel.pixel(vp,[end.x(),end.y(),end.z()])
            if b is None or math.dist(a,b)<18:continue
            result.append((axis,a,b,label,color))
        return result

    def hit_axis(self,light,x,y):
        best=None;distance=9
        for axis,a,b,_,_ in self.handles(light):
            dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy)
            t=((x-a[0])*dx+(y-a[1])*dy)/(length*length)
            if t<14/length or t>1.12:continue
            d=math.hypot(x-a[0]-t*dx,y-a[1]-t*dy)
            if d<distance:best=axis;distance=d
        return best

    def select_light(self,identity):
        # Keep the host's extension selection in sync, so its Delete key and
        # deselection callbacks still work after we consume the mouse press.
        vp=self.viewport
        entry=next((p for p in getattr(vp,'_ext_pickables',[]) if p['pick']==self.panel.pick),None)
        if entry is not None:
            vp.clear_extension_pick()
            vp.extension_pick=(entry,identity)
        self.panel.select(identity)

    def press(self,x,y):
        panel=self.panel;vp=self.viewport;light=panel.current();axis=None
        if light:axis=self.hit_axis(light,x,y)
        if axis is None:
            identity=panel.pick(vp,x,y)
            light=next((item for item in panel.lights() if item['id']==identity),None)
        if light is None:return False
        _,normal=vp._pixel_to_ray(vp.width()/2,vp.height()/2)
        if normal is None:return False
        if axis is not None:
            normal=normal-axis*QVector3D.dotProduct(normal,axis)
            if normal.length()<.05:return False
            normal.normalize()
        origin=QVector3D(*light['pos']);plane=(origin,normal)
        anchor=vp._ray_hit_plane((x,y),plane)
        if anchor is None:return False
        self.select_light(light['id'])
        self.state={'scene':panel.app.scene,'light':light,'plane':plane,'anchor':anchor,
                    'axis':axis,'screen':(x,y),'pos':light['pos'][:],'moved':False}
        vp.setFocus();vp.grabMouse();vp.setCursor(Qt.CursorShape.ClosedHandCursor)
        panel.app.window.statusBar().showMessage('拖曳光源；放開定位，Esc 取消。紅 X／綠 Y／藍 Z 可限制方向。')
        vp.update();return True

    def move(self,x,y):
        state=self.state
        if state is None:return
        if state['scene'] is not self.panel.app.scene or not self.available():self.cancel();return
        point=self.viewport._ray_hit_plane((x,y),state['plane'])
        if point is None:return
        if not state['moved'] and math.dist((x,y),state['screen'])<3:return
        state['moved']=True;delta=point-state['anchor']
        if state['axis'] is not None:delta=state['axis']*QVector3D.dotProduct(delta,state['axis'])
        pos=QVector3D(*state['light']['pos'])+delta
        values=[pos.x(),pos.y(),pos.z()]
        if all(math.isfinite(v) and abs(v)<=1e7 for v in values):state['pos']=values
        self.viewport.update()

    def preview(self):
        state=self.state
        return self.panel.translated(state['light'],state['pos']) if state and state['moved'] else None

    def cancel(self):
        active=self.state is not None;self.state=None
        if active:
            self.eat_release=True;self.viewport.releaseMouse();self.viewport.unsetCursor()
            self.panel.app.window.statusBar().clearMessage();self.viewport.update()

    def release(self,x,y):
        self.move(x,y);state=self.state;self.cancel();self.eat_release=False
        if not state or not state['moved'] or math.dist(state['pos'],state['light']['pos'])<1e-7:return
        panel=self.panel;identity=state['light']['id'];lights=panel.lights()
        if state['scene'] is not panel.app.scene:return
        if not any(light['id']==identity for light in lights):return
        panel.store([panel.translated(light,state['pos']) if light['id']==identity else light for light in lights])
        self.select_light(identity)

    def eventFilter(self,obj,event):
        kind=event.type()
        if kind==QEvent.Type.ShortcutOverride and self.state and event.key()==Qt.Key.Key_Escape:
            event.accept();return True
        if kind==QEvent.Type.KeyPress and self.state and event.key()==Qt.Key.Key_Escape:
            self.cancel();return True
        if kind in (QEvent.Type.FocusOut,QEvent.Type.Hide,QEvent.Type.UngrabMouse):
            if self.state:self.cancel()
        if kind==QEvent.Type.MouseButtonRelease and event.button()==Qt.MouseButton.LeftButton:
            if self.state:self.release(event.position().x(),event.position().y());return True
            if self.eat_release:self.eat_release=False;return True
        if kind==QEvent.Type.MouseMove and self.state:
            self.move(event.position().x(),event.position().y());return True
        if kind==QEvent.Type.MouseButtonPress:
            if self.state:return True
            self.eat_release=False
            if event.button()==Qt.MouseButton.LeftButton and event.modifiers()==Qt.KeyboardModifier.NoModifier and self.available():
                return self.press(event.position().x(),event.position().y())
        return False

    def draw(self,viewport,painter):
        if not self.available():return
        light=self.preview() or self.panel.current()
        if not light:return
        for axis,a,b,label,color in self.handles(light):
            painter.setPen(QPen(QColor(color),3));painter.drawLine(QPointF(*a),QPointF(*b))
            dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy);ux,uy=dx/length,dy/length
            for sign in (-1,1):painter.drawLine(QPointF(*b),QPointF(b[0]-ux*9+sign*uy*5,b[1]-uy*9-sign*ux*5))
            painter.drawText(QPointF(b[0]+6,b[1]-5),label)
