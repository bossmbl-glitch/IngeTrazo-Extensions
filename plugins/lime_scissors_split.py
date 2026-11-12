# SPDX-License-Identifier: MIT
"""IngeTrazo 0.6.0: plane scissors, two capped groups, one undo step.

Version 1.2.0. Uses IngeTrazo's solid conversion to preserve source paint/UV.
"""
from __future__ import annotations

from copy import deepcopy
from math import cos, sin, radians

import numpy as np
from PySide6.QtCore import Qt, QPointF, QByteArray
from PySide6.QtGui import QVector3D, QColor, QPen, QPolygonF, QIcon, QPixmap
from PySide6.QtWidgets import QToolBar


from core.group import Group, world_mesh
from core.history import group_owner_list, Command
from core.solids import (
    _Sources, _to_manifold, _loops_from, _group_from_loops, solid_report,
    SolidOperationCommand,
)

VERSION = '1.2.0'


def xyz(v):
    return np.array([v.x(), v.y(), v.z()], dtype=float)


def normal_for(axis, azimuth=0., elevation=0.):
    if axis < 3:
        return np.eye(3)[axis]
    a, e = radians(azimuth), radians(elevation)
    return np.array([cos(e)*cos(a), cos(e)*sin(a), sin(e)])


def bounds_points(group):
    mesh = world_mesh(group)
    points = np.array([xyz(v.position) for v in mesh.vertices])
    if not len(points):
        raise ValueError('這個群組沒有可切割的幾何。')
    lo, hi = points.min(axis=0), points.max(axis=0)
    corners = np.array([[x,y,z] for x in (lo[0],hi[0])
                        for y in (lo[1],hi[1]) for z in (lo[2],hi[2])])
    return points, corners


def plane_corners(corners, normal, offset):
    center = corners.mean(axis=0)
    center += normal * (offset - np.dot(center, normal))
    ref = np.array([0.,0.,1.]) if abs(normal[2]) < .9 else np.array([0.,1.,0.])
    u = np.cross(normal,ref)
    u /= np.linalg.norm(u)
    v = np.cross(normal,u)
    r = max(np.linalg.norm(corners.max(axis=0)-corners.min(axis=0))*.6, .001)
    return np.array([center-u*r-v*r, center+u*r-v*r,
                     center+u*r+v*r, center-u*r+v*r])


def split_group(target, normal, offset):
    """Read source only; return two validated, capped world-space groups."""
    import manifold3d as m3d
    n = np.asarray(normal, dtype=float)
    length = np.linalg.norm(n)
    if not np.isfinite(n).all() or not np.isfinite(offset) or length < 1e-12:
        raise ValueError('切割平面的設定無效。')
    # Offset is measured along the unit normal, as in the position controls.
    n = n/length
    mesh = world_mesh(target)
    solid, _ = solid_report(mesh)
    if not solid:
        raise ValueError('群組目前不是封閉實體。請先處理破面、重疊面或內部接合面，再使用剪刀。')
    sources = _Sources()
    original = _to_manifold(target, sources)
    if original.status() != m3d.Error.NoError or original.volume() <= 1e-12:
        raise ValueError('無法將這個群組辨識為有效實體，原模型未變更。')
    positive, negative = original.split_by_plane(tuple(n), float(offset))
    if positive.is_empty() or negative.is_empty():
        raise ValueError('切割平面必須穿過模型內部；請調整位置後再剪開。')
    volume = original.volume()
    if abs(positive.volume()+negative.volume()-volume) > max(1e-10, volume*1e-7):
        raise ValueError('切割前後體積驗證未通過，原模型未變更。')
    made = []
    scale = max(np.linalg.norm(np.ptp(np.array([xyz(v.position) for v in mesh.vertices]),axis=0)), 1.)
    tolerance = max(1e-7, scale*1e-7)
    for man, suffix in ((negative,'A'),(positive,'B')):
        if man.status() != m3d.Error.NoError:
            raise ValueError('其中一側無法形成有效實體，原模型未變更。')
        loops = _loops_from(man,sources)
        tagged = []
        # Plane-created triangles may reuse arbitrary face IDs in the kernel.
        # Geometric classification assigns one cap source distinct from paint.
        cap_id = len(sources.attrs)
        for pts, attrs, fid in loops:
            is_cap = all(abs(np.dot(n,xyz(p))-offset) <= tolerance for p in pts)
            if is_cap:
                attrs, fid = {}, cap_id
            tagged.append((pts,attrs,fid))
        result = _group_from_loops(tagged,sources,f'{target.name}_{suffix}',target.layer)
        result.material = deepcopy(getattr(target,'material',None))
        ok, result_volume = solid_report(result.mesh)
        if not ok or abs(result_volume-man.volume()) > max(1e-9, volume*2e-5):
            raise ValueError('切口補面驗證未通過，原模型未變更。')
        made.append(result)
    return made


