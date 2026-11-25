# SPDX-License-Identifier: GPL-3.0-or-later
"""Transactional path editing. Working points and overlays never enter the scene."""
import copy
import hashlib
import math
import numpy as np
from PySide6.QtCore import Qt,QPointF
from PySide6.QtGui import QColor,QPen,QVector3D
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,
    QComboBox,QCheckBox,QPushButton,QDoubleSpinBox)
from core.group import Group
from core.history import Command
from tools.base import Tool
from .profile_operations import build_mesh,_position_fit,_map_mesh


def _homogeneous(matrix):
    result=np.eye(4);result[:,:3]=np.asarray(matrix,dtype=float)
    return result


def _placement_matrix(group):
    transform=getattr(group,'xform',None)
    if transform is None:return np.eye(4)
    origin=transform.map(QVector3D(0,0,0))
    axes=[transform.map(QVector3D(*axis))-origin for axis in ((1,0,0),(0,1,0),(0,0,1))]
    return _homogeneous([v.toTuple() for v in axes]+[origin.toTuple()])


def _fingerprint(group):
    """Detect external edits while the editor is open, not just count changes."""
    index={v:i for i,v in enumerate(group.mesh.vertices)}
    geometry=([v.position.toTuple() for v in group.mesh.vertices],
              [(tuple(index[v] for v in f.loop),tuple(tuple(index[v] for v in h) for h in f.hole_loops),f.attrs,f.interior) for f in group.mesh.faces],
              [(index[e.v0],index[e.v1],e.hidden,e.soft,e.layer,e.curve) for e in group.mesh.edges])
    properties=(group.uid,group.name,group.ext,group.material,group.layer,group.locked,
                group.hidden,bool(group.children),group.component,group.ifc,group.billboard,
                group.axes,group.exploded,group.explode_offset,_placement_matrix(group).tolist())
    return hashlib.sha256(repr((geometry,properties)).encode()).hexdigest()


def _crossing_segments(points,closed,details=False):
    """Reject crossings and collinear overlaps between non-adjacent segments."""
    count=len(points) if closed else len(points)-1
    for i in range(count):
        a=np.asarray(points[i]);b=np.asarray(points[(i+1)%len(points)])
        for j in range(i+2,count):
            if closed and i==0 and j==count-1:continue
            c=np.asarray(points[j]);d=np.asarray(points[(j+1)%len(points)])
            if np.any(np.maximum(a,b)<np.minimum(c,d)-1e-8) or np.any(np.maximum(c,d)<np.minimum(a,b)-1e-8):continue
            u=b-a;v=d-c
            solution,_,rank,_=np.linalg.lstsq(np.column_stack((u,-v)),c-a,rcond=None)
            if rank==2:
                s,t=solution
                if -1e-8<=s<=1+1e-8 and -1e-8<=t<=1+1e-8 and np.linalg.norm(a+s*u-c-t*v)<1e-7:
                    return (i,j) if details else True
            elif np.linalg.norm(np.cross(c-a,u))<1e-9:
                dots=[np.dot(c-a,u)/np.dot(u,u),np.dot(d-a,u)/np.dot(u,u)]
                if max(min(dots),0)<=min(max(dots),1):return (i,j) if details else True
    return False


class ReplacePathCommand(Command):
    """Replace exactly one group in place; preserve list order on undo/redo."""
    keeps_paint=True
    def __init__(self,session,replacement):
        self.session=session;self.old=session.group;self.new=replacement

    def do(self,scene):
        self.session.assert_unchanged(scene)
        scene.groups[scene.groups.index(self.old)]=self.new
        scene.selection.discard(self.old);scene.selection.add(self.new)
        scene.version+=1

    def undo(self,scene):
        if self.new not in scene.groups:raise ValueError('路徑線板已不在原模型中，無法復原。')
        scene.groups[scene.groups.index(self.new)]=self.old
        scene.selection.discard(self.new);scene.selection.add(self.old)
        scene.version+=1


class PathEditSession:
    """One source group, an independent world path, and one atomic replacement."""
    def __init__(self,scene,group):
        from . import PLUGIN_KEY
        if getattr(scene,'edit_group',None) is not None:raise ValueError('請先結束群組編輯，再編輯線板路徑。')
        if group not in scene.groups or not isinstance(group,Group):raise ValueError('請選取一個頂層輪廓群組。')
        if group.locked:raise ValueError('請先解除線板群組鎖定。')
        if group.children:raise ValueError('目前不支援含巢狀子物件的輪廓群組。')
        if group.is_component():raise ValueError('元件實例請先轉為獨立群組。')
        recipe=copy.deepcopy((group.ext or {}).get(PLUGIN_KEY))
        if not isinstance(recipe,dict) or recipe.get('kind')=='stamp' or not recipe.get('path') or not recipe.get('section'):
            raise ValueError('請選取有保存路徑及剖面的線板；單一面戳記不適用。')
        points=np.asarray(recipe['path'],dtype=float)
        if points.ndim!=2 or points.shape[1]!=3 or not 2<=len(points)<=2048 or not np.isfinite(points).all():
            raise ValueError('原線板的路徑資料無效或節點數超出範圍。')
        if not isinstance(recipe.get('settings'),dict):raise ValueError('原線板沒有完整的剖面參數。')
        baseline=build_mesh(recipe)
        def topology(mesh):
            lookup={v:i for i,v in enumerate(mesh.vertices)}
            return sorted((tuple(sorted(lookup[v] for v in face.loop)),
                           tuple(sorted(tuple(sorted(lookup[v] for v in loop)) for loop in face.hole_loops)))
                          for face in mesh.faces)
        if topology(baseline)!=topology(group.mesh) or any(face.interior for face in group.mesh.faces):
            raise ValueError('線板拓撲或特殊面已被手動修改，無法安全編輯生成路徑。')
        attrs=[copy.deepcopy(f.attrs) for f in group.mesh.faces]
        if attrs and any(a!=attrs[0] for a in attrs):
            raise ValueError('線板含逐面不同材質／屬性，無法在改變路徑時完整保留，請先統一逐面設定。')
        self.scene=scene;self.group=group;self.recipe=recipe
        self.attrs=attrs[0] if attrs else {}
        identity=np.vstack((np.eye(3),np.zeros(3)))
        prior=_homogeneous(recipe.get('geometry_transform',identity))
        self.mesh_matrix=prior@_homogeneous(_position_fit(recipe,group))
        self.world_matrix=self.mesh_matrix@_placement_matrix(group)
        if not np.isfinite(self.world_matrix).all() or abs(np.linalg.det(self.world_matrix[:3,:3]))<1e-12:
            raise ValueError('線板包含零縮放或無法反算的定位，無法編輯路徑。')
        self.inverse=np.linalg.inv(self.world_matrix)
        self.points=self.to_world(recipe['path'])
        self.closed=bool(recipe.get('closed'))
        self.original=(copy.deepcopy(self.points),self.closed)
        self.undo_stack=[];self.redo_stack=[]
        self.fingerprint=_fingerprint(group)
        self.local_normal=None
        orientation=recipe.get('mouse_orientation') or {}
        if orientation.get('horizontal'):self.local_normal=np.array((0.,0.,1.))
        elif orientation.get('wall_frame'):
            self.local_normal=np.asarray(orientation['plane_normal_override'],dtype=float)
        elif recipe.get('experimental') and orientation.get('plane_normal_override'):
            self.local_normal=np.asarray(orientation['plane_normal_override'],dtype=float)
        if self.local_normal is not None:
            self.world_normal=np.linalg.inv(self.world_matrix[:3,:3])@self.local_normal
            self.world_normal/=np.linalg.norm(self.world_normal)
        else:self.world_normal=None

    def to_world(self,points):
        return [tuple(row[:3]) for row in np.column_stack((np.asarray(points,dtype=float),np.ones(len(points))))@self.world_matrix]

    def to_local(self,points):
        return [tuple(row[:3]) for row in np.column_stack((np.asarray(points,dtype=float),np.ones(len(points))))@self.inverse]

    def _record(self):
        self.undo_stack.append((copy.deepcopy(self.points),self.closed));self.redo_stack.clear()

    def move(self,index,point):
        point=tuple(float(x) for x in point)
        if len(point)!=3 or not all(math.isfinite(x) for x in point):raise ValueError('節點座標必須是有限數值。')
        if math.dist(self.points[index],point)<1e-9:return
        self._record();self.points[index]=point

    def insert(self,segment,point):
        count=len(self.points) if self.closed else len(self.points)-1
        if not 0<=segment<count:raise ValueError('請點選路徑線段來新增節點。')
        if len(self.points)>=2048:raise ValueError('路徑最多2048個節點。')
        point=tuple(float(x) for x in point)
        if len(point)!=3 or not all(math.isfinite(x) for x in point):raise ValueError('節點座標必須是有限數值。')
        self._record();self.points.insert(segment+1,point)
        return segment+1

    def extend_endpoint(self,index):
        if self.closed or index not in (0,len(self.points)-1):raise ValueError('請點選開放路徑的起點或終點。')
        if len(self.points)>=2048:raise ValueError('路徑最多2048個節點。')
        self._record();point=self.points[index]
        if index==0:self.points.insert(0,point);return 0
        self.points.append(point);return len(self.points)-1

    def delete(self,index):
        if len(self.points)<=(3 if self.closed else 2):raise ValueError('開放路徑至少2點，封閉路徑至少3點。')
        self._record();del self.points[index]

    def set_closed(self,value):
        value=bool(value)
        if value==self.closed:return
        if value and len(self.points)<3:raise ValueError('封閉路徑至少需要3個節點。')
        self._record();self.closed=value

    def undo(self):
        if not self.undo_stack:return
        self.redo_stack.append((copy.deepcopy(self.points),self.closed))
        self.points,self.closed=self.undo_stack.pop()

    def redo(self):
        if not self.redo_stack:return
        self.undo_stack.append((copy.deepcopy(self.points),self.closed))
        self.points,self.closed=self.redo_stack.pop()

    def validate(self):
        points=self.to_local(self.points)
        if not (3 if self.closed else 2)<=len(points)<=2048:raise ValueError('路徑節點數不正確。')
        if not all(math.isfinite(c) for p in points for c in p):raise ValueError('路徑包含無效座標。')
        for i,a in enumerate(points):
            if any(math.dist(a,b)<1e-8 for b in points[i+1:]):raise ValueError('路徑包含重複節點，請移開或刪除重複點。')
        if self.local_normal is not None:
            n=self.local_normal/np.linalg.norm(self.local_normal)
            original=np.asarray(self.recipe['path'][0])
            if any(abs(np.dot(np.asarray(pt)-original,n))>1e-5 for pt in points):
                raise ValueError('節點已離開原踢腳高度或固定牆面；請將節點移回原平面。')
        if _crossing_segments(points,self.closed):raise ValueError('路徑有自交或重疊線段，請調整節點。')
        return points

    def assert_unchanged(self,scene):
        if scene is not self.scene or self.group not in scene.groups or getattr(scene,'edit_group',None) is not None:
            raise ValueError('模型或原線板已改變，請取消後重新進入編輯路徑。')
        if _fingerprint(self.group)!=self.fingerprint:
            raise ValueError('原線板已被其他操作修改，請取消後重新編輯，避免覆寫。')

    def replacement(self):
        from . import PLUGIN_KEY
        points=self.validate()
        new=copy.deepcopy(self.recipe);new['path']=[[float(c) for c in pt] for pt in points];new['closed']=self.closed
        mesh=build_mesh(new,self.attrs)
        _map_mesh(mesh,self.mesh_matrix[:,:3])
        new['geometry_transform']=self.mesh_matrix[:,:3].tolist()
        replacement=copy.copy(self.group);replacement.mesh=mesh;replacement.ext=copy.deepcopy(self.group.ext)
        replacement.ext[PLUGIN_KEY]=new
        return replacement

    def commit(self,viewport):
        self.assert_unchanged(viewport.scene)
        original_points,original_closed=self.original
        if self.closed==original_closed and len(self.points)==len(original_points) and all(math.dist(a,b)<1e-8 for a,b in zip(self.points,original_points)):
            return self.group
        replacement=self.replacement()
        viewport.history.execute(ReplacePathCommand(self,replacement))
        if getattr(viewport.history,'last_error',None):raise RuntimeError(viewport.history.last_error)
        if replacement not in viewport.scene.groups:raise RuntimeError('路徑修改未成功提交，原線板已保留。')
        viewport.notify_scene_changed()
        return replacement