class PlaneSplitCommand(SolidOperationCommand):
    """Reuse native group replacement, selection restoration and undo/redo."""
    def __init__(self,target,created):
        super().__init__('split',[target])
        self.removed = [target]
        self.created = list(created)


class BatchPlaneSplitCommand(Command):
    """Replace each source in its own slot, all as one undoable operation."""
    def __init__(self,pairs):
        self.pairs=list(pairs)
        self.before=None
        self.after=None
        self.selection_before=None
        self.selection_after=None

    def do(self,scene):
        if self.before is None:
            owners=[]
            for target,_ in self.pairs:
                owner=group_owner_list(scene,target)
                if owner is None:
                    raise ValueError('來源群組已改變，請重新選取後切割。')
                if not any(owner is other for other in owners):
                    owners.append(owner)
            self.before=[(owner,list(owner)) for owner in owners]
            replacements=dict(self.pairs)
            self.after=[(owner,[out for group in content
                                for out in replacements.get(group,[group])])
                        for owner,content in self.before]
            self.selection_before=set(scene.selection)
            self.selection_after=(self.selection_before-set(replacements))|{
                out for _,created in self.pairs for out in created}
        for owner,content in self.after:
            owner[:]=content
        scene.selection.clear()
        scene.selection.update(self.selection_after)
        scene.version+=1

    def undo(self,scene):
        for owner,content in self.before:
            owner[:]=content
        scene.selection.clear()
        scene.selection.update(self.selection_before)
        scene.version+=1


class ScissorsSession:
    def __init__(self,app,targets):
        targets=[targets] if isinstance(targets,Group) else list(targets)
        self.app,self.vp,self.targets=app,app.viewport,targets
        self.target=targets[0]
        self.scene=self.vp.scene
        self.version=self.scene.content_version
        self.group_points=[bounds_points(g)[0] for g in targets]
        self.points=np.concatenate(self.group_points)
        lo,hi=self.points.min(axis=0),self.points.max(axis=0)
        self.corners=np.array([[x,y,z] for x in (lo[0],hi[0])
                              for y in (lo[1],hi[1]) for z in (lo[2],hi[2])])
        self.axis=0
        self.anchor=self.points.mean(axis=0)
        self.active=True
        self.old_tool=self.vp.active_tool
        self.notice=''
        self.last_screen=None
        self.marker=None
        self.snapped=False
        # Exact ray hits on the selected object's skin avoid a ground-plane
        # cursor making every cut stick at the bounding-box end.
        meshes=[world_mesh(g) for g in targets]
        triangles=[tuple(xyz(p) for p in tri) for mesh in meshes
                   for f in mesh.faces for tri in f.triangulate(f.normal())]
        self.triangles=np.asarray(triangles,dtype=float).reshape(-1,3,3)
        self.edges=np.asarray([(xyz(e.a),xyz(e.b)) for mesh in meshes for e in mesh.edges
                               if not e.hidden and not e.soft],dtype=float).reshape(-1,2,3)
        self.tool=_make_mouse_tool(self)
        self.update_marker()

    def plane(self):
        n=np.eye(3)[self.axis]
        return n,float(np.dot(n,self.anchor))

    def hint(self):
        direction=('X 左右','Y 前後','Z 上下')[self.axis]
        return f'剪刀 [{direction}] {len(self.targets)} 個群組｜Ctrl 切換方向｜左鍵剪開｜Esc 取消'

    def valid_context(self):
        return (self.app.viewport is self.vp and self.vp.scene is self.scene
                and self.scene.content_version==self.version
                and all(group_owner_list(self.scene,g) is not None for g in self.targets))

    def ray_hit(self,origin,direction):
        tri=self.triangles
        if not len(tri):
            return None
        e1,e2=tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]
        h=np.cross(direction,e2)
        det=np.einsum('ij,ij->i',e1,h)
        usable=np.abs(det)>1e-12
        inv=np.zeros(len(det))
        inv[usable]=1./det[usable]
        delta=origin-tri[:,0]
        u=np.einsum('ij,ij->i',delta,h)*inv
        q=np.cross(delta,e1)
        v=(q@direction)*inv
        t=np.einsum('ij,ij->i',e2,q)*inv
        hits=usable&(u>=-1e-8)&(v>=-1e-8)&(u+v<=1.+1e-8)&(t>=0)
        return float(t[hits].min()) if hits.any() else None

    def edge_snap(self,ctx):
        if not len(self.edges):
            return None
        px,py,front=self.vp.world_to_pixels(self.edges.reshape(-1,3))
        screen=np.column_stack((px,py)).reshape(-1,2,2)
        visible=np.asarray(front).reshape(-1,2).all(axis=1)
        delta=screen[:,1]-screen[:,0]
        length2=np.einsum('ij,ij->i',delta,delta)
        cursor=np.array([ctx.screen.x(),ctx.screen.y()])
        parameter=np.clip(np.einsum('ij,ij->i',cursor-screen[:,0],delta)/np.maximum(length2,1e-15),0,1)
        closest=screen[:,0]+delta*parameter[:,None]
        distances=np.linalg.norm(closest-cursor,axis=1)
        candidates=np.flatnonzero(visible&(length2>1e-8)&(distances<=12.))
        ray=getattr(self.vp,'_pixel_to_ray',None)
        for index in sorted(candidates,key=lambda i:distances[i]):
            a,b=self.edges[index]
            t=parameter[index]
            if ray is not None:
                origin,direction=ray(*closest[index])
                origin,direction=xyz(origin),xyz(direction)
                segment=b-a
                w=origin-a
                aa=float(np.dot(direction,direction))
                bb=float(np.dot(direction,segment))
                cc=float(np.dot(segment,segment))
                dd=float(np.dot(direction,w))
                ee=float(np.dot(segment,w))
                denominator=aa*cc-bb*bb
                if abs(denominator)>1e-16:
                    t=np.clip((aa*ee-bb*dd)/denominator,0.,1.)
                point=a+(b-a)*t
                hit=self.ray_hit(origin,direction)
                depth=float(np.dot(point-origin,direction)/aa)
                tolerance=max(1e-5,float(np.linalg.norm(np.ptp(self.points,axis=0)))*1e-5)
                if hit is not None and depth-hit>tolerance:
                    continue  # A back edge must not snap through the front skin.
            return a+(b-a)*t
        return None

    def update_marker(self):
        self.marker=None
        if not len(self.edges):
            return
        n,off=self.plane()
        a,b=self.edges[:,0],self.edges[:,1]
        da,db=a@n-off,b@n-off
        denom=da-db
        mask=(np.abs(denom)>1e-12)&(da*db<=1e-12)
        if not mask.any():
            return
        points=a[mask]+(b-a)[mask]*(da[mask]/denom[mask])[:,None]
        px,py,front=self.vp.world_to_pixels(points)
        ids=np.flatnonzero(front)
        if not len(ids):
            return
        if self.last_screen is not None:
            scores=(px-self.last_screen.x())**2+(py-self.last_screen.y())**2
        else:
            scores=np.linalg.norm(points-self.anchor,axis=1)
        self.marker=points[min(ids,key=lambda i:scores[i])]

    def intersects(self,points):
        _,off=self.plane()
        return points[:,self.axis].min()+1e-7<off<points[:,self.axis].max()-1e-7

    def cursor_point(self,ctx):
        ray=getattr(self.vp,'_pixel_to_ray',None)
        if ray is None:
            return xyz(ctx.world)
        origin,direction=ray(ctx.screen.x(),ctx.screen.y())
        origin,direction=xyz(origin),xyz(direction)
        hit=self.ray_hit(origin,direction)
        if hit is not None:
            return origin+direction*hit
        # Off-object dragging follows a camera-facing plane through the center.
        camera=getattr(self.vp,'camera',None)
        eye=getattr(camera,'eye',None)
        normal=xyz(eye())-self.points.mean(axis=0) if callable(eye) else -direction
        denom=float(np.dot(direction,normal))
        if abs(denom)>1e-12:
            t=float(np.dot(self.points.mean(axis=0)-origin,normal)/denom)
            if t>=0:
                return origin+direction*t
        return xyz(ctx.world)

    def hover(self,ctx):
        if not self.valid_context():
            self.finish('模型或專案已變更，請重新選取群組再按剪刀。')
            return False
        self.last_screen=ctx.screen
        snap=self.edge_snap(ctx)
        self.snapped=snap is not None
        self.anchor=snap if self.snapped else self.cursor_point(ctx)
        self.update_marker()
        if self.snapped:
            self.marker=self.anchor.copy()
        self.notice=''
        self.vp.update()
        return True

    def cycle(self):
        self.axis=(self.axis+1)%3
        self.update_marker()
        if self.snapped:
            self.marker=self.anchor.copy()
        self.notice=''
        self.vp.flash_status(self.hint(),7000)
        self.vp.update()

    def cut(self,ctx):
        if not self.hover(ctx):
            return
        try:
            n,off=self.plane()
            pairs=[]
            skipped=0
            # Prepare every cut before changing the scene: failure on one
            # selected group must not leave the others half-processed.
            for target,points in zip(self.targets,self.group_points):
                if not self.intersects(points):
                    skipped+=1
                    continue
                pairs.append((target,split_group(target,n,off)))
            if not pairs:
                raise ValueError('目前切割平面沒有穿過任何選取群組，請移到模型內部。')
            self.vp.history.execute(BatchPlaneSplitCommand(pairs))
            error=getattr(self.vp.history,'last_error',None)
            rejected=getattr(self.vp.history,'last_rejected',None)
            if error or rejected:
                raise ValueError(str(error or rejected))
            self.vp.notify_scene_changed()
            message=f'已切開 {len(pairs)} 個群組，產生 {len(pairs)*2} 個補面群組。Ctrl+Z 可復原。'
            if skipped:
                message+=f' {skipped} 個未相交群組保持原狀。'
            self.finish(message)
        except Exception as exc:
            self.notice=str(exc)
            self.vp.flash_status('未完成切割：'+self.notice,8000)
            self.vp.update()

    def finish(self,message='已取消剪刀，原模型未變更。'):
        self.active=False
        if self.vp.active_tool is self.tool:
            activate=getattr(self.app.window,'_activate_tool',None)
            if callable(activate):
                activate('select')
            else:
                self.vp.set_active_tool(self.old_tool)
        self.vp.flash_status(message,7000)
        self.vp.update()