class ProfilePathEditTool(Tool):
    name='編輯輪廓路徑'
    description='移動、新增、刪除節點；Enter完成、Esc取消。'
    wireframe_color=(.1,.55,.95,1.)
    wireframe_depth_tested=False
    def __init__(self,panel,session,show_dialog=True):
        self.panel=panel;self.session=session;self.dialog=None;self.show_dialog=show_dialog
        self.mode=0;self.selected=None;self.moving=False;self.candidate=None
        self.axis=None;self.press_screen=None;self.hover_screen=None;self.done=False;self.viewport=None
        self.error=''

    def on_activate(self,viewport):
        self.viewport=viewport
        if not callable(getattr(viewport,'_world_to_pixel',None)):
            raise ValueError('目前主程式沒有可用的節點投影介面。')
        if self.show_dialog:self._open_dialog()
        self._status();viewport.update()

    def _open_dialog(self):
        dialog=QDialog(self.panel,Qt.Tool|Qt.WindowTitleHint|Qt.WindowCloseButtonHint)
        self.dialog=dialog;dialog.setWindowTitle('編輯路徑｜完成後重建線板');dialog.setMinimumWidth(390)
        layout=QVBoxLayout(dialog)
        label=QLabel('彩色路徑為待套用內容，原線板保留作參考。\n移動：點節點，再點新位置；也可按住拖曳。\n新增：點線段插點，或點開放端點延長；刪除：點節點。')
        label.setWordWrap(True);layout.addWidget(label)
        self.mode_combo=QComboBox();self.mode_combo.addItems(['移動節點','新增節點','刪除節點'])
        self.mode_combo.currentIndexChanged.connect(self._set_mode);layout.addWidget(self.mode_combo)
        self.closed_box=QCheckBox('封閉路徑');self.closed_box.setChecked(self.session.closed)
        self.closed_box.toggled.connect(self._closed_changed);layout.addWidget(self.closed_box)
        self.node_label=QLabel('請點選路徑節點。');layout.addWidget(self.node_label)
        form=QFormLayout();self.coordinates=[]
        for axis in 'XYZ':
            control=QDoubleSpinBox();control.setRange(-1e9,1e9);control.setDecimals(3);control.setSuffix(' mm')
            self.coordinates.append(control);form.addRow(axis,control)
        layout.addLayout(form)
        apply_node=QPushButton('套用節點座標');apply_node.clicked.connect(self._apply_coordinates);layout.addWidget(apply_node)
        undo_row=QHBoxLayout()
        for title,callback in [('上一步',self.undo),('重做',self.redo)]:
            button=QPushButton(title);button.clicked.connect(callback);undo_row.addWidget(button)
        layout.addLayout(undo_row)
        self.message=QLabel('Enter／Space 完成；Esc 取消；方向鍵鎖軸。');self.message.setWordWrap(True);layout.addWidget(self.message)
        row=QHBoxLayout();finish=QPushButton('完成並重建');cancel=QPushButton('取消')
        finish.clicked.connect(self.finish);cancel.clicked.connect(self.cancel)
        row.addWidget(finish);row.addWidget(cancel);layout.addLayout(row)
        for button in dialog.findChildren(QPushButton):button.setAutoDefault(False)
        dialog.rejected.connect(self.cancel);dialog.show()
        self._focus_viewport()

    def _focus_viewport(self):
        focus=getattr(self.viewport,'setFocus',None)
        if callable(focus):focus(Qt.OtherFocusReason)

    def _status(self,message=None):
        text=message or f'編輯路徑：{len(self.session.points)}點｜Enter完成、Esc取消；方向鍵鎖軸。'
        if self.dialog is not None:self.message.setText(text)
        if self.viewport is not None:self.viewport.flash_status(text,3500)

    def _sync(self):
        if self.selected is not None and self.selected>=len(self.session.points):self.selected=None
        if self.dialog is not None:
            self.closed_box.blockSignals(True);self.closed_box.setChecked(self.session.closed);self.closed_box.blockSignals(False)
            self.node_label.setText(f'節點 {self.selected+1}／{len(self.session.points)}' if self.selected is not None else '請點選路徑節點。')
            if self.selected is not None:
                point=self.candidate if self.moving and self.candidate is not None else self.session.points[self.selected]
                for control,value in zip(self.coordinates,point):control.setValue(value*1000.)
        self.viewport.update()

    def _set_mode(self,value):
        self.mode=value;self._stop_move();self._focus_viewport();self._sync()

    def _closed_changed(self,value):
        try:self.session.set_closed(value);self._stop_move();self._sync()
        except ValueError as exc:self._status(str(exc));self._sync()

    def _apply_coordinates(self):
        if self.selected is None:self._status('請先點選一個節點。');return
        self._stop_move();self.session.move(self.selected,[control.value()/1000. for control in self.coordinates])
        self._sync();self._focus_viewport()

    def _stop_move(self):
        self.moving=False;self.candidate=None;self.press_screen=None;self.hover_screen=None

    def _project(self,point):
        return self.viewport._world_to_pixel(QVector3D(*point))

    def _node_at(self,screen):
        candidates=[]
        for i,point in enumerate(self.session.points):
            pixel=self._project(point)
            if pixel is not None:candidates.append(((pixel[0]-screen.x())**2+(pixel[1]-screen.y())**2,i))
        if candidates:
            distance,index=min(candidates)
            if distance<=12**2:return index
        return None

    def _segment_at(self,screen):
        points=self.session.points;best=None
        for i in range(len(points) if self.session.closed else len(points)-1):
            a=self._project(points[i]);b=self._project(points[(i+1)%len(points)])
            if a is None or b is None:continue
            vector=np.array(b)-a;length=float(vector@vector)
            if length<1:continue
            t=float(np.clip((np.array((screen.x(),screen.y()))-a)@vector/length,0.,1.))
            pixel=np.array(a)+t*vector
            distance=float(np.linalg.norm(pixel-(screen.x(),screen.y())))
            if distance<=12 and .001<t<.999 and (best is None or distance<best[0]):best=(distance,i,t)
        return None if best is None else best[1:]

    def _point(self,ctx):
        point=tuple(float(c) for c in ctx.world.toTuple())
        if self.session.world_normal is not None:
            n=self.session.world_normal;origin=np.asarray(self.session.original[0][0])
            point=tuple(np.asarray(point)-n*np.dot(np.asarray(point)-origin,n))
        if self.axis is not None and self.selected is not None:
            result=list(self.session.points[self.selected]);result['xyz'.index(self.axis)]=point['xyz'.index(self.axis)]
            return tuple(result)
        return point

    def on_click(self,ctx):
        if self.done:return
        try:
            if self.moving:
                self.session.move(self.selected,self._point(ctx));self._stop_move();self._sync();return
            index=self._node_at(ctx.screen)
            if self.mode==2:
                if index is None:self._status('請點選要刪除的節點。');return
                self.session.delete(index);self.selected=None;self._sync();return
            if self.mode==1:
                if not self.session.closed and index in (0,len(self.session.points)-1):
                    index=self.session.extend_endpoint(index)
                else:
                    segment=self._segment_at(ctx.screen)
                    if segment is None:self._status('請點線段插點，或點開放路徑端點來延長。');return
                    i,t=segment;a=np.array(self.session.points[i]);b=np.array(self.session.points[(i+1)%len(self.session.points)])
                    index=self.session.insert(i,a+t*(b-a))
            if index is None:self.selected=None;self._sync();return
            self.selected=index;self.moving=True;self.candidate=self.session.points[index]
            self.press_screen=(ctx.screen.x(),ctx.screen.y());self.hover_screen=self.press_screen
            self._sync()
        except (ValueError,IndexError) as exc:self._status(str(exc))

    def on_hover(self,ctx):
        if self.moving:
            self.candidate=self._point(ctx);self.hover_screen=(ctx.screen.x(),ctx.screen.y());self._sync()

    def on_release(self,viewport):
        if self.moving and self.press_screen and self.hover_screen and math.dist(self.press_screen,self.hover_screen)>4:
            self.session.move(self.selected,self.candidate);self._stop_move();self._sync()

    def drag_plane(self,viewport):
        if self.session.world_normal is not None:
            return QVector3D(*self.session.original[0][0]),QVector3D(*self.session.world_normal)
        origin=self.session.points[self.selected or 0]
        camera=getattr(viewport,'camera',None)
        normal=(0.,1.,0.)
        if camera is not None:
            eye=getattr(camera,'eye',None)
            if callable(eye):
                delta=eye()-QVector3D(*origin)
                if delta.length()>1e-7:normal=delta.normalized().toTuple()
        return QVector3D(*origin),QVector3D(*normal)

    def _display_points(self):
        points=list(self.session.points)
        if self.moving and self.selected is not None and self.candidate is not None:points[self.selected]=self.candidate
        return points

    def rubber_band_lines(self):
        points=self._display_points()
        return [(QVector3D(*points[i]),QVector3D(*points[(i+1)%len(points)])) for i in range(len(points) if self.session.closed else len(points)-1)]

    def draw_overlay(self,viewport,painter):
        painter.save()
        try:
            pixels=[self._project(point) for point in self._display_points()]
            painter.setPen(QPen(QColor('#087dcb'),3))
            for i in range(len(pixels) if self.session.closed else len(pixels)-1):
                a,b=pixels[i],pixels[(i+1)%len(pixels)]
                if a is not None and b is not None:painter.drawLine(QPointF(*a),QPointF(*b))
            hidden_nodes=getattr(self,'overlay_hidden_nodes',())
            for i,pixel in enumerate(pixels):
                if pixel is None:continue
                if i in hidden_nodes:continue
                painter.setPen(QPen(QColor('white'),1.5));painter.setBrush(QColor('#f19823' if i==self.selected else '#168bd2'))
                point=QPointF(*pixel);painter.drawEllipse(point,6,6)
                painter.setPen(QColor('#12415d'));painter.drawText(point+QPointF(8,-8),str(i+1))
        finally:painter.restore()

    def undo(self):
        self._stop_move();self.session.undo();self.selected=None;self._sync()

    def redo(self):
        self._stop_move();self.session.redo();self.selected=None;self._sync()

    def on_key(self,viewport,key,modifiers):
        if key==int(Qt.Key_Escape):self.cancel();return True
        if modifiers and modifiers&Qt.ControlModifier:
            if key==int(Qt.Key_Z):self.undo();return True
            if key==int(Qt.Key_Y):self.redo();return True
        if key in (int(Qt.Key_Return),int(Qt.Key_Enter),int(Qt.Key_Space)):self.finish();return True
        if key in (int(Qt.Key_Delete),int(Qt.Key_Backspace)):
            if self.selected is not None:
                try:self.session.delete(self.selected);self.selected=None;self._stop_move();self._sync()
                except ValueError as exc:self._status(str(exc))
            return True
        axes={int(Qt.Key_Right):'x',int(Qt.Key_Left):'y',int(Qt.Key_Up):'z',int(Qt.Key_Down):None}
        if key in axes:
            requested=axes[key];self.axis=None if self.axis==requested else requested
            self._status('已解除軸鎖定' if self.axis is None else self.axis.upper()+'軸鎖定');return True
        return False

    def status_clause(self):return '編輯路徑｜Enter完成｜Esc取消｜方向鍵鎖軸｜Ctrl+Z/Y上一步／重做'

    def finish(self):
        if self.done:return
        try:
            if self.moving:self.session.move(self.selected,self.candidate);self._stop_move()
            result=self.session.commit(self.viewport)
            self.done=True;self._leave()
            self.viewport.flash_status('路徑已完成；線板已重建，可一次復原。' if result is not self.session.group else '路徑未變更，原線板保留。',4000)
        except (ValueError,RuntimeError,ArithmeticError) as exc:
            self.error=str(exc);self._status(self.error)

    def cancel(self):
        if self.done:return
        self.done=True;self._leave();self.viewport.flash_status('已取消編輯路徑，原線板保留。',3000)

    def _leave(self):
        from tools.select import SelectTool
        if getattr(self.viewport,'active_tool',None) is self:self.viewport.set_active_tool(SelectTool())
        else:self.on_deactivate(self.viewport)
        self.viewport.update()

    def on_cancel(self,viewport):self.cancel()

    def on_deactivate(self,viewport):
        self.done=True;self._stop_move()
        if self.dialog is not None:
            dialog=self.dialog;self.dialog=None;dialog.close();dialog.deleteLater()
        viewport.update()


def activate_path_edit(panel):
    viewport=panel.app.viewport
    existing=getattr(viewport,'active_tool',None)
    if isinstance(existing,ProfilePathEditTool):
        surface=getattr(existing,'toolbar',None) or existing.dialog
        if surface is not None:surface.show();surface.raise_()
        return
    try:
        selected=list(viewport.scene.selection)
        if len(selected)!=1:raise ValueError('請只選取一個由本套件建立的線板群組。')
        from .path_aid_session import ProductivePathSession
        from .path_aids import ProductivePathEditTool
        session=ProductivePathSession(viewport.scene,selected[0])
        if not callable(getattr(viewport,'_world_to_pixel',None)):raise ValueError('目前主程式沒有可用的節點投影介面。')
        viewport.set_active_tool(ProductivePathEditTool(panel,session))
    except (ValueError,TypeError,AttributeError,ArithmeticError,KeyError,IndexError) as exc:panel.report(str(exc))