def _make_mouse_tool(session):
    from tools.base import Tool

    class MouseScissors(Tool):
        name='剪刀分割群組'
        description='滑鼠指定位置，Ctrl 切換 X／Y／Z 方向，左鍵剪開，Esc 取消。'
        uses_snap=False

        def on_activate(self,viewport):
            viewport.flash_status(session.hint(),8000)

        def on_deactivate(self,viewport):
            session.active=False
            viewport.update()

        def claims_key(self,key,modifiers):
            # Native viewport checks this before numeric entry and shortcuts,
            # and delivers only the first press, never modifier auto-repeat.
            return key in (Qt.Key_Control,Qt.Key_Escape)

        def on_key(self,viewport,key,modifiers):
            if key==Qt.Key_Control:
                session.cycle()
                return True
            if key==Qt.Key_Escape:
                session.finish()
                return True
            return False

        def on_key_release(self,viewport,key):
            if key==Qt.Key_Control:
                viewport._ctrl_alone=False
                return True
            return False

        def on_hover(self,ctx):
            session.hover(ctx)

        def on_click(self,ctx):
            session.cut(ctx)

        def on_double_click(self,ctx):
            pass  # Never run a second cut from the double-click's extra event.

        def on_cancel(self,viewport):
            session.finish()

        def drag_plane(self,viewport):
            center=QVector3D(*session.points.mean(axis=0))
            camera=getattr(viewport,'camera',None)
            eye=getattr(camera,'eye',None)
            normal=eye()-center if callable(eye) else QVector3D(0,-1,0)
            return center,normal

    return MouseScissors()


def _draw_preview(app,viewport,painter):
    session=getattr(app.window,'_lime_scissors_session',None)
    if (session is None or not session.active or viewport is not session.vp
            or viewport.scene is not session.scene):
        return
    n,off=session.plane()
    corners=plane_corners(session.corners,n,off)
    px,py,front=viewport.world_to_pixels(corners)
    outside=not any(session.intersects(points) for points in session.group_points)
    color=QColor(220,65,55,230) if outside or session.notice else QColor(210,30,205,230)
    if all(front):
        painter.setPen(QPen(color,2))
        fill=QColor(color)
        fill.setAlpha(40)
        painter.setBrush(fill)
        painter.drawPolygon(QPolygonF([QPointF(float(x),float(y)) for x,y in zip(px,py)]))
    if session.marker is not None:
        mx,my,mfront=viewport.world_to_pixels(session.marker[None,:])
        if mfront[0]:
            point=QPointF(float(mx[0]),float(my[0]))
            painter.setPen(QPen(QColor(255,255,255),3))
            painter.setBrush(QColor(220,25,210))
            painter.drawEllipse(point,6.,6.)
            painter.setPen(QPen(QColor(100,0,105),1))
            painter.drawEllipse(point,6.,6.)
    painter.setPen(QColor(45,45,45))
    painter.setBrush(QColor(255,255,255,225))
    text=session.notice or session.hint()
    # Compact on-canvas hint is the only interface while cutting.
    metrics=painter.fontMetrics()
    width=metrics.horizontalAdvance(text)+18
    painter.drawRoundedRect(12,12,width,metrics.height()+14,5,5)
    painter.drawText(21,19+metrics.ascent(),text)


def open_scissors(app):
    old=getattr(app.window,'_lime_scissors_session',None)
    if old is not None and old.active:
        old.finish('')
    scene=app.scene
    selected=list(scene.selection)
    if not selected or any(not isinstance(g,Group) for g in selected):
        app.viewport.flash_status('請先選取一個或多個群組／元件，再按剪刀圖示。',7000)
        return
    context=scene.groups if scene.edit_group is None else (scene.edit_group.children or [])
    for target in selected:
        visible,locked=scene._layer_state(target)
        if scene.entity_locked(target) or not scene.entity_visible(target) or not visible or locked:
            app.viewport.flash_status('請先顯示並解除鎖定要切割的群組。',7000)
            return
        if target not in context:
            app.viewport.flash_status('請在相同編輯層級選取群組，再按剪刀。',7000)
            return
    selected.sort(key=context.index)
    try:
        session=ScissorsSession(app,selected)
        app.window._lime_scissors_session=session
        app.viewport.set_active_tool(session.tool)
        app.viewport.setFocus()
    except Exception as exc:
        app.viewport.flash_status('無法啟動剪刀：'+str(exc),8000)


def setup(app):
    action=app.add_menu_action('剪刀：滑鼠直接分割 v1.2.0',lambda:open_scissors(app),
        tip='滑鼠預覽位置，Ctrl 切換方向，左鍵剪開，Esc 取消。')
    if action is None:
        return
    svg=b'''<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
    <rect x="3" y="3" width="58" height="58" rx="12" fill="#eef3f7"/>
    <path d="M23 39L49 13M30 34L50 51" fill="none" stroke="#37546b" stroke-width="5" stroke-linecap="round"/>
    <circle cx="19" cy="45" r="9" fill="none" stroke="#208f86" stroke-width="5"/>
    <circle cx="20" cy="19" r="9" fill="none" stroke="#208f86" stroke-width="5"/>
    <path d="M26 25L35 34" stroke="#37546b" stroke-width="5"/><circle cx="33" cy="33" r="3" fill="#dc40c7"/>
    </svg>'''
    pixmap=QPixmap()
    pixmap.loadFromData(QByteArray(svg),'SVG')
    action.setIcon(QIcon(pixmap))
    action.setAutoRepeat(False)
    toolbar=QToolBar('剪刀分割',app.window)
    toolbar.setObjectName('lime_scissors_split_toolbar')
    toolbar.addAction(action)
    app.window.addToolBar(toolbar)
    app.add_overlay(lambda viewport,painter:_draw_preview(app,viewport,painter))

