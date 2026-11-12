# SPDX-License-Identifier: MIT
"""Lime Integrated Tools v0.19.0: 20 tool entries.
Geometry: metres; legacy saved parameters: mm; all length controls: cm.
"""
from copy import deepcopy
from pathlib import Path
import secrets
import numpy as np
from PySide6.QtCore import Qt,QTimer,QPointF,QSize,QRectF,QCoreApplication,QEvent
from PySide6.QtGui import QVector3D,QMatrix4x4,QColor,QIcon,QPen,QPolygonF,QPixmap,QCursor,QPainter,QImage,QWheelEvent
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,
    QWidget,QComboBox,QLineEdit,QPushButton,QDoubleSpinBox,QColorDialog,QToolBar,
    QFrame,QScrollArea,QGridLayout,QRadioButton,QButtonGroup,QSizePolicy,QListWidget,QListWidgetItem,QCheckBox,QSpinBox,QToolButton,QTableWidget,QTableWidgetItem)
from core.group import Group,world_mesh,transformed_mesh as _host_transformed_mesh
from core.mesh import Face,Edge
from core.history import Command,MoveVerticesCommand,ScaleVerticesCommand,_dirty_group_chunks
from core.materials import Material,register,has_own_material

VERSION='0.19.0'
ROOT=Path(__file__).resolve().parent


def transformed_mesh(source,matrix):
    # IngeTrazo 0.6.0's copy paths drop hidden/layer edge flags. Carry them
    # explicitly; hidden edges are not smoothing connections.
    result=_host_transformed_mesh(source,matrix)
    for edge in source.edges:
        a,b=result.vertex_at(matrix.map(edge.a)),result.vertex_at(matrix.map(edge.b))
        if a is not None and b is not None:
            copied=result.find_edge(a,b)
            if copied is not None:
                copied.soft=edge.soft;copied.hidden=edge.hidden;copied.curve=edge.curve;copied.layer=edge.layer
    return result


def xyz(v):
    return np.array([v.x(),v.y(),v.z()],dtype=float)


def editable(scene,entity):
    visible,locked=scene._layer_state(entity)
    return visible and not locked and not scene.entity_locked(entity)


def selection(scene,groups_only=False):
    items=list(scene.selection)
    types=(Group,) if groups_only else (Group,Face,Edge)
    if not items or any(not isinstance(e,types) for e in items):
        raise ValueError('請先選取群組／元件。' if groups_only else '請選取群組、元件、面或線。')
    context=scene.groups if scene.edit_group is None else (scene.edit_group.children or [])
    if any(isinstance(e,Group) and e not in context for e in items):
        raise ValueError('請在目前編輯層級選取物件。')
    if any(not editable(scene,e) for e in items) or any(
        not editable(scene,level['group']) for level in scene._edit_stack):
        raise ValueError('選取物件、外層群組或圖層已鎖定／隱藏，請先解除。')
    return items


def points_of(items):
    points=[]
    for entity in items:
        if isinstance(entity,Group):
            points.extend(xyz(v.position) for v in world_mesh(entity).vertices)
        elif isinstance(entity,Face):
            points.extend(xyz(v.position) for loop in (entity.loop,*entity.hole_loops) for v in loop)
        elif isinstance(entity,Edge):
            points.extend((xyz(entity.a),xyz(entity.b)))
    if not points:
        raise ValueError('選取物件沒有可量測的幾何。')
    return np.asarray(points)


def bounds(items):
    p=points_of(items)
    return p.min(axis=0),p.max(axis=0)


def execute(viewport,command):
    viewport.history.execute(command)
    error=getattr(viewport.history,'last_error',None) or getattr(viewport.history,'last_rejected',None)
    if error:
        raise ValueError(str(error))
    viewport.notify_scene_changed()


class TransformGroups(Command):
    keeps_paint=True
    def __init__(self,groups,matrix):
        self.groups=list(groups)
        self.matrix=QMatrix4x4(matrix)
        self.before=None

    def do(self,scene):
        if self.before is None:
            self.before=[(g,QMatrix4x4(g.xform) if g.xform is not None else None,g.component)
                         for g in self.groups]
        for g,old,component in self.before:
            g.xform=self.matrix*(old if old is not None else QMatrix4x4())
            g.component=component if old is not None else False
        scene.version+=1

    def undo(self,scene):
        for g,old,component in self.before:
            g.xform=QMatrix4x4(old) if old is not None else None
            g.component=component
        scene.version+=1


class AtomicCommands(Command):
    def __init__(self,commands):
        self.commands=commands
    def do(self,scene):
        done=[]
        try:
            for cmd in self.commands:
                cmd.do(scene)
                done.append(cmd)
        except Exception:
            for cmd in reversed(done):
                cmd.undo(scene)
            raise
    def undo(self,scene):
        for cmd in reversed(self.commands):
            cmd.undo(scene)


def resize_height(viewport,target_mm):
    scene=viewport.scene
    items=selection(scene)
    target=float(target_mm)/1000.
    if not np.isfinite(target):
        raise ValueError('請輸入有限的毫米數值。')
    if len(items)==1 and isinstance(items[0],Face):
        points=points_of(items)
        if np.ptp(points[:,2])>1e-5:
            raise ValueError('請只選一個水平面，直立面或斜面沒有單一標高。')
        delta=target-float(points[:,2].mean())
        if abs(delta)<1e-9:
            return '面的標高已符合設定。'
        execute(viewport,MoveVerticesCommand([QVector3D(*p) for p in points],QVector3D(0,0,delta)))
        return f'水平面標高已調整至 {target_mm:g} mm。Ctrl+Z 可復原。'
    if target<=0:
        raise ValueError('物件高度必須大於 0 mm。')
    lo,hi=bounds(items)
    height=hi[2]-lo[2]
    if height<1e-9:
        raise ValueError('選取範圍沒有高度；調整標高時請只選一個水平面。')
    factor=target/height
    matrix=QMatrix4x4()
    matrix.translate(0,0,float(lo[2]));matrix.scale(1,1,float(factor));matrix.translate(0,0,float(-lo[2]))
    commands=[]
    groups=[e for e in items if isinstance(e,Group)]
    if groups:
        commands.append(TransformGroups(groups,matrix))
    raw=[e for e in items if not isinstance(e,Group)]
    if raw:
        commands.append(ScaleVerticesCommand([QVector3D(*p) for p in points_of(raw)],
            QVector3D(0,0,float(lo[2])),(1.,1.,float(factor))))
    execute(viewport,AtomicCommands(commands))
    return f'選取範圍高度已調整至 {target_mm:g} mm，底部保持原位。Ctrl+Z 可復原。'


def clearance(viewport,floor_m=0.):
    items=selection(viewport.scene,True)
    if len(items)!=1:
        raise ValueError('離地量測請只選一個群組或元件。')
    lo,hi=bounds(items)
    return {'bottom':(lo[2]-floor_m)*1000,'top':(hi[2]-floor_m)*1000,
            'center':((lo[2]+hi[2])/2-floor_m)*1000,'height':(hi[2]-lo[2])*1000}


def move_clearance(viewport,mode,target_mm,floor_m=0.):
    if mode not in ('bottom','top','center'):
        raise ValueError('量測位置無效。')
    target=float(target_mm)
    if not np.isfinite(target):
        raise ValueError('請輸入有限的毫米數值。')
    items=selection(viewport.scene,True)
    current=clearance(viewport,floor_m)[mode]
    m=QMatrix4x4();m.translate(0,0,(target-current)/1000)
    execute(viewport,TransformGroups(items,m))
    return '已垂直移動至指定離地高度，物件尺寸不變。Ctrl+Z 可復原。'


def clone_tree(group):
    """Detach the selected hierarchy before changing face paint, not siblings."""
    copied=Group(transformed_mesh(group.mesh,QMatrix4x4()),name=group.name)
    for key in ('layer','ifc','billboard','text3d','hidden','material','ext','locked','component'):
        setattr(copied,key,deepcopy(getattr(group,key)))
    copied.xform=QMatrix4x4(group.xform) if group.xform is not None else None
    copied.axes=QMatrix4x4(group.axes) if group.axes is not None else None
    copied.children=[clone_tree(child) for child in group.children or ()]
    return copied


def walk_groups(group):
    yield group
    for child in group.children or ():
        yield from walk_groups(child)


class PaintGroups(Command):
    def __init__(self,groups,materials,mode):
        if mode not in ('all','blank','outer'):
            raise ValueError('上色模式無效。')
        self.groups=list(groups);self.materials=list(materials);self.mode=mode
        self.before=None;self.after=None

    def do(self,scene):
        if self.before is not None:
            self.restore(scene,self.after,self.registry_after)
            return
        self.before=[(g,g.mesh,g.children,deepcopy(g.material),deepcopy(g.ext)) for g in self.groups]
        self.registry_before=dict(scene.materials)
        prepared=[]
        try:
            for group,material in zip(self.groups,self.materials):
                name=register(scene.materials,material)
                attrs=scene.materials[name].face_attrs()
                if self.mode=='outer':
                    if any(auto_front(f.attrs) for n in walk_groups(group) for f in n.mesh.faces) or any((n.ext or {}).get('lime_face_auto') for n in walk_groups(group)):
                        copy=clone_tree(group);copy.material=deepcopy(attrs);bind_face_materials(copy)
                        prepared.append((group,copy.mesh,copy.children,deepcopy(attrs),deepcopy(copy.ext)))
                    else:prepared.append((group,group.mesh,group.children,deepcopy(attrs),deepcopy(group.ext)))
                    continue
                copy=clone_tree(group);restore_auto_markers(copy)
                def paint(node,inherited=None):
                    previous=node.material or inherited
                    if self.mode=='all':
                        node.material=deepcopy(attrs)
                    for face in node.mesh.faces:
                        own=(has_own_material(face.attrs) or bool(face.attrs.get('mat')) or 'opacity' in face.attrs) and not auto_front(face.attrs)
                        if self.mode=='all' or (not own and not previous):
                            back=face.attrs.get('back')
                            for key in ('color','texture','opacity','mat'):
                                face.attrs.pop(key,None)
                            face.attrs.update(deepcopy(attrs))
                            if self.mode=='all' or not isinstance(back,dict):
                                face.attrs['back']=True
                    for child in node.children or ():
                        paint(child,previous)
                paint(copy);stash_auto_materials(copy)
                prepared.append((group,copy.mesh,copy.children,copy.material,deepcopy(copy.ext)))
            self.after=prepared
            self.registry_after=dict(scene.materials)
        except Exception:
            scene.materials.clear();scene.materials.update(self.registry_before)
            self.before=None
            raise
        self.restore(scene,self.after,self.registry_after)

    def restore(self,scene,states,registry):
        for group,mesh,children,material,ext in states:
            group.mesh=mesh;group.children=children;group.material=deepcopy(material);group.ext=deepcopy(ext)
            for child in walk_groups(group):
                child.mesh._mut_serial+=1
                child.mesh._chunk_dirty=True
                child.mesh._attrs_dirty=True
        scene.materials.clear();scene.materials.update(registry)
        _dirty_group_chunks(scene)
        scene.version+=1

    def undo(self,scene):
        self.restore(scene,self.before,self.registry_before)


def paint_selection(viewport,source='custom',color='#AABBCC',model_name='',mode='all',targets=None):
    scene=viewport.scene
    if targets is None:
        groups=selection(scene,True)
    else:
        groups=list(targets)
        context=scene.groups if scene.edit_group is None else (scene.edit_group.children or [])
        if (not groups or any(not isinstance(g,Group) or g not in context or not editable(scene,g) for g in groups)
                or any(not editable(scene,level['group']) for level in scene._edit_stack)):
            raise ValueError('請點選目前編輯層級、未鎖定的群組或元件。')
    if mode!='outer':
        if scene.edit_definition() is not None:
            raise ValueError('請退出共用元件編輯後，選取外層物件上色。')
        if any(not editable(scene,g) for root in groups for g in walk_groups(root)):
            raise ValueError('內部有鎖定群組或圖層，請解除後再上色，或改用只上外層。')
        if any(not editable(scene,f) for root in groups for g in walk_groups(root) for f in g.mesh.faces):
            raise ValueError('內部有鎖定或隱藏圖層的面，請解除後再上色。')
    materials=[]
    used=set()
    for _ in groups:
        if source=='model':
            material=scene.materials.get(model_name)
            if material is None:
                raise ValueError('模型材質已變更，請重新整理材質清單。')
        else:
            if source=='random':
                while True:
                    value='#'+''.join(f'{48+secrets.randbelow(177):02X}' for i in range(3))
                    if value not in used:
                        used.add(value);break
            elif source=='custom':
                value=color
            else:
                raise ValueError('材質來源無效。')
            q=QColor(value)
            if not q.isValid() or len(value)!=7 or not value.startswith('#'):
                raise ValueError('請輸入 #RRGGBB 色碼。')
            material=Material('群組色 '+q.name().upper(),color=(q.redF(),q.greenF(),q.blueF()))
        materials.append(material)
    execute(viewport,PaintGroups(groups,materials,mode))
    # Ask cached placement/color consumers to rebuild from the new paint.
    for cache in ('_epoch_same','_placements_memo','_epoch_memo'):
        if hasattr(viewport,cache):setattr(viewport,cache,None)
    if hasattr(viewport,'_edges_version'):viewport._edges_version=-1
    viewport.update()
    return f'已上色至 {len(groups)} 個群組／元件。Ctrl+Z 可復原。'


def centering_delta(handle_center,target_points,whole_center=None):
    points=np.asarray(target_points)
    edges=np.roll(points,-1,axis=0)-points
    lengths=np.linalg.norm(edges,axis=1)
    if lengths.max()<1e-10:
        raise ValueError('目標面沒有有效邊線。')
    u=edges[int(lengths.argmax())]/lengths.max()
    normal=np.sum(np.cross(points-points[0],np.roll(points,-1,axis=0)-points[0]),axis=0)
    if np.linalg.norm(normal)<1e-12:
        raise ValueError('目標面無效。')
    normal/=np.linalg.norm(normal)
    v=np.cross(normal,u)
    p=points-points[0]
    center=points[0]+u*((p@u).min()+(p@u).max())/2+v*((p@v).min()+(p@v).max())/2
    if whole_center is not None:
        center=whole_center-normal*np.dot(whole_center-points[0],normal)
    delta=center-handle_center
    delta-=normal*np.dot(delta,normal)
    return delta,center


class CenterSession:
    def __init__(self,app,mode,handle=None):
        self.app,self.vp,self.scene=app,app.viewport,app.scene
        self.mode,self.handle=mode,handle
        self.version=self.scene.content_version
        self.old_tool=self.vp.active_tool
        self.active=True;self.preview=None
        self.bounds_cache={}
        self.tool=self.make_tool()

    def stop(self):
        self.active=False
        if self.vp.active_tool is self.tool:
            activate=getattr(self.app.window,'_activate_tool',None)
            if callable(activate):activate('select')
            else:self.vp.set_active_tool(self.old_tool)
        self.vp.update()

    def hover(self,ctx):
        self.preview=None
        if self.app.viewport is not self.vp or self.vp.scene is not self.scene or self.scene.content_version!=self.version:
            self.stop();self.vp.flash_status('模型或專案已變更，請重新啟動置中工具。',6000);return
        if self.handle is None:return
        face,placement=self.vp.pick_face_placement(ctx.screen.x(),ctx.screen.y())
        owner=self.vp.pick_group(ctx.screen.x(),ctx.screen.y())
        if face is None or owner is self.handle or (self.mode=='object' and owner is None):return
        matrix=getattr(placement,'xform',None)
        points=np.array([xyz(matrix.map(p) if matrix is not None else p) for p in face.vertices])
        if self.handle not in self.bounds_cache:self.bounds_cache[self.handle]=bounds([self.handle])
        lo,hi=self.bounds_cache[self.handle];start=(lo+hi)/2
        whole=None
        if self.mode=='object':
            if owner not in self.bounds_cache:self.bounds_cache[owner]=bounds([owner])
            a,b=self.bounds_cache[owner];whole=(a+b)/2
        delta,center=centering_delta(start,points,whole)
        self.preview=(points,start,start+delta,center,delta)
        self.vp.update()

    def click(self,ctx):
        if self.handle is None:
            handle=self.vp.pick_group(ctx.screen.x(),ctx.screen.y())
            context=self.scene.groups if self.scene.edit_group is None else (self.scene.edit_group.children or [])
            if handle is not None and handle in context and editable(self.scene,handle):
                self.handle=handle;self.scene.selection.clear();self.scene.selection.add(handle)
                self.scene.bump_view()
                self.vp.update()
            return
        self.hover(ctx)
        if self.preview is None:
            self.vp.flash_status('請點選目標表面，避開要移動的物件本身。',5000);return
        if not editable(self.scene,self.handle):raise ValueError('物件已鎖定。')
        delta=self.preview[-1]
        if np.linalg.norm(delta)>1e-9:
            matrix=QMatrix4x4();matrix.translate(QVector3D(*delta))
            execute(self.vp,TransformGroups([self.handle],matrix))
        self.stop();self.vp.flash_status('已置中，保留方向與安裝深度。Ctrl+Z 可復原。',6000)

    def make_tool(self):
        from tools.base import Tool
        session=self
        class CenterTool(Tool):
            name='萊姆置中';uses_snap=False
            def claims_key(self,key,modifiers):return key==Qt.Key_Escape
            def on_key(self,vp,key,modifiers):
                if key==Qt.Key_Escape:session.stop();return True
                return False
            def on_activate(self,vp):vp.flash_status('先選取或點選要移動的物件，再點目標表面。Esc 取消。',7000)
            def on_deactivate(self,vp):session.active=False;vp.update()
            def on_hover(self,ctx):
                try:session.hover(ctx)
                except Exception as exc:session.preview=None;ctx.viewport.flash_status(cm_text(str(exc)),5000)
            def on_click(self,ctx):
                try:session.click(ctx)
                except Exception as exc:ctx.viewport.flash_status(cm_text(str(exc)),6000)
            def on_cancel(self,vp):session.stop()
        return CenterTool()


def start_center(app,mode):
    items=list(app.scene.selection)
    if items:
        items=selection(app.scene,True)
        if len(items)!=1:raise ValueError('置中時請只選一個要移動的群組或元件。')
    old=getattr(app.window,'_lime_integrated_center',None)
    if old is not None and old.active:old.stop()
    session=CenterSession(app,mode,items[0] if items else None)
    app.window._lime_integrated_center=session
    app.viewport.set_active_tool(session.tool);app.viewport.setFocus()
    return '置中工具已啟動，請點選目標表面；Esc 取消。' if items else '請先點要移動的物件，再點目標表面；Esc 取消。'


STYLE='''
QDialog,QScrollArea,QWidget#page {background:#f3f6fa;color:#163247;font-family:"Microsoft JhengHei","Noto Sans CJK TC",sans-serif;font-size:14px;}
QLabel {background:transparent;color:#17364b;}
QLabel[role="title"] {font-size:24px;font-weight:700;color:#123346;}
QLabel[role="muted"] {color:#6b8197;font-size:13px;}
QLabel[role="metric"] {color:#0875cf;font-size:23px;}
QFrame[role="card"] {background:white;border:1px solid #d6e1ec;border-radius:11px;}
QLineEdit,QComboBox,QDoubleSpinBox,QSpinBox {background:white;border:1px solid #b8cbe0;border-radius:6px;padding:9px;color:#17364b;min-height:20px;}
QDoubleSpinBox QLineEdit,QSpinBox QLineEdit {background:transparent;border:0;padding:0;min-height:0;color:#17364b;}
QDoubleSpinBox::up-button,QDoubleSpinBox::down-button,QSpinBox::up-button,QSpinBox::down-button {width:0px;border:0;}
QComboBox::drop-down {border:0;width:24px;}
QComboBox::down-arrow {image:url("ARROW_PATH");width:10px;height:6px;}
QPushButton {background:#fff;border:1px solid #bbd0e2;border-radius:7px;padding:9px 12px;color:#15516c;min-height:19px;}
QPushButton:hover {background:#edf6fa;border-color:#61a7c3;}
QPushButton:disabled {color:#8b9da9;background:#f5f7f8;border-color:#e0e6eb;}
QPushButton[role="primary"] {background:#1276cd;color:white;border:0;font-weight:600;}
QPushButton[role="cursor"] {background:#1693a9;color:white;border:0;font-weight:600;}
QPushButton[role="chip"] {border-radius:14px;background:#f5f8f4;color:#416658;padding:6px 10px;font-size:12px;}
QPushButton[role="chip"]:checked {background:#286b55;color:white;border-color:#286b55;}
QPushButton[role="tool"] {background:white;border:1px solid #d8e2d8;border-radius:12px;text-align:left;padding:0;min-height:110px;}
QPushButton[role="tool"]:disabled {background:#f6f8f5;border-color:#e2e7e1;}
QRadioButton {background:transparent;color:#24465c;spacing:8px;padding:3px 0;}
QRadioButton::indicator {width:14px;height:14px;border:1px solid #8aa2b5;border-radius:8px;background:#fff;}
QRadioButton::indicator:checked {background:#1378c8;border:2px solid #d5e8f9;}
QScrollArea {border:0;}
'''
STYLE=STYLE.replace('ARROW_PATH',(ROOT/'lime_integrated_assets'/'arrow.svg').as_posix())


def label(text='',role=None):
    out=CmLabel(text);out.setWordWrap(True)
    if role:out.setProperty('role',role)
    return out


def button(text,callback,role=None):
    out=QPushButton(text);out.setAutoDefault(False)
    if role:out.setProperty('role',role)
    out.clicked.connect(callback)
    return out


def card(layout):
    frame=QFrame();frame.setProperty('role','card')
    content=QVBoxLayout(frame);content.setContentsMargins(16,17,16,17);content.setSpacing(10)
    layout.addWidget(frame)
    return content


class NumericLockMixin:
    """Lock user input while allowing explicit model loads to refresh values."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._locked=False
        self._lock_wrapper=None
        self.lock_button=QToolButton(self);self.lock_button.setFocusPolicy(Qt.NoFocus)
        self.lock_button.setFixedSize(88,40);self.lock_button.clicked.connect(self.toggle_lock)
        self.lock_button.setStyleSheet('QToolButton {border:0;background:#e9f0f7;font-size:13px;padding:0;}')
        self.set_locked(False)
        self._position_lock()
    def _position_lock(self):
        if self._lock_wrapper is None:self.lock_button.move(max(0,self.width()-92),max(0,(self.height()-40)//2))
        edit=self.lineEdit() if hasattr(self,'lineEdit') else self
        margins=edit.textMargins();edit.setTextMargins(margins.left(),margins.top(),8 if self._lock_wrapper is not None else 94,margins.bottom())
        self.lock_button.raise_()
    def resizeEvent(self,event):
        super().resizeEvent(event);self._position_lock()
    def set_locked(self,locked):
        self._locked=bool(locked);self.setReadOnly(self._locked)
        edit=self.lineEdit() if hasattr(self,'lineEdit') else self
        edit.setReadOnly(self._locked)
        self.refresh_lock_state()
        self.setProperty('numericLocked',self._locked)
    def refresh_lock_state(self):
        enabled=self.isEnabled()
        self.lock_button.setEnabled(enabled)
        self.lock_button.setText(('已鎖定\n點此解鎖' if self._locked else '可輸入\n點此鎖定') if enabled else '模式停用')
        self.lock_button.setToolTip(('已鎖定：點一下解鎖' if self._locked else '點一下鎖定此數值') if enabled else '目前功能模式停用此欄位，請先切換相關設定。')
        color=('#e6b28b;background:#fff0e3;color:#85420f;' if self._locked else '#91bdac;background:#e7f5ee;color:#225942;') if enabled else '#cccccc;background:#eeeeee;color:#777777;'
        self.lock_button.setStyleSheet('QToolButton {border:1px solid '+color+'border-radius:4px;font-size:12px;padding:0;}')
    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type()==QEvent.EnabledChange and hasattr(self,'lock_button'):self.refresh_lock_state()
    def toggle_lock(self):
        self.set_locked(not self._locked)
        if not self._locked and self.isEnabled():
            self.setFocus(Qt.MouseFocusReason)
            self.selectAll()
    def attach_lock_button(self):
        if self._lock_wrapper is not None:return
        parent=self.parentWidget()
        if parent is None or parent.layout() is None:return
        wrapper=QWidget(parent);row=QHBoxLayout(wrapper);row.setContentsMargins(0,0,0,0);row.setSpacing(6)
        item=parent.layout().replaceWidget(self,wrapper,Qt.FindChildrenRecursively)
        if item is None:wrapper.deleteLater();return
        self._lock_wrapper=wrapper
        row.addWidget(self,1);row.addWidget(self.lock_button)
        self._position_lock();self.refresh_lock_state();self.lock_button.show();wrapper.show()
    def wheelEvent(self,event):
        parent=self.parentWidget()
        while parent is not None:
            if isinstance(parent,QScrollArea):
                target=parent.viewport()
                mapped=QPointF(target.mapFromGlobal(event.globalPosition().toPoint()))
                forwarded=QWheelEvent(mapped,event.globalPosition(),event.pixelDelta(),event.angleDelta(),event.buttons(),event.modifiers(),event.phase(),event.inverted())
                QCoreApplication.sendEvent(target,forwarded)
                event.accept();return
            parent=parent.parentWidget()
        event.ignore()
    def keyPressEvent(self,event):
        if self._locked and event.key() not in (Qt.Key_Tab,Qt.Key_Backtab):event.accept();return
        super().keyPressEvent(event)
    def stepBy(self,steps):
        if not self._locked:super().stepBy(steps)


class LockedDoubleSpinBox(NumericLockMixin,QDoubleSpinBox):pass
class LockedSpinBox(NumericLockMixin,QSpinBox):pass
class LockedNumericText(NumericLockMixin,QLineEdit):pass


def number(value=0,minimum=-1e9):
    out=LockedDoubleSpinBox();out.setRange(minimum,1e9);out.setDecimals(3);out.setValue(value)
    out.lineEdit().setTextMargins(10,0,8,0)
    return out


class StyledWindow(QDialog):
    def __init__(self,app,title,width=460,height=750):
        super().__init__(app.window)
        self.app=app;self.setWindowTitle(title+'｜IngeTrazo '+VERSION)
        self.setWindowModality(Qt.NonModal);self.setStyleSheet(STYLE);self.resize(width,height);self.setMinimumWidth(width-30)
        outer=QVBoxLayout(self);self.outer=outer;outer.setContentsMargins(0,0,0,0)
        scroll=QScrollArea();scroll.setWidgetResizable(True);outer.addWidget(scroll)
        page=QWidget();page.setObjectName('page');scroll.setWidget(page)
        self.body=QVBoxLayout(page);self.body.setContentsMargins(22,24,22,18);self.body.setSpacing(14)
        self.status=label('','muted');outer.addWidget(self.status);outer.setSpacing(4)
        self.status.setContentsMargins(22,4,22,12)
        lock_row=QHBoxLayout();lock_row.setContentsMargins(22,4,22,4)
        lock_row.addWidget(label('綠色可輸入；橘色已鎖定\n滾輪只捲動面板','muted'));lock_row.addStretch()
        lock_row.addWidget(button('全部鎖定',lambda:self.lock_numbers(True)))
        lock_row.addWidget(button('全部解鎖',lambda:self.lock_numbers(False)))
        self.numeric_lock_bar=QWidget();self.numeric_lock_bar.setLayout(lock_row);outer.insertWidget(0,self.numeric_lock_bar)
    def lock_numbers(self,locked):
        for field in self.findChildren(QWidget):
            if isinstance(field,NumericLockMixin):field.set_locked(locked)
    def showEvent(self,event):
        for field in self.findChildren(QWidget):
            if isinstance(field,NumericLockMixin):field.attach_lock_button()
        super().showEvent(event)
        self.numeric_lock_bar.setVisible(any(isinstance(f,NumericLockMixin) for f in self.findChildren(QWidget)))
    def message(self,text,error=False):
        self.status.setText(cm_text(str(text)));self.status.setStyleSheet('color:#b6483d;' if error else 'color:#51758a;')
    def run(self,callback):
        try:self.message(callback() or '已完成。Ctrl＋Z 可復原。')
        except Exception as exc:self.message(str(exc),True)


def view_paint_warning(viewport):
    style=getattr(viewport,'style_override',None) or getattr(viewport.scene,'display_style',None)
    if getattr(style,'face_mode','textures') in ('monochrome','hidden_line','wireframe'):
        return '目前視圖未顯示材質，按「顯示材質」查看上色結果。'
    if getattr(style,'color_by_layer',False):
        return '目前以圖層顏色顯示，按「顯示材質」查看物件顏色。'
    return ''


class ShowMaterialStyle(Command):
    def __init__(self):self.before=None
    def do(self,scene):
        if self.before is None:self.before=scene.display_style.copy()
        scene.display_style=self.before.copy();scene.display_style.face_mode='textures';scene.display_style.color_by_layer=False;scene.version+=1
    def undo(self,scene):scene.display_style=self.before.copy();scene.version+=1


class PaintSession:
    def __init__(self,window):
        self.window=window;self.app=window.app;self.vp=self.app.viewport;self.scene=self.vp.scene
        self.context=self.scene.edit_group;self.old_tool=self.vp.active_tool;self.active=True
        self.hovered=None;self.hover_box=None;self.hover_key=None
        self.tool=self.make_tool()
    def valid(self):
        return self.app.viewport is self.vp and self.vp.scene is self.scene and self.scene.edit_group is self.context
    def stop(self):
        self.active=False;self.hovered=None;self.hover_box=None
        if self.vp.active_tool is self.tool:
            activate=getattr(self.app.window,'_activate_tool',None)
            if callable(activate):activate('select')
            else:self.vp.set_active_tool(self.old_tool)
        self.vp.update()
    def pick(self,ctx):
        if not self.valid():
            self.stop();self.window.message('專案或編輯層級已變更，請重新啟用游標上色。',True);return None
        group=self.vp.pick_group(ctx.screen.x(),ctx.screen.y())
        allowed=self.scene.groups if self.context is None else (self.context.children or [])
        if group not in allowed or group is None or not editable(self.scene,group):return None
        return group
    def make_tool(self):
        from tools.base import Tool
        session=self
        class CursorPaint(Tool):
            name='群組游標上色';icon='paint';uses_snap=False
            def claims_key(self,key,modifiers):return key==Qt.Key_Escape
            def on_key(self,vp,key,modifiers):
                if key==Qt.Key_Escape:self.on_cancel(vp);return True
                return False
            def on_activate(self,vp):vp.flash_status('游標上色：點選群組連續套用，Esc 結束。',8000)
            def on_deactivate(self,vp):session.active=False;session.hover_box=None;vp.update()
            def on_cancel(self,vp):session.stop();session.window.message('游標上色已結束。')
            def on_hover(self,ctx):
                group=session.pick(ctx);session.hovered=group
                key=(id(group),session.scene.content_version)
                if key!=session.hover_key:
                    session.hover_key=key;session.hover_box=bounds([group]) if group is not None else None
                ctx.viewport.update()
            def on_click(self,ctx):
                group=session.pick(ctx)
                if group is None:
                    session.window.message('請點選目前編輯層級、未鎖定的群組或元件。',True);return
                session.window.apply_targets([group]);session.hover_key=None
            def on_double_click(self,ctx):pass
        return CursorPaint()


class PaintWindow(StyledWindow):
    def __init__(self,app):
        super().__init__(app,'群組上色',470,850)
        self.body.addWidget(label('群組上色','title'))
        self.body.addWidget(label('選擇顏色後，點模型即可上色；也可套用至目前選取。','muted'))
        self.count=label('','muted');self.body.addWidget(self.count)
        c=card(self.body);c.addWidget(label('選擇材質／顏色'))
        radios=QHBoxLayout();self.sources={};self.source_group=QButtonGroup(self)
        for text,key in [('模型已有材質','model'),('自訂顏色','custom'),('隨機色','random')]:
            radio=QRadioButton(text);self.sources[key]=radio;self.source_group.addButton(radio);radios.addWidget(radio)
            radio.toggled.connect(lambda checked:self.preview_color() if checked else None)
        c.addLayout(radios)
        self.materials=QComboBox();self.materials.currentIndexChanged.connect(self.preview_color);c.addWidget(self.materials)
        preview=QHBoxLayout();self.swatch=QLabel();self.swatch.setFixedSize(84,68);self.swatch.setAlignment(Qt.AlignCenter);preview.addWidget(self.swatch)
        right=QVBoxLayout();self.hex=QLineEdit('#C6B89E');self.hex.setMaxLength(7);self.hex.textChanged.connect(self.preview_color);right.addWidget(self.hex)
        actions=QHBoxLayout();actions.addWidget(button('選色',self.choose_color));actions.addWidget(button('換個顏色',self.shuffle_color));right.addLayout(actions)
        preview.addLayout(right);c.addLayout(preview)
        self.color_hint=label('','muted');c.addWidget(self.color_hint)
        c.addWidget(label('常用色票','muted'));palette=QHBoxLayout();palette.setSpacing(8)
        self.palette=[]
        for color in ('#FFFFFF','#DAD8D2','#C6B89E','#B48B69','#86A88A','#234D6C','#C56C55','#555555'):
            sw=QPushButton();sw.setFixedSize(32,32);sw.setAutoDefault(False);sw.setToolTip(color);sw.setAccessibleName(color)
            sw.setStyleSheet(f'background:{color};border:1px solid #b4c3d0;border-radius:6px;')
            sw.clicked.connect(lambda checked=False,value=color:self.use_color(value));palette.addWidget(sw);self.palette.append(sw)
        palette.addStretch();c.addLayout(palette)
        c.addWidget(button('重新整理模型材質',self.refresh_materials))
        c=card(self.body);c.addWidget(label('上色方式'))
        self.modes={};self.mode_group=QButtonGroup(self)
        for text,key,description in [('全部覆蓋','all','內部與巢狀物件，包含面的正反面。'),
            ('只上未有材質的部分','blank','保留原有材質，也保留從外層繼承的材質。'),
            ('只上群組外層','outer','只套在群組本身，內部已有材質的面保留。')]:
            radio=QRadioButton(text);self.modes[key]=radio;self.mode_group.addButton(radio);c.addWidget(radio)
            c.addWidget(label(description,'muted'))
        self.modes['all'].setChecked(True)
        self.cursor_button=button('啟用游標上色',self.start_cursor,'cursor');self.cursor_button.setIcon(QIcon(str(ROOT/'lime_integrated_assets'/'paint.png')))
        footer=QWidget();footer.setObjectName('page');actions=QVBoxLayout(footer);actions.setContentsMargins(22,8,22,4);actions.setSpacing(8)
        actions.addWidget(self.cursor_button)
        self.apply_button=button('套用至選取群組',self.apply_selected);actions.addWidget(self.apply_button)
        self.outer.insertWidget(1,footer)
        self.body.addWidget(label('可連續點選不同模型上色，Ctrl＋Z 復原。Esc 結束游標。','muted'))
        self.view_notice=label('','muted');self.body.addWidget(self.view_notice)
        self.show_material=button('顯示材質',self.display_materials);self.body.addWidget(self.show_material)
        self.body.addStretch()
        self.session=None;self.last_scene=None
        self.sources['custom'].setChecked(True);self.refresh_materials();self.refresh_state()
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh_state);self.timer.start(400)
    def source(self):return next((key for key,radio in self.sources.items() if radio.isChecked()),'custom')
    def mode(self):return next((key for key,radio in self.modes.items() if radio.isChecked()),'all')
    def options(self):
        source=self.source();name=self.materials.currentData() or ''
        if source=='model' and name not in self.app.scene.materials:raise ValueError('目前沒有可用模型材質，請重新整理或改選自訂顏色。')
        if source=='custom' and (len(self.hex.text())!=7 or not QColor(self.hex.text()).isValid()):raise ValueError('請輸入有效的 #RRGGBB 色碼。')
        return dict(source=source,color=self.hex.text(),model_name=name,mode=self.mode())
    def use_color(self,value):self.sources['custom'].setChecked(True);self.hex.setText(value.upper());self.preview_color()
    def choose_color(self):
        chosen=QColorDialog.getColor(QColor(self.hex.text()),self,'選擇顏色')
        if chosen.isValid():self.use_color(chosen.name())
    def shuffle_color(self):
        old=self.hex.text().upper()
        while True:
            color=f'#{secrets.randbelow(0x1000000):06X}'
            if color!=old:break
        self.use_color(color)
    def refresh_materials(self):
        previous=self.materials.currentData();self.materials.blockSignals(True);self.materials.clear()
        for name in sorted(self.app.scene.materials):self.materials.addItem(name,name)
        index=self.materials.findData(previous)
        if index>=0:self.materials.setCurrentIndex(index)
        self.materials.blockSignals(False);self.preview_color()
    def preview_color(self,*_):
        if not hasattr(self,'color_hint'):return
        source=self.source();self.materials.setVisible(source=='model');self.hex.setEnabled(source=='custom')
        self.swatch.clear();self.swatch.setPixmap(QPixmap())
        color=QColor(self.hex.text());hint='目前選取的自訂顏色'
        if source=='model':
            mat=self.app.scene.materials.get(self.materials.currentData())
            if mat is not None:
                rgb=mat.color or (.65,.65,.65);color=QColor.fromRgbF(*rgb[:3]);hint='模型材質：'+mat.name
                texture=mat.texture or {};image=QPixmap(texture.get('path','')) if texture.get('path') else QPixmap()
                if not image.isNull():self.swatch.setPixmap(image.scaled(80,64,Qt.KeepAspectRatio,Qt.SmoothTransformation));hint+='（貼圖）'
                elif texture:hint+='（找不到貼圖檔，先顯示代表色）'
            else:color=QColor('#EBEFF3');hint='尚無模型材質，請改用自訂顏色。'
        elif source=='random':
            color=QColor('#E4EEE8');self.swatch.setText('隨機色');hint='每次點選群組，產生新的隨機顏色。'
        if not color.isValid():color=QColor('#F0E8E5');hint='請輸入有效色碼，例如 #C6B89E。'
        self.swatch.setStyleSheet('background:'+color.name()+';border:1px solid #aebdca;border-radius:8px;')
        self.swatch.setProperty('hex_color',color.name().upper());self.color_hint.setText(hint)
    def refresh_state(self):
        if self.last_scene is not self.app.scene:self.last_scene=self.app.scene;self.refresh_materials()
        count=sum(isinstance(e,Group) for e in self.app.scene.selection);self.count.setText(f'目前選取 {count} 個群組／元件')
        warning=view_paint_warning(self.app.viewport);self.view_notice.setText(warning);self.view_notice.setVisible(bool(warning));self.show_material.setVisible(bool(warning))
        self.cursor_button.setText('結束游標上色' if self.session and self.session.active else '啟用游標上色')
    def apply_targets(self,targets=None):
        try:
            message=paint_selection(self.app.viewport,targets=targets,**self.options())
            warning=view_paint_warning(self.app.viewport)
            if warning:message+=' '+warning
            if targets:message=targets[0].name+'：'+message
            self.message(message);self.refresh_state();return True
        except Exception as exc:self.message('未完成上色：'+str(exc),True);return False
    def apply_selected(self):self.apply_targets()
    def start_cursor(self):
        if self.session and self.session.active:self.session.stop();self.refresh_state();return
        try:
            self.options();self.session=PaintSession(self);self.app.window._lime_integrated_paint=self.session
            self.app.viewport.set_active_tool(self.session.tool);self.app.viewport.setFocus()
            self.message('游標上色已啟用，點選群組可連續上色；Esc 結束。');self.refresh_state()
        except Exception as exc:self.message(str(exc),True)
    def display_materials(self):
        def apply():
            if getattr(self.app.viewport,'style_override',None) is not None:raise ValueError('目前有視圖樣式覆寫，請先回到模型的材質顯示模式。')
            execute(self.app.viewport,ShowMaterialStyle())
            sync=getattr(self.app.window,'_sync_style_menu',None)
            if callable(sync):sync()
            self.refresh_state();return '已切換為材質顯示，Ctrl＋Z 可復原。'
        self.run(apply)
    def cleanup(self):
        self.timer.stop()
        if self.session and self.session.active:self.session.stop()
    def closeEvent(self,event):self.cleanup();super().closeEvent(event)
    def done(self,result):self.cleanup();super().done(result)


class ClearanceWindow(StyledWindow):
    def __init__(self,app):
        super().__init__(app,'物件離地高度',470,800)
        self.body.addWidget(label('物件離地高度','title'))
        self.body.addWidget(label('外框底部、頂部與中心，單位 mm。負值表示低於地板。','muted'))
        metrics=QHBoxLayout();self.metrics={}
        for title,key in [('底部離地','bottom'),('頂部離地','top'),('中心離地','center')]:
            frame=QFrame();frame.setProperty('role','card');v=QVBoxLayout(frame);v.setContentsMargins(11,14,11,13)
            v.addWidget(label(title,'muted'));value=label('—','metric');v.addWidget(value);v.addWidget(label('mm','muted'));metrics.addWidget(frame);self.metrics[key]=value
        self.body.addLayout(metrics)
        c=card(self.body);c.addWidget(label('要調整的位置'));self.mode=QComboBox()
        for title,key in [('外框底部','bottom'),('外框頂部','top'),('外框中心','center')]:self.mode.addItem(title,key)
        c.addWidget(self.mode);c.addWidget(label('目標離地高度（mm）'));self.target=length_number();c.addWidget(self.target)
        c.addWidget(button('套用高度，垂直移動物件',self.apply_height,'primary'))
        c.addWidget(label('整個物件上下移動，尺寸不變。底部設為 0 即貼齊地板。可用 Ctrl＋Z 復原。','muted'))
        c=card(self.body);c.addWidget(label('地板標高（模型 Z，mm）'));row=QHBoxLayout();self.floor=length_number();row.addWidget(self.floor);row.addWidget(button('設定',self.save_floor));c.addLayout(row)
        c.addWidget(label('預設 Z＝0。修改地板標高只更新量測基準。','muted'))
        self.size_label=label('物件高度：— mm','muted');self.body.addWidget(self.size_label);self.body.addStretch()
        self.measure_data=None;self.measure_key=None;self.last_floor=None;self.last_scene=None;self.last_selected=None
        self.mode.currentIndexChanged.connect(self.read_target)
        self.refresh_state();self.read_target()
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh_state);self.timer.start(400)
    def floor_m(self):return float(self.app.document_data(default={}).get('floor_m',0.))
    def refresh_state(self):
        scene=self.app.scene;floor=self.floor_m()
        changed=self.last_scene is not scene
        if changed or self.last_floor!=floor:self.floor.setValue(floor*1000);self.last_floor=floor;self.last_scene=scene
        selected=tuple(sorted(id(e) for e in scene.selection));key=(id(scene),scene.content_version,selected,floor)
        if key!=self.measure_key:
            self.measure_key=key;self.measure_data=None
            try:
                groups=selection(scene,True)
                if len(groups)!=1:raise ValueError('離地量測請只選一個群組或元件。')
                lo,hi=bounds(groups);self.measure_data=(lo,hi)
                values={'bottom':(lo[2]-floor)*1000,'top':(hi[2]-floor)*1000,'center':((lo[2]+hi[2])/2-floor)*1000}
                for name,value in self.metrics.items():value.setText(f'{values[name]/10:.2f}')
                self.size_label.setText(f'物件高度：{(hi[2]-lo[2])*1000:.1f} mm｜Ctrl＋Z 復原')
                if selected!=self.last_selected:self.read_target()
            except ValueError as exc:
                for value in self.metrics.values():value.setText('—')
                self.size_label.setText(str(exc))
        self.last_selected=selected;self.app.viewport.update()
    def read_target(self,*_):
        if self.measure_data is None:return
        lo,hi=self.measure_data;mode=self.mode.currentData()
        z={'bottom':lo[2],'top':hi[2],'center':(lo[2]+hi[2])/2}[mode]
        self.target.setValue(float(z-self.floor_m())*1000);self.app.viewport.update()
    def apply_height(self):
        self.run(lambda:move_clearance(self.app.viewport,self.mode.currentData(),self.target.value(),self.floor_m()));self.refresh_state()
    def save_floor(self):
        def apply():
            data=self.app.document_data(default={});data['floor_m']=self.floor.value()/1000;self.app.set_document_data(data);self.refresh_state();self.read_target()
            return '地板標高已更新，物件沒有移動。Ctrl＋Z 可復原。'
        self.run(apply)
    def closeEvent(self,event):self.timer.stop();self.app.viewport.update();super().closeEvent(event)
    def done(self,result):self.timer.stop();self.app.viewport.update();super().done(result)


class ResizeWindow(StyledWindow):
    def __init__(self,app):
        super().__init__(app,'調整面／物件高度',460,430)
        self.body.addWidget(label('調整面／物件高度','title'))
        self.body.addWidget(label('物件：調整高度尺寸，保持底部位置。\n單一水平面：調整絕對標高。','muted'))
        c=card(self.body);c.addWidget(label('目標高度／標高（mm）'));self.target=length_number(1000);c.addWidget(self.target)
        c.addWidget(button('讀取目前選取高度',self.read_current))
        c.addWidget(button('套用高度',self.apply_height,'primary'));self.body.addStretch();self.read_current()
    def read_current(self):
        def read():
            items=selection(self.app.scene)
            if len(items)==1 and isinstance(items[0],Face):
                points=points_of(items)
                if np.ptp(points[:,2])>1e-5:raise ValueError('請選一個水平面。')
                value=float(points[:,2].mean())*1000
            else:lo,hi=bounds(items);value=float(hi[2]-lo[2])*1000
            self.target.setValue(value);return '已讀取目前高度。'
        self.run(read)
    def apply_height(self):self.run(lambda:resize_height(self.app.viewport,self.target.value()))


EXPERIMENTAL_SUBTOOLS={'ffd_snap','legacy_handles'}
EXPERIMENTAL={'cad_height','resize','floor','cabinet_composite','export',*EXPERIMENTAL_SUBTOOLS}

AVAILABLE={'paint','clearance','resize','center_object','center_face','cad_height','floor','lathe','ffd','glass_new','glass_edit','louver_new','louver_edit','decorative_new','decorative_edit','cabinet','cabinet_edit','profile_library','export','cabinet_composite'}


class ToolboxWindow(StyledWindow):
    def __init__(self,app):
        super().__init__(app,'萊姆整合工具箱',700,870)
        import json
        self.catalog=json.loads((ROOT/'lime_integrated_assets'/'catalog.json').read_text(encoding='utf-8'))
        self.category='全部';self.setStyleSheet(STYLE+'QDialog,QWidget#page {background:#f4f7f1;}')
        header=QHBoxLayout();header.addWidget(label('萊姆整合工具箱','title'));header.addStretch();header.addWidget(label(f'{len(AVAILABLE)} 項可用／20 項原工具＋2 項試驗功能','muted'));self.body.addLayout(header)
        self.body.addWidget(label('門片、建模、材質與整理，集中在同一個地方。','muted'))
        self.search=QLineEdit();self.search.setPlaceholderText('搜尋工具，例如：高度、玻璃、置中…');self.search.setMinimumHeight(30);self.search.textChanged.connect(self.rebuild);self.body.addWidget(self.search)
        chips=QGridLayout();chips.setSpacing(6);self.chips={}
        for i,title in enumerate(['全部','門片與櫃體','高度與地板','對齊與造型','材質與匯出','試驗中']):
            chip=QPushButton(title);chip.setAutoDefault(False);chip.setCheckable(True);chip.setChecked(i==0);chip.setProperty('role','chip')
            chip.clicked.connect(lambda checked=False,value=title:self.set_category(value));chips.addWidget(chip,i//6,i%6);self.chips[title]=chip
        self.body.addLayout(chips);self.grid=QGridLayout();self.grid.setSpacing(11);self.body.addLayout(self.grid);self.body.addStretch()
        self.cards=[];self.rebuild()
        self.message('下方分隔為試驗中工具；灰色項目尚未移植。')
    def set_category(self,value):
        self.category=value
        for key,chip in self.chips.items():chip.setChecked(key==value)
        self.rebuild()
    def rebuild(self,*_):
        while self.grid.count():
            item=self.grid.takeAt(0)
            if item.widget():item.widget().hide();item.widget().deleteLater()
        self.cards=[];query=self.search.text().strip().casefold()
        matches=[item for item in self.catalog if (self.category=='全部' or item['category']==self.category or (self.category=='試驗中' and item['id'] in EXPERIMENTAL))
                 and (not query or query in (item['title']+' '+item['description']+(' 試驗中' if item['id'] in EXPERIMENTAL else '')).casefold())]
        ordered=[item for item in matches if item['id'] not in EXPERIMENTAL]+[item for item in matches if item['id'] in EXPERIMENTAL]
        grid_row=0;column=0;section_added=False;self.section_divider=None
        for item in ordered:
            if item['id'] in EXPERIMENTAL and not section_added:
                if column:grid_row+=1;column=0
                divider=QFrame();divider.setFrameShape(QFrame.HLine);divider.setObjectName('lime_experimental_divider');divider.setStyleSheet('background:#b9c8b0;min-height:2px;max-height:2px;border:0;')
                self.grid.addWidget(divider,grid_row,0,1,2);grid_row+=1;self.section_divider=divider
                heading=label('試驗中','section');heading.setStyleSheet('font-size:17px;font-weight:700;color:#8a622b;padding:7px 0;');self.grid.addWidget(heading,grid_row,0,1,2);grid_row+=1;section_added=True
            usable=item['id'] in AVAILABLE or item['id'] in EXPERIMENTAL_SUBTOOLS
            card_button=QPushButton();card_button.setAutoDefault(False);card_button.setProperty('role','tool');card_button.setMinimumHeight(105)
            card_button.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed);card_button.setFixedHeight(125)
            row=QHBoxLayout(card_button);row.setContentsMargins(13,13,13,13);row.setSpacing(12)
            icon=QLabel();icon.setPixmap(QPixmap(str(ROOT/'lime_integrated_assets'/item['icon'])).scaled(32,32,Qt.KeepAspectRatio,Qt.SmoothTransformation));icon.setFixedWidth(34);row.addWidget(icon,0,Qt.AlignTop)
            content=QVBoxLayout();title=label(item['title']+('  ·  試驗中' if item['id'] in EXPERIMENTAL else ''));title.setStyleSheet('font-weight:600;color:#173f49;' if usable else 'font-weight:600;color:#8b9e9d;')
            description=label(item['description'] if usable else item['description']+'（待移植）','muted');content.addWidget(title);content.addWidget(description);row.addLayout(content)
            for child in (icon,title,description):child.setAttribute(Qt.WA_TransparentForMouseEvents)
            card_button.setEnabled(usable);card_button.setToolTip(cm_text(item['description']) if usable else '此工具尚未移植，目前不能使用。')
            card_button.clicked.connect(lambda checked=False,key=item['id']:dispatch(self.app,key));self.grid.addWidget(card_button,grid_row,column);self.cards.append((item['id'],card_button));column+=1
            if column==2:grid_row+=1;column=0
        self.grid.setColumnStretch(0,1);self.grid.setColumnStretch(1,1)
        if not matches:self.grid.addWidget(label('沒有符合的工具。','muted'),0,0)


def show_window(app,kind):
    classes={'home':ToolboxWindow,'paint':PaintWindow,'clearance':ClearanceWindow,'resize':ResizeWindow,'cad_height':CADWindow,'floor':FloorWindow,'lathe':LatheWindow,'ffd':FFDWindow,'profile_library':AccessoryWindow,'export':BatchExportWindow,'cabinet_composite':CompositeWindow,'legacy_handles':LegacyHandlesWindow}
    key='_lime_suite_'+kind;window=getattr(app.window,key,None)
    if window is None or not window.isVisible():window=(ParametricWindow(app,kind.split('_')[0],kind.endswith('_edit')) if kind.startswith(('glass_','louver_')) else DecorativeWindow(app,kind.endswith('_edit')) if kind.startswith('decorative_') else CabinetWindow(app,kind=='cabinet_edit') if kind in ('cabinet','cabinet_edit') else classes[kind](app));setattr(app.window,key,window)
    window.show();window.raise_();window.activateWindow();return window


def dispatch(app,key):
    try:
        if key=='ffd_snap':
            window=show_window(app,'ffd')
            if window.engine is not None and window.selected:window.start_surface_snap()
            else:window.message('表面吸附 · 試驗中：先選控制點，再按吸附到表面。')
            return window
        if key in ('paint','clearance','resize','cad_height','floor','lathe','ffd','glass_new','glass_edit','louver_new','louver_edit','decorative_new','decorative_edit','cabinet','cabinet_edit','profile_library','export','cabinet_composite','legacy_handles'):return show_window(app,key)
        if key=='home':return show_window(app,'home')
        if key in ('center_object','center_face'):return start_center(app,'object' if key=='center_object' else 'face')
        app.viewport.flash_status('此工具尚未移植。',5000)
    except Exception as exc:app.viewport.flash_status(cm_text(str(exc)),7000)


def _draw_base_overlay(app,viewport,painter):
    center=getattr(app.window,'_lime_integrated_center',None)
    if center is not None and center.active and center.vp is viewport and center.preview is not None:
        points,start,end,target,_=center.preview;px,py,front=viewport.world_to_pixels(points)
        if all(front):
            painter.setPen(QPen(QColor(0,165,200),2));painter.setBrush(Qt.NoBrush);painter.drawPolygon(QPolygonF([QPointF(float(x),float(y)) for x,y in zip(px,py)]))
        px,py,front=viewport.world_to_pixels(np.array([start,end,target]))
        if all(front):
            painter.drawLine(QPointF(float(px[0]),float(py[0])),QPointF(float(px[1]),float(py[1])));painter.setBrush(QColor(220,35,45));painter.drawEllipse(QPointF(float(px[2]),float(py[2])),5.,5.)
    paint=getattr(app.window,'_lime_integrated_paint',None)
    if paint is not None and paint.active and paint.vp is viewport and paint.hover_box is not None:
        lo,hi=paint.hover_box;corners=np.array([[x,y,z] for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])]);px,py,front=viewport.world_to_pixels(corners)
        painter.setPen(QPen(QColor(16,161,181),2))
        for i in range(8):
            for bit in (1,2,4):
                j=i^bit
                if i<j and front[i] and front[j]:painter.drawLine(QPointF(float(px[i]),float(py[i])),QPointF(float(px[j]),float(py[j])))
    window=getattr(app.window,'_lime_suite_clearance',None)
    if window is None or not window.isVisible() or app.viewport is not viewport or window.measure_data is None:return
    if window.measure_key!=(id(viewport.scene),viewport.scene.content_version,tuple(sorted(id(e) for e in viewport.scene.selection)),window.floor_m()):return
    lo,hi=window.measure_data;point=(lo+hi)/2;point[2]={'bottom':lo[2],'top':hi[2],'center':point[2]}[window.mode.currentData()];floor=point.copy();floor[2]=window.floor_m()
    px,py,front=viewport.world_to_pixels(np.array([point,floor]))
    if all(front):
        painter.setPen(QPen(QColor(205,45,45),2));painter.setBrush(QColor(205,45,45));a,b=QPointF(float(px[0]),float(py[0])),QPointF(float(px[1]),float(py[1]))
        painter.drawLine(a,b);painter.drawEllipse(a,5.,5.);painter.drawText(a+QPointF(9,-8),f'{(point[2]-floor[2])*100:.2f} cm')


def setup(app):
    entries=[('萊姆整合工具箱','suite.png','home'),('群組上色','paint.png','paint'),('調整面／物件高度','resize.png','resize'),
             ('物件離地高度','clearance.png','clearance'),('整體置中','center.png','center_object'),('面置中','face.png','center_face'),
             ('CAD 圖層長高度','tool_cad_height.png','cad_height'),('產生輪廓地板','tool_floor.png','floor'),('Lathe 旋轉成形','tool_lathe.png','lathe'),('FFD 自由變形','tool_ffd.png','ffd'),('新增玻璃門','tool_glass_new.png','glass_new'),('玻璃門設定','tool_glass_edit.png','glass_edit'),('新增百葉窗','tool_louver_new.png','louver_new'),('百葉窗設定','tool_louver_edit.png','louver_edit'),('新增造型門片','tool_decorative_new.png','decorative_new'),('造型門片設定','tool_decorative_edit.png','decorative_edit'),('新增參數櫃體','tool_cabinet.png','cabinet'),('櫃體設定','tool_cabinet_edit.png','cabinet_edit'),('配件資料庫','tool_profile_library.png','profile_library'),('元件批次匯出（試驗中）','tool_export.png','export'),('組合櫃（試驗中）','tool_cabinet_composite.png','cabinet_composite')]
    toolbar=QToolBar('萊姆整合工具箱',app.window);toolbar.setObjectName('lime_integrated_tools_port');toolbar.setIconSize(QSize(32,32))
    for title,icon,key in entries:
        action=app.add_menu_action(title,lambda command=key:dispatch(app,command),tip='萊姆整合工具箱 IngeTrazo '+VERSION)
        if action is not None:action.setIcon(QIcon(str(ROOT/'lime_integrated_assets'/icon)));action.setAutoRepeat(False);toolbar.addAction(action)
    app.window.addToolBar(toolbar);app.add_overlay(lambda viewport,painter:draw_overlay(app,viewport,painter))
    if not getattr(app.window,'_lime_parametric_watcher',None):app.window._lime_parametric_watcher=ParametricScaleWatcher(app)


# ---- Second migration batch: independent geometry, no original CAD edits ----
from core.mesh import Mesh
from core.layers import DEFAULT_LAYER
from core.group import iter_placements
from core.arrangement import planar_arrangement,_build_graph,_trace_faces,_prune_dangling,_signed_area,_point_in_polygon


class AddGenerated(Command):
    def __init__(self,group):self.group=bind_face_materials(group);self.owner=None;self.before_selection=None
    def do(self,scene):
        if any(not editable(scene,level['group']) for level in scene._edit_stack):raise ValueError('外層群組或圖層已鎖定，無法建立模型。')
        if self.owner is None:
            self.owner=scene.groups if scene.edit_group is None else scene.edit_group.children
            self.before_selection=set(scene.selection)
        if self.group not in self.owner:self.owner.append(self.group)
        scene.selection.clear();scene.selection.add(self.group);scene.version+=1
    def undo(self,scene):
        if self.group in self.owner:self.owner.remove(self.group)
        scene.selection.clear();scene.selection.update(self.before_selection);scene.version+=1


def scan_segments(scene,scope='selection',purpose='cad'):
    if scene.edit_group is not None:raise ValueError('請先退出群組編輯，再讀取 CAD／牆體。')
    roots=(list(scene.groups)+list(scene.mesh.edges)) if scope=='all' else list(scene.selection)
    if not roots:raise ValueError('請先選取 CAD、牆體群組或線框。')
    found={}
    def tag(own,inherited):return own if own and own!=DEFAULT_LAYER else inherited or DEFAULT_LAYER
    def add(edge,matrix,inherited):
        name=tag(getattr(edge,'layer',None),inherited)
        a,b=edge.a,edge.b
        if matrix is not None:a,b=matrix.map(a),matrix.map(b)
        found.setdefault(name,[]).append((xyz(a),xyz(b)))
    def visit(group,parent=None,inherited=None):
        generated=(group.ext or {}).get('lime_suite_generated')
        if (generated and (purpose=='cad' or generated=='floor')) or group.hidden:return
        visible,_=scene._layer_state(group)
        if not visible:return
        matrix=group.xform if parent is None else (parent*group.xform if group.xform is not None else parent)
        inherited=tag(group.layer,inherited)
        for edge in group.mesh.edges:add(edge,matrix,inherited)
        for child in group.children or ():visit(child,matrix,inherited)
    for root in roots:
        if isinstance(root,Group):visit(root)
        elif isinstance(root,Edge):add(root,None,None)
        elif isinstance(root,Face):
            for loop in (root.loop,*root.hole_loops):
                for a,b in zip(loop,loop[1:]+loop[:1]):found.setdefault(root.attrs.get('layer') or DEFAULT_LAYER,[]).append((xyz(a.position),xyz(b.position)))
        else:raise ValueError('請只選取群組、元件、面或線。')
    if not found:raise ValueError('沒有可用線條；已略過工具產生的模型。')
    return found


def unique_xy(segments):
    snap=1e-5;seen=set();result=[]
    for a,b in segments:
        a,b=np.asarray(a)[:2],np.asarray(b)[:2]
        ka,kb=tuple(np.rint(a/snap).astype(int)),tuple(np.rint(b/snap).astype(int))
        key=tuple(sorted((ka,kb)))
        if ka==kb or key in seen:continue
        seen.add(key);result.append((np.array(ka,dtype=float)*snap,np.array(kb,dtype=float)*snap))
    return result


def xy_regions(segments):
    xy=unique_xy(segments)
    regions=planar_arrangement([(QVector3D(float(a[0]),float(a[1]),0),QVector3D(float(b[0]),float(b[1]),0)) for a,b in xy],QVector3D(),QVector3D(0,0,1))
    return [(np.array([xyz(v)[:2] for v in outer]),[np.array([xyz(v)[:2] for v in hole]) for hole in holes]) for outer,holes in regions]


def polygon_group(outer,holes,height,base,name):
    if not np.isfinite([height,base]).all() or height<0:raise ValueError('高度／厚度需為有效數值且不能小於 0。')
    if height==0:
        mesh=Mesh();face=mesh.add_face([QVector3D(float(x),float(y),float(base)) for x,y in outer],
            [[QVector3D(float(x),float(y),float(base)) for x,y in hole] for hole in holes] or None)
        if face.normal().z()<0:
            from core.mesh import turn_face_over
            turn_face_over(face)
        return Group(mesh,name=name)
    import manifold3d as m3d
    from core.solids import _Sources,_loops_from,_group_from_loops,solid_report
    section=m3d.CrossSection([np.asarray(outer),*[np.asarray(h) for h in holes]],m3d.FillRule.EvenOdd)
    if section.area()<=1e-12:raise ValueError('輪廓沒有可用面積。')
    solid=section.extrude(float(height)).translate((0.,0.,float(base)))
    if solid.status()!=m3d.Error.NoError:raise ValueError('輪廓無法生成封閉實體。')
    sources=_Sources();group=_group_from_loops(_loops_from(solid,sources),sources,name,None)
    ok,volume=solid_report(group.mesh)
    if not ok or abs(volume-section.area()*height)>max(1e-9,section.area()*height*1e-5):raise ValueError('生成後的封閉性／體積驗證未通過。')
    return group


def cad_model(found,names,height_mm,base_mm,keep_holes=True):
    height,base=float(height_mm)/1000,float(base_mm)/1000
    if not np.isfinite([height,base]).all() or height<=0:raise ValueError('長出的高度必須大於 0 mm。')
    if not names:raise ValueError('請至少勾選一個圖層。')
    result=Group(name=f'CAD 立體_{height_mm/10:g}cm');result.ext={'lime_suite_generated':'cad'}
    report=[]
    for name in names:
        if name not in found:raise ValueError('圖層已變更，請重新讀取。')
        segments=found[name];z=np.array([p[2] for line in segments for p in line])
        if np.ptp(z)>.0001:raise ValueError(f'圖層「{name}」不是同一水平面，請先整理 CAD。')
        regions=xy_regions(segments);layer=Group(name=name);layer.layer=None if name==DEFAULT_LAYER else name
        for outer,holes in regions:
            if keep_holes:
                test=Mesh();face=test.add_face([QVector3D(float(x),float(y),0) for x,y in outer],[[QVector3D(float(x),float(y),0) for x,y in h] for h in holes] or None)
                triangles=face.triangulate();sample=np.mean([xyz(p)[:2] for p in triangles[0]],axis=0)
                depth=sum(_point_in_polygon(sample.tolist(),loop.tolist()) for loop,_ in regions)
                if depth%2==0:continue
            solid=polygon_group(outer,holes,height,base,f'{name}_{len(layer.children)+1}')
            solid.ext={'lime_suite_generated':'cad'};layer.children.append(solid)
        if layer.children:
            layer.adopt(layer.children);layer.component=False;result.children.append(layer)
        report.append(f'{name}：{len(layer.children)} 個區域')
    if not result.children:raise ValueError('找不到封閉區域；請檢查斷線、缺口及圖層是否完整。')
    result.adopt(result.children);result.component=False
    return result,'；'.join(report)


def convex_hull(points):
    ordered=sorted(set(tuple(p) for p in points))
    if len(ordered)<3:raise ValueError('輪廓不足三個點。')
    def cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    def half(data):
        out=[]
        for p in data:
            while len(out)>1 and cross(out[-2],out[-1],p)<=0:out.pop()
            out.append(p)
        return out
    hull=half(ordered)[:-1]+half(ordered[::-1])[:-1]
    if len(hull)<3:raise ValueError('所有外圍點在同一直線。')
    return np.array(hull)


def outer_outline(segments,mode='trace',gap=6.,step=.25):
    xy=unique_xy(segments)
    if not xy:raise ValueError('選取範圍沒有可用輪廓。')
    points=np.array([p for line in xy for p in line]);lo,hi=points.min(axis=0),points.max(axis=0)
    if mode=='hull':return convex_hull(points)
    if mode=='rectangle':return np.array([lo,[hi[0],lo[1]],hi,[lo[0],hi[1]]])
    if mode not in ('trace','closed'):raise ValueError('輪廓模式無效。')
    coords,adj=_build_graph(xy)
    if mode=='trace' and gap>0:
        from core.arrangement import _key
        labels={};counter=0
        for key in adj:
            if key in labels:continue
            counter+=1;todo=[key];labels[key]=counter
            while todo:
                for other in adj[todo.pop()]:
                    if other not in labels:labels[other]=counter;todo.append(other)
        directions={}
        for a,b in xy:
            d=b-a;length=np.linalg.norm(d)
            if length<1e-8:continue
            u=d/length
            if u[0]<-1e-8 or (abs(u[0])<1e-8 and u[1]<0):u=-u
            first,last=(a,b) if np.dot(a,u)<np.dot(b,u) else (b,a)
            directions.setdefault(round(float(np.arctan2(u[1],u[0]))*1e6),[]).append((first,last,u))
        bridges=[]
        for rows in directions.values():
            for a,b,u in rows:
                for c,d,_ in rows:
                    distance=float(np.dot(c-b,u));delta=c-b;offset=abs(float(u[0]*delta[1]-u[1]*delta[0]))
                    if not (1e-5<distance<=gap) or labels.get(_key(*b))==labels.get(_key(*c)):continue
                    tolerance=step if min(abs(u[0]),abs(u[1]))<1e-6 else .0001
                    if offset>tolerance:continue
                    if offset<=1e-5:bridges.append((b,c))
                    else:corner=b+u*distance;bridges.extend(((b,corner),(corner,c)))
        coords,adj=_build_graph(xy+bridges)
    _prune_dangling(adj)
    loops=_trace_faces(coords,adj)
    candidates=[np.array([coords[k] for k in loop]) for loop in loops]
    candidates=[loop for loop in candidates if _signed_area(loop.tolist())<0]
    if not candidates:raise ValueError('沒有封閉外圍；請選完整牆體，或調整補線距離。')
    ring=max(candidates,key=lambda loop:abs(_signed_area(loop.tolist())))
    # Pruned spur/bridge walks may retrace vertices; retain simple cycles only.
    stack=[];positions={};simple=[]
    for point in [*map(tuple,ring),tuple(ring[0])]:
        if point in positions:
            start=positions[point];cycle=stack[start:]
            if len(cycle)>=3:simple.append(np.array(cycle))
            for removed in stack[start+1:]:positions.pop(removed,None)
            stack=stack[:start+1]
        else:positions[point]=len(stack);stack.append(point)
    if simple:ring=max(simple,key=lambda loop:abs(_signed_area(loop.tolist())))
    if abs(_signed_area(ring.tolist()))<.15*float(np.prod(hi-lo)):raise ValueError('未找到整體外框，請選完整牆體或調整補線距離。')
    return ring


def floor_model(found,thickness_mm,surface_mm,mode,gap_mm,step_mm):
    thickness,surface,gap,step=np.array([thickness_mm,surface_mm,gap_mm,step_mm],dtype=float)/1000
    if not np.isfinite([thickness,surface,gap,step]).all() or min(thickness,gap,step)<0:raise ValueError('厚度、補線距離與轉角差距不能小於 0。')
    segments=[line for rows in found.values() for line in rows]
    ring=outer_outline(segments,mode,gap,step)
    group=polygon_group(ring,[],float(thickness),float(surface-thickness),'最外圍地板')
    group.ext={'lime_suite_generated':'floor','thickness_mm':thickness_mm,'surface_mm':surface_mm,'outline_mode':mode}
    return group,ring


def ordered_profile(scene):
    items=list(scene.selection)
    if not items or any(not isinstance(e,Edge) or e not in scene.mesh.edges for e in items):raise ValueError('請只選一條連續剖面線；群組內的線請先進入編輯再選取。')
    adjacent={}
    for edge in items:
        for vertex in (edge.v0,edge.v1):adjacent.setdefault(vertex,[]).append(edge)
    if any(len(edges)>2 for edges in adjacent.values()):raise ValueError('剖面不能分岔。')
    ends=[v for v,edges in adjacent.items() if len(edges)==1]
    if len(ends) not in (0,2):raise ValueError('請選單一開放或封閉輪廓。')
    current=ends[0] if ends else items[0].v0;points=[xyz(current.position)];used=set()
    while True:
        edge=next((e for e in adjacent[current] if e not in used),None)
        if edge is None:break
        used.add(edge);current=edge.v1 if current is edge.v0 else edge.v0;points.append(xyz(current.position))
    if len(used)!=len(items):raise ValueError('剖面包含不相連的線段。')
    return np.array(points)


def lathe_model(points,origin,axis=2,angle=360.,segments=64,flip=False,smooth=True):
    from math import radians,cos,sin
    from core.solids import _Sources,_group_from_loops
    points=np.asarray(points,dtype=float);origin=np.asarray(origin,dtype=float)
    if axis not in (0,1,2) or not 1<=abs(angle)<=360 or not 3<=segments<=256 or int(segments)!=segments:raise ValueError('旋轉軸、角度或分段數無效。')
    segments=int(segments)
    if len(points)<2 or (len(points)-1)*segments>40000:raise ValueError('剖面／分段過多，最多 40,000 格。')
    u,v=(axis+1)%3,(axis+2)%3;local=points-origin
    epsilon=max(1e-9,float(np.max(np.linalg.norm(local,axis=1)))*1e-9)
    radial=local[:,[u,v]];r=np.linalg.norm(radial,axis=1)
    if r.max()<epsilon:raise ValueError('剖面完全在旋轉軸上。')
    direction=radial[int(r.argmax())]/r.max()
    if np.max(np.abs(radial[:,0]*direction[1]-radial[:,1]*direction[0]))>max(1e-6,epsilon*10) or np.min(radial@direction)<-epsilon:
        raise ValueError('剖面需位於同一個含軸平面，並在旋轉軸同一側；請調整軸位置。')
    full=abs(abs(angle)-360)<1e-8;count=segments if full else segments+1
    rings=[]
    for i in range(count):
        theta=radians(angle)*i/segments;c,s=cos(theta),sin(theta);row=local.copy()
        row[:,u]=local[:,u]*c-local[:,v]*s;row[:,v]=local[:,u]*s+local[:,v]*c
        row[r<epsilon,u]=0;row[r<epsilon,v]=0;rings.append(row+origin)
    if full:rings.append(rings[0])
    loops=[];seen=set()
    for i in range(segments):
        for j in range(len(points)-1):
            quad=[rings[i][j],rings[i][j+1],rings[i+1][j+1],rings[i+1][j]]
            unique=[]
            for p in quad:
                if not any(np.linalg.norm(p-q)<=epsilon for q in unique):unique.append(p)
            if len(unique)<3:continue
            for k in range(1,len(unique)-1):
                triangle=[unique[0],unique[k],unique[k+1]]
                if np.linalg.norm(np.cross(triangle[1]-triangle[0],triangle[2]-triangle[0]))<epsilon**2:raise ValueError('旋轉後有退化面，請調整軸位置。')
                key=tuple(sorted(tuple(np.rint(p/epsilon).astype(np.int64)) for p in triangle))
                if key in seen:raise ValueError('旋轉後有重複面，請調整剖面或旋轉軸。')
                seen.add(key)
                from core.mesh import _key
                qpoints=[QVector3D(*p) for p in triangle]
                if len({_key(p) for p in qpoints})<3:raise ValueError('尺寸太小或分段過密，請降低分段數或放大剖面。')
                loops.append((qpoints,{},j))
    if not loops:raise ValueError('沒有可生成的曲面。')
    sources=_Sources();group=_group_from_loops(loops,sources,f'Lathe {"XYZ"[axis]} {angle:g}°',None)
    from core.orient import orient_outward
    from core.mesh import turn_face_over
    orient_outward(group.mesh)
    if flip:
        for face in group.mesh.faces:turn_face_over(face)
    if smooth:
        for edge in group.mesh.edges:
            if len(edge.faces)==2 and QVector3D.dotProduct(edge.faces[0].normal(),edge.faces[1].normal())>.94:edge.soft=True
    group.ext={'lime_suite_generated':'lathe'}
    return group


class FFDEngine:
    def __init__(self,points,size=3):
        self.points=np.asarray(points,dtype=float)
        if not len(self.points) or not np.isfinite(self.points).all():raise ValueError('沒有有效頂點。')
        self.minimum=self.points.min(axis=0);span=np.ptp(self.points,axis=0);diagonal=np.linalg.norm(span)
        if diagonal<1e-8:raise ValueError('物件尺寸太小。')
        thin=span<diagonal*1e-7;span[thin]=diagonal*.1;self.minimum[thin]-=span[thin]/2
        self.span=span;self.set_size(size)
    def set_size(self,size):
        if size not in (2,3):raise ValueError('控制框只支援 2×2×2 或 3×3×3。')
        self.size=size
        self.coordinates=np.array([(i,j,k) for k in range(size) for j in range(size) for i in range(size)])
        self.rest=self.minimum+self.coordinates/(size-1)*self.span
        self.controls=self.rest.copy();self.locked=set()
    def evaluate(self,points):
        points=np.asarray(points,dtype=float);out=np.empty_like(points);offsets=self.controls-self.rest
        for start in range(0,len(points),32768):
            block=points[start:start+32768];t=(block-self.minimum)/self.span
            if self.size==2:bases=[np.stack((1-t[:,a],t[:,a]),axis=1) for a in range(3)]
            else:bases=[np.stack(((1-t[:,a])**2,2*t[:,a]*(1-t[:,a]),t[:,a]**2),axis=1) for a in range(3)]
            weights=bases[0][:,self.coordinates[:,0]]*bases[1][:,self.coordinates[:,1]]*bases[2][:,self.coordinates[:,2]]
            out[start:start+len(block)]=block+weights@offsets
        return out
    def transform(self,selected,mode,axis,value):
        selected=set(selected);movable=sorted(selected-self.locked)
        if not movable:raise ValueError('請選取未鎖定控制點。')
        if mode!='move' and len(selected)<2:raise ValueError('旋轉或縮放至少選兩個控制點。')
        if not np.isfinite(value):raise ValueError('數值必須有限。')
        if axis not in (0,1,2):raise ValueError('請選 X、Y 或 Z 方向。')
        pivot=self.controls[sorted(selected)].mean(axis=0)
        if mode=='move':self.controls[movable,axis]+=value
        elif mode=='scale':
            if not .01<=value<=100:raise ValueError('縮放比例需介於 0.01～100。')
            self.controls[movable,axis]=pivot[axis]+(self.controls[movable,axis]-pivot[axis])*value
        elif mode=='rotate':
            from math import radians,cos,sin
            a,b=(axis+1)%3,(axis+2)%3;angle=radians(value);v=self.controls[movable]-pivot;new=v.copy()
            new[:,a]=v[:,a]*cos(angle)-v[:,b]*sin(angle);new[:,b]=v[:,a]*sin(angle)+v[:,b]*cos(angle)
            self.controls[movable]=new+pivot
        else:raise ValueError('變形模式無效。')
    def validate(self):
        if not np.isfinite(self.controls).all():raise ValueError('控制點包含無效數值。')
        # Refuse folded/collapsed cages before touching the source mesh.
        unit=np.array([(x,y,z) for x in np.linspace(0,1,5) for y in np.linspace(0,1,5) for z in np.linspace(0,1,5)])
        points=self.minimum+unit*self.span;jac=[]
        for axis in range(3):
            delta=np.zeros(3);delta[axis]=self.span[axis]*1e-5
            jac.append((self.evaluate(points+delta)-self.evaluate(points-delta))/(2*delta[axis]))
        if np.min(np.linalg.det(np.stack(jac,axis=2)))<=1e-5:raise ValueError('控制框已翻轉或壓扁，請縮小變形量。')


def deformed_tree(root,engine):
    from core.topology import fold_nonplanar_faces
    engine.validate();copy=clone_tree(root)
    def visit(group,parent=None):
        matrix=group.xform if parent is None else (parent*group.xform if group.xform is not None else parent)
        inverse,ok=matrix.inverted() if matrix is not None else (QMatrix4x4(),True)
        if not ok:raise ValueError('物件含零縮放，無法變形。')
        vertices=list(group.mesh.vertices)
        if vertices:
            world=np.array([xyz(matrix.map(v.position) if matrix is not None else v.position) for v in vertices])
            changed=engine.evaluate(world)
            if not np.isfinite(changed).all():raise ValueError('變形座標無效。')
            for vertex,point in zip(vertices,changed):group.mesh.place_vertex(vertex,inverse.map(QVector3D(*point)))
            fold_nonplanar_faces(group.mesh)
            if any(face.area()<=1e-12 for face in group.mesh.faces):raise ValueError('變形產生退化面，請縮小變形量。')
        for child in group.children or ():visit(child,matrix)
    visit(copy)
    return copy


class ReplaceGeometry(Command):
    def __init__(self,target,replacement):self.target=target;self.replacement=replacement;self.before=None
    def do(self,scene):
        if self.before is None:self.before=(self.target.mesh,self.target.children)
        self.target.mesh=self.replacement.mesh;self.target.children=self.replacement.children
        _dirty_group_chunks(scene);scene.version+=1
    def undo(self,scene):
        self.target.mesh,self.target.children=self.before;_dirty_group_chunks(scene);scene.version+=1


def mesh_preview(group,limit=12000):
    mesh=world_mesh(group);edges=list(mesh.edges)
    if len(edges)>limit:edges=[edges[i] for i in np.linspace(0,len(edges)-1,limit,dtype=int)]
    return np.array([(xyz(e.a),xyz(e.b)) for e in edges],dtype=float).reshape(-1,2,3)


class GeometryWindow(StyledWindow):
    def __init__(self,app,title,width=480,height=740):
        super().__init__(app,title,width,height);self.vp=app.viewport;self.scene=app.scene
        self.context=self.scene.edit_group;self.version=self.scene.content_version;self.preview_lines=None
        self.body.addWidget(label(title,'title'))
    def check(self,strict=False):
        if self.app.viewport is not self.vp or self.vp.scene is not self.scene or self.scene.edit_group is not self.context:raise ValueError('專案或編輯層級已變更，請重新開啟工具。')
        if strict and self.scene.content_version!=self.version:raise ValueError('來源模型已變更，請重新開啟工具。')
    def closeEvent(self,event):self.preview_lines=None;self.vp.update();super().closeEvent(event)
    def done(self,result):self.preview_lines=None;self.vp.update();super().done(result)


class CADWindow(GeometryWindow):
    def __init__(self,app):
        super().__init__(app,'CAD 圖層長高度');self.body.addWidget(label('依封閉線框生成立體，保留原始 CAD。尺寸單位 mm。','muted'))
        c=card(self.body);self.scope=QComboBox();self.scope.addItem('目前選取','selection');self.scope.addItem('全部可用線條','all');c.addWidget(self.scope)
        c.addWidget(button('讀取圖層',self.load_layers));self.layers=QListWidget();self.layers.setMinimumHeight(180);c.addWidget(self.layers)
        c=card(self.body);c.addWidget(label('長出高度（mm）'));self.extrude_height=length_number(3000,0);c.addWidget(self.extrude_height)
        c.addWidget(label('底部標高（mm）'));self.base=length_number(0);c.addWidget(self.base)
        self.holes=QCheckBox('保留內圈孔洞（奇偶填滿）');self.holes.setChecked(True);c.addWidget(self.holes)
        self.body.addWidget(button('依勾選圖層建立立體',self.generate,'primary'));self.body.addStretch();self.found={};self.load_layers()
    def load_layers(self):
        def load():
            self.check();self.found=scan_segments(self.scene,self.scope.currentData());self.layers.clear()
            for name,lines in sorted(self.found.items()):
                item=QListWidgetItem(f'{name}（{len(lines)} 條線）');item.setData(Qt.UserRole,name);item.setFlags(item.flags()|Qt.ItemIsUserCheckable);item.setCheckState(Qt.Checked);self.layers.addItem(item)
            return f'已讀取 {len(self.found)} 個圖層。'
        self.run(load)
    def generate(self):
        def create():
            self.check();found=scan_segments(self.scene,self.scope.currentData());names=[self.layers.item(i).data(Qt.UserRole) for i in range(self.layers.count()) if self.layers.item(i).checkState()==Qt.Checked]
            group,report=cad_model(found,names,self.extrude_height.value(),self.base.value(),self.holes.isChecked());execute(self.vp,AddGenerated(group));return '已建立 CAD 立體。'+report+'。Ctrl＋Z 可復原。'
        self.run(create)


class FloorWindow(GeometryWindow):
    def __init__(self,app):
        super().__init__(app,'產生輪廓地板');self.body.addWidget(label('選取完整牆體或 CAD，地板在原座標建立，厚度向下。','muted'))
        c=card(self.body);self.mode=QComboBox()
        for title,key in [('貼合牆線（保留轉角、補齊缺口）','trace'),('最外圍輪廓（保留凹角）','closed'),('自動包覆外圍（填平凹角）','hull'),('最大矩形範圍','rectangle')]:self.mode.addItem(title,key)
        c.addWidget(self.mode);c.addWidget(label('地板厚度（mm，0＝只有面）'));self.thickness=length_number(100,0);c.addWidget(self.thickness)
        c.addWidget(label('地板表面標高（mm）'));self.surface=length_number(0);c.addWidget(self.surface)
        c.addWidget(label('最大補線距離（mm）'));self.gap=length_number(6000,0);c.addWidget(self.gap)
        c.addWidget(label('轉角補線差距（mm）'));self.step=length_number(250,0);c.addWidget(self.step)
        self.body.addWidget(button('預覽輪廓',self.preview));self.body.addWidget(button('建立地板',self.generate,'primary'));self.body.addStretch();self.read_source()
    def read_source(self):
        def read():
            self.check();self.found=scan_segments(self.scene,purpose='floor');z=min(float(p[2]) for lines in self.found.values() for line in lines for p in line);self.surface.setValue(z*1000);return '已讀取選取範圍。'
        self.run(read)
    def model(self):
        self.check();found=scan_segments(self.scene,purpose='floor');return floor_model(found,self.thickness.value(),self.surface.value(),self.mode.currentData(),self.gap.value(),self.step.value())
    def preview(self):
        def show():
            _,ring=self.model();points=np.column_stack((ring,np.full(len(ring),self.surface.value()/1000)));self.preview_lines=np.array(list(zip(points,np.roll(points,-1,axis=0))));self.version=self.scene.content_version;self.vp.update();return '藍色輪廓是地板預覽，原模型未修改。'
        self.run(show)
    def generate(self):
        def create():
            group,_=self.model();execute(self.vp,AddGenerated(group));self.preview_lines=None;return '已建立獨立地板群組。Ctrl＋Z 可復原。'
        self.run(create)


class LatheWindow(GeometryWindow):
    def __init__(self,app):
        super().__init__(app,'Lathe 旋轉成形',470,830);self.body.addWidget(label('先選取單一連續剖面線。預覽不改原線，按建立才寫入。','muted'))
        self.profile=None;self.preview_group=None;self.pick_tool=None
        c=card(self.body);self.axis=QComboBox();self.axis.addItems(['X 軸','Y 軸','Z 軸']);self.axis.setCurrentIndex(2);self.axis.hide()
        axes=QHBoxLayout();self.axis_buttons=[];self.axis_group=QButtonGroup(self)
        for i,title in enumerate(('X 軸','Y 軸','Z 軸')):
            choice=QPushButton(title);choice.setCheckable(True);choice.setAutoDefault(False);choice.setProperty('role','chip');choice.setChecked(i==2);self.axis_group.addButton(choice);self.axis_buttons.append(choice);axes.addWidget(choice)
            choice.clicked.connect(lambda checked=False,index=i:self.axis.setCurrentIndex(index))
        self.axis.currentIndexChanged.connect(lambda index:self.axis_buttons[index].setChecked(True));c.addLayout(axes)
        self.align=QComboBox();self.align.addItems(['外框最小點','外框中心','外框最大點','自訂軸位置']);c.addWidget(self.align)
        self.origin=[length_number(0) for _ in range(3)]
        for title,widget in zip(('軸位置 X（mm）','軸位置 Y（mm）','軸位置 Z（mm）'),self.origin):c.addWidget(label(title));c.addWidget(widget)
        c.addWidget(button('在模型上點選軸位置',self.start_pick));c.addWidget(label('旋轉角度（度）'));self.angle=number(360,1);self.angle.setMaximum(360);c.addWidget(self.angle)
        c.addWidget(label('分段數（3～256）'));self.segments=LockedSpinBox();self.segments.setRange(3,256);self.segments.setValue(64);c.addWidget(self.segments)
        self.smooth=QCheckBox('平滑曲面邊線');self.smooth.setChecked(True);c.addWidget(self.smooth);self.flip=QCheckBox('反轉面方向');c.addWidget(self.flip)
        self.body.addWidget(button('重新讀取選取剖面',self.read_profile));self.body.addWidget(button('建立旋轉模型',self.generate,'primary'));self.body.addWidget(label('開放剖面或未滿 360° 可能產生開放曲面，不自動補上原剖面沒有的端面。','muted'))
        for control in (self.axis,self.align):control.currentIndexChanged.connect(self.refresh_preview)
        for spin in self.origin+[self.angle,self.segments]:spin.valueChanged.connect(self.refresh_preview)
        for check in (self.smooth,self.flip):check.toggled.connect(self.refresh_preview)
        self.read_profile()
    def read_profile(self):
        def read():
            self.check();self.profile=ordered_profile(self.scene);self.version=self.scene.content_version;self.refresh_preview()
            if self.preview_group is None:raise ValueError(self.status.text())
            return '已讀取剖面並更新預覽。'
        self.run(read)
    def parameters(self):
        if self.profile is None:raise ValueError('請先選取剖面線並重新讀取。')
        mode=self.align.currentIndex()
        origin=(self.profile.min(axis=0),(self.profile.min(axis=0)+self.profile.max(axis=0))/2,self.profile.max(axis=0))[mode] if mode<3 else np.array([s.value()/1000 for s in self.origin])
        if mode<3:
            for spin,value in zip(self.origin,origin):spin.blockSignals(True);spin.setValue(float(value)*1000);spin.blockSignals(False)
        for spin in self.origin:spin.setEnabled(mode==3)
        return dict(points=self.profile,origin=origin,axis=self.axis.currentIndex(),angle=self.angle.value(),segments=self.segments.value(),flip=self.flip.isChecked(),smooth=self.smooth.isChecked())
    def refresh_preview(self,*_):
        self.preview_group=None;self.preview_lines=None
        if self.profile is None:return
        try:
            self.check(True);self.preview_group=lathe_model(**self.parameters());self.preview_lines=mesh_preview(self.preview_group);self.message('旋轉線框預覽已更新，按建立才寫入模型。')
        except Exception as exc:self.message(str(exc),True)
        self.vp.update()
    def generate(self):
        def create():
            self.check(True);group=lathe_model(**self.parameters());execute(self.vp,AddGenerated(group));self.preview_lines=None;self.profile=None;return '已建立獨立 Lathe 群組，原剖面線保留。Ctrl＋Z 可復原。'
        self.run(create)
    def start_pick(self):
        from tools.base import Tool
        window=self;self.old_tool=self.vp.active_tool
        class PickAxis(Tool):
            name='Lathe 軸位置';uses_snap=True
            def on_activate(self,vp):vp.flash_status('點模型指定旋轉軸通過的位置；Esc 取消。',6000)
            def on_deactivate(self,vp):pass
            def claims_key(self,key,mods):return key==Qt.Key_Escape
            def on_key(self,vp,key,mods):
                if key==Qt.Key_Escape:window.stop_pick();return True
                return False
            def on_cancel(self,vp):window.stop_pick()
            def on_click(self,ctx):
                window.align.setCurrentIndex(3)
                for spin,value in zip(window.origin,xyz(ctx.world)):spin.blockSignals(True);spin.setValue(float(value)*1000);spin.blockSignals(False)
                window.refresh_preview();window.stop_pick()
        try:self.check(True);self.pick_tool=PickAxis();self.vp.set_active_tool(self.pick_tool);self.vp.setFocus()
        except Exception as exc:self.message(str(exc),True)
    def stop_pick(self):
        if self.pick_tool is not None and self.vp.active_tool is self.pick_tool:self.vp.set_active_tool(self.old_tool)
        self.pick_tool=None
    def closeEvent(self,event):self.stop_pick();super().closeEvent(event)
    def done(self,result):self.stop_pick();super().done(result)


class FFDWindow(GeometryWindow):
    def __init__(self,app):
        super().__init__(app,'FFD 自由變形',490,860)
        self.surface_pending=False;self.engine=None;self.active=False;self.target=None;self.tool=None;self.drag=None;self.selected=set();self.history=[]
        self.body.addWidget(label('先選一個群組／元件。移動控制點只改預覽，確認後才寫入。','muted'))
        self.body.addWidget(button('開始目前選取物件',self.start,'primary'));self.info=label('未開始','muted');self.body.addWidget(self.info)
        c=card(self.body);row=QHBoxLayout();self.grid_size=QComboBox();self.grid_size.addItem('2×2×2',2);self.grid_size.addItem('3×3×3',3);self.grid_size.setCurrentIndex(1);row.addWidget(self.grid_size)
        self.high=QCheckBox('高面數模式');self.high.setChecked(True);row.addWidget(self.high);c.addLayout(row)
        self.mode=QComboBox()
        for text,key in [('移動（mm）','move'),('旋轉（度）','rotate'),('縮放（倍率）','scale')]:self.mode.addItem(text,key)
        c.addWidget(self.mode);self.axis=QComboBox();self.axis.addItems(['X 方向','Y 方向','Z 方向','自由移動（滑鼠）']);c.addWidget(self.axis)
        self.box_select=QCheckBox('框選控制點（取消勾選後可拖曳）');c.addWidget(self.box_select)
        c.addWidget(label('點控制點選取，Shift 加選／取消；也可選整層控制點。','muted'))
        layer=QHBoxLayout();self.layer_axis=QComboBox();self.layer_axis.addItems(['X 層','Y 層','Z 層']);layer.addWidget(self.layer_axis)
        self.layer=LockedSpinBox();self.layer.setRange(0,2);layer.addWidget(self.layer);layer.addWidget(button('選取整層',self.select_layer));c.addLayout(layer)
        row=QHBoxLayout();row.addWidget(button('全選',lambda:self.choose_all(True)));row.addWidget(button('清除選取',lambda:self.choose_all(False)));c.addLayout(row)
        row=QHBoxLayout();row.addWidget(button('鎖定選取點',lambda:self.lock_points(True)));row.addWidget(button('解鎖選取點',lambda:self.lock_points(False)));c.addLayout(row)
        c.addWidget(label('數值（移動 mm／旋轉度／縮放倍率）'));self.value=length_number(10);c.addWidget(self.value);self.mode.currentIndexChanged.connect(lambda *_:self.value.set_length_mode(self.mode.currentData()=='move',self.mode.currentData()));self.mode.setItemText(0,'移動（cm）')
        c.addWidget(button('套用數值到預覽',self.apply_numeric));c.addWidget(button('復原上一步預覽',self.undo_preview));c.addWidget(button('重設未鎖定控制點',self.reset_points))
        c.addWidget(button('吸附選取控制點到表面（試驗中）',self.start_surface_snap));c.addWidget(label('沿目前 X／Y／Z 方向與表面相交；自由方向沿法線投影。僅改預覽，確認才寫入。','muted'))
        footer=QWidget();footer.setObjectName('page');buttons=QHBoxLayout(footer);buttons.setContentsMargins(22,8,22,4)
        buttons.addWidget(button('確認變形',self.confirm,'primary'));buttons.addWidget(button('取消預覽',self.cancel));self.outer.insertWidget(1,footer)
        self.body.addWidget(label('紅色＝鎖定、藍色＝已選取。彩色方向線顯示移動軸。\n高面數使用抽樣線框預覽，確認時處理全部頂點。','muted'));self.body.addStretch()
        self.grid_size.currentIndexChanged.connect(self.change_grid);self.box_select.toggled.connect(self.change_box);self.start()
    def start_surface_snap(self):
        def run():
            self.need()
            if not self.selected-self.engine.locked:raise ValueError('請先選取未鎖定控制點。')
            self.end_drag();self.box_select.setChecked(False);self.surface_pending=True;self.vp.setFocus();return '表面吸附 · 試驗中：點目標面；Esc 取消吸附，原模型不變。'
        self.run(run)
    def snap_surface(self,ctx):
        self.need();face,placement=self.vp.pick_face_placement(ctx.screen.x(),ctx.screen.y());owner=self.vp.pick_group(ctx.screen.x(),ctx.screen.y())
        if face is None or owner is self.target:raise ValueError('請點其他模型的目標表面，避開變形物件本身。')
        snapshot=self.snapshot();count=snap_ffd_controls(self.engine,self.selected,face,placement,self.axis.currentIndex());self.history.append(snapshot);self.surface_pending=False;self.rebuild();self.message(f'已吸附 {count} 個控制點到預覽；按確認變形才寫入。')
    def need(self):
        self.check(True)
        if self.engine is None or not self.active:raise ValueError('請先選取物件，按「開始目前選取物件」。')
    def start(self):
        def begin():
            if self.active:self.cancel()
            self.vp=self.app.viewport;self.scene=self.app.scene;self.context=self.scene.edit_group;self.version=self.scene.content_version
            items=selection(self.scene,True)
            if len(items)!=1:raise ValueError('FFD 請只選一個群組或元件。')
            target=items[0]
            if any(not editable(self.scene,g) for g in walk_groups(target)):raise ValueError('內部有鎖定群組，請先解除。')
            count=sum(len(g.mesh.vertices) for g in walk_groups(target));limit=500000 if self.high.isChecked() else 15000
            if count>limit:raise ValueError(f'超過 {limit:,} 頂點上限，請啟用高面數或拆分模型。')
            self.target=target;self.engine=FFDEngine(points_of([target]),self.grid_size.currentData());self.selected=set();self.history=[]
            self.source_lines=mesh_preview(target,2000);self.old_tool=self.vp.active_tool;self.tool=self.make_tool();self.tool.box_select=self.box_select.isChecked();self.active=True
            self.vp.set_active_tool(self.tool);self.vp.setFocus();self.rebuild();return 'FFD 已開始，選取控制點後拖曳或輸入數值。'
        self.run(begin)
    def rebuild(self):
        if self.engine is None:return
        self.preview_lines=self.engine.evaluate(self.source_lines.reshape(-1,3)).reshape(-1,2,3)
        self.info.setText(f'{len(self.engine.points):,} 頂點｜已選 {len(self.selected)} 點｜鎖定 {len(self.engine.locked)} 點')
        self.layer.setMaximum(self.engine.size-1);self.vp.update()
    def change_grid(self,*_):
        if self.engine is not None and self.active:self.engine.set_size(self.grid_size.currentData());self.selected=set();self.history=[];self.rebuild();self.message('已重設控制框。')
    def change_box(self,*_):
        if self.tool is not None:self.tool.box_select=self.box_select.isChecked()
    def choose_all(self,all_points):
        if self.engine is not None:self.selected=set(range(len(self.engine.controls))) if all_points else set();self.rebuild()
    def select_layer(self):
        if self.engine is not None:self.selected=set(np.flatnonzero(self.engine.coordinates[:,self.layer_axis.currentIndex()]==self.layer.value()).tolist());self.rebuild()
    def lock_points(self,lock):
        if self.engine is not None:
            if lock:self.engine.locked.update(self.selected)
            else:self.engine.locked.difference_update(self.selected)
            self.rebuild()
    def snapshot(self):return self.engine.controls.copy(),set(self.engine.locked)
    def restore_snapshot(self,snapshot):self.engine.controls=snapshot[0].copy();self.engine.locked=set(snapshot[1])
    def apply_numeric(self):
        def apply():
            self.need();snapshot=self.snapshot();axis=self.axis.currentIndex();mode=self.mode.currentData();value=self.value.value()/1000 if mode=='move' else self.value.value()
            try:self.engine.transform(self.selected,mode,axis,value);self.engine.validate()
            except Exception:self.restore_snapshot(snapshot);raise
            self.history.append(snapshot);self.rebuild();return '已套用到預覽，按確認變形才寫入模型。'
        self.run(apply)
    def undo_preview(self):
        if self.engine is not None and self.history:self.restore_snapshot(self.history.pop());self.rebuild()
    def reset_points(self):
        if self.engine is not None:
            self.history.append(self.snapshot());indices=[i for i in range(len(self.engine.controls)) if i not in self.engine.locked];self.engine.controls[indices]=self.engine.rest[indices];self.rebuild()
    def cursor_world(self,ctx,point):
        ray=getattr(self.vp,'_pixel_to_ray',None)
        camera=getattr(self.vp,'camera',None);eye=getattr(camera,'eye',None)
        if ray is not None and callable(eye):
            o,d=ray(ctx.screen.x(),ctx.screen.y());o,d=xyz(o),xyz(d);normal=xyz(eye())-point;denom=np.dot(d,normal)
            if abs(denom)>1e-12:return o+d*np.dot(point-o,normal)/denom
        return xyz(ctx.world)
    def pick_control(self,ctx):
        px,py,front=self.vp.world_to_pixels(self.engine.controls);distance=np.hypot(px-ctx.screen.x(),py-ctx.screen.y());distance=np.where(front,distance,np.inf)
        index=int(np.argmin(distance));return index if distance[index]<=12 else None
    def begin_drag(self,ctx):
        if self.surface_pending:self.snap_surface(ctx);return
        self.need();index=self.pick_control(ctx)
        if index is None:
            if not (ctx.modifiers&Qt.ShiftModifier):self.selected=set();self.rebuild()
            return
        if ctx.modifiers&Qt.ShiftModifier:
            if index in self.selected:self.selected.remove(index)
            else:self.selected.add(index)
            self.rebuild();return
        if index not in self.selected:self.selected={index}
        if not self.box_select.isChecked() and self.selected-self.engine.locked:
            point=self.engine.controls[index].copy();self.drag={'snapshot':self.snapshot(),'point':point,'world':self.cursor_world(ctx,point),'screen':ctx.screen.x()}
        self.rebuild()
    def drag_to(self,ctx):
        if self.drag is None:return
        self.need();self.restore_snapshot(self.drag['snapshot']);axis=self.axis.currentIndex();mode=self.mode.currentData()
        if mode=='move':
            delta=self.cursor_world(ctx,self.drag['point'])-self.drag['world']
            if axis==3:self.engine.controls[sorted(self.selected-self.engine.locked)]+=delta
            else:self.engine.transform(self.selected,'move',axis,float(delta[axis]))
        elif mode=='rotate':self.engine.transform(self.selected,'rotate',axis,(ctx.screen.x()-self.drag['screen'])*.5)
        else:self.engine.transform(self.selected,'scale',axis,float(np.clip(np.exp((ctx.screen.x()-self.drag['screen'])*.01),.01,100)))
        self.rebuild()
    def end_drag(self):
        if self.drag is not None:
            try:
                self.engine.validate()
                if not np.allclose(self.engine.controls,self.drag['snapshot'][0]):self.history.append(self.drag['snapshot'])
            except Exception as exc:self.restore_snapshot(self.drag['snapshot']);self.message(str(exc),True)
            self.drag=None;self.rebuild()
    def stop_tool(self):
        self.surface_pending=False;self.active=False;self.drag=None;self.preview_lines=None
        if self.tool is not None and self.vp.active_tool is self.tool:
            activate=getattr(self.app.window,'_activate_tool',None)
            if callable(activate):activate('select')
            else:self.vp.set_active_tool(self.old_tool)
        self.vp.update()
    def confirm(self):
        def apply():
            self.end_drag();self.need();replacement=deformed_tree(self.target,self.engine);execute(self.vp,ReplaceGeometry(self.target,replacement));self.stop_tool();self.engine=None
            return '已確認變形，面板保留。Ctrl＋Z 可復原；可選下一件再按開始。'
        self.run(apply)
    def cancel(self):self.surface_pending=False;self.stop_tool();self.engine=None;self.message('已取消預覽，原模型未變更，面板保留。')
    def make_tool(self):
        from tools.base import Tool
        window=self
        class FFDTool(Tool):
            name='FFD 控制框';uses_snap=False;box_select=False
            def on_activate(self,vp):vp.flash_status('FFD：點選／Shift 加選控制點，拖曳或輸入數值。Esc 取消。',7000)
            def on_deactivate(self,vp):window.active=False;window.drag=None;window.preview_lines=None;vp.update()
            def claims_key(self,key,mods):return key in (Qt.Key_Escape,Qt.Key_X,Qt.Key_Y,Qt.Key_Z)
            def on_key(self,vp,key,mods):
                if key==Qt.Key_Escape:
                    if window.surface_pending:window.surface_pending=False;window.message('已取消表面吸附，保留變形預覽。')
                    else:window.cancel()
                    return True
                if key in (Qt.Key_X,Qt.Key_Y,Qt.Key_Z):window.axis.setCurrentIndex({Qt.Key_X:0,Qt.Key_Y:1,Qt.Key_Z:2}[key]);vp.update();return True
                return False
            def on_cancel(self,vp):
                if window.surface_pending:window.surface_pending=False;window.message('已取消表面吸附。')
                else:window.cancel()
            def on_click(self,ctx):
                try:window.begin_drag(ctx)
                except Exception as exc:window.message(str(exc),True)
            def on_hover(self,ctx):
                try:window.drag_to(ctx)
                except Exception as exc:
                    if window.drag is not None:window.restore_snapshot(window.drag['snapshot']);window.drag=None;window.rebuild()
                    window.message(str(exc),True)
            def on_release(self,vp):window.end_drag()
            def on_box_select(self,vp,rect,crossing,additive=False,mode=None):
                if window.engine is None:return
                px,py,front=vp.world_to_pixels(window.engine.controls);x0,y0,x1,y1=rect;ids=set(np.flatnonzero(front&(px>=x0)&(px<=x1)&(py>=y0)&(py<=y1)).tolist())
                if mode=='toggle':window.selected.symmetric_difference_update(ids)
                elif mode=='remove':window.selected.difference_update(ids)
                elif additive or mode=='add':window.selected.update(ids)
                else:window.selected=ids
                window.rebuild()
            def drag_plane(self,vp):
                if window.engine is None:return None
                center=window.drag['point'] if window.drag else window.engine.controls.mean(axis=0);point=QVector3D(*center)
                camera=getattr(vp,'camera',None);eye=getattr(camera,'eye',None);normal=eye()-point if callable(eye) else QVector3D(0,-1,0)
                return point,normal
        return FFDTool()
    def closeEvent(self,event):self.cancel();super().closeEvent(event)
    def done(self,result):self.cancel();super().done(result)


def draw_overlay(app,viewport,painter):
    from PySide6.QtCore import QLineF
    _draw_base_overlay(app,viewport,painter)
    for name in ('floor','lathe','ffd','glass_new','glass_edit','louver_new','louver_edit','decorative_new','decorative_edit','cabinet','cabinet_edit','cabinet_composite'):
        window=getattr(app.window,'_lime_suite_'+name,None)
        if window is None or not window.isVisible() or window.vp is not viewport or window.scene is not viewport.scene:continue
        if window.preview_lines is not None and window.scene.content_version==window.version and (name!='ffd' or window.active):
            lines=window.preview_lines;px,py,front=viewport.world_to_pixels(lines.reshape(-1,3));px,py,front=px.reshape(-1,2),py.reshape(-1,2),front.reshape(-1,2)
            painter.setPen(QPen(QColor(0,158,184),1.5));painter.drawLines([QLineF(float(x[0]),float(y[0]),float(x[1]),float(y[1])) for x,y,ok in zip(px,py,front) if all(ok)])
        if getattr(window,'conversion_front_lines',None) is not None and window.preview_lines is not None and window.scene.content_version==window.version:draw_volume_front(window,viewport,painter)
        if name=='ffd' and window.active and window.engine is not None:
            engine=window.engine;px,py,front=viewport.world_to_pixels(engine.controls);painter.setPen(QPen(QColor(110,128,143),1))
            for i,coord in enumerate(engine.coordinates):
                for axis,step in enumerate((1,engine.size,engine.size**2)):
                    j=i+step
                    if coord[axis]<engine.size-1 and front[i] and front[j]:painter.drawLine(QPointF(float(px[i]),float(py[i])),QPointF(float(px[j]),float(py[j])))
                if front[i]:
                    color=QColor('#dc4545' if i in engine.locked else '#147ed0' if i in window.selected else '#f7b842');painter.setPen(QPen(QColor('white'),1));painter.setBrush(color);painter.drawEllipse(QPointF(float(px[i]),float(py[i])),5.,5.);painter.setPen(QPen(QColor(110,128,143),1))
            if window.selected:
                center=engine.controls[sorted(window.selected)].mean(axis=0);size=float(np.linalg.norm(engine.span))*.18;gizmo=np.array([center,center+[size,0,0],center+[0,size,0],center+[0,0,size]])
                gx,gy,ok=viewport.world_to_pixels(gizmo)
                for i,color in enumerate(('#d44a45','#3d9a61','#287fc9'),1):
                    if ok[0] and ok[i]:painter.setPen(QPen(QColor(color),2));painter.drawLine(QPointF(float(gx[0]),float(gy[0])),QPointF(float(gx[i]),float(gy[i])));painter.drawText(QPointF(float(gx[i])+4,float(gy[i])-3),'XYZ'[i-1])


# ---- Third batch: original glass-door and louver stock ----
from functools import lru_cache
import math
import json

PARAM_KEY='lime_suite_parametric'


@lru_cache(maxsize=8)
def stock_data(kind):
    if kind not in ('glass','louver','decorative','cabinet','composite','classical','pull','kitchen'):raise ValueError('未知的參數模型。')
    return json.loads((ROOT/'lime_integrated_assets'/(kind+'_stock.json')).read_text(encoding='utf-8'))


@lru_cache(maxsize=100)
def stock_mesh(kind,name):
    data=stock_data(kind)['parts'][name];mesh=Mesh()
    for item in data['faces']:
        rings=[[QVector3D(*v) for v in ring] for ring in item['rings']]
        face=mesh.add_face(rings[0],rings[1:] or None);face.attrs.update(deepcopy(item['attrs']))
    if len(mesh.faces)!=len(data['faces']):raise ValueError('原型面數不符，已停止。')
    # A hidden edge must never become a smoothing connection.
    flags=data.get('edge_flags')
    if flags is None:flags=[{'points':segment,'soft':True,'hidden':False} for segment in data['soft']]
    for entry in flags:
        a,b=entry['points'];va,vb=mesh.vertex_at(QVector3D(*a)),mesh.vertex_at(QVector3D(*b))
        if va is not None and vb is not None:
            edge=mesh.find_edge(va,vb)
            if edge is not None:edge.soft=entry['soft'];edge.hidden=entry['hidden']
    return mesh


def stock_matrix(values,origin=None):
    a=list(values)
    for i in range(3):a[12+i]=(a[12+i]-(origin[i] if origin else 0.))/1000.
    return QMatrix4x4(*[a[c*4+r] for r in range(4) for c in range(4)])


def stock_leaf(kind,leaf,lo,hi,source_origin=None):
    old_lo,old_hi=np.asarray(leaf['bounds_mm'],float);lo=np.asarray(lo,float);hi=np.asarray(hi,float)
    size=old_hi-old_lo
    scale=np.divide(hi-lo,size,out=np.ones(3),where=np.abs(size)>1e-7)
    if np.any(scale<=0) or not np.all(np.isfinite(scale)):raise ValueError('尺寸不足以保留原型零件。')
    shift=(lo-scale*old_lo)/1000.
    mapping=QMatrix4x4();mapping.translate(*map(float,shift));mapping.scale(*map(float,scale))
    result=Group(stock_mesh(kind,leaf['definition']),name=leaf['definition'])
    result.xform=mapping*stock_matrix(leaf['transform_mm'],source_origin)
    result.component=True
    return result


def glass_layout(values=None):
    p={'door_mode':'single','glass_columns':2,'center_gap':3.,'width':701.25,'height':2200.,'depth':50.,'side_width':70.,'belly_height':450.,'handle_length':300.,'handle_height':950.,'handle_type':'recessed','pull_length':220.,'pull_edge':'left','pull_offset':0.,'pull_side':'both','pull_stand':0.}
    p.update({k:v for k,v in (values or {}).items() if k in p})
    for key,options in [('door_mode',('single','double')),('handle_type',('recessed','pull')),('pull_edge',('left','right')),('pull_side',('front','back','both'))]:
        if p[key] not in options:raise ValueError('玻璃門把手類型、位置或正反面設定無效。')
    for key in set(p)-{'door_mode','handle_type','pull_edge','pull_side'}:
        p[key]=float(p[key])
        if not math.isfinite(p[key]):raise ValueError('尺寸必須為有限數值。')
    if p['glass_columns'] not in (1,2):raise ValueError('每扇玻璃欄數只能為 1 或 2。')
    p['glass_columns']=int(p['glass_columns'])
    if not 0<=p['center_gap']<=50:raise ValueError('中間門縫須介於 0～5 cm。')
    for key,low,high,title in [('width',250,6000 if p['door_mode']=='double' else 3000,'寬度'),('height',1250,5000,'高度'),('side_width',50,400,'左右框料寬度'),('belly_height',60,4000,'肚板高度'),('handle_length',30,4000,'把手長度')]:
        if not low<=p[key]<=high:raise ValueError(f'{title}須介於 {low}～{high} mm。')
    if not 10<=p['depth']<=1000:raise ValueError('玻璃門本體厚度須介於 1～100 cm。')
    leaf_width=(p['width']-p['center_gap'])/2 if p['door_mode']=='double' else p['width']
    if leaf_width<max(250,2*p['side_width']+100):raise ValueError('每扇門片至少寬 25 cm，且須放得下左右框料與玻璃；請增加總寬、減少框料或改單扇。')
    if not 60<=p['pull_length']<=4000 or not 0<=p['pull_stand']<=1000:raise ValueError('外凸把手長度須介於 6～400 cm，離門面介於 0～100 cm。')
    if p['handle_type']=='pull' and abs(p['pull_offset'])+4.143>p['side_width']/2:raise ValueError('外凸把手超出框料，請減少距框料中心偏移或增加框料寬度。')
    length=p['pull_length'] if p['handle_type']=='pull' else p['handle_length']
    bottom=p['handle_height']-length/2;top=p['handle_height']+length/2
    if bottom<p['belly_height']+190:raise ValueError('把手下端需高於肚板上方橫料至少 10 mm。')
    if top>p['height']-100:raise ValueError('把手上端需低於頂框底部至少 10 mm。')
    if (p['height']-p['belly_height']-362)/4<32:raise ValueError('肚板過高，玻璃格空間不足。')
    return p


def glass_point(v,p,grid,single_cell=False):
    x,y,z=map(float,v);side=p['side_width'];width=p['width']-2*(side-70);delta=width-701.25
    if not grid:
        if x<=70.00001:xx=x*side/70
        elif x>=631.24999:xx=p['width']-(701.25-x)*side/70
        else:xx=(x if x<=90.00001 else x+delta if x>=611.24999 else 90+(x-90)*(width-180)/521.25)+(side-70)
    elif single_cell:
        if x<=88.00001:xx=x+side-70
        elif x<330.625:xx=side+18+(x-88)*(p['width']-2*side-36)/242.625
        else:xx=p['width']-side-10+(x-338.625)
    else:
        if x<=88.00001:xx=x
        elif x<330.625:xx=88+(x-88)*(width/2-108)/242.625
        elif x<=370.62501:xx=x+delta/2
        elif x<613.25:xx=width/2+20+(x-370.625)*(width/2-108)/242.625
        else:xx=x+delta
        xx+=side-70
    if grid:
        cell=(p['height']-p['belly_height']-362)/4
        if z<=640.00001:zz=z+p['belly_height']-450
        elif z>=2099.99999:zz=z+p['height']-2200
        else:
            zz=None
            for row in range(4):
                start=640+371*row;target=p['belly_height']+190+(cell+24)*row
                if z<=start+347+.00001:
                    zz=target+z-start if z<=start+8+.00001 else target+cell+z-start-347 if z>=start+339-.00001 else target+8+(z-start-8)*(cell-16)/331
                    break
                if row<3 and z<=start+371+.00001:zz=target+cell+z-start-347;break
            if zz is None:raise ValueError('玻璃原型座標超出範圍。')
    else:
        bottom=p['handle_height']-p['handle_length']/2;top=p['handle_height']+p['handle_length']/2
        source=[0,90,110,520,540,630,800,806,1094,1100,2110,2200]
        target=[0,90,110,p['belly_height']+70,p['belly_height']+90,p['belly_height']+180,bottom,bottom+6,top-6,top,p['height']-90,p['height']]
        zz=float(np.interp(z,source,target))
    return np.array([xx,y,zz])


def glass_pull_handle(p,back=False):
    root=Group(name='外凸長把手｜'+('反面' if back else '正面'));root.ext={'lime_glass_pull':True}
    x=p['side_width']/2+p['pull_offset']
    if p['pull_edge']=='right':x=p['width']-x
    placement=QMatrix4x4();placement.translate(x/1000,(p['depth']+p['pull_stand'] if back else -p['pull_stand'])/1000,p['handle_height']/1000)
    depth=QMatrix4x4()
    if back:depth.scale(1,-1,1)
    parts=[]
    for role in ('bar','lower','upper'):
        resize=QMatrix4x4()
        if role=='bar':resize.scale(1,1,p['pull_length']/140.)
        else:resize.translate(0,0,(-1 if role=='lower' else 1)*(p['pull_length']-140.)/2000)
        mesh=transformed_mesh(stock_mesh('pull',role),placement*depth*resize)
        if back:
            for face in mesh.faces:
                face.loop.reverse()
                for hole in face.hole_loops:hole.reverse()
        child=Group(mesh,name={'bar':'握桿','lower':'下固定座','upper':'上固定座'}[role]);child.ext={'lime_stock_id':'pull_'+role};parts.append(child)
    root.adopt(parts);return root


def _glass_single_model(values=None):
    p=glass_layout(values);data=stock_data('glass');root=Group(name=f'玻璃格子門片 {p["width"]/10:g}×{p["height"]/10:g} cm');parents={():root}
    for leaf in data['template']['leaves']:
        if p['glass_columns']==1 and leaf['grid'] and (leaf['definition']=='glassdoor_P19' or (len(leaf['path'])>2 and leaf['path'][1] in ('8321','8359','8364','8369'))):continue
        if p['handle_type']=='pull' and (leaf['definition'].startswith('IN_Handle_') or leaf['definition'] in ('IHB_glassdoorP10','IHB_glassdoorP11','IHB_glassdoorP12','IHB_glassdoorP13')):continue
        path=tuple(leaf['path'][:-1])
        for depth in range(1,len(path)+1):
            key=path[:depth]
            if key not in parents:
                sub=Group(name='崁把手' if key[-1]=='13773' else f'玻璃格 {p["glass_columns"]}×4' if key[-1]=='13782' else '原型玻璃單格')
                sub.ext={"lime_stock_path":"/".join(key)}
                if key[-1]=='13773':sub.material={'color':[.24,.26,.29]};sub.ext['recess_mm']=19.
                parents[key]=sub;parent=parents[key[:-1]];parent.adopt([*(parent.children or []),sub])
        lo,hi=[glass_point(v,p,leaf['grid'],p['glass_columns']==1 and len(leaf['path'])>2 and leaf['path'][1] in ('8339','8344','8349','8354')) for v in leaf['bounds_mm']];name=leaf['definition'];old_lo,old_hi=leaf['bounds_mm']
        if name.startswith('IN_Handle_'):
            lo[0]=old_lo[0]+(p['side_width']-70)/2;hi[0]=old_hi[0]+(p['side_width']-70)/2
        elif name=='IHB_glassdoorP11':lo[0]=0.;hi[0]=(p['side_width']-37)/2
        elif name=='IHB_glassdoorP12':lo[0]=(p['side_width']+37)/2;hi[0]=p['side_width']
        part=stock_leaf('glass',leaf,lo,hi);part.ext={'lime_stock_id':str(leaf['id'])}
        parent=parents[path];parent.adopt([*(parent.children or []),part])
    if p['handle_type']=='pull':
        stile=recessed_solid([0,p['belly_height']+180,p['side_width'],p['height']-90],[],[],0,0,50,'左直料｜無內凹');stile.ext={**(stile.ext or {}),'lime_stock_id':'glass_plain_left_stile'};root.adopt([*root.children,stile])
    root.ext={PARAM_KEY:{'kind':'glass','params':p,'version':1},'lime_suite_generated':'glass'}
    detach_stock(root)
    if abs(p['depth']-50.)>1e-8:
        depth_map=QMatrix4x4();depth_map.scale(1,p['depth']/50.,1)
        def bake_depth(node,parent):
            matrix=parent*(node.xform if node.xform is not None else QMatrix4x4());node.mesh=transformed_mesh(node.mesh,matrix);node.xform=QMatrix4x4()
            for child in node.children or ():bake_depth(child,matrix)
        bake_depth(root,depth_map)
    hide_stock_joins(root)
    root.ext[PARAM_KEY]['geometry_revision']=2;root.ext['glass_body_depth_mm']=p['depth']
    if p['handle_type']=='pull':
        root.adopt([*root.children,*[glass_pull_handle(p,back=side=='back') for side in ('front','back') if p['pull_side'] in (side,'both')]])
    check_parametric_bounds(root,p['width'],p['height'],p['depth']*1.04 if p['handle_type']=='recessed' else None)
    return bind_face_materials(root)


def glass_model(values=None):
    p=glass_layout(values)
    if p['door_mode']=='single':return _glass_single_model(p)
    width=(p['width']-p['center_gap'])/2;local=deepcopy(p);local.update(door_mode='single',width=width,pull_edge='left')
    right=_glass_single_model(local);left=clone_tree(right)
    reflection=QMatrix4x4();reflection.translate(width/1000,0,0);reflection.scale(-1,1,1)
    def bake(node,parent):
        matrix=parent*(node.xform if node.xform is not None else QMatrix4x4());node.mesh=transformed_mesh(node.mesh,matrix)
        if matrix.determinant()<0:
            for face in node.mesh.faces:
                face.loop.reverse()
                for hole in face.hole_loops:hole.reverse()
        node.xform=QMatrix4x4()
        for child in node.children or ():bake(child,matrix)
    bake(left,reflection);bake(right,QMatrix4x4());left.name='左門片';right.name='右門片';right.xform=QMatrix4x4();right.xform.translate((width+p['center_gap'])/1000,0,0)
    for role,leaf in [('left',left),('right',right)]:
        leaf.ext.pop(PARAM_KEY,None);leaf.ext['lime_suite_generated']='glass_leaf';leaf.ext['lime_glass_leaf']=role
    root=Group(name=f'對開玻璃門 {p["width"]/10:g}×{p["height"]/10:g} cm');root.adopt([left,right])
    root.ext={PARAM_KEY:{'kind':'glass','params':p,'version':1,'geometry_revision':2},'lime_suite_generated':'glass','glass_body_depth_mm':p['depth'],'glass_leaf_width_mm':width}
    check_parametric_bounds(root,p['width'],p['height'],p['depth']*1.04 if p['handle_type']=='recessed' else None)
    return bind_face_materials(root)


def warp_stock(v,old_min,old_length,new_min,new_length,cap):
    x=v-old_min
    if x<=cap:return new_min+x
    if x>=old_length-cap:return new_min+new_length-(old_length-x)
    return new_min+cap+(x-cap)*(new_length-2*cap)/(old_length-2*cap)


def louver_layout(values=None):
    t=stock_data('louver')['template'];p={'width':t['width'],'height':t['height'],'panels':3,'angle':t['blade_reference_angle'],'direction':'same','target_gap':8.,'panel_gap':2.}
    p.update({k:v for k,v in (values or {}).items() if k in p})
    for key in ('width','height','angle','target_gap','panel_gap','panels'):
        p[key]=float(p[key])
        if not math.isfinite(p[key]):raise ValueError('參數必須為有限數值。')
    if p['panels']!=int(p['panels']) or not 1<=p['panels']<=20:raise ValueError('窗扇數須為 1～20 的整數。')
    p['panels']=int(p['panels'])
    if not -90<=p['angle']<=90:raise ValueError('葉片角度須介於 -90～90 度。')
    if not all(0<p[k]<=20000 for k in ('width','height')):raise ValueError('寬高須大於零且不超過 20000 mm。')
    if p['target_gap']<=0 or not 0<=p['panel_gap']<=20:raise ValueError('淨距須大於零；扇間隙須介於 0～20 mm。')
    if p['direction'] not in ('same','opposite'):raise ValueError('旋轉方式無效。')
    bay=(p['width']-t['inset_left']-t['inset_right']-(p['panels']-1)*p['panel_gap'])/p['panels']
    sash_height=p['height']-t['inset_top']-t['inset_bottom']
    if bay<=2*t['sash_x_cap']+10 or sash_height<=2*t['sash_z_cap']+10:raise ValueError('尺寸不足以保留窗扇框料接頭，請增加寬高或減少扇數。')
    length=bay-2*t['stile'];usable=sash_height-2*t['rail'];rows=[]
    for i in range(p['panels']):
        angle=p['angle']*(-1 if p['direction']=='opposite' and i%2 else 1);a=math.radians(angle)
        zs=[y*math.sin(a)+z*math.cos(a) for y,z in t['neutral_profile']]
        rows.append({'angle':angle,'zmin':min(zs),'zmax':max(zs),'projection':max(zs)-min(zs)})
    projection=max(r['projection'] for r in rows)
    if usable<projection:raise ValueError('可用高度不足一片原型葉片。')
    blades=max(1,int(usable/(projection+p['target_gap'])))
    if blades*p['panels']>3000:raise ValueError('葉片總數超過 3000，請增加目標淨距。')
    for r in rows:
        r['gap']=(usable-blades*r['projection'])/blades;r['margin']=r['gap']/2;r['pitch']=r['projection']+r['gap']
    return {'params':p,'bay':bay,'sash_height':sash_height,'blade_length':length,'usable_height':usable,'blades':blades,'rows':rows}


def louver_model(values=None):
    l=louver_layout(values);p=l['params'];t=stock_data('louver')['template'];root=Group(name=f'百葉窗 {p["panels"]}扇 {p["width"]/10:g}×{p["height"]/10:g} cm');children=[]
    def frame(part,mapping):
        sub=Group(name=part['name']);sub.adopt(stock_leaf('louver',leaf,*[mapping(v) for v in leaf['bounds_mm']],t['origin_mm']) for leaf in part['leaves']);return sub
    for part in t['outer_parts']:
        children.append(frame(part,lambda v:[warp_stock(v[0],0,t['width'],0,p['width'],t['outer_cap']),v[1],warp_stock(v[2],0,t['height'],0,p['height'],t['outer_cap'])]))
    leaf=t['blade'];offset=QMatrix4x4();offset.scale(l['blade_length']/t['blade_length'],1,1);offset.translate(-t['blade_min_x']/1000,-t['blade_center_y']/1000,-t['blade_center_z']/1000)
    blade_source=offset*stock_matrix(leaf['transform_mm'],t['origin_mm'])
    for index in range(p['panels']):
        sash=Group(name=f'窗扇 {index+1}');x=t['inset_left']+index*(l['bay']+p['panel_gap']);z=t['inset_bottom'];parts=[]
        for part in t['sash_parts']:
            parts.append(frame(part,lambda v:[warp_stock(v[0],t['sash_min'][0],t['sash_width'],x,l['bay'],t['sash_x_cap']),v[1],warp_stock(v[2],t['sash_min'][2],t['sash_height'],z,l['sash_height'],t['sash_z_cap'])]))
        row=l['rows'][index];rotate=QMatrix4x4();rotate.rotate(row['angle']-t['blade_reference_angle'],1,0,0)
        for i in range(l['blades']):
            center=z+t['rail']+row['margin']-row['zmin']+i*row['pitch'];move=QMatrix4x4();move.translate((x+t['stile'])/1000,t['blade_center_y']/1000,center/1000)
            blade=Group(stock_mesh('louver',leaf['definition']),name=f'原型葉片 {i+1}');blade.xform=move*rotate*blade_source;blade.component=True;parts.append(blade)
        sash.adopt(parts);children.append(sash)
    root.adopt(children);root.ext={PARAM_KEY:{'kind':'louver','params':p,'version':1},'lime_suite_generated':'louver','blades_per_panel':l['blades'],'actual_gap_mm':l['rows'][0]['gap']}
    detach_stock(root)
    check_parametric_bounds(root,p['width'],p['height'])
    return bind_face_materials(root)


def detach_stock(root):
    # Each new model gets independent definitions; blades within it still share.
    meshes={}
    for node in walk_groups(root):
        if not node.mesh.faces:continue
        key=id(node.mesh)
        if key not in meshes:meshes[key]=transformed_mesh(node.mesh,QMatrix4x4())
        node.mesh=meshes[key]


def check_parametric_bounds(group,width,height,depth=None):
    # Bounds from every original vertex, without welding thousands of blades.
    points=[]
    def visit(g,parent):
        matrix=parent*(g.xform if g.xform is not None else QMatrix4x4())
        points.extend(xyz(matrix.map(v.position)) for v in g.mesh.vertices)
        for child in g.children or ():visit(child,matrix)
    visit(group,QMatrix4x4());a=np.asarray(points);span=(a.max(axis=0)-a.min(axis=0))*1000
    if abs(span[0]-width)>.15 or abs(span[2]-height)>.15 or (depth is not None and abs(span[1]-depth)>.15):raise ValueError(f'原型尺寸驗證未通過：{(span/10).round(3)} cm，未寫入模型。')
    return a.min(axis=0),a.max(axis=0)


def parametric_info(group,kind=None):
    info=(group.ext or {}).get(PARAM_KEY)
    if not isinstance(info,dict) or info.get('kind') not in ('glass','louver','decorative','cabinet','composite') or (kind and info['kind']!=kind):raise ValueError('請選取本套件建立的'+('玻璃門。' if kind=='glass' else '百葉窗。' if kind=='louver' else '造型門組。' if kind=='decorative' else '參數櫃體。' if kind=='cabinet' else '組合櫃。' if kind=='composite' else '參數模型。'))
    return deepcopy(info)


def parametric_transform(group):
    matrix=QMatrix4x4(group.xform) if group.xform is not None else QMatrix4x4()
    if (group.ext or {}).get('lime_decorative_basis_baked') and group.axes is not None:matrix=matrix*group.axes
    a=np.array(matrix.data()).reshape(4,4).T;rot=a[:3,:3];scale=np.linalg.norm(rot,axis=0)
    if np.any(scale<1e-6):raise ValueError('模型不可縮放至零。')
    unit=rot/scale
    if not np.allclose(unit.T@unit,np.eye(3),atol=1e-5):raise ValueError('模型含斜切變形，無法自動還原固定框料。')
    a[:3,:3]=unit;rigid=QMatrix4x4(*map(float,a.flatten()))
    return scale,rigid


def effective_parameters(group,kind=None):
    info=parametric_info(group,kind);scale,rigid=parametric_transform(group);p=info['params']
    if info['kind']=='decorative':
        layout=decorative_layout(p)
        p['width']+=layout['outer_width']*(float(scale[0])-1.)
        p['height']+=layout['outer_height']*(float(scale[2])-1.)
    else:p['width']*=float(scale[0]);p['height']*=float(scale[2])
    if info['kind'] in ('cabinet','composite'):p['depth']*=float(scale[1])
    if info['kind']=='glass':p['depth']=float(p.get('depth',50.))*float(scale[1])
    return info['kind'],p,rigid


def carry_parametric_paint(source,replacement):
    """Keep stock face ordering and stable child names when regenerating."""
    def visit(old,new):
        new.material=deepcopy(old.material) if old.material is not None else new.material;new.layer=old.layer
        old_pair=bool((old.ext or {}).get('lime_cabinet_handle_pair'));new_pair=bool((new.ext or {}).get('lime_cabinet_handle_pair'))
        if new_pair and not old_pair:
            for child in new.children or ():visit(old,child)
            return
        if old_pair and not new_pair and old.children:
            visit(old.children[0],new);return
        old_keys=(old.ext or {}).get('lime_surface_keys');new_keys=(new.ext or {}).get('lime_surface_keys')
        if (old.ext or {}).get('lime_classical_source') and (old.ext or {}).get('lime_classical_source')==(new.ext or {}).get('lime_classical_source') and len(old.mesh.faces)==len(new.mesh.faces):
            new.mesh=transformed_mesh(new.mesh,QMatrix4x4())
            for a,b in zip(old.mesh.faces,new.mesh.faces):b.attrs=deepcopy(a.attrs)
        elif old_keys and new_keys and len(old_keys)==len(old.mesh.faces) and len(new_keys)==len(new.mesh.faces):
            by_key={json.dumps(key):face for key,face in zip(old_keys,old.mesh.faces)}
            new.mesh=transformed_mesh(new.mesh,QMatrix4x4())
            for key,face in zip(new_keys,new.mesh.faces):
                prior=by_key.get(json.dumps(key))
                if prior is not None:face.attrs=deepcopy(prior.attrs)
        elif len(old.mesh.faces)==len(new.mesh.faces) and old.mesh.faces:
            if any(a.attrs!=b.attrs for a,b in zip(old.mesh.faces,new.mesh.faces)):
                new.mesh=transformed_mesh(new.mesh,QMatrix4x4())
                for a,b in zip(old.mesh.faces,new.mesh.faces):b.attrs=deepcopy(a.attrs)
        def key(child):return (child.ext or {}).get('lime_stock_path') or (child.ext or {}).get('lime_stock_id') or (child.ext or {}).get('lime_decorative_role') or child.name
        if old.mesh.faces and len(old.mesh.faces)!=len(new.mesh.faces) and all(f.attrs==old.mesh.faces[0].attrs for f in old.mesh.faces):
            new.mesh=transformed_mesh(new.mesh,QMatrix4x4())
            for face in new.mesh.faces:face.attrs=deepcopy(old.mesh.faces[0].attrs)
        old_children={key(child):child for child in old.children or ()}
        # Newly added blades inherit the first blade's paint if no prior row exists.
        blade=next((child for child in old.children or () if child.name.startswith('原型葉片 ')),None)
        for child in new.children or ():
            prior=old_children.get(key(child))
            if prior is None and child.name.startswith('原型葉片 '):prior=blade
            if prior is not None:visit(prior,child)
    visit(source,replacement)
    return replacement


class UpdateParametric(Command):
    def __init__(self,target,replacement,rigid):self.target=target;self.replacement=bind_face_materials(carry_parametric_paint(target,replacement));self.rigid=QMatrix4x4(rigid);self.before=None
    def do(self,scene):
        if not editable(scene,self.target):raise ValueError('物件已隱藏或鎖定，無法修改。')
        if self.before is None:self.before=(self.target.mesh,self.target.children,deepcopy(self.target.ext),self.target.name,QMatrix4x4(self.target.xform) if self.target.xform is not None else None,QMatrix4x4(self.target.axes) if self.target.axes is not None else None)
        was_baked=bool((self.target.ext or {}).get('lime_decorative_basis_baked'))
        self.target.mesh=self.replacement.mesh;self.target.adopt(self.replacement.children or []);self.target.ext=deepcopy(self.target.ext or {});self.target.ext.update(deepcopy(self.replacement.ext));self.target.name=self.replacement.name;self.target.xform=QMatrix4x4(self.rigid)
        if was_baked:self.target.ext.pop('lime_decorative_basis_baked',None);self.target.axes=None
        _dirty_group_chunks(scene);scene.version+=1
    def undo(self,scene):
        self.target.mesh,self.target.children,self.target.ext,self.target.name,self.target.xform,self.target.axes=self.before;_dirty_group_chunks(scene);scene.version+=1


def build_parametric(kind,params):
    return {'glass':glass_model,'louver':louver_model,'decorative':decorative_model,'cabinet':cabinet_model,'composite':composite_model}[kind](params)


class ParametricWindow(GeometryWindow):
    def __init__(self,app,kind,editing=False):
        self._front_pick_tool=None;self._conversion_source=None;self._conversion_data=None;self._conversion_signature=None;self.conversion_front_lines=None;self.conversion_label='門片正面'
        self.kind=kind;self.editing=editing;self.target=None;self.rigid=QMatrix4x4();self.preview_group=None
        super().__init__(app,('玻璃門' if kind=='glass' else '百葉窗')+('設定' if editing else '｜新增'),500,790)
        self.body.addWidget(label('尺寸單位 mm。保留原型框料與剖面，調整後先預覽，再套用。','muted'))
        c=card(self.body);self.info=label('');c.addWidget(self.info);c.addWidget(button('讀取目前選取模型',self.read_target))
        if kind=='glass':
            c=card(self.body);c.addWidget(label('塊體轉玻璃門片'));c.addWidget(button('讀取選取塊體 → 轉玻璃門片',self.read_volume,'primary'));c.addWidget(button('直接點選門片正面',self.start_front_pick,'primary'))
            self.volume_front=QComboBox()
            for key in ('-Y','+Y','-X','+X','-Z','+Z'):self.volume_front.addItem('正面：塊體軸 '+key,key)
            self.volume_front.setEnabled(False);c.addWidget(self.volume_front);self.volume_front.currentIndexChanged.connect(self.volume_front_changed)
            c.addWidget(label('讀取封閉長方體的實際寬、高與本體厚度。橘色填面、「門片正面」標籤及箭頭標示正面；預覽後按轉換才替換。','muted'))
            self.cancel_volume=button('取消塊體轉換',self.cancel_conversion);self.cancel_volume.setEnabled(False);c.addWidget(self.cancel_volume)
        values=glass_layout() if kind=='glass' else louver_layout()['params'];self.fields={};form=QFormLayout();c=card(self.body);c.addLayout(form)
        labels={'door_mode':'門片形式','glass_columns':'每扇玻璃欄數','center_gap':'中間門縫','width':'總寬（單扇為門寬）','height':'高度','depth':'門片本體厚度','side_width':'左右框料寬度','belly_height':'肚板高度','handle_length':'把手長度','handle_height':'把手中心離門底','panels':'窗扇數量','angle':'葉片角度（度）','target_gap':'目標葉片淨距','panel_gap':'窗扇間隙','direction':'旋轉方式','handle_type':'把手類型','pull_length':'外凸把手長度','pull_edge':'外凸把手側邊','pull_offset':'距框料中心偏移','pull_side':'外凸把手安裝面','pull_stand':'外凸把手離門面'}
        if kind!='glass':labels['width']='寬度'
        for key,value in values.items():
            if key in ('door_mode','glass_columns','handle_type','pull_edge','pull_side'):
                field=QComboBox()
                for title,entry in {'door_mode':[('單扇門','single'),('對開雙扇門','double')],'glass_columns':[('1 欄 × 4 層',1),('2 欄 × 4 層',2)],'handle_type':[('內凹崁把手','recessed'),('外凸長把手（霧金）','pull')],'pull_edge':[('左側框料','left'),('右側框料','right')],'pull_side':[('正反兩面','both'),('只正面','front'),('只反面','back')]}[key]:field.addItem(title,entry)
            elif key=='direction':field=QComboBox();field.addItem('同向','same');field.addItem('對向','opposite')
            elif key=='panels':field=LockedSpinBox();field.setRange(1,20);field.setValue(value)
            else:
                field=number(value,minimum=-90) if key=='angle' else length_number(value,-20000 if key=='pull_offset' else 0);field.setMaximum(90 if key=='angle' else 20000);field.setDecimals(2);field.setSuffix(' °' if key=='angle' else ' mm')
            self.fields[key]=field;form.addRow(labels[key],field)
            if isinstance(field,QComboBox):field.setCurrentIndex(field.findData(value))
        c=card(self.body);self.summary=label('');c.addWidget(self.summary)
        self.handle_note=label('','muted');c.addWidget(self.handle_note)
        self.body.addStretch();footer=QHBoxLayout();footer.setContentsMargins(20,6,20,8);footer.addWidget(button('預覽',self.preview));self.apply_button=button('更新／修復選取門片' if editing and kind=='glass' else '更新模型' if editing else '建立模型',self.apply_model,'primary');footer.addWidget(self.apply_button);self.outer.insertLayout(self.outer.count()-1,footer)
        self._refresh_timer=QTimer(self);self._refresh_timer.setSingleShot(True);self._refresh_timer.timeout.connect(self.preview)
        for field in self.fields.values():
            signal=field.currentIndexChanged if isinstance(field,QComboBox) else field.valueChanged
            signal.connect(self.changed)
        self.sync_fields()
        if editing:self.read_target()
        else:self.info.setText('新增：在目前編輯層級的原點建立，可建立後移動。');self.preview()
    def sync_fields(self):
        if self.kind=='glass':
            pull=self.fields['handle_type'].currentData()=='pull';double=self.fields['door_mode'].currentData()=='double';self.fields['handle_length'].setEnabled(not pull);self.fields['center_gap'].setEnabled(double)
            for key in ('pull_length','pull_edge','pull_offset','pull_side','pull_stand'):self.fields[key].setEnabled(pull)
            self.fields['pull_edge'].setEnabled(pull and not double)
            self.handle_note.setText(('對開輸入總寬，扣中縫後等分兩扇；把手自動靠中縫。' if double else '')+'外凸把手預設長 22 cm，粗細與固定座保持；原內凹區補平。正反兩面位置對齊。預設霧金，可用群組上色變更。' if pull else ('對開兩扇把手靠中縫。' if double else '')+'玻璃每扇 4 層，欄數可選；雙面內凹把手，原型厚 5 cm 時凹深 1.9 cm，凹深隨本體厚度同比調整。')
        else:self.handle_note.setText('依高度計算葉片數量，平均分配淨距；上下各留半個淨距。')
    def changed(self,*_):self.sync_fields();self._refresh_timer.start(220)
    def parameters(self):
        return {key:(field.currentData() if isinstance(field,QComboBox) else field.value()) for key,field in self.fields.items()}
    def read_target(self):
        def read():
            self.check();items=selection(self.scene,True)
            if len(items)!=1:raise ValueError('請只選取一個要設定的模型。')
            target=items[0];kind,values,rigid=effective_parameters(target,self.kind)
            if self.kind=='glass':values=glass_layout(values)
            if self.kind=='glass':
                self.stop_front_pick(False);self._conversion_source=None;self._conversion_data=None;self.conversion_front_lines=None;self.volume_front.setEnabled(False);self.cancel_volume.setEnabled(False);self.apply_button.setEnabled(True)
            self.target=target;self.rigid=rigid;self.editing=True;self.version=self.scene.content_version
            for key,field in self.fields.items():
                field.blockSignals(True)
                if isinstance(field,QComboBox):field.setCurrentIndex(field.findData(values[key]))
                else:field.setValue(values[key])
                field.blockSignals(False)
            self.sync_fields();self.info.setText('正在修改：'+target.name);self.apply_button.setText('更新／修復選取門片' if self.kind=='glass' else '更新模型');self.preview()
            return '已讀取模型，修改參數後按更新。'
        self.run(read)
    def ensure_target(self):
        if self._conversion_source is not None:
            if selection(self.scene,True)!=[self._conversion_source]:raise ValueError('塊體選取已變更，請重新讀取。')
            if volume_signature(self._conversion_source)!=self._conversion_signature:raise ValueError('來源塊體已變更，請重新讀取。')
            if any(not editable(self.scene,n) for n in walk_groups(self._conversion_source)):raise ValueError('塊體或內部圖層已隱藏／鎖定。')
            return
        if not self.editing:return
        items=selection(self.scene,True)
        if self.target is None or items!=[self.target]:raise ValueError('選取已變更，請按「讀取目前選取模型」。')
        parametric_info(self.target,self.kind)
    def preview(self):
        try:
            self.check(strict=self.editing);self.ensure_target();model=build_parametric(self.kind,self.parameters());self.preview_group=model
            model.xform=QMatrix4x4(self.rigid);self.preview_lines=parametric_preview(model,4000);self.version=self.scene.content_version
            if self.kind=='louver':self.summary.setText(f'每扇 {model.ext["blades_per_panel"]} 片｜實際淨距 {model.ext["actual_gap_mm"]:.2f} mm')
            else:
                info=parametric_info(model)['params'];self.summary.setText(('對開雙扇｜' if info['door_mode']=='double' else '單扇｜')+f'每扇玻璃 {info["glass_columns"]} 欄 × 4 層｜'+(f'外凸長把手 {info["pull_length"]/10:g} cm｜'+{'front':'正面','back':'反面','both':'正反兩面'}[info['pull_side']] if info['handle_type']=='pull' else f'雙面內凹把手，凹深 {info["depth"]*.038:g} cm'))
            if self.kind=='glass':self.summary.setText(self.summary.text()+f'｜本體厚 {self.parameters()["depth"]/10:g} cm')
            self.message('預覽完成，按'+('更新模型' if self.editing else '建立模型')+'套用。')
        except Exception as exc:self.preview_group=None;self.preview_lines=None;self.message(str(exc),True)
        self.conversion_front_lines=None
        if self._conversion_source is not None and self.preview_group is not None:
            values=self.parameters();w,h=values['width']/1000,values['height']/1000
            corners=np.array([[0,-.001,0],[w,-.001,0],[w,-.001,h],[0,-.001,h]]);segments=list(zip(corners,np.roll(corners,-1,axis=0)));center=np.array([w/2,-.001,h/2]);segments.append((center,center+[0,-min(.1,w*.25),0]))
            self.conversion_front_lines=np.array([[xyz(self.rigid.map(QVector3D(*map(float,p)))) for p in line] for line in segments])
        self.vp.update()
    def apply_model(self):
        def apply():
            self.stop_front_pick(True);self.check(strict=self.editing);self.ensure_target();model=build_parametric(self.kind,self.parameters())
            if self._conversion_source is not None:
                source=self._conversion_source;execute(self.vp,ConvertVolumeCabinet(source,model,self.rigid,self._conversion_signature));self.target=source;self.editing=True
                self._conversion_source=None;self._conversion_data=None;self.conversion_front_lines=None;self.volume_front.setEnabled(False);self.cancel_volume.setEnabled(False);self.apply_button.setText('更新／修復選取門片');self.info.setText('正在修改：'+source.name)
            elif self.editing:
                # Re-read placement so moving a target while a panel is open never gets lost.
                _,_,rigid=effective_parameters(self.target,self.kind);execute(self.vp,UpdateParametric(self.target,model,rigid))
            else:execute(self.vp,AddGenerated(model));self.target=model;self.editing=True;self.rigid=QMatrix4x4(model.xform or QMatrix4x4());self.apply_button.setText('更新／修復選取門片' if self.kind=='glass' else '更新模型');self.info.setText('正在修改：'+model.name)
            self.version=self.scene.content_version;self.preview_lines=None;self.preview_group=None;self.summary.setText('已套用；面板保留，可繼續調整。')
            return '已'+('更新' if self.editing else '建立')+'模型，Ctrl＋Z 可復原。'
        self.run(apply)
    def closeEvent(self,event):self.stop_front_pick(False);self._refresh_timer.stop();super().closeEvent(event)
    def done(self,result):self.stop_front_pick(False);self._refresh_timer.stop();super().done(result)

    def start_front_pick(self):
        def start():
            self.check();self.ensure_target()
            if self._conversion_source is None:raise ValueError('請先讀取選取量體。')
            self.stop_front_pick(False)
            window=self;from tools.base import Tool
            self._front_pick_old=self.vp.active_tool;self._front_pick_saved=(self.volume_front.currentData(),{k:self.fields[k].value() for k in ('width','height','depth')},QMatrix4x4(self.rigid))
            class FrontPick(Tool):
                name='點選玻璃門片正面';uses_snap=False
                def on_activate(self,vp):vp.flash_status('直接點選量體正面；Esc 取消。',6000)
                def on_deactivate(self,vp):pass
                def claims_key(self,key,mods):return key==Qt.Key_Escape
                def on_key(self,vp,key,mods):
                    if key==Qt.Key_Escape:window.stop_front_pick(False);return True
                    return False
                def on_cancel(self,vp):window.stop_front_pick(False)
                def choose(self,ctx):
                    window.check();window.ensure_target()
                    if window.vp.pick_group(ctx.screen.x(),ctx.screen.y()) is not window._conversion_source:return False
                    origin,direction=window.vp._pixel_to_ray(ctx.screen.x(),ctx.screen.y())
                    if origin is None or direction is None:return False
                    face=volume_front_from_ray(window._conversion_data,xyz(origin),xyz(direction))
                    if face is None:return False
                    window.volume_front.setCurrentIndex(window.volume_front.findData(face));return True
                def on_hover(self,ctx):
                    try:self.choose(ctx)
                    except Exception as exc:window.message(str(exc),True);window.stop_front_pick(False)
                def on_click(self,ctx):
                    try:
                        if self.choose(ctx):window.stop_front_pick(True);window.message('已指定玻璃門片正面；橘色填面、標籤與箭頭表示朝外方向。')
                        else:window.message('請直接點已讀取量體上的面。',True)
                    except Exception as exc:window.message(str(exc),True);window.stop_front_pick(False)
            self._front_pick_tool=FrontPick();self.vp.set_active_tool(self._front_pick_tool);self.vp.setFocus();return '請直接點量體正面；滑過可預覽，Esc 取消。'
        self.run(start)
    def stop_front_pick(self,commit=False):
        tool=getattr(self,'_front_pick_tool',None)
        if tool is None:return
        self._front_pick_tool=None
        if self.vp.active_tool is tool:self.vp.set_active_tool(self._front_pick_old)
        if not commit and self._conversion_source is not None:
            key,values,rigid=self._front_pick_saved;self.volume_front.blockSignals(True);self.volume_front.setCurrentIndex(self.volume_front.findData(key));self.volume_front.blockSignals(False)
            for name,value in values.items():self.fields[name].blockSignals(True);self.fields[name].setValue(value);self.fields[name].blockSignals(False)
            self.rigid=rigid;self.preview()

    def read_volume(self):
        self.stop_front_pick(False);self.apply_button.setEnabled(False);self.cancel_volume.setEnabled(True)
        self._conversion_source=None;self._conversion_data=None;self._conversion_signature=None;self.preview_group=None;self.preview_lines=None;self.conversion_front_lines=None;self.volume_front.setEnabled(False)
        def read():
            self.check();items=selection(self.scene,True)
            if len(items)!=1:raise ValueError('請只選取一個立方體／長方體群組或元件。')
            source=items[0]
            if any(not editable(self.scene,n) for n in walk_groups(source)):raise ValueError('量體或內部圖層已隱藏／鎖定。')
            data=detect_cabinet_volume(source)
            self._conversion_source=source;self._conversion_data=data;self._conversion_signature=volume_signature(source)
            self.editing=False;self.target=None;self._last_model_snapshot=None;self.version=self.scene.content_version
            self.volume_front.setEnabled(True);self.cancel_volume.setEnabled(True);self.apply_button.setEnabled(True)
            self.volume_front_changed();self.apply_button.setText('轉換選取量體');self.info.setText('正在轉換：'+source.name+'；橘色填面與「玻璃門片正面」標籤表示正面。')
            return '已讀取量體。請選正面及配置，預覽後按「轉換選取量體」；原量體尚未修改。'
        self.run(read)
    def volume_front_changed(self,*_):
        if self._conversion_source is None:return
        values,self.rigid=cabinet_volume_placement(self._conversion_data,self.volume_front.currentData())
        for key,value in values.items():
            field=self.fields[key];field.blockSignals(True);field.setValue(value);field.blockSignals(False)
        self.preview()
    def cancel_conversion(self):
        self.stop_front_pick(False)
        self._conversion_source=None;self._conversion_data=None;self._conversion_signature=None;self.conversion_front_lines=None
        self.volume_front.setEnabled(False);self.cancel_volume.setEnabled(False);self.apply_button.setEnabled(True);self.rigid=QMatrix4x4();self.editing=False;self.target=None
        self.apply_button.setText('建立玻璃門片');self.info.setText('已取消量體轉換，原量體保留；現在於目前編輯層級原點新建。');self.preview()


def decorative_scale_owner(scene,target):
    """Resolve structural members by identity, including older saved doors."""
    def visit(node,owner=None,path=()):
        info=(node.ext or {}).get(PARAM_KEY)
        if info and info.get('kind')=='decorative':owner=node;path=()
        if owner is not None:
            if node is target and path:
                if len(path)==1 and (node.ext or {}).get('lime_decorative_role') in ('leaf','frame'):return owner,path
                if len(path)==2 and (path[0].ext or {}).get('lime_decorative_role')=='frame' and node.name in ('左門框','右門框','上門框'):return owner,path
                return None
        for child in node.children or ():
            found=visit(child,owner,path+(child,) if owner is not None else ())
            if found:return found
        return None
    for node in scene.groups:
        found=visit(node)
        if found:return found
    return None


def decorative_part_update(scene,root,path,native):
    """Infer opening sizes and preserve the native anchor in assembly axes."""
    from core.history import scale_matrix
    p=decorative_layout(parametric_info(root,'decorative')['params'])['params']
    layout=decorative_layout(p);target=path[-1]
    if any(not editable(scene,level['group']) for level in scene._edit_stack):raise ValueError('外層群組已鎖定或隱藏。')
    opened={level['group']:level for level in scene._edit_stack}
    basis=QMatrix4x4(opened[root]['frame']) if root in opened and opened[root].get('frame') is not None else QMatrix4x4(root.xform) if root.xform is not None else QMatrix4x4()
    inverse,ok=basis.inverted()
    if not ok:raise ValueError('造型門座標不可逆。')
    # An oblique scale is a shear in door axes and cannot describe an opening.
    affine=inverse*scale_matrix(native.center,native.factor,native.axes)*basis
    linear=np.array(affine.data()).reshape(4,4).T[:3,:3];diagonal=np.diag(linear)
    if not np.allclose(linear,np.diag(diagonal),atol=2e-5) or np.any(diagonal<=0):raise ValueError('請沿門組本身的軸向拉伸；不支援斜切或負倍率翻轉。')
    if np.allclose(diagonal[[0,2]],1,atol=1e-6):
        # Depth is a stock dimension; a pure thickness drag is restored.
        pass
    parent=QMatrix4x4(root.xform) if root.xform is not None else QMatrix4x4()
    for node in path[:-1]:parent=parent*(node.xform if node.xform is not None else QMatrix4x4())
    points=[]
    def own_mesh(node,matrix):
        points.extend(xyz(inverse.map(matrix.map(v.position))) for v in node.mesh.vertices)
    matrix=parent*(target.xform if target.xform is not None else QMatrix4x4())
    role=(target.ext or {}).get('lime_decorative_role')
    if role=='frame':
        for child in target.children or ():
            if child.name in ('左門框','右門框','上門框'):own_mesh(child,matrix*(child.xform if child.xform is not None else QMatrix4x4()))
    else:own_mesh(target,matrix)
    if not points:raise ValueError('找不到原始門片或門框實體。')
    points=np.asarray(points);lo=points.min(0)*1000.;hi=points.max(0)*1000.;size=hi-lo
    offset=np.zeros(3)
    if role=='leaf':
        p['width']=p['width'] if abs(diagonal[0]-1)<1e-6 else float(size[0]);p['height']=p['height'] if abs(diagonal[2]-1)<1e-6 else float(size[2]);new=decorative_layout(p)
        offset[[0,2]]=lo[[0,2]]-np.asarray(new['leaf_origin'])[[0,2]]
    elif role=='frame':
        p['width']=p['width'] if abs(diagonal[0]-1)<1e-6 else float(size[0])-p['frame_left']-p['frame_right']-p['gap_left']-p['gap_right']
        p['height']=p['height'] if abs(diagonal[2]-1)<1e-6 else float(size[2])-p['frame_top']-p['gap_top']-p['gap_bottom'];new=decorative_layout(p)
        offset[[0,2]]=lo[[0,2]]
    else:
        p['individual_frame']=bool(p['individual_frame'] or abs(diagonal[0 if target.name in ('左門框','右門框') else 2]-1)>1e-6)
        if target.name in ('左門框','右門框'):
            side='left' if target.name=='左門框' else 'right'
            p['frame_'+side]=p['frame_'+side] if abs(diagonal[0]-1)<1e-6 else float(size[0]);p['height']=p['height'] if abs(diagonal[2]-1)<1e-6 else float(size[2])-p['gap_top']-p['gap_bottom']
            new=decorative_layout(p)
            expected_x=0 if side=='left' else new['outer_width']-p['frame_right']
            offset[0]=lo[0]-expected_x;offset[2]=lo[2]
        else:
            p['frame_top']=p['frame_top'] if abs(diagonal[2]-1)<1e-6 else float(size[2]);p['width']=p['width'] if abs(diagonal[0]-1)<1e-6 else float(size[0])-p['frame_left']-p['frame_right']-p['gap_left']-p['gap_right']
            new=decorative_layout(p);offset[0]=lo[0];offset[2]=lo[2]-(new['outer_height']-p['frame_top'])
    # Native depth scaling never changes the profile, panel or frame depth.
    move=QMatrix4x4();move.translate(float(offset[0]/1000),0,float(offset[2]/1000));rigid=basis*move
    replacement=decorative_model(new['params']);replacement.xform=rigid
    # Recreate the same open-edit coordinate representation as the host.
    from core.scene import Scene
    old_to_new={root:replacement}
    def pair(old,fresh):
        by_name={child.name:child for child in fresh.children or ()}
        for child in old.children or ():
            other=by_name.get(child.name)
            if other is None:
                role=(child.ext or {}).get('lime_decorative_role')
                other=next((n for n in fresh.children or () if role and (n.ext or {}).get('lime_decorative_role')==role),None)
            if other is None:raise ValueError('門組內含自行新增或改名的子物件，請先移出，避免重建遺失。')
            old_to_new[child]=other;pair(child,other)
    pair(root,replacement)
    if root in opened:replacement.ext['lime_decorative_basis_baked']=True
    for level in scene._edit_stack:
        node=old_to_new.get(level['group'])
        if node is not None:
            from core.group import group_frame
            frame=group_frame(node)
            Scene._bake_container_xform(node)
            if frame is not None:node.axes=frame
    return UpdateDecorativeParts(root,replacement,old_to_new)


class UpdateDecorativeParts(Command):
    """Keep structural identities: native scale undo and open edit paths survive."""
    def __init__(self,root,replacement,mapping):
        self.root=root;self.replacement=bind_face_materials(carry_parametric_paint(root,replacement));self.mapping=mapping;self.before=None;self.after=None
    @staticmethod
    def capture(nodes):
        return [(n,n.mesh,n.children,deepcopy(n.ext),n.name,QMatrix4x4(n.xform) if n.xform is not None else None,QMatrix4x4(n.axes) if n.axes is not None else None) for n in nodes]
    @staticmethod
    def restore(state):
        for node,mesh,children,ext,name,xform,axes in state:
            node.mesh=mesh;node.children=children;node.ext=deepcopy(ext);node.name=name;node.xform=QMatrix4x4(xform) if xform is not None else None;node.axes=QMatrix4x4(axes) if axes is not None else None
    def do(self,scene):
        if self.after is not None:self.restore(self.after)
        else:
            if any(not editable(scene,n) for n in self.mapping):raise ValueError('門組內有隱藏或鎖定的結構，請先解除。')
            self.before=self.capture(self.mapping)
            reverse={fresh:old for old,fresh in self.mapping.items()}
            for old,fresh in self.mapping.items():
                # Keep empty edit-context meshes stable for history/scene pointers.
                if old.mesh.vertices or fresh.mesh.vertices:old.mesh=fresh.mesh
                old.children=[reverse.get(child,child) for child in fresh.children] if fresh.children is not None else None
                old.ext={**deepcopy(old.ext or {}),**deepcopy(fresh.ext or {})}
                if old is self.root and not (fresh.ext or {}).get('lime_decorative_basis_baked'):old.ext.pop('lime_decorative_basis_baked',None)
                old.name=fresh.name
                old.xform=QMatrix4x4(fresh.xform) if fresh.xform is not None else None;old.axes=QMatrix4x4(fresh.axes) if fresh.axes is not None else None
            self.after=self.capture(self.mapping)
        self.refresh(scene)
    def refresh(self,scene):
        if scene.edit_group in self.mapping:scene.mesh=scene.edit_group.mesh
        for level in scene._edit_stack:
            if level['group'] in self.mapping:
                from core.group import group_frame
                level['frame']=group_frame(level['group'])
        _dirty_group_chunks(scene);scene.version+=1
    def undo(self,scene):self.restore(self.before);self.refresh(scene)


class ParametricScaleWatcher:
    """Rebuild only the latest native scale, after the scale tool is left.

    It never polls and changes arbitrary transformed models or replays undo.
    The native scale and rebuild become one undo/redo operation.
    """
    def __init__(self,app):
        self.app=app;self.seen=None;self.busy=False
        self.timer=QTimer(app.window);self.timer.setInterval(350);self.timer.timeout.connect(self.poll);self.timer.start()
    def rollback_invalid_door_scale(self,vp,top,selection_before):
        history=vp.history;scene=vp.scene
        if not history.undo_stack or history.undo_stack[-1] is not top:return False
        history.undo();history.last_error=None;history.last_rejected=None
        if history.redo_stack and history.redo_stack[-1] is top:history.redo_stack.pop()
        context=scene.groups if scene.edit_group is None else (scene.edit_group.children or [])
        valid=set(context)|set(scene.mesh.faces)|set(scene.mesh.edges)
        scene.selection.clear();scene.selection.update(item for item in selection_before if item in valid)
        _dirty_group_chunks(scene);vp.notify_scene_changed();vp.update()
        latest=history.undo_stack[-1] if history.undo_stack else None;self.seen=(id(scene),id(latest),scene.content_version)
        return True
    def poll(self):
        if self.busy:return
        vp=self.app.viewport;scene=vp.scene;history=vp.history
        tool=getattr(vp,'active_tool',None)
        if tool is not None and (type(tool).__name__=='ScaleTool' or getattr(tool,'name',None)=='Scale'):return
        top=history.undo_stack[-1] if history.undo_stack else None
        token=(id(scene),id(top),scene.content_version)
        if token==self.seen:return
        self.seen=token
        if top is None or history.redo_stack:return
        from core.history import ScaleGroupCommand,CompoundCommand
        def affected(cmd):
            if isinstance(cmd,ScaleGroupCommand):return [cmd.group]
            if isinstance(cmd,CompoundCommand):return [g for c in cmd.commands for g in affected(c)]
            return []
        targets=[];parts={}
        def scaled(cmd):
            if isinstance(cmd,ScaleGroupCommand):return [cmd]
            if isinstance(cmd,CompoundCommand):return [c for sub in cmd.commands for c in scaled(sub)]
            return []
        for native in scaled(top):
            g=native.group
            if (g.ext or {}).get(PARAM_KEY):
                if g not in targets:targets.append(g)
            else:
                owner=decorative_scale_owner(scene,g)
                if owner is not None:
                    root,path=owner;parts.setdefault(root,[]).append((path,native))
        if not targets and not parts:return
        selection_before=set(scene.selection);has_door=bool(parts) or any((g.ext or {}).get(PARAM_KEY,{}).get('kind')=='decorative' for g in targets)
        try:
            if has_door and any(not editable(scene,level['group']) for level in scene._edit_stack):raise ValueError('外層群組已隱藏或鎖定。')
            commands=[]
            for root,members in parts.items():
                if root in targets or len(members)!=1:raise ValueError('同一門組一次請只拉伸一個門片、門框或框料。')
                path,native=members[0];commands.append(decorative_part_update(scene,root,path,native))
            for group in targets:
                if not editable(scene,group):
                    if (group.ext or {}).get(PARAM_KEY,{}).get('kind')=='decorative':raise ValueError('造型門已隱藏或鎖定。')
                    continue
                kind,params,rigid=effective_parameters(group)
                scale,_=parametric_transform(group)
                if np.allclose(scale,1,atol=1e-6):continue
                commands.append(UpdateParametric(group,build_parametric(kind,params),rigid))
            if not commands:return
            self.busy=True;rebuild=AtomicCommands(commands)
            cap=history.max_steps
            try:
                if cap==1:history.max_steps=2
                execute(vp,rebuild)
            finally:history.max_steps=cap
            if len(history.undo_stack)>=2 and history.undo_stack[-2] is top and history.undo_stack[-1] is rebuild:
                merged=AtomicCommands([top,rebuild]);merged._history_mesh=top._history_mesh;history.undo_stack[-2:]=[merged]
            self.seen=(id(scene),id(history.undo_stack[-1]),scene.content_version)
            vp.flash_status('已依個別拉伸連動門片與門框，門縫與厚度保持設定。Ctrl＋Z 可一次復原。' if parts else '已依拉伸尺寸更新，保留框料與厚度。Ctrl＋Z 可一次復原。',6000)
        except Exception as exc:
            restored=False
            if has_door:
                try:restored=self.rollback_invalid_door_scale(vp,top,selection_before)
                except Exception as rollback_error:vp.flash_status('造型門自動還原失敗：'+str(rollback_error),8000)
            if restored:vp.flash_status('已自動還原本次不合法的造型門拉伸：'+str(exc)+'；請改用設定面板調整。',8000)
            else:vp.flash_status('自動更新未套用：'+str(exc)+'；Ctrl＋Z 可復原此次拉伸，或用設定面板修正尺寸。',8000)
        finally:self.busy=False



def hide_stock_joins(root):
    from collections import defaultdict
    faces=[];segments=defaultdict(list);shapes=defaultdict(list)
    def point_key(point):return tuple(round(float(x),5) for x in point)
    def visit(node,parent):
        matrix=parent*(node.xform if node.xform is not None else QMatrix4x4())
        fids={}
        for face in node.mesh.faces:
            loops=[[xyz(matrix.map(v.position)) for v in ring] for ring in (face.loop,*face.hole_loops)]
            pts=np.asarray(loops[0]);normal=np.sum(np.cross(pts,np.roll(pts,-1,axis=0)),axis=0);length=np.linalg.norm(normal)
            if length<1e-12:continue
            normal/=length;idx=len(faces);fids[face]=idx
            signature=json.dumps(face.attrs,sort_keys=True)
            record=(normal,pts,signature);faces.append(record)
            shape=tuple(sorted(tuple(sorted(point_key(p) for p in ring)) for ring in loops));shapes[shape].append(idx)
        for edge in node.mesh.edges:
            a,b=xyz(matrix.map(edge.a)),xyz(matrix.map(edge.b));ka,kb=point_key(a),point_key(b)
            if ka!=kb:segments[tuple(sorted((ka,kb)))].append((node,edge,[fids[f] for f in edge.faces if f in fids]))
        for child in node.children or ():visit(child,matrix)
    visit(root,QMatrix4x4());buried=set()
    for ids in shapes.values():
        if len(ids)==2:
            a,b=ids
            if faces[a][2]==faces[b][2] and faces[a][0]@faces[b][0]<-.999999:buried.update(ids)
    marks=defaultdict(set)
    for (a,b),entries in segments.items():
        ids={i for _,_,group in entries for i in group};inside=ids&buried;outside=list(ids-buried)
        if len(inside)!=2 or len(outside)!=2:continue
        x,y=outside;nx,px,sx=faces[x];ny,py,sy=faces[y]
        if sx!=sy or nx@ny<.999999:continue
        transverse=np.cross(nx,np.asarray(b)-a);norm=np.linalg.norm(transverse)
        if norm<1e-9:continue
        transverse/=norm
        sa=(px-np.asarray(a))@transverse;sb=(py-np.asarray(a))@transverse
        left=(sa.max()<=1e-5 and sa.min()<-1e-5 and sb.min()>=-1e-5 and sb.max()>1e-5)
        right=(sb.max()<=1e-5 and sb.min()<-1e-5 and sa.min()>=-1e-5 and sa.max()>1e-5)
        if left or right:
            for node,edge,_ in entries:marks[node].add(edge)
    count=0
    for node,edges in marks.items():
        # A stock mesh may be placed elsewhere: hide only this placement's joins.
        clone=transformed_mesh(node.mesh,QMatrix4x4())
        # Match by geometry, not by iteration order through the copy.
        keys={tuple(sorted((point_key(xyz(e.a)),point_key(xyz(e.b))))) for e in edges}
        for edge in clone.edges:
            if tuple(sorted((point_key(xyz(edge.a)),point_key(xyz(edge.b))))) in keys:
                if not edge.hidden:count+=1
                edge.hidden=True
        node.mesh=clone
    root.ext['hidden_stock_joins']=count
    return count



def parametric_preview(root,limit=4000):
    lines=[]
    def visit(node,parent):
        matrix=parent*(node.xform if node.xform is not None else QMatrix4x4())
        for edge in node.mesh.edges:
            if not edge.hidden and not edge.soft:lines.append((xyz(matrix.map(edge.a)),xyz(matrix.map(edge.b))))
        for child in node.children or ():visit(child,matrix)
    visit(root,QMatrix4x4())
    if len(lines)>limit:lines=[lines[i] for i in np.linspace(0,len(lines)-1,limit,dtype=int)]
    return np.asarray(lines,dtype=float).reshape(-1,2,3)

# ---- Face paint binding: explicit pixels survive any group nesting ----
PAINT_KEYS=('color','texture','opacity','mat')


def paint_recipe(attrs):return {k:deepcopy(attrs[k]) for k in PAINT_KEYS if (attrs or {}).get(k) is not None}


def recipe_equal(a,b):return json.dumps(a,sort_keys=True)==json.dumps(b,sort_keys=True)


def auto_front(attrs):
    return '_lime_auto_front' in attrs and recipe_equal(paint_recipe(attrs),attrs['_lime_auto_front'])


def bind_face_materials(root):
    """Bake nearest inherited paint, or white, to both sides. Keep user paint.

    Auto recipes are tracked so blank/outer paint modes remain useful. A face
    edited outside this extension stops being automatic as soon as it differs.
    """
    prepared={}
    def visit(node,inherited=None):
        inherited=paint_recipe(node.material) or inherited
        cache_key=(id(node.mesh),json.dumps(inherited,sort_keys=True))
        if node.mesh.faces and cache_key in prepared:node.mesh=prepared[cache_key]
        elif node.mesh.faces:
            mesh=transformed_mesh(node.mesh,QMatrix4x4())
            for original,copied in zip(node.mesh.faces,mesh.faces):copied.attrs=deepcopy(original.attrs)
            restore_node_markers(node,mesh)
            for face in mesh.faces:
                attrs=face.attrs;front=paint_recipe(attrs)
                automatic=auto_front(attrs)
                if automatic or not front:
                    front=deepcopy(inherited or {'color':[1.,1.,1.]})
                    for key in PAINT_KEYS:attrs.pop(key,None)
                    attrs.update(front);attrs['_lime_auto_front']=deepcopy(front)
                else:attrs.pop('_lime_auto_front',None)
                back=attrs.get('back')
                back_auto=('_lime_auto_back' in attrs and isinstance(back,dict) and recipe_equal(paint_recipe(back),attrs['_lime_auto_back']))
                if back_auto or not isinstance(back,dict) or not paint_recipe(back):
                    # A default back matches its own front; glass stays glass.
                    attrs['back']=deepcopy(front);attrs['_lime_auto_back']=deepcopy(front)
                else:attrs.pop('_lime_auto_back',None)
            node.mesh=mesh;prepared[cache_key]=mesh
        for child in node.children or ():visit(child,inherited)
    visit(root)
    stash_auto_materials(root)
    return root


# ---- Fourth batch: double-sided decorative leaf and three-sided frame ----
DECORATIVE_DEFAULTS={
    'width':900.,'height':2100.,'frame_depth':120.,'front_setback':20.,'back_setback':20.,
    'leaf_extra_front':20.,'leaf_extra_back':20.,'left':110.,'right':110.,'top':140.,'bottom':140.,
    'individual_leaf':False,'middle':100.,'lower_height':420.,'recess_front':5.,'recess_back':5.,
    'with_frame':True,'frame_left':60.,'frame_right':60.,'frame_top':60.,'individual_frame':False,
    'individual_rebate':False,'frame_rebate_front_width':20.,'frame_rebate_back_width':20.,
    'frame_rebate_front_depth':20.,'frame_rebate_back_depth':20.,
    'gap_left':3.,'gap_right':3.,'gap_top':3.,'gap_bottom':3.,
    'leaf_profile_snapshot':None,'frame_profile_snapshot':None,'leaf_profile_side':'both','frame_profile_side':'both',
    'handle_mount':'manual','handle_rail_x':'center','handle_reference':'left','handle_asset':None,'handle_side':'both','handle_edge':'right','handle_margin':55.,'handle_height':950.,'handle_angle':0.,'handle_stand':0.}


def decorative_layout(values=None):
    values=values or {};p=deepcopy(DECORATIVE_DEFAULTS)
    validate_accessory_parameters(values)
    p.update({k:v for k,v in values.items() if k in p})
    for key,default in DECORATIVE_DEFAULTS.items():
        if isinstance(default,bool):
            if not isinstance(p[key],bool):raise ValueError('開關設定無效。')
        elif isinstance(default,(int,float)):
            p[key]=float(p[key])
            if not math.isfinite(p[key]):raise ValueError('尺寸必須為有限數值。')
    if not p['individual_leaf']:p['right']=p['left'];p['bottom']=p['top']
    if not p['individual_frame']:p['frame_right']=p['frame_left'];p['frame_top']=p['frame_left']
    if not p['individual_rebate']:
        p['frame_rebate_back_width']=p['frame_rebate_front_width'];p['frame_rebate_back_depth']=p['frame_rebate_front_depth']
    if p['with_frame']:
        for side in ('front','back'):p[side+'_setback']=(p['frame_rebate_'+side+'_depth'] if p['frame_rebate_'+side+'_width']>0 else 0.)+p['leaf_extra_'+side]
    if not all(100<=p[k]<=10000 for k in ('width','height')):raise ValueError('門片寬高須介於 100～10000 mm。')
    if not 20<=p['frame_depth']<=1000:raise ValueError('門框深度須介於 20～1000 mm。')
    if any(p[k]<0 for k in ('front_setback','back_setback','leaf_extra_front','leaf_extra_back')):raise ValueError('退入距離不得為負。')
    thickness=p['frame_depth']-p['front_setback']-p['back_setback']
    if thickness<10:raise ValueError('門片厚度不足 10 mm，請增加門框深度或減少退入。')
    if p['recess_front']<0 or p['recess_back']<0 or p['recess_front']+p['recess_back']>thickness-2:raise ValueError('正反內凹深度須為正值或零，並保留至少 2 mm 底厚。')
    if any(p[k]<5 for k in ('left','right','top','bottom','middle')):raise ValueError('門片框料至少 5 mm。')
    upper=p['height']-p['top']-p['bottom']-p['lower_height']-p['middle']
    if upper<10 or p['lower_height']<10 or p['width']-p['left']-p['right']<10:raise ValueError('上下內框淨高及淨寬至少 10 mm。')
    if any(p[k]<5 for k in ('frame_left','frame_right','frame_top')) or any(not 0<=p[k]<=50 for k in ('gap_left','gap_right','gap_top','gap_bottom')):raise ValueError('門框寬至少 5 mm，四邊門縫須介於 0～50 mm。')
    for side in ('front','back'):
        if not 0<=p['frame_rebate_'+side+'_width']<min(p['frame_left'],p['frame_right'],p['frame_top']) or p['frame_rebate_'+side+'_depth']<0:raise ValueError('內口退縮寬須小於框料，寬深不得為負。')
    if sum(p['frame_rebate_'+s+'_depth'] if p['frame_rebate_'+s+'_width']>0 else 0 for s in ('front','back'))>p['frame_depth']-2:raise ValueError('正反內口退縮須保留至少 2 mm 框體。')
    ox=p['frame_left']+p['gap_left'] if p['with_frame'] else 0.;oz=p['gap_bottom'] if p['with_frame'] else 0.
    lower=[p['left'],p['bottom'],p['width']-p['right'],p['bottom']+p['lower_height']]
    upper_rect=[lower[0],lower[3]+p['middle'],lower[2],p['height']-p['top']]
    return {'params':p,'leaf_thickness':thickness,'upper_height':upper,'fields':[lower,upper_rect],
            'leaf_origin':[ox,p['front_setback'],oz],
            'outer_width':p['width']+(p['frame_left']+p['frame_right']+p['gap_left']+p['gap_right'] if p['with_frame'] else 0),
            'outer_height':p['height']+(p['frame_top']+p['gap_top']+p['gap_bottom'] if p['with_frame'] else 0)}


def recessed_solid(rect,front_fields,back_fields,front_depth,back_depth,thickness,name,extra_front_cuts=()):
    import manifold3d as m3d
    from core.solids import _Sources,_loops_from,_group_from_loops
    x,z,x2,z2=np.asarray(rect,float)/1000.;t=thickness/1000.
    def box(lo,hi):
        lo=np.asarray(lo);hi=np.asarray(hi)
        return m3d.Manifold.cube(tuple(hi-lo)).translate(tuple(lo))
    body=box([x,0,z],[x2,t,z2]);eps=1e-7
    for fields,depth,front in [(front_fields,front_depth,True),(back_fields,back_depth,False),*[( [field],depth,True) for field,depth in extra_front_cuts]]:
        if depth<=0:continue
        for field in fields:
            a,c,b,d=np.asarray(field,float)/1000.
            if b<=a or d<=c:continue
            low=-eps if front else t-depth/1000.;high=depth/1000. if front else t+eps
            cutter=box([a,low,c],[b,high,d]);body=body-cutter
    if body.status()!=m3d.Error.NoError or body.is_empty():raise ValueError('內凹幾何無法建立，請檢查尺寸。')
    sources=_Sources();sources.attrs=[{}]
    # Assign a uniform face provenance; all real paint is bound after construction.
    mesh=body.to_mesh64();body=m3d.Manifold(m3d.Mesh64(vert_properties=np.array(mesh.vert_properties,dtype=np.float64,order='C'),tri_verts=np.array(mesh.tri_verts,dtype=np.uint64,order='C'),face_id=np.zeros(len(mesh.tri_verts),dtype=np.uint64)))
    group=_group_from_loops(_loops_from(body,sources),sources,name,None)
    from core.solids import solid_report
    ok,volume=solid_report(group.mesh)
    if not ok or abs(volume-body.volume())>max(1e-9,body.volume()*1e-4):raise ValueError('門片封閉性或體積驗證未通過，未寫入文件。')
    stamp_surface_keys(group)
    return group


def decorative_model(values=None):
    layout=decorative_layout(values);p=layout['params'];root=Group(name=f'造型門組 {p["width"]/10:g}×{p["height"]/10:g} cm');children=[]
    if p['with_frame']:
        w,h=layout['outer_width'],layout['outer_height'];frame=Group(name='門框｜左、右、上三邊');parts=[]
        specs=[('左門框',[0,0,p['frame_left'],h-p['frame_top']]),('右門框',[w-p['frame_right'],0,w,h-p['frame_top']]),('上門框',[0,h-p['frame_top'],w,h])]
        for index,(name,r) in enumerate(specs):
            fields=[]
            for side in ('front','back'):
                rw=p['frame_rebate_'+side+'_width']
                field=[r[2]-rw,r[1],r[2],r[3]] if index==0 else [r[0],r[1],r[0]+rw,r[3]] if index==1 else [p['frame_left']-rw,r[1],w-p['frame_right']+rw,r[1]+rw]
                fields.append([field] if rw>0 else [])
            part=recessed_solid(r,fields[0],fields[1],p['frame_rebate_front_depth'],p['frame_rebate_back_depth'],p['frame_depth'],name);parts.append(part)
        frame.adopt(parts);frame.ext={'lime_decorative_role':'frame'};children.append(frame)
    leaf=recessed_solid([0,0,p['width'],p['height']],layout['fields'],layout['fields'],p['recess_front'],p['recess_back'],layout['leaf_thickness'],'門片｜正反造型')
    leaf.xform=QMatrix4x4();leaf.xform.translate(*[v/1000. for v in layout['leaf_origin']]);leaf.ext={**(leaf.ext or {}),'lime_decorative_role':'leaf'};children.append(leaf)
    root.adopt(children);root.ext={PARAM_KEY:{'kind':'decorative','params':p,'version':1},'lime_suite_generated':'decorative'}
    add_decorative_accessories(root,layout)
    return bind_face_materials(root)


class DecorativeWindow(GeometryWindow):
    def __init__(self,app,editing=False):
        super().__init__(app,'造型門片＋門框'+('設定' if editing else '｜新增'),620,860)
        self.kind='decorative';self.editing=editing;self.target=None;self.rigid=QMatrix4x4();self.preview_group=None;self.fields={}
        self.body.addWidget(label('正反兩格造型、三邊門框，無下框／門檻。單位 mm。\n雙擊進入門組，選門片或門框拉伸；按空白鍵離開縮放後連動。\n再進入門框群組，可拉伸左右框與上框。厚度仍依設定保持。','muted'))
        self.info=label('新增模型建立在目前編輯層級的原點。');self.body.addWidget(self.info);self.body.addWidget(button('讀取目前選取門組',self.read_target))
        groups=[('門片尺寸與退入',[('width','門片寬度'),('height','門片高度'),('frame_depth','門框深度'),('leaf_extra_front','正面額外退入'),('leaf_extra_back','反面額外退入'),('front_setback','正面總退入'),('back_setback','反面總退入')]),
                ('門片框料',[('individual_leaf','個別調整四邊'),('left','左框料'),('right','右框料'),('top','上框料'),('bottom','下框料')]),
                ('上下內框',[('middle','中橫料高度'),('lower_height','下內框淨高'),('recess_front','正面內凹'),('recess_back','反面內凹')]),
                ('門框',[('with_frame','建立三邊門框'),('individual_frame','個別調整三邊'),('frame_left','左門框寬'),('frame_right','右門框寬'),('frame_top','上門框寬')]),
                ('內口退縮',[('individual_rebate','正反面個別調整'),('frame_rebate_front_width','正面退縮寬'),('frame_rebate_back_width','反面退縮寬'),('frame_rebate_front_depth','正面退縮深'),('frame_rebate_back_depth','反面退縮深')]),
                ('四邊門縫',[('gap_left','左門縫'),('gap_right','右門縫'),('gap_top','上門縫'),('gap_bottom','下門縫')])]
        for title,items in groups:
            c=card(self.body);c.addWidget(label(title));grid=QGridLayout();c.addLayout(grid)
            for i,(key,title) in enumerate(items):
                default=DECORATIVE_DEFAULTS[key]
                if isinstance(default,bool):field=QCheckBox(title);field.setChecked(default);grid.addWidget(field,i//2,(i%2)*2,1,2)
                else:field=length_number(default,0);field.setMaximum(10000);field.setSuffix(' mm');grid.addWidget(label(title),i//2,(i%2)*2);grid.addWidget(field,i//2,(i%2)*2+1)
                self.fields[key]=field
        c=card(self.body);self.summary=label('');c.addWidget(self.summary);c.addWidget(label('線板與把手可從資料庫選用，幾何快照會隨模型儲存。','muted'));self.accessories=DecorativeAccessories(self)
        self.body.addStretch();footer=QHBoxLayout();footer.setContentsMargins(20,5,20,8);footer.addWidget(button('預覽',self.preview));self.apply_button=button('更新門組' if editing else '建立門組',self.apply_model,'primary');footer.addWidget(self.apply_button);self.outer.insertLayout(self.outer.count()-1,footer)
        self._refresh_timer=QTimer(self);self._refresh_timer.setSingleShot(True);self._refresh_timer.timeout.connect(self.preview)
        for f in self.fields.values():
            signal=f.toggled if isinstance(f,QCheckBox) else f.valueChanged
            signal.connect(self.changed)
        self.sync_fields()
        if editing:self.read_target()
        else:self.preview()
    def parameters(self):
        p={k:f.isChecked() if isinstance(f,QCheckBox) else f.value() for k,f in self.fields.items()};p.update(self.accessories.parameters());return p
    def sync_fields(self):
        def linked(target,source,active):
            self.fields[target].setEnabled(active)
            if not active:self.fields[target].blockSignals(True);self.fields[target].setValue(self.fields[source].value());self.fields[target].blockSignals(False)
        linked('right','left',self.fields['individual_leaf'].isChecked());linked('bottom','top',self.fields['individual_leaf'].isChecked())
        for k in ('frame_right','frame_top'):linked(k,'frame_left',self.fields['individual_frame'].isChecked())
        linked('frame_rebate_back_width','frame_rebate_front_width',self.fields['individual_rebate'].isChecked());linked('frame_rebate_back_depth','frame_rebate_front_depth',self.fields['individual_rebate'].isChecked())
        for side in ('front','back'):
            field=self.fields[side+'_setback'];field.setEnabled(not self.fields['with_frame'].isChecked())
            if self.fields['with_frame'].isChecked():
                v=(self.fields['frame_rebate_'+side+'_depth'].value() if self.fields['frame_rebate_'+side+'_width'].value()>0 else 0)+self.fields['leaf_extra_'+side].value();field.blockSignals(True);field.setValue(v);field.blockSignals(False)
        if hasattr(self,'accessories'):self.accessories.sync_fields()
    def changed(self,*_):self.sync_fields();self._refresh_timer.start(250)
    def read_target(self):
        def read():
            self.check();items=selection(self.scene,True)
            if len(items)!=1:raise ValueError('請只選取整個造型門組。')
            kind,p,rigid=effective_parameters(items[0],'decorative');self.target=items[0];self.rigid=rigid;self.editing=True;self.version=self.scene.content_version
            for key,f in self.fields.items():
                f.blockSignals(True)
                if isinstance(f,QCheckBox):f.setChecked(p[key])
                else:f.setValue(p[key])
                f.blockSignals(False)
            self.accessories.load(p);self.sync_fields();self.apply_button.setText('更新門組');self.info.setText('正在修改：'+self.target.name);self.preview();return '已讀取門組。'
        self.run(read)
    def ensure_target(self):
        if self.editing and (self.target is None or selection(self.scene,True)!=[self.target]):raise ValueError('選取已變更，請重新讀取門組。')
    def preview(self):
        try:
            self.check(strict=self.editing);self.ensure_target();p=self.parameters();layout=decorative_layout(p);model=decorative_model(p);model.xform=QMatrix4x4(self.rigid);self.preview_group=model;self.preview_lines=parametric_preview(model);self.version=self.scene.content_version
            self.summary.setText(f'門片厚 {layout["leaf_thickness"]:g} mm｜上內框 {layout["upper_height"]:g} mm\n整組外尺寸 {layout["outer_width"]:g} × {layout["outer_height"]:g} mm');self.message('預覽完成。門框與門片分開群組；設定及縮放請選整組。')
        except Exception as exc:self.preview_group=None;self.preview_lines=None;self.message(str(exc),True)
        self.vp.update()
    def apply_model(self):
        def apply():
            self.check(strict=self.editing);self.ensure_target();model=decorative_model(self.parameters())
            if self.editing:
                _,_,rigid=effective_parameters(self.target,'decorative');execute(self.vp,UpdateParametric(self.target,model,rigid))
            else:execute(self.vp,AddGenerated(model));self.target=model;self.editing=True;self.apply_button.setText('更新門組');self.info.setText('正在修改：'+model.name)
            self.version=self.scene.content_version;self.preview_group=None;self.preview_lines=None;return '已套用造型門組，Ctrl＋Z 可復原。'
        self.run(apply)
    def closeEvent(self,event):self._refresh_timer.stop();super().closeEvent(event)
    def done(self,result):self._refresh_timer.stop();super().done(result)



def restore_node_markers(node,mesh):
    states=(node.ext or {}).get('lime_face_auto',[])
    if len(states)!=len(mesh.faces):return
    for face,state in zip(mesh.faces,states):
        for side in ('front','back'):
            recipe=state.get(side)
            attrs=face.attrs if side=='front' else face.attrs.get('back')
            if recipe and isinstance(attrs,dict) and recipe_equal(paint_recipe(attrs),recipe):face.attrs.setdefault('_lime_auto_'+side,deepcopy(recipe))


def restore_auto_markers(root):
    for node in walk_groups(root):restore_node_markers(node,node.mesh)


def stash_auto_materials(root):
    for node in walk_groups(root):
        if not node.mesh.faces:continue
        states=[]
        for face in node.mesh.faces:
            attrs=face.attrs;state={}
            if auto_front(attrs):state['front']=deepcopy(attrs['_lime_auto_front'])
            back=attrs.get('back')
            if isinstance(back,dict) and '_lime_auto_back' in attrs and recipe_equal(paint_recipe(back),attrs['_lime_auto_back']):state['back']=deepcopy(attrs['_lime_auto_back'])
            states.append(state)
        node.ext=deepcopy(node.ext or {})
        if any(states):node.ext['lime_face_auto']=states
        else:node.ext.pop('lime_face_auto',None)



def stamp_surface_keys(group):
    # Boolean triangle order can change with dimensions. Match logical planes
    # and disconnected fields instead of copying paint by face-list position.
    records=[];planes={axis:set() for axis in range(3)}
    for face in group.mesh.faces:
        normal=xyz(face.normal());axis=int(np.argmax(np.abs(normal)));sign=1 if normal[axis]>0 else -1
        points=np.array([xyz(v.position) for v in face.loop]);offset=round(float(points[:,axis].mean()),6)
        planes[axis].add(offset);records.append((axis,sign,offset,points.mean(0)))
    levels={axis:{v:i for i,v in enumerate(sorted(values))} for axis,values in planes.items()}
    buckets={}
    for i,(axis,sign,offset,center) in enumerate(records):buckets.setdefault((axis,sign,levels[axis][offset]),[]).append((i,center))
    keys=[None]*len(records)
    for key,items in buckets.items():
        for index,(i,center) in enumerate(sorted(items,key=lambda x:tuple(x[1][[2,0,1]]))):keys[i]=[*key,index]
    group.ext={**(group.ext or {}),'lime_surface_keys':keys}

# ---- Centimetre UI, retaining legacy mm parameters and metre geometry ----
import re
from PySide6.QtGui import QValidator


def cm_text(text):
    text=str(text)
    pattern=r'(?<![A-Za-z0-9_.])([+-]?\d+(?:\.\d+)?(?:\s*(?:～|~|–|×|與|至|到|、)\s*[+-]?\d+(?:\.\d+)?)*)\s*mm\b'
    def replace(match):
        values=re.sub(r'[+-]?\d+(?:\.\d+)?',lambda m:format(float(m.group())/10,'.8g'),match.group(1))
        return values+' cm'
    return re.sub(r'\bmm\b','cm',re.sub(pattern,replace,text))


class CmLabel(QLabel):
    def __init__(self,text=''):super().__init__(cm_text(text))
    def setText(self,text):super().setText(cm_text(text))


class LengthSpinBox(LockedDoubleSpinBox):
    """The UI reads/writes cm; value() remains legacy mm for scene APIs."""
    def __init__(self):
        self._length_mode=True
        super().__init__();self.setSuffix(' cm');self.setSingleStep(1.)
    def setSuffix(self,suffix):super().setSuffix(cm_text(suffix))
    def textFromValue(self,value):
        value=float(value)/(10 if getattr(self,'_length_mode',True) else 1)
        return format(value,'.3f')
    def input_number(self,text):
        text=str(text).strip()
        if self.prefix() and text.startswith(self.prefix()):text=text[len(self.prefix()):]
        suffix=self.suffix().strip()
        if suffix and text.endswith(suffix):text=text[:-len(suffix)]
        return text.strip().replace(',','')
    def valueFromText(self,text):
        value=float(self.input_number(text));return value*(10 if self._length_mode else 1)
    def validate(self,text,position):
        raw=self.input_number(text)
        if raw in ('','-','+','.','-.','+.'):return QValidator.Intermediate,text,position
        try:
            value=float(raw)*(10 if self._length_mode else 1)
            if not math.isfinite(value):return QValidator.Invalid,text,position
            if not self.minimum()<=value<=self.maximum():return QValidator.Intermediate,text,position
        except (ValueError,OverflowError):return QValidator.Invalid,text,position
        return QValidator.Acceptable,text,position
    def set_length_mode(self,enabled,kind='move'):
        self._length_mode=bool(enabled);self.setSuffix(' cm' if enabled else ' °' if kind=='rotate' else ' ×')
        self.lineEdit().setText(self.prefix()+self.textFromValue(self.value())+self.suffix())


def length_number(value=0,minimum=-1e9):
    field=LengthSpinBox();field.setRange(minimum,1e9);field.setDecimals(3);field.setValue(value)
    return field

# ---- Fifth batch: fixed-stock cabinet, fronts and matching drawer boards ----
CLASSICAL_FRAME_MM=80.25719225406647
CLASSICAL_CENTER_MM=174.-2*CLASSICAL_FRAME_MM

CABINET_DEFAULTS={'classical_left':CLASSICAL_FRAME_MM,'classical_right':CLASSICAL_FRAME_MM,'classical_top':CLASSICAL_FRAME_MM,'classical_bottom':CLASSICAL_FRAME_MM,'width':600.,'height':1800.,'depth':600.,'panels':1,
    'left':70.,'right':70.,'top':90.,'bottom':90.,'recess':5.,
    'profile_mode':'none','profile_width':15.,'profile_height':5.,'rect_width':10.,'rect_depth':5.,
    'door_style':'original','face_mode':'doors','drawer_columns':1,'drawer_rows':3,'drawer_heights':[],
    'handle_count':1,'handle_spacing':200.,'profile_snapshot':None,'handle_asset':None,'handle_position':'center_right','handle_x':40.,'handle_z':40.,'handle_angle':0.,'handle_stand':0.,'handle_pair':True,'handle_z_overrides':{}}


def cabinet_layout(values=None):
    values=values or {};p=deepcopy(CABINET_DEFAULTS);p.update({k:v for k,v in values.items() if k in p})
    if p['profile_mode']=='library' and (p.get('profile_snapshot') or {}).get('kind')=='recess_rectangle':
        dims=np.ptp(section_points(p['profile_snapshot']),axis=0)
        for key,value in zip(('rect_width','rect_depth'),dims):
            if key not in values:p[key]=float(value)
        if 'recess' not in values:p['recess']=max(p['recess'],p['rect_depth'])
    if values.get('zones') or values.get('profile_points'):raise ValueError('自訂分區與配件資料尚未接入，請使用本版的等寬門片或抽屜配置。')
    for key in ('width','height','depth','left','right','top','bottom','classical_left','classical_right','classical_top','classical_bottom','recess','profile_width','profile_height','rect_width','rect_depth'):
        p[key]=float(p[key])
        if not math.isfinite(p[key]):raise ValueError('尺寸必須為有限數值。')
    for key in ('panels','drawer_columns','drawer_rows'):
        value=float(p[key])
        if not math.isfinite(value) or value!=int(value) or not 1<=value<=20:raise ValueError('門片數、抽屜欄數與層數須為 1～20 的整數。')
        p[key]=int(value)
    if not all(60<p[k]<=20000 for k in ('width','height')):raise ValueError('櫃體寬高須大於 6 cm，且不超過 2000 cm。')
    if not 90<p['depth']<=20000:raise ValueError('櫃體總深須大於 9 cm，且不超過 2000 cm。')
    if any(p[k]<5 for k in ('left','right','top','bottom')):raise ValueError('門片四邊框料至少 0.5 cm。')
    if any(not 35<=p[k]<=20000 for k in ('classical_left','classical_right','classical_top','classical_bottom')):raise ValueError('古典門片四邊框料須介於 3.5～2000 cm，以保留內外雕線。')
    if not 0<=p['recess']<=28:raise ValueError('門片厚 3 cm，內凹須介於 0～2.8 cm，保留至少 0.2 cm 底厚。')
    if p['face_mode'] not in ('doors','drawers','open'):raise ValueError('面板配置無效。')
    if p['door_style'] not in ('original','classical','kitchen_c'):raise ValueError('門片樣式無效。')
    validate_accessory_parameters(p)
    count=float(p['handle_count']);spacing=float(p['handle_spacing'])
    if not math.isfinite(count) or count not in (1,2):raise ValueError('每片面板把手數只能為 1 或 2。')
    if not math.isfinite(spacing) or not 1<=spacing<=20000:raise ValueError('雙把手中心間距須介於 0.1～2000 cm。')
    p['handle_count']=int(count);p['handle_spacing']=spacing
    overrides=p.get('handle_z_overrides',{})
    if not isinstance(overrides,dict) or len(overrides)>100:raise ValueError('把手高度設定無效。')
    checked={}
    for index,value in overrides.items():
        if not isinstance(index,str) or not index.isdigit() or not 1<=int(index)<=100:raise ValueError('把手高度識別碼無效。')
        number=float(value)
        if not math.isfinite(number):raise ValueError('把手高度須為有限數值。')
        checked[str(int(index))]=number
    p['handle_z_overrides']=checked
    if p['profile_mode'] not in ('none','bevel','step','library','rectangle'):raise ValueError('線板模式無效。')
    if p['door_style']=='original' and p['profile_mode']=='library' and not p['profile_snapshot']:raise ValueError('請從資料庫選擇線板剖面。')
    if p['door_style']=='original' and p['profile_mode'] in ('bevel','step') and not all(1<=p[k]<=100 for k in ('profile_width','profile_height')):raise ValueError('線板寬高須介於 0.1～10 cm。')
    rectangle=cabinet_rectangle(p)
    if rectangle is not None:
        rw,rd=rectangle
        if not 0.1<=rw<=2000 or not 0.1<=rd<=28:raise ValueError('矩形階差寬度須為 0.01～200 cm，深度須為 0.01～2.8 cm。')
        if p['recess']<rd:raise ValueError('總內凹深度不得小於矩形階差深度。')
    w,h=p['width'],p['height'];panels=[];boards=[]
    if p['face_mode']=='doors':
        for i in range(p['panels']):panels.append([i*w/p['panels'],0.,(i+1)*w/p['panels'],h])
    elif p['face_mode']=='drawers':
        rows,cols=p['drawer_rows'],p['drawer_columns']
        if rows*cols>100:raise ValueError('抽屜面板總數不可超過 100 片。')
        raw=p['drawer_heights']
        if not isinstance(raw,list) or len(raw)>rows:raise ValueError('抽屜層高須為陣列，項目不得超過層數。')
        sizes=[float(raw[i]) if i<len(raw) and raw[i] is not None else None for i in range(rows)]
        if any(v is not None and (not math.isfinite(v) or v<=0) for v in sizes):raise ValueError('指定的抽屜層高必須大於零。')
        remaining=h-sum(v for v in sizes if v is not None);flex=sizes.count(None)
        if flex==0 and abs(remaining)>.01:raise ValueError('抽屜指定層高總和與櫃高不符。')
        sizes=[v if v is not None else remaining/flex for v in sizes]
        if any(v<20 for v in sizes):raise ValueError('每層抽屜高度至少 2 cm。')
        p['drawer_heights']=[float(raw[i]) if i<len(raw) and raw[i] is not None else None for i in range(rows)]
        rectangles=[];cursor=h
        for size in sizes:rectangles.append([0.,cursor-size,w,cursor]);cursor-=size
        bay=w/cols
        for col in range(cols):
            for r in rectangles:panels.append([bay*col,r[1],bay*(col+1),r[3]])
            for row,r in enumerate(rectangles[:-1]):
                joint=r[1];left=30. if col==0 else bay*col+15;right=w-30. if col==cols-1 else bay*(col+1)-15
                rect=[left,joint-15,right,joint+15]
                if right<=left or rect[1]<30 or rect[3]>h-30:raise ValueError('抽屜層板空間不足，請增加櫃體或抽屜層高。')
                cuts=[[left,rect[1],right,joint-2],[left,joint+2,right,rect[3]]]
                boards.append((f'抽屜層板 {col+1}-{row+1}',rect,cuts))
        for col in range(1,cols):
            joint=bay*col;rect=[joint-15,30.,joint+15,h-30.]
            boards.append((f'抽屜欄隔板 {col}',rect,[[rect[0],rect[1],joint-2,rect[3]],[joint+2,rect[1],rect[2],rect[3]]]))
    fronts=[]
    for i,r in enumerate(panels):
        door=[r[0]+2,r[1]+2,r[2]-2,r[3]-2]
        if p['door_style']=='classical':
            left,right,top,bottom=classical_frame(p)
            if door[2]-door[0]<left+right+CLASSICAL_CENTER_MM-1e-6 or door[3]-door[1]<top+bottom+CLASSICAL_CENTER_MM-1e-6:raise ValueError(f'第 {i+1} 片古典面板放不下框料，請減少框料寬度或增加面板尺寸。')
            field=[door[0]+left,door[1]+bottom,door[2]-right,door[3]-top]
        else:field=[door[0]+p['left'],door[1]+p['bottom'],door[2]-p['right'],door[3]-p['top']]
        if p['door_style']=='kitchen_c':
            if door[2]-door[0]<10 or door[3]-door[1]<35:raise ValueError(f'第 {i+1} 片廚具門放不下 C 把手，門寬至少 1 cm、含把手總高至少 3.5 cm（另留面板邊縫）。')
            field=[door[0],door[1],door[2],door[3]-25.]
        elif field[2]-field[0]<10 or field[3]-field[1]<10:raise ValueError(f'第 {i+1} 片面板放不下框料，請增加面板尺寸或減少框料。')
        section_width=float(section_points(p['profile_snapshot'])[:,0].max()) if p['door_style']=='original' and p['profile_mode']=='library' else p['profile_width']
        if rectangle is not None and 2*rectangle[0]+10>min(field[2]-field[0],field[3]-field[1]):raise ValueError(f'第 {i+1} 片面板放不下矩形階差，請縮小寬度或增加面板尺寸。')
        if rectangle is None and p['door_style']=='original' and p['profile_mode']!='none' and 2*section_width>=min(field[2]-field[0],field[3]-field[1]):raise ValueError('線板寬度超過內框一半，請縮小線板或增加面板尺寸。')
        fronts.append((door,field))
    return {'params':p,'fronts':fronts,'drawer_boards':boards}


def cabinet_rectangle(p):
    if p.get('door_style','original')!='original':return None
    if p.get('profile_mode')=='rectangle' or (p.get('profile_mode')=='library' and (p.get('profile_snapshot') or {}).get('kind')=='recess_rectangle'):
        return float(p['rect_width']),float(p['rect_depth'])
    return None


def rectangle_inner(field,width):return [field[0]+width,field[1]+width,field[2]-width,field[3]-width]


def cabinet_profile(rect,p,name):
    if p['profile_mode']=='library':return sweep_section(rect,section_points(p['profile_snapshot']),p['recess'],name)
    width,height=p['profile_width'],p['profile_height']
    if p['profile_mode']=='bevel':profile=[(0,0),(width,0),(width,height*.25),(0,height)]
    else:profile=[(0,0),(width,0),(width,height*.35),(width*.5,height*.35),(width*.5,height),(0,height)]
    x,z,x2,z2=rect;rings=[np.array([[x+s,p['recess']-depth,z+s],[x2-s,p['recess']-depth,z+s],[x2-s,p['recess']-depth,z2-s],[x+s,p['recess']-depth,z2-s]])/1000 for s,depth in profile]
    polys=[]
    for i,a in enumerate(rings):
        b=rings[(i+1)%len(rings)]
        for k in range(4):polys.append([a[k],a[(k+1)%4],b[(k+1)%4],b[k]])
    volume=sum(np.dot(a,np.cross(b,c)) for poly in polys for a,b,c in [(poly[0],poly[j],poly[j+1]) for j in range(1,len(poly)-1)])/6
    if volume<0:polys=[list(reversed(poly)) for poly in polys]
    g=Group(name=name)
    for poly in polys:g.mesh.add_face([QVector3D(*map(float,v)) for v in poly])
    from core.solids import solid_report
    if not solid_report(g.mesh)[0]:raise ValueError('線板封閉性未通過，未建立櫃體。')
    stamp_surface_keys(g);return g


def classical_frame(p=None):
    p=p or {};return tuple(float(p.get('classical_'+key,CLASSICAL_FRAME_MM)) for key in ('left','right','top','bottom'))


def classical_axis(value,old,new,low,high):
    """Expand the flat band; translate rather than scale both carved edges."""
    delta_low=low-CLASSICAL_FRAME_MM;delta_high=high-CLASSICAL_FRAME_MM
    def edge(v,delta):
        if v<=6.:return v
        if v>=57.:return v+delta
        return 6.+(v-6.)*(51.+delta)/51.
    if value<=82.01:return edge(value,delta_low)
    if value>=old-82.01:return new-edge(old-value,delta_high)
    left=82.+delta_low;right=82.+delta_high
    return left+(value-82.)*(new-left-right)/(old-164.)


def classical_panel(rect,name='古典門片',frame=None):
    """Keep the source edge sections; change only flat stock and panel spans."""
    x,z,x2,z2=rect;width,height=x2-x,z2-z
    left,right,top,bottom=classical_frame() if frame is None else tuple(map(float,frame))
    if not all(math.isfinite(v) and 35<=v<=20000 for v in (left,right,top,bottom)):raise ValueError('古典門片四邊框料須介於 3.5～2000 cm。')
    if width<left+right+CLASSICAL_CENTER_MM-1e-6 or height<top+bottom+CLASSICAL_CENTER_MM-1e-6:raise ValueError('古典面板放不下框料，請減少框料寬度或增加面板尺寸。')
    def point(v):
        a,b,c=xyz(v)*1000
        return QVector3D((classical_axis(a,800.,width,left,right)+x)/1000,b/1000,(classical_axis(c,400.,height,bottom,top)+z)/1000)
    children=[]
    for key,part in stock_data('classical')['parts'].items():
        source=stock_mesh('classical',key);mesh=Mesh()
        for face in source.faces:
            rings=[[point(v.position) for v in loop] for loop in (face.loop,*face.hole_loops)]
            copied=mesh.add_face(rings[0],rings[1:] or None);copied.attrs=deepcopy(face.attrs)
        if len(mesh.faces)!=len(source.faces):raise ValueError('古典門片面數不符，請檢查框料尺寸。')
        for edge in source.edges:
            a,b=mesh.vertex_at(point(edge.a)),mesh.vertex_at(point(edge.b))
            if a is not None and b is not None:
                copied=mesh.find_edge(a,b)
                if copied is not None:copied.soft=edge.soft;copied.hidden=edge.hidden;copied.curve=edge.curve;copied.layer=edge.layer
        child=Group(mesh,name='古典零件 '+key);child.ext={'lime_classical_source':part['name']};stamp_surface_keys(child);children.append(child)
    result=Group(name=name);result.adopt(children);return result


@lru_cache(maxsize=64)
def classical_triangles(width,height,frame=None):
    panel=classical_panel([0,0,width,height],frame=frame);triangles=[]
    for child in panel.children:
        for face in child.mesh.faces:
            triangles.extend([[xyz(v)*1000 for v in triangle] for triangle in face.triangulate()])
    a=np.asarray(triangles);a.setflags(write=False);return a



def kitchen_panel(rect,name='廚具門(C把手)'):
    """Map the supplied CCC stock, extending only the extrusion axis of C."""
    x,z,x2,z2=map(float,rect);width=x2-x;height=z2-z;data=stock_data('kitchen')
    if width<10 or height<35:raise ValueError('廚具門淨寬至少 1 cm，含 C 把手淨高至少 3.5 cm。')
    children=[]
    for key,title in (('board','門板'),('handle','C形把手')):
        source=stock_mesh('kitchen',key);mesh=Mesh()
        def point(v):
            a,b,c=xyz(v)*1000
            # Snap shared source endpoints before remapping float32 vertices,
            # so the board and C profile meet at exactly the same height.
            for value in (0.,data['board_height_mm'],data['height_mm']):
                if abs(c-value)<.001:c=value;break
            for value in (0.,data['width_mm']):
                if abs(a-value)<.001:a=value;break
            zz=c/data['board_height_mm']*(height-data['handle_height_mm']) if key=='board' else c-data['height_mm']+height
            return QVector3D((x+a/data['width_mm']*width)/1000,b/1000,(z+zz)/1000)
        for face in source.faces:
            rings=[[point(v.position) for v in loop] for loop in (face.loop,*face.hole_loops)]
            copied=mesh.add_face(rings[0],rings[1:] or None);copied.attrs=deepcopy(face.attrs)
        if len(mesh.faces)!=len(source.faces):raise ValueError('廚具門原型面數不符，已停止。')
        for edge in source.edges:
            a,b=mesh.vertex_at(point(edge.a)),mesh.vertex_at(point(edge.b))
            if a is not None and b is not None:
                copied=mesh.find_edge(a,b)
                if copied is not None:copied.soft=edge.soft;copied.hidden=edge.hidden
        child=Group(mesh,name=title);stamp_surface_keys(child)
        child.ext['lime_kitchen_part']='board' if key=='board' else 'c_handle'
        children.append(child)
    result=Group(name=name);result.adopt(children);return result


def cabinet_front_depth(p,rect,field,x,z):
    if p.get('door_style')=='kitchen_c':return 0.
    if p.get('door_style','original')!='classical':
        rectangle=cabinet_rectangle(p)
        if rectangle is not None:
            inner=rectangle_inner(field,rectangle[0])
            if inner[0]<x<inner[2] and inner[1]<z<inner[3]:return p['recess']
            if field[0]<x<field[2] and field[1]<z<field[3]:return p['recess']-rectangle[1]
            return 0.
        return p['recess'] if field[0]<x<field[2] and field[1]<z<field[3] else 0.
    triangles=classical_triangles(rect[2]-rect[0],rect[3]-rect[1],classical_frame(p));a=triangles[:,0];u=triangles[:,1]-a;v=triangles[:,2]-a
    det=u[:,0]*v[:,2]-u[:,2]*v[:,0];valid=abs(det)>1e-8;det=np.where(valid,det,1.)
    dx=x-rect[0]-a[:,0];dz=z-rect[1]-a[:,2]
    b=(dx*v[:,2]-dz*v[:,0])/det;c=(u[:,0]*dz-u[:,2]*dx)/det
    inside=valid&(b>=-1e-6)&(c>=-1e-6)&(b+c<=1+1e-6)
    if not inside.any():raise ValueError('把手安裝點不在古典門片表面。')
    return float(np.min((a[:,1]+b*u[:,1]+c*v[:,1])[inside]))


def cabinet_depth_candidates(p,rect,field,x,row,target):
    if p.get('door_style','original')!='classical':
        rectangle=cabinet_rectangle(p);return sorted(set([0.,p['recess'],*([p['recess']-rectangle[1]] if rectangle else [])]))
    triangles=classical_triangles(rect[2]-rect[0],rect[3]-rect[1],classical_frame(p));result=[]
    for tri in triangles:
        a=tri[0];normal=np.cross(tri[1]-a,tri[2]-a)
        matrix=np.array([[normal[1],normal[2]],[row[1],row[2]]])
        if abs(np.linalg.det(matrix))<1e-8:continue
        rhs=[normal@a-normal[0]*(x-rect[0]),target-row[0]*x-row[2]*rect[1]+row[1]*p['handle_stand']]
        depth,local_z=np.linalg.solve(matrix,rhs)
        z=local_z+rect[1]
        if rect[1]-1e-5<=z<=rect[3]+1e-5 and abs(cabinet_front_depth(p,rect,field,x,z)-depth)<1e-4:result.append(float(depth))
    return result


def cabinet_model(values=None):
    layout=cabinet_layout(values);p=layout['params'];w,h,d=p['width'],p['height'],p['depth'];body=Group(name='櫃身｜板厚 3 cm');parts=[]
    for name,r in [('左側板',[0,0,30,h]),('右側板',[w-30,0,w,h]),('底板',[30,0,w-30,30]),('頂板',[30,h-30,w-30,h])]:
        field=[max(r[0],2),max(r[1],2),min(r[2],w-2),min(r[3],h-2)]
        board=recessed_solid(r,[field],[],10,0,d-20,name);board.xform=QMatrix4x4();board.xform.translate(0,.02,0);parts.append(board)
    back=recessed_solid([30,30,w-30,h-30],[],[],0,0,30,'背板');back.xform=QMatrix4x4();back.xform.translate(0,(d-30)/1000,0);parts.append(back)
    for name,r,cuts in layout['drawer_boards']:
        board=recessed_solid(r,cuts,[],10,0,d-50,name);board.xform=QMatrix4x4();board.xform.translate(0,.02,0);parts.append(board)
    body.adopt(parts);body.ext={'lime_cabinet_part':'body'};children=[body]
    for i,(rect,field) in enumerate(layout['fronts']):
        name=('抽屜面板' if p['face_mode']=='drawers' else '門片')+f' {i+1:02d}'
        rectangle=cabinet_rectangle(p)
        if p['door_style']=='kitchen_c':panel=kitchen_panel(rect,'廚具'+name+'(C把手)')
        elif p['door_style']=='classical':panel=classical_panel(rect,'古典'+name,classical_frame(p))
        elif rectangle is not None:
            rw,rd=rectangle;inner=rectangle_inner(field,rw)
            panel=recessed_solid(rect,[field],[],p['recess']-rd,0,30,name,extra_front_cuts=[(inner,p['recess'])]);panel.ext['lime_rectangle_step']={'width_mm':rw,'depth_mm':rd,'inner_rect':inner}
        else:panel=recessed_solid(rect,[field],[],p['recess'],0,30,name)
        panel.ext={**(panel.ext or {}),'lime_cabinet_part':'front'};children.append(panel)
        if p['door_style']=='original' and p['profile_mode']!='none' and rectangle is None:children.append(cabinet_profile(field,p,f'內框線板 {i+1:02d}'))
    root=Group(name=f'參數櫃體 {w/10:g}×{h/10:g}×{d/10:g} cm');root.adopt(children)
    root.ext={PARAM_KEY:{'kind':'cabinet','params':p,'version':1},'lime_suite_generated':'cabinet','stock_mm':30,'front_thickness_mm':20 if p['door_style'] in ('classical','kitchen_c') else 30,'front_gap_mm':2,'reveal_depth_mm':20}
    add_cabinet_handles(root,layout)
    return bind_face_materials(root)


def volume_front_from_ray(data,origin,direction):
    basis=data['basis'];points=data['points']@basis;lo=points.min(0);hi=points.max(0)
    origin=np.asarray(origin,float)@basis;direction=np.asarray(direction,float)@basis
    near=-np.inf;far=np.inf;near_face=None;far_face=None
    for axis in range(3):
        if abs(direction[axis])<1e-12:
            if not lo[axis]-1e-8<=origin[axis]<=hi[axis]+1e-8:return None
            continue
        a=(lo[axis]-origin[axis])/direction[axis];b=(hi[axis]-origin[axis])/direction[axis];fa='-'+('XYZ'[axis]);fb='+'+('XYZ'[axis])
        if a>b:a,b,fa,fb=b,a,fb,fa
        if a>near:near,near_face=a,fa
        if b<far:far,far_face=b,fb
    if near>far or far<0:return None
    return near_face if near>=0 else far_face


def draw_volume_front(window,viewport,painter):
    lines=window.conversion_front_lines;corners=lines[:4,0];px,py,ok=viewport.world_to_pixels(corners)
    if not all(ok):return
    painter.save();polygon=QPolygonF([QPointF(float(x),float(y)) for x,y in zip(px,py)])
    painter.setPen(QPen(QColor('#f08019'),4));painter.setBrush(QColor(255,153,35,85));painter.drawPolygon(polygon)
    arrow=lines[-1];ax,ay,good=viewport.world_to_pixels(arrow)
    if all(good):
        a,b=np.array([ax[0],ay[0]]),np.array([ax[1],ay[1]]);delta=b-a;length=np.linalg.norm(delta)
        painter.setPen(QPen(QColor('#d35f00'),5));painter.drawLine(QPointF(*map(float,a)),QPointF(*map(float,b)))
        if length>8:
            unit=delta/length;side=np.array([-unit[1],unit[0]])
            painter.setBrush(QColor('#d35f00'));painter.drawPolygon(QPolygonF([QPointF(*map(float,v)) for v in (b,b-unit*18+side*9,b-unit*18-side*9)]))
    center=QPointF(float(np.mean(px)),float(np.mean(py)));rect=QRectF(center.x()-68,center.y()-20,136,40)
    painter.setPen(Qt.NoPen);painter.setBrush(QColor('#9f4400'));painter.drawRoundedRect(rect,6,6)
    font=painter.font();font.setPixelSize(21);font.setBold(True);painter.setFont(font);painter.setPen(QColor('white'));painter.drawText(rect,Qt.AlignCenter,getattr(window,'conversion_label','櫃體正面'));painter.restore()


CUBOID_FRONTS={'-Y':(1,-1),'+Y':(1,1),'-X':(0,-1),'+X':(0,1),'-Z':(2,-1),'+Z':(2,1)}


def detect_cabinet_volume(group):
    """Validate a closed cuboid, using face planes instead of a world AABB."""
    from core.solids import solid_report
    from core.group import group_frame
    if (group.ext or {}).get(PARAM_KEY):raise ValueError('此物件已是參數模型，請使用對應設定。')
    if any(getattr(n,'hidden',False) or getattr(n,'locked',False) for n in walk_groups(group)):raise ValueError('量體內有隱藏或鎖定物件，請先解除。')
    mesh=world_mesh(group)
    if len(mesh.vertices)>100000:raise ValueError('量體過於複雜；請選擇單一長方體。')
    positions=np.array([xyz(v.position) for v in mesh.vertices])
    if not len(positions):raise ValueError('量體沒有幾何。')
    centered=QMatrix4x4();centered.translate(*map(float,-positions.mean(0)))
    solid,volume=solid_report(transformed_mesh(mesh,centered))
    if not solid:raise ValueError('請選取封閉的立方體／長方體群組，不能有開口或多餘線。')
    axes=[]
    for face in mesh.faces:
        normal=xyz(face.normal());length=np.linalg.norm(normal)
        if length<1e-9:raise ValueError('量體含退化面。')
        normal/=length
        if not any(abs(normal@axis)>1-1e-5 for axis in axes):axes.append(normal)
    if len(axes)!=3 or not np.allclose(np.array(axes)@np.array(axes).T,np.eye(3),atol=2e-4):raise ValueError('目前僅支援長方體；斜切、曲面與不規則量體不能轉換。')
    frame=group_frame(group);reference=np.array(frame.data()).reshape(4,4).T[:3,:3] if frame is not None else np.eye(3)
    def closest(direction,available):
        direction=direction/max(np.linalg.norm(direction),1e-12);index=max(available,key=lambda i:abs(axes[i]@direction));axis=axes[index].copy()
        return index,axis if axis@direction>=0 else -axis
    zi,z=closest(reference[:,2],range(3));xi,x=closest(reference[:,0],[i for i in range(3) if i!=zi]);y=np.cross(z,x);basis=np.stack([x,y,z],axis=1)
    points=np.array([xyz(v.position) for v in mesh.vertices]);local=points@basis;lo=local.min(0);hi=local.max(0);sizes=hi-lo;tolerance=max(2e-6,float(sizes.max())*1e-5)
    if np.any(sizes<1e-5):raise ValueError('量體尺寸不足。')
    for face in mesh.faces:
        coords=np.array([xyz(v.position) for v in face.loop])@basis
        if not any(np.all(np.abs(coords[:,i]-side)<tolerance) for i in range(3) for side in (lo[i],hi[i])):raise ValueError('量體含凹凸或內部面，並非完整長方體。')
    if abs(volume-np.prod(sizes))>max(1e-10,float(np.prod(sizes))*2e-4):raise ValueError('量體體積與長方體不符，請確認沒有孔洞或重疊。')
    return {'points':points,'basis':basis,'sizes':sizes,'volume':volume}


def cabinet_volume_placement(data,front='-Y'):
    if front not in CUBOID_FRONTS:raise ValueError('量體正面方向無效。')
    axis,sign=CUBOID_FRONTS[front];depth=-data['basis'][:,axis]*sign
    up=data['basis'][:,2 if axis!=2 else 1];right=np.cross(depth,up);rotation=np.stack([right,depth,up],axis=1)
    points=data['points']@rotation;lo=points.min(0);hi=points.max(0);size=(hi-lo)*1000.;origin=rotation@lo
    rigid=QMatrix4x4(float(rotation[0,0]),float(rotation[0,1]),float(rotation[0,2]),float(origin[0]),float(rotation[1,0]),float(rotation[1,1]),float(rotation[1,2]),float(origin[1]),float(rotation[2,0]),float(rotation[2,1]),float(rotation[2,2]),float(origin[2]),0,0,0,1)
    return {'width':float(size[0]),'height':float(size[2]),'depth':float(size[1])},rigid


def volume_signature(group):
    return [(n,n.mesh.capture_state(),list(n.xform.data()) if n.xform is not None else None,list(n.axes.data()) if n.axes is not None else None,deepcopy(n.material),deepcopy(n.ext),n.layer,n.hidden,n.locked) for n in walk_groups(group)]


class ConvertVolumeCabinet(UpdateParametric):
    def __init__(self,target,replacement,rigid,signature):
        super().__init__(target,replacement,rigid);self.signature=signature
    def do(self,scene):
        if self.before is None and volume_signature(self.target)!=self.signature:raise ValueError('來源量體已變更，請重新讀取。')
        super().do(scene);self.target.axes=None


class CabinetElevation(QWidget):
    """Read-only front elevation, independent of the scene camera/rotation."""
    def __init__(self):
        super().__init__();self.layout_data=None;self.handles=[];self.error='';self.setMinimumHeight(260)
    def clear_model(self,message='尺寸無效，無法顯示立面預覽。'):
        self.layout_data=None;self.handles=[];self.error=str(message);self.update()
    def set_model(self,model):
        info=parametric_info(model,'cabinet');self.layout_data=cabinet_layout(info['params']);self.handles=[];self.error=''
        for child in model.children or ():
            if child.name.startswith('把手 '):
                parts=child.children if (child.ext or {}).get('lime_cabinet_handle_pair') else [child]
                for part in parts:
                    lo,hi=bounds([part]);self.handles.append({'name':child.name+'｜'+part.name if part is not child else child.name,'rect':[lo[0]*1000,lo[2]*1000,hi[0]*1000,hi[2]*1000]})
        self.update()
    def drawing_geometry(self):
        if self.layout_data is None:return None
        p=self.layout_data['params'];margin_x=68.;margin_y=35.;scale=min(max(1,self.width()-2*margin_x)/p['width'],max(1,self.height()-2*margin_y)/p['height'])
        return scale,(self.width()-p['width']*scale)/2,(self.height()-p['height']*scale)/2
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing);painter.fillRect(self.rect(),QColor('#f5f8fb'))
        if self.layout_data is None:
            painter.setPen(QColor('#9b5140'));painter.drawText(self.rect().adjusted(10,10,-10,-10),Qt.AlignCenter|Qt.TextWordWrap,self.error or '調整尺寸後顯示立面預覽');painter.end();return
        layout=self.layout_data;p=layout['params'];scale,left,top=self.drawing_geometry();w,h=p['width'],p['height']
        def rect(box):
            x,z,x2,z2=box;return QRectF(left+x*scale,top+(h-z2)*scale,(x2-x)*scale,(z2-z)*scale)
        outer=rect([0,0,w,h]);painter.setPen(QPen(QColor('#466b82'),1.7));painter.setBrush(QColor('#cad8e3'));painter.drawRect(outer)
        painter.setBrush(QColor('#f8f4e9'));painter.drawRect(rect([30,30,w-30,h-30]))
        for index,(door,field) in enumerate(layout['fronts'],1):
            painter.setPen(QPen(QColor('#34556a'),1.2));painter.setBrush(QColor('#e0eaf1' if p['face_mode']=='doors' else '#e7edf5'));painter.drawRect(rect(door))
            painter.setPen(QPen(QColor('#92a9b9'),1));painter.setBrush(QColor('#f9fbfc'));painter.drawRect(rect(field))
            if p['door_style']=='kitchen_c':painter.setPen(QPen(QColor('#805b26'),1));painter.setBrush(QColor('#d7ab51'));painter.drawRect(rect([door[0],door[3]-25,door[2],door[3]]))
            if p['door_style']=='original' and p['profile_mode']!='none':painter.setPen(QPen(QColor('#347b70'),2));painter.setBrush(Qt.NoBrush);painter.drawRect(rect(field))
            rectangle=cabinet_rectangle(p)
            if rectangle is not None:painter.setBrush(Qt.NoBrush);painter.drawRect(rect(rectangle_inner(field,rectangle[0])))
            painter.setPen(QColor('#5a7283'));r=rect(door);font=painter.font();font.setPixelSize(11);painter.setFont(font)
            title=('廚具' if p['door_style']=='kitchen_c' else '古典' if p['door_style']=='classical' else '')+('門片' if p['face_mode']=='doors' else '抽屜')+str(index)
            if r.width()<55:title=str(index)
            if r.width()>12 and r.height()>16:painter.drawText(r.adjusted(3,3,-3,-3),Qt.AlignLeft|Qt.AlignTop,title)
        if not layout['fronts']:
            painter.setPen(QColor('#6f7e88'));painter.drawText(outer,Qt.AlignCenter,'開放櫃')
        for handle in self.handles:
            r=rect(handle['rect']);center=r.center();r=QRectF(center.x()-max(3,r.width())/2,center.y()-max(3,r.height())/2,max(3,r.width()),max(3,r.height()))
            painter.setPen(QPen(QColor('#805b26'),1));painter.setBrush(QColor('#d7ab51'));painter.drawRoundedRect(r,2,2)
        painter.setPen(QPen(QColor('#71899a'),1));painter.drawLine(QPointF(left,top-10),QPointF(left+w*scale,top-10));painter.drawLine(QPointF(left-13,top),QPointF(left-13,top+h*scale))
        font=painter.font();font.setPixelSize(12);painter.setFont(font);painter.setPen(QColor('#294f68'))
        painter.drawText(QRectF(left-15,0,w*scale+30,max(18,top-13)),Qt.AlignCenter,f'寬 {w/10:g} cm')
        painter.save();painter.translate(left-31,top+h*scale/2);painter.rotate(-90);painter.drawText(QRectF(-80,-12,160,24),Qt.AlignCenter,f'高 {h/10:g} cm');painter.restore()
        painter.drawText(QRectF(0,top+h*scale+8,self.width(),24),Qt.AlignCenter,f'正面示意｜深 {p["depth"]/10:g} cm｜'+('綠框：線板｜' if p['door_style']=='original' and p['profile_mode']!='none' else '')+'金色：把手')
        painter.end()


class CabinetWindow(GeometryWindow):
    def __init__(self,app,editing=False):
        super().__init__(app,'參數櫃體'+('設定' if editing else '｜新增'),600,870)
        self._front_pick_tool=None;self._conversion_source=None;self._conversion_data=None;self._conversion_signature=None;self.conversion_front_lines=None;self._last_model_snapshot=None
        self.kind='cabinet';self.editing=editing;self.target=None;self.rigid=QMatrix4x4();self.preview_group=None;self.fields={}
        self.body.addWidget(label('所有長度單位 cm。櫃身厚 3 cm；原有門片厚 3 cm，古典門片厚 2 cm。','muted'))
        self.info=label('在目前編輯層級原點建立，之後可移動／旋轉定位。');self.body.addWidget(self.info);self.body.addWidget(button('讀取目前選取櫃體',self.read_target))
        c=card(self.body);self.elevation_toggle=QCheckBox('顯示櫃體立面預覽（可收合）');self.elevation_toggle.setChecked(True);c.addWidget(self.elevation_toggle);self.elevation=CabinetElevation();c.addWidget(self.elevation);self.elevation_toggle.toggled.connect(self.elevation.setVisible)
        c=card(self.body);c.addWidget(label('量體轉參數櫃體'))
        c.addWidget(button('讀取選取量體 → 轉參數櫃體',self.read_volume,'primary'))
        c.addWidget(button('直接點選櫃體正面',self.start_front_pick,'primary'))
        self.volume_front=QComboBox()
        for key in ('-Y','+Y','-X','+X','-Z','+Z'):self.volume_front.addItem('正面：量體軸 '+key,key)
        self.volume_front.setEnabled(False);c.addWidget(self.volume_front);self.volume_front.currentIndexChanged.connect(self.volume_front_changed)
        c.addWidget(label('選取封閉長方體群組／元件，讀入實際尺寸。橘色填面＋「櫃體正面」大字及朝外箭頭表示正面；轉換前可調整門片、抽屜、線板與把手。','muted'))
        self.cancel_volume=button('取消量體轉換',self.cancel_conversion);self.cancel_volume.setEnabled(False);c.addWidget(self.cancel_volume)
        c=card(self.body);c.addWidget(label('門片樣式'));self.door_style=QComboBox();self.door_style.addItem('原有框板門片','original');self.door_style.addItem('古典門片（原型造型）','classical');self.door_style.addItem('廚具門(C把手)','kitchen_c');c.addWidget(self.door_style)
        self.style_note=label('','muted');c.addWidget(self.style_note)
        for title,keys in [('櫃體尺寸',[('width','寬度'),('height','高度'),('depth','總深（含門片）')]),('面板框料',[('left','左框料'),('right','右框料'),('top','上框料'),('bottom','下框料'),('recess','內凹深度')])]:
            c=card(self.body);c.addWidget(label(title));form=QFormLayout();c.addLayout(form)
            for key,title in keys:field=length_number(CABINET_DEFAULTS[key],0);field.setMaximum(20000);form.addRow(title,field);self.fields[key]=field
            if 'left' in dict(keys):
                self.frame_form=form
                for key,title in [('classical_left','左框料寬度'),('classical_right','右框料寬度'),('classical_top','上框料寬度'),('classical_bottom','下框料寬度')]:
                    field=length_number(CABINET_DEFAULTS[key],35);field.setMaximum(20000);form.addRow(title,field);self.fields[key]=field
                    field.setToolTip('從門片外緣量至中央肚板邊界；保留雕線形狀，最小 3.5 cm。')
        c=card(self.body);c.addWidget(label('面板配置'));self.face_mode=QComboBox()
        for title,key in [('等寬門片','doors'),('抽屜面板＋對應隔板','drawers'),('開放櫃（無門片）','open')]:self.face_mode.addItem(title,key)
        c.addWidget(self.face_mode);form=QFormLayout();c.addLayout(form)
        for key,title in [('panels','門片數'),('drawer_columns','抽屜欄數'),('drawer_rows','每欄層數')]:
            field=LockedSpinBox();field.setRange(1,20);field.setValue(CABINET_DEFAULTS[key]);form.addRow(title,field);self.fields[key]=field
        self.heights=LockedNumericText();self.heights.setPlaceholderText('由上往下，例如 20, 30, auto；單位 cm');form.addRow('指定層高（可留空）',self.heights)
        c=card(self.body);c.addWidget(label('內框線板'));self.profile_mode=QComboBox()
        for title,key in [('不加線板','none'),('簡易斜面','bevel'),('階梯線板','step'),('資料庫剖面','library'),('內凹矩形階差','rectangle')]:self.profile_mode.addItem(title,key)
        c.addWidget(self.profile_mode);form=QFormLayout();c.addLayout(form)
        for key,title in [('profile_width','線板寬度'),('profile_height','線板高度'),('rect_width','矩形階差寬度'),('rect_depth','矩形階差深度')]:
            field=length_number(CABINET_DEFAULTS[key],.1 if key.startswith('rect_') else 1);field.setMaximum(2000 if key=='rect_width' else 28 if key=='rect_depth' else 100);form.addRow(title,field);self.fields[key]=field
        self.save_rectangle_button=button('將目前矩形階差存入剖面資料庫',self.save_rectangle_profile);c.addWidget(self.save_rectangle_button)
        c.addWidget(label('矩形預設寬 1 cm、深 0.5 cm；向內形成階差，不凸出門面。總內凹深度至少等於階差深度，必要時自動提高。','muted'))
        c.addWidget(label('可從下方資料庫選用自訂剖面與把手；任意多區分割仍待組合櫃功能接入。','muted'))
        self.summary=label('');self.body.addWidget(self.summary);self.accessories=CabinetAccessories(self);self.body.addStretch()
        footer=QHBoxLayout();footer.setContentsMargins(20,5,20,8);footer.addWidget(button('預覽',self.preview));self.apply_button=button('更新櫃體' if editing else '建立櫃體',self.apply_model,'primary');footer.addWidget(self.apply_button);self.outer.insertLayout(self.outer.count()-1,footer)
        self._refresh_timer=QTimer(self);self._refresh_timer.setSingleShot(True);self._refresh_timer.timeout.connect(self.preview)
        for field in self.fields.values():field.valueChanged.connect(self.changed)
        for combo in (self.face_mode,self.profile_mode,self.door_style):combo.currentIndexChanged.connect(self.changed)
        self.heights.textChanged.connect(self.changed);self.sync_fields()
        self._sync_timer=QTimer(self);self._sync_timer.setInterval(300);self._sync_timer.timeout.connect(self.sync_model_values);self._sync_timer.start()
        if editing:self.read_target()
        else:self.preview()
    def showEvent(self,event):
        super().showEvent(event);self.sync_fields()
    def start_front_pick(self):
        def start():
            self.check();self.ensure_target()
            if self._conversion_source is None:raise ValueError('請先讀取選取量體。')
            self.stop_front_pick(False)
            window=self;from tools.base import Tool
            self._front_pick_old=self.vp.active_tool;self._front_pick_saved=(self.volume_front.currentData(),{k:self.fields[k].value() for k in ('width','height','depth')},QMatrix4x4(self.rigid))
            class FrontPick(Tool):
                name='點選櫃體正面';uses_snap=False
                def on_activate(self,vp):vp.flash_status('直接點選量體正面；Esc 取消。',6000)
                def on_deactivate(self,vp):pass
                def claims_key(self,key,mods):return key==Qt.Key_Escape
                def on_key(self,vp,key,mods):
                    if key==Qt.Key_Escape:window.stop_front_pick(False);return True
                    return False
                def on_cancel(self,vp):window.stop_front_pick(False)
                def choose(self,ctx):
                    window.check();window.ensure_target()
                    if window.vp.pick_group(ctx.screen.x(),ctx.screen.y()) is not window._conversion_source:return False
                    origin,direction=window.vp._pixel_to_ray(ctx.screen.x(),ctx.screen.y())
                    if origin is None or direction is None:return False
                    face=volume_front_from_ray(window._conversion_data,xyz(origin),xyz(direction))
                    if face is None:return False
                    window.volume_front.setCurrentIndex(window.volume_front.findData(face));return True
                def on_hover(self,ctx):
                    try:self.choose(ctx)
                    except Exception as exc:window.message(str(exc),True);window.stop_front_pick(False)
                def on_click(self,ctx):
                    try:
                        if self.choose(ctx):window.stop_front_pick(True);window.message('已指定櫃體正面；橘色填面、標籤與箭頭表示朝外方向。')
                        else:window.message('請直接點已讀取量體上的面。',True)
                    except Exception as exc:window.message(str(exc),True);window.stop_front_pick(False)
            self._front_pick_tool=FrontPick();self.vp.set_active_tool(self._front_pick_tool);self.vp.setFocus();return '請直接點量體正面；滑過可預覽，Esc 取消。'
        self.run(start)
    def stop_front_pick(self,commit=False):
        tool=getattr(self,'_front_pick_tool',None)
        if tool is None:return
        self._front_pick_tool=None
        if self.vp.active_tool is tool:self.vp.set_active_tool(self._front_pick_old)
        if not commit and self._conversion_source is not None:
            key,values,rigid=self._front_pick_saved;self.volume_front.blockSignals(True);self.volume_front.setCurrentIndex(self.volume_front.findData(key));self.volume_front.blockSignals(False)
            for name,value in values.items():self.fields[name].blockSignals(True);self.fields[name].setValue(value);self.fields[name].blockSignals(False)
            self.rigid=rigid;self.preview()

    def read_volume(self):
        self.stop_front_pick(False);self.apply_button.setEnabled(False);self.cancel_volume.setEnabled(True)
        def read():
            self.check();items=selection(self.scene,True)
            if len(items)!=1:raise ValueError('請只選取一個立方體／長方體群組或元件。')
            source=items[0]
            if any(not editable(self.scene,n) for n in walk_groups(source)):raise ValueError('量體或內部圖層已隱藏／鎖定。')
            data=detect_cabinet_volume(source)
            self._conversion_source=source;self._conversion_data=data;self._conversion_signature=volume_signature(source)
            self.editing=False;self.target=None;self._last_model_snapshot=None;self.version=self.scene.content_version
            self.volume_front.setEnabled(True);self.cancel_volume.setEnabled(True);self.apply_button.setEnabled(True)
            self.volume_front_changed();self.apply_button.setText('轉換選取量體');self.info.setText('正在轉換：'+source.name+'；橘色填面與「櫃體正面」標籤表示正面。')
            return '已讀取量體。請選正面及配置，預覽後按「轉換選取量體」；原量體尚未修改。'
        self.run(read)
    def volume_front_changed(self,*_):
        if self._conversion_source is None:return
        values,self.rigid=cabinet_volume_placement(self._conversion_data,self.volume_front.currentData())
        for key,value in values.items():
            field=self.fields[key];field.blockSignals(True);field.setValue(value);field.blockSignals(False)
        self.preview()
    def cancel_conversion(self):
        self.stop_front_pick(False)
        self._conversion_source=None;self._conversion_data=None;self._conversion_signature=None;self.conversion_front_lines=None
        self.volume_front.setEnabled(False);self.cancel_volume.setEnabled(False);self.apply_button.setEnabled(True);self.rigid=QMatrix4x4();self.editing=False;self.target=None
        self.apply_button.setText('建立櫃體');self.info.setText('已取消量體轉換，原量體保留；現在於目前編輯層級原點新建。');self.preview()
    def sync_model_values(self):
        if not self.editing or self.target is None or self._conversion_source is not None:return
        if self.vp.scene is not self.scene or self.scene.edit_group is not self.context:return
        tool=getattr(self.vp,'active_tool',None)
        if tool is not None and (type(tool).__name__=='ScaleTool' or getattr(tool,'name',None)=='Scale'):return
        try:
            if selection(self.scene,True)!=[self.target]:return
            _,params,rigid=effective_parameters(self.target,'cabinet');params=cabinet_layout(params)['params']
            snapshot=(deepcopy(params),list(rigid.data()),self.accessories.floor_mm())
            if self._last_model_snapshot==snapshot:
                if self.version!=self.scene.content_version:self.version=self.scene.content_version;self.preview()
                return
            before=self._last_model_snapshot[0] if self._last_model_snapshot is not None else {}
            self._refresh_timer.stop()
            for key,field in self.fields.items():
                if before.get(key)!=params.get(key):field.blockSignals(True);field.setValue(params[key]);field.blockSignals(False)
            for combo,key in ((self.face_mode,'face_mode'),(self.profile_mode,'profile_mode'),(self.door_style,'door_style')):
                if before.get(key)!=params.get(key):combo.blockSignals(True);combo.setCurrentIndex(combo.findData(params[key]));combo.blockSignals(False)
            if before.get('drawer_heights')!=params.get('drawer_heights'):
                self.heights.blockSignals(True);self.heights.setText(', '.join('auto' if v is None else f'{v/10:g}' for v in params['drawer_heights']));self.heights.blockSignals(False)
            accessory_keys=set(params)-set(self.fields)-{'face_mode','profile_mode','door_style','drawer_heights'}
            if any(before.get(k)!=params.get(k) for k in accessory_keys):self.accessories.load(params)
            self.rigid=rigid;self._last_model_snapshot=snapshot;self.version=self.scene.content_version;self.sync_fields();self.info.setText('正在修改：'+self.target.name+'｜尺寸已自動同步');self.preview()
        except Exception as exc:self.message('自動同步未套用：'+str(exc),True)

    def sync_fields(self):
        classic=self.door_style.currentData()=='classical';kitchen=self.door_style.currentData()=='kitchen_c';original=not classic and not kitchen
        self.style_note.setText('門片厚 2 cm；左、右、上、下框料可獨立調整（各至少 3.5 cm）。加減平直區段，保留內外雕線及全部把手設定。' if classic else '門板厚 2 cm；上緣 C 把手高 2.5 cm，剖面不縮放。門板與 C 把手為獨立子群組，可分別上材質；更新與拉伸後保留。此款使用內建 C 把手，暫停額外把手與內框線板，切回其他門片恢復設定。' if kitchen else '可調整四邊框料、內凹深度及選用線板。')
        for key in ('left','right','top','bottom'):
            for name,active in ((key,original),('classical_'+key,classic)):
                field=self.fields[name];row=field._lock_wrapper if field._lock_wrapper is not None else field
                self.frame_form.setRowVisible(row,active);field.setVisible(active);field.setEnabled(active)
        self.fields['recess'].setEnabled(original)
        special=False
        if self.profile_mode.currentData()=='library' and hasattr(self,'accessories'):
            try:special=(self.accessories.profile.value() or {}).get('kind')=='recess_rectangle'
            except Exception:pass
        rectangular=(self.profile_mode.currentData()=='rectangle' or special) and original
        for key in ('rect_width','rect_depth'):self.fields[key].setEnabled(rectangular and self.face_mode.currentData()!='open')
        self.save_rectangle_button.setEnabled(rectangular and self.face_mode.currentData()!='open')
        if rectangular and self.fields['recess'].value()<self.fields['rect_depth'].value():
            self.fields['recess'].blockSignals(True);self.fields['recess'].setValue(self.fields['rect_depth'].value());self.fields['recess'].blockSignals(False)
        mode=self.face_mode.currentData();self.fields['panels'].setEnabled(mode=='doors')
        for key in ('drawer_columns','drawer_rows'):self.fields[key].setEnabled(mode=='drawers')
        self.heights.setEnabled(mode=='drawers')
        for key in ('profile_width','profile_height'):self.fields[key].setEnabled(self.profile_mode.currentData() in ('bevel','step') and mode!='open' and original)
        self.profile_mode.setEnabled(mode!='open' and original)
        if hasattr(self,'accessories'):
            self.accessories.profile.setEnabled(original and mode!='open');self.accessories.sync_fields()
    def save_rectangle_profile(self):
        def save():
            self.check();width=self.fields['rect_width'].value();depth=self.fields['rect_depth'].value()
            if not .1<=width<=2000 or not .1<=depth<=28:raise ValueError('矩形階差尺寸無效。')
            name,ok=QInputDialog.getText(self,'儲存內凹矩形剖面','剖面名稱',text=f'內凹矩形 {width/10:g}×{depth/10:g} cm')
            if not ok:return '已取消儲存；資料庫與模型未變更。'
            record=store_for(self.app).add_profile(name,[[0,0],[width,0],[width,depth],[0,depth]],kind='recess_rectangle')
            self.accessories.profile.load(profile_snapshot(record));return '已存入剖面資料庫；可重複選用，模型未修改。'
        self.run(save)
    def changed(self,*_):self.sync_fields();self._refresh_timer.start(300)
    def parameters(self):
        p={k:f.value() for k,f in self.fields.items()};p['face_mode']=self.face_mode.currentData();p['door_style']=self.door_style.currentData();p['profile_mode']=self.profile_mode.currentData();p['drawer_heights']=[]
        if p['face_mode']=='drawers' and self.heights.text().strip():
            for value in self.heights.text().replace('，',',').split(','):
                value=value.strip().lower();p['drawer_heights'].append(None if value in ('','auto') else float(value)*10)
        p.update(self.accessories.parameters(p));return p
    def read_target(self):
        def read():
            self.check();items=selection(self.scene,True)
            if len(items)!=1:raise ValueError('請只選取整個參數櫃體。')
            _,values,rigid=effective_parameters(items[0],'cabinet');values=cabinet_layout(values)['params'];self.apply_button.setEnabled(True);self._conversion_source=None;self._conversion_data=None;self.conversion_front_lines=None;self.volume_front.setEnabled(False);self.cancel_volume.setEnabled(False);self._last_model_snapshot=(deepcopy(cabinet_layout(values)['params']),list(rigid.data()),self.accessories.floor_mm());self.target=items[0];self.rigid=rigid;self.editing=True;self.version=self.scene.content_version
            for key,f in self.fields.items():f.blockSignals(True);f.setValue(values[key]);f.blockSignals(False)
            for combo,key in ((self.face_mode,'face_mode'),(self.profile_mode,'profile_mode'),(self.door_style,'door_style')):combo.blockSignals(True);combo.setCurrentIndex(combo.findData(values.get(key,CABINET_DEFAULTS.get(key))));combo.blockSignals(False)
            self.heights.blockSignals(True);self.heights.setText(', '.join('auto' if v is None else f'{v/10:g}' for v in values['drawer_heights']));self.heights.blockSignals(False)
            self.accessories.load(values);self.sync_fields();self.apply_button.setText('更新櫃體');self.info.setText('正在修改：'+self.target.name);self.preview();return '已讀取櫃體。'
        self.run(read)
    def ensure_target(self):
        if self._conversion_source is not None:
            if selection(self.scene,True)!=[self._conversion_source]:raise ValueError('量體選取已變更，請重新讀取。')
            if volume_signature(self._conversion_source)!=self._conversion_signature:raise ValueError('來源量體已變更，請重新讀取。')
            if any(not editable(self.scene,n) for n in walk_groups(self._conversion_source)):raise ValueError('量體或內部圖層已隱藏／鎖定。')
        if self.editing and (self.target is None or selection(self.scene,True)!=[self.target]):raise ValueError('選取已變更，請重新讀取櫃體。')
    def preview(self):
        try:
            self.check(strict=self.editing);self.ensure_target();layout=cabinet_layout(self.parameters());model=cabinet_model(layout['params']);model.xform=QMatrix4x4(self.rigid);self.preview_group=model;self.preview_lines=parametric_preview(model);self.version=self.scene.content_version;self.accessories.show_floor_heights(model);self.elevation.set_model(model)
            self.summary.setText(('櫃身厚 3 cm｜廚具門板厚 2 cm｜C 把手高 2.5 cm｜' if layout['params']['door_style']=='kitchen_c' else '櫃身厚 3 cm｜古典門片厚 2 cm｜' if layout['params']['door_style']=='classical' else '板厚 3 cm｜')+f'面板 {len(layout["fronts"])} 片｜抽屜隔板／層板 {len(layout["drawer_boards"])} 片\n門片各邊距 0.2 cm，相鄰門片縫寬 0.4 cm；接縫退入 2 cm。'+(f'\n開放櫃沿用前方退入基準，實體櫃深 {(layout["params"]["depth"]-20)/10:g} cm。' if layout['params']['face_mode']=='open' else ''));self.message('預覽完成，按建立／更新才寫入。縮放與設定請選取整個櫃體。')
        except Exception as exc:self.preview_group=None;self.preview_lines=None;self.elevation.clear_model(str(exc));self.message(str(exc),True)
        self.conversion_front_lines=None
        if self._conversion_source is not None and self.preview_group is not None:
            values=self.parameters();w,h=values['width']/1000,values['height']/1000
            corners=np.array([[0,-.001,0],[w,-.001,0],[w,-.001,h],[0,-.001,h]])
            segments=list(zip(corners,np.roll(corners,-1,axis=0)));center=np.array([w/2,-.001,h/2]);segments.append((center,center+[0,-min(.1,w*.25),0]))
            self.conversion_front_lines=np.array([[xyz(self.rigid.map(QVector3D(*map(float,p)))) for p in line] for line in segments])
        self.vp.update()
    def apply_model(self):
        def apply():
            self.stop_front_pick(True);self.sync_model_values();self.check(strict=self.editing);self.ensure_target();model=cabinet_model(self.parameters())
            if self._conversion_source is not None:
                source=self._conversion_source;execute(self.vp,ConvertVolumeCabinet(source,model,self.rigid,self._conversion_signature))
                self.target=source;self.editing=True;self._conversion_source=None;self._conversion_data=None;self.conversion_front_lines=None;self.volume_front.setEnabled(False);self.cancel_volume.setEnabled(False)
                self.apply_button.setText('更新櫃體');self.info.setText('正在修改：'+source.name)
            elif self.editing:
                _,_,rigid=effective_parameters(self.target,'cabinet');execute(self.vp,UpdateParametric(self.target,model,rigid))
            else:execute(self.vp,AddGenerated(model));self.target=model;self.editing=True;self.apply_button.setText('更新櫃體');self.info.setText('正在修改：'+model.name)
            _,params,self.rigid=effective_parameters(self.target,'cabinet');self._last_model_snapshot=(deepcopy(params),list(self.rigid.data()),self.accessories.floor_mm());self.accessories.show_floor_heights(self.target,force=True);self.elevation.set_model(self.target)
            self.setWindowTitle('參數櫃體設定｜IngeTrazo '+VERSION);self.body.itemAt(0).widget().setText('參數櫃體｜設定')
            self.version=self.scene.content_version;self.preview_group=None;self.preview_lines=None;return '已套用櫃體，Ctrl＋Z 可復原。'
        self.run(apply)
    def closeEvent(self,event):self.stop_front_pick(False);self._sync_timer.stop();self._refresh_timer.stop();super().closeEvent(event)
    def done(self,result):self.stop_front_pick(False);self._sync_timer.stop();self._refresh_timer.stop();super().done(result)

# ---- Persistent accessory library and self-contained model snapshots ----
import os,hashlib,base64,tempfile,uuid
from PySide6.QtCore import QStandardPaths,QLockFile,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog,QMessageBox,QInputDialog,QTabWidget

ASSET_MAX_BYTES=32*1024*1024
ANCHORS={'top_left':(0,1),'top_center':(.5,1),'top_right':(1,1),
         'center_left':(0,.5),'center':(.5,.5),'center_right':(1,.5),
         'bottom_left':(0,0),'bottom_center':(.5,0),'bottom_right':(1,0)}


def asset_name(value):
    value=str(value).strip()
    if not value or len(value)>80 or any(ord(c)<32 for c in value):raise ValueError('名稱須為 1～80 字，且不可包含換行或控制字元。')
    return value


def normalize_section(points):
    a=np.asarray(points,dtype=float)
    if a.ndim==1 and len(a)%2==0:a=a.reshape(-1,2)
    if a.ndim!=2 or a.shape[1]!=2 or not 3<=len(a)<=2048 or not np.isfinite(a).all():raise ValueError('剖面需有 3～2048 個有限座標點。')
    if np.linalg.norm(a[0]-a[-1])<1e-6:a=a[:-1]
    a=a-np.min(a,axis=0);a=np.round(a,6)
    for _ in range(len(a)):
        before=np.roll(a,1,axis=0);after=np.roll(a,-1,axis=0);u=a-before;v=after-a
        cross=u[:,0]*v[:,1]-u[:,1]*v[:,0];remove=(np.linalg.norm(u,axis=1)<1e-6)|((abs(cross)<1e-9)&(np.sum(u*v,axis=1)>=0))
        if not remove.any() or len(a)<=3:break
        a=np.delete(a,int(np.flatnonzero(remove)[0]),axis=0)
    area=float(np.sum(a[:,0]*np.roll(a[:,1],-1)-a[:,1]*np.roll(a[:,0],-1))/2)
    if len(a)<3 or np.min(np.ptp(a,axis=0))<.01 or abs(area)<1e-5:raise ValueError('剖面沒有有效寬、高或面積。')
    # Reject crossed/touching non-adjacent segments instead of silently filling them.
    def orient(a,b,c):
        u,v=b-a,c-a;return float(u[0]*v[1]-u[1]*v[0])
    ends=np.roll(a,-1,axis=0);minimums=np.minimum(a,ends);maximums=np.maximum(a,ends)
    for i in range(len(a)):
        x,y=a[i],ends[i]
        candidates=np.flatnonzero(np.all(np.maximum(np.minimum(x,y),minimums)<=np.minimum(np.maximum(x,y),maximums)+1e-7,axis=1))
        for j in candidates:
            if j<=i+1 or (i==0 and j==len(a)-1):continue
            v,w=a[j],ends[j];o=[orient(x,y,v),orient(x,y,w),orient(v,w,x),orient(v,w,y)]
            if o[0]*o[1]<=1e-12 and o[2]*o[3]<=1e-12:raise ValueError('剖面有交叉或重疊邊，請先整理為單一封閉輪廓。')
    if area<0:a=a[::-1]
    minimum=min(map(tuple,a));candidates=[np.roll(a,-i,axis=0) for i in range(len(a)) if tuple(a[i])==minimum]
    return min(candidates,key=lambda v:tuple(v.ravel())).tolist()


def placement(value=None):
    p={'anchor':'bottom_left','mirror_x':False,'mirror_y':False,'rotation':0.};p.update(value or {})
    if p['anchor'] not in ANCHORS or not isinstance(p['mirror_x'],bool) or not isinstance(p['mirror_y'],bool):raise ValueError('剖面基準點或鏡像設定無效。')
    p['rotation']=float(p['rotation'])
    if not math.isfinite(p['rotation']):raise ValueError('剖面旋轉角度無效。')
    p['rotation']%=360
    return {k:p[k] for k in ('anchor','mirror_x','mirror_y','rotation')}


def rectangle_section_size(points):
    a=np.asarray(normalize_section(points));size=a.max(0)
    expected=np.array([[0,0],[size[0],0],[size[0],size[1]],[0,size[1]]])
    if len(a)!=4 or not all(np.min(np.linalg.norm(a-v,axis=1))<1e-5 for v in expected):raise ValueError('內凹矩形剖面必須是長方形。')
    return size


def profile_snapshot(record):
    result={'id':str(record.get('id','')),'name':asset_name(record['name']),
            'points_mm':normalize_section(record['points_mm']),'placement':placement(record.get('placement'))}
    if record.get('kind')=='recess_rectangle':rectangle_section_size(result['points_mm']);result['kind']='recess_rectangle'
    return result


def section_points(record):
    if not isinstance(record,dict):raise ValueError('剖面快照不完整，請重新選取。')
    r=profile_snapshot(record);a=np.array(r['points_mm']);p=r['placement']
    if p['mirror_x']:a[:,0]*=-1
    if p['mirror_y']:a[:,1]*=-1
    theta=math.radians(p['rotation']);c,s=math.cos(theta),math.sin(theta);a=a@np.array([[c,s],[-s,c]])
    a-=a.min(axis=0);a-=a.max(axis=0)*np.array(ANCHORS[p['anchor']])
    return a


def section_fingerprint(points):return hashlib.sha256(json.dumps(normalize_section(points),separators=(',',':')).encode()).hexdigest()


def _asset_id(value,prefix):
    if not isinstance(value,str) or not re.fullmatch(prefix+r'_[a-f0-9]{24}',value):raise ValueError('配件識別碼無效。')
    return value


def _atomic_json(path,data):
    raw=json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
    path.parent.mkdir(parents=True,exist_ok=True);fd,name=tempfile.mkstemp(prefix='.lime_',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        if json.loads(Path(name).read_text(encoding='utf-8'))!=data:raise ValueError('資料寫入核對失敗。')
        os.replace(name,path)
    finally:
        if Path(name).exists():Path(name).unlink()


class AccessoryStore:
    def __init__(self,directory=None):
        self.directory=Path(directory) if directory is not None else Path(QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation))/'LimeTools'/'IngeTrazoAccessories'
        self.path=self.directory/'catalog.json'
    def load(self):
        if not self.path.exists():return {'schema':2,'units':'mm','profiles':[],'handles':[]}
        try:
            raw=self.path.read_bytes();data=json.loads(raw)
            if data.get('schema')!=2 or data.get('units')!='mm':raise ValueError('不支援的資料格式。')
            seen=set()
            for record in data['profiles']:
                _asset_id(record['id'],'p');profile_snapshot(record)
                if record['id'] in seen:raise ValueError('重複的配件識別碼。')
                seen.add(record['id'])
            for record in data['handles']:
                _asset_id(record['id'],'h');asset_name(record['name'])
                if not re.fullmatch('[a-f0-9]{64}',record['sha256']) or record['id'] in seen:raise ValueError('把手紀錄無效。')
                if len(record['size_mm'])!=3 or not np.isfinite(record['size_mm']).all() or np.any(np.asarray(record['size_mm'])<0):raise ValueError('把手尺寸無效。')
                seen.add(record['id'])
            return data
        except Exception as exc:raise ValueError('配件資料庫無法讀取，原資料未覆寫：'+str(exc)) from exc
    def commit(self,data,token):
        self.directory.mkdir(parents=True,exist_ok=True);lock=QLockFile(str(self.path)+'.lock');lock.setStaleLockTime(30000)
        if not lock.tryLock(1000):raise ValueError('配件資料庫正在使用，請稍後重試。')
        try:
            current=self.path.read_bytes() if self.path.exists() else None
            if current!=token:raise ValueError('資料庫已變更，請重新整理後再試。')
            if current is not None:
                old=json.loads(current);_atomic_json(self.path.with_suffix('.json.bak'),old)
            _atomic_json(self.path,data)
        finally:lock.unlock()
    def begin(self):
        token=self.path.read_bytes() if self.path.exists() else None;data=self.load()
        if token!=(self.path.read_bytes() if self.path.exists() else None):raise ValueError('資料庫已變更，請重新整理後再試。')
        return data,token
    def add_profile(self,name,points,config=None,kind=None):
        name=asset_name(name);points=normalize_section(points);config=placement(config);fp=section_fingerprint(points)
        if kind not in (None,'recess_rectangle'):raise ValueError('剖面種類無效。')
        if kind=='recess_rectangle':rectangle_section_size(points)
        data,token=self.begin()
        for r in data['profiles']:
            if section_fingerprint(r['points_mm'])==fp and placement(r.get('placement'))==config and r.get('kind')==kind:return deepcopy(r)
            if r['name'].casefold()==name.casefold():raise ValueError('已有同名但不同形狀的剖面，請改用其他名稱。')
        r={'id':'p_'+uuid.uuid4().hex[:24],'name':name,'points_mm':points,'placement':config}
        if kind is not None:r['kind']=kind
        data['profiles'].append(r);self.commit(data,token);return deepcopy(r)
    def add_handle(self,name,group,front='-Y',mount='center'):
        name=asset_name(name);data,token=self.begin()
        if any(r['name'].casefold()==name.casefold() for r in data['handles']):raise ValueError('已有同名把手。')
        group=normalize_handle(group,front,mount);payload=group_igz_bytes(group)
        if len(payload)>ASSET_MAX_BYTES:raise ValueError('把手資料過大，請先簡化模型或貼圖。')
        identity='h_'+uuid.uuid4().hex[:24];lo,hi=bounds([group]);r={'id':identity,'name':name,'sha256':hashlib.sha256(payload).hexdigest(),'size_mm':((hi-lo)*1000).tolist(),'front':front,'mount':mount}
        self.directory.mkdir(parents=True,exist_ok=True);path=self.directory/(identity+'.igz')
        try:path.write_bytes(payload);data['handles'].append(r);self.commit(data,token)
        except Exception:
            path.unlink(missing_ok=True);raise
        return deepcopy(r)
    def snapshot_handle(self,record):
        identity=_asset_id(record['id'],'h');path=self.directory/(identity+'.igz')
        if not path.is_file() or path.stat().st_size>ASSET_MAX_BYTES:raise ValueError('把手檔案遺失或過大，請重新收錄。')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=record['sha256']:raise ValueError('把手檔案核對不符，請重新收錄。')
        return {**deepcopy(record),'blob_b64':base64.b64encode(raw).decode('ascii')}
    def update(self,kind,identity,name,config=None):
        if kind not in ('profiles','handles'):raise ValueError('配件種類無效。')
        name=asset_name(name);data,token=self.begin();row=next((r for r in data[kind] if r['id']==identity),None)
        if row is None:raise ValueError('找不到此配件。')
        if any(r['id']!=identity and r['name'].casefold()==name.casefold() for r in data[kind]):raise ValueError('名稱已被使用。')
        row['name']=name
        if kind=='profiles':row['placement']=placement(config)
        self.commit(data,token);return deepcopy(row)
    def delete(self,kind,identity):
        if kind not in ('profiles','handles'):raise ValueError('配件種類無效。')
        data,token=self.begin();old=len(data[kind]);data[kind]=[r for r in data[kind] if r['id']!=identity]
        if len(data[kind])==old:raise ValueError('找不到此配件。')
        # Keep asset bytes for the catalog backup; placed models also hold snapshots.
        self.commit(data,token)
    def export(self,path):
        data=self.load();data['handles']=[self.snapshot_handle(r) for r in data['handles']];data['format']='LimeAccessoryBackup';_atomic_json(Path(path),data)
    def import_json(self,path):
        path=Path(path)
        if path.stat().st_size>128*1024*1024:raise ValueError('備份檔過大，請分批匯入。')
        incoming=json.loads(path.read_text(encoding='utf-8-sig'));data,token=self.begin();new_files=[];added=0
        if not isinstance(incoming,dict):raise ValueError('配件備份格式無效。')
        if incoming.get('schema')==1:
            records=[{'id':'p_'+uuid.uuid4().hex[:24],'name':r['name'],'points_mm':(np.array(r['points'],float).reshape(-1,2)*25.4).tolist(),'placement':r.get('placement')} for r in incoming['profiles']];handle_rows=[]
        elif incoming.get('schema')==2 and incoming.get('units')=='mm':records=incoming['profiles'];handle_rows=incoming.get('handles',[])
        else:raise ValueError('不支援的配件備份格式。')
        for row in records:
            r=profile_snapshot(row);r['id']=_asset_id(r['id'],'p');fingerprint=section_fingerprint(r['points_mm'])
            if any(section_fingerprint(old['points_mm'])==fingerprint and placement(old.get('placement'))==r['placement'] and old.get('kind')==r.get('kind') for old in data['profiles']):continue
            if any(old['name'].casefold()==r['name'].casefold() or old['id']==r['id'] for old in data['profiles']):raise ValueError('剖面名稱或識別碼衝突，原資料未變更。')
            data['profiles'].append(r);added+=1
        pending=[]
        for row in handle_rows:
            raw=handle_bytes(row);identity=_asset_id(row['id'],'h');name=asset_name(row['name'])
            if any(r['id']==identity and r['sha256']==row['sha256'] for r in data['handles']):continue
            if any(r['id']==identity or r['name'].casefold()==name.casefold() for r in data['handles']):raise ValueError('把手名稱或識別碼衝突，原資料未變更。')
            # Parse before committing any catalog changes or writing asset files.
            handle_from_snapshot(row)
            r={k:deepcopy(v) for k,v in row.items() if k!='blob_b64'};r['name']=name;data['handles'].append(r);pending.append((identity,raw));added+=1
        try:
            self.directory.mkdir(parents=True,exist_ok=True)
            for identity,raw in pending:
                target=self.directory/(identity+'.igz')
                if target.exists():
                    if target.read_bytes()!=raw:raise ValueError('把手檔案名稱衝突，未覆寫既有檔案。')
                else:target.write_bytes(raw);new_files.append(target)
            self.commit(data,token)
        except Exception:
            for target in new_files:target.unlink(missing_ok=True)
            raise
        return added


def store_for(app):
    return AccessoryStore(getattr(app.window,'_lime_asset_directory',None))


def profile_from_group(group):
    faces=[]
    for node,matrix in iter_placements(group):
        for face in node.mesh.faces:
            if face.hole_loops:raise ValueError('剖面不能包含孔洞。')
            pts=np.array([xyz(matrix.map(v.position) if matrix is not None else v.position) for v in face.loop]);faces.append(pts)
    if not faces:raise ValueError('請提供一個封閉的平面剖面面。')
    unique=[]
    for pts in faces:
        key=frozenset(tuple(np.round(p,6)) for p in pts)
        if not any(key==old for old,_ in unique):unique.append((key,pts))
    if len(unique)!=1:raise ValueError('檔案或選取含多個面，請整理為單一封閉平面剖面。')
    pts=unique[0][1];origin=pts[0];_,_,axes=np.linalg.svd(pts-origin,full_matrices=False);normal=axes[-1]
    if np.max(abs((pts-origin)@normal))>1e-6:raise ValueError('剖面不在同一平面。')
    up=np.array([0.,1.,0.]) if abs(normal[2])>.9999 else np.array([0.,0.,1.]);v=up-normal*(up@normal);v/=np.linalg.norm(v);u=np.cross(v,normal)
    if u[int(np.argmax(abs(u[:2])))]<0:u=-u
    return normalize_section(np.column_stack(((pts-origin)@u,(pts-origin)@v))*1000)


def selected_profile(scene):
    items=selection(scene)
    if len(items)!=1:raise ValueError('請只選取一個剖面面或只含該面的群組。')
    item=items[0]
    if isinstance(item,Group):return profile_from_group(item)
    if isinstance(item,Face):
        g=Group();g.mesh=Mesh();face=g.mesh.add_face([v.position for v in item.loop],[[v.position for v in h] for h in item.hole_loops] or None);return profile_from_group(g)
    raise ValueError('請選取封閉剖面面，不能只選一條線。')


def read_asset_group(path):
    from core.scene import Scene
    path=Path(path);scene=Scene()
    if path.suffix.lower()=='.igz':
        from formats import igz
        igz.load_into(scene,path)
    elif path.suffix.lower()=='.skp':
        from formats.skp import load_skp
        try:load_skp(scene,path)
        except Exception as exc:raise ValueError('SKP 匯入失敗，請確認本機 IngeTrazo 可讀取此檔，或改用 .igz：'+str(exc)) from exc
    else:raise ValueError('請使用 .igz 或 .skp 檔案。')
    root=Group(name=path.stem);root.adopt([Group(scene.mesh,name='檔案幾何'),*scene.groups])
    if not points_of([root]).size:raise ValueError('檔案沒有可用幾何。')
    return root


def normalize_handle(group,front='-Y',mount='center'):
    if mount not in ('center','origin'):raise ValueError('把手安裝基準無效。')
    bases={'-Y':[[1,0,0],[0,1,0],[0,0,1]],'+Y':[[-1,0,0],[0,-1,0],[0,0,1]],
           '+Z':[[1,0,0],[0,0,1],[0,-1,0]],'-Z':[[1,0,0],[0,0,-1],[0,1,0]],
           '+X':[[0,-1,0],[1,0,0],[0,0,1]],'-X':[[0,1,0],[-1,0,0],[0,0,1]]}
    if front not in bases:raise ValueError('把手正面方向無效。')
    root=clone_tree(group)
    if sum(len(n.mesh.vertices) for n in walk_groups(root))>100000:raise ValueError('把手超過十萬頂點，請先簡化。')
    old=QMatrix4x4(root.xform) if root.xform is not None else QMatrix4x4();old.setColumn(3,__import__('PySide6.QtGui',fromlist=['QVector4D']).QVector4D(0,0,0,1))
    b=np.array(bases[front],float).T;orientation=QMatrix4x4(float(b[0,0]),float(b[0,1]),float(b[0,2]),0,float(b[1,0]),float(b[1,1]),float(b[1,2]),0,float(b[2,0]),float(b[2,1]),float(b[2,2]),0,0,0,0,1)
    root.xform=orientation*old;lo,hi=bounds([root]);anchor=np.array([(lo[0]+hi[0])/2,hi[1],(lo[2]+hi[2])/2]) if mount=='center' else np.zeros(3)
    if min(hi[0]-lo[0],hi[2]-lo[2])<1e-5:raise ValueError('把手寬高不足，請檢查正面方向。')
    move=QMatrix4x4();move.translate(*map(float,-anchor));root.xform=move*root.xform
    return bind_face_materials(root)


def group_igz_bytes(group):
    from core.scene import Scene
    from formats import igz
    scene=Scene();scene.groups=[group]
    with tempfile.TemporaryDirectory(prefix='lime_handle_') as folder:
        path=Path(folder)/'handle.igz';igz.save_scene(scene,path);return path.read_bytes()


def handle_bytes(record):
    if not isinstance(record,dict) or 'blob_b64' not in record:raise ValueError('把手快照不完整，請重新選取。')
    if len(record['blob_b64'])>ASSET_MAX_BYTES*1.4:raise ValueError('把手快照過大。')
    try:raw=base64.b64decode(record['blob_b64'],validate=True)
    except Exception as exc:raise ValueError('把手快照無效。') from exc
    if not raw or len(raw)>ASSET_MAX_BYTES or hashlib.sha256(raw).hexdigest()!=record.get('sha256'):raise ValueError('把手快照核對不符。')
    _asset_id(record.get('id'),'h');asset_name(record.get('name',''))
    size=np.asarray(record.get('size_mm',[]),float)
    if size.shape!=(3,) or not np.isfinite(size).all() or np.any(size<0):raise ValueError('把手尺寸紀錄無效。')
    import zipfile,io
    try:
        if raw.startswith(b'PK\x03\x04'):
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if sum(info.file_size for info in archive.infolist())>256*1024*1024:raise ValueError('把手解壓後資料過大。')
        else:
            document=json.loads(raw)
            if not isinstance(document,dict) or 'scene' not in document:raise ValueError('把手快照不是有效的模型檔。')
    except zipfile.BadZipFile as exc:raise ValueError('把手快照不是有效的模型檔。') from exc
    return raw


@lru_cache(maxsize=8)
def _decoded_handle(digest,blob):
    from core.scene import Scene
    from formats import igz
    scene=Scene()
    with tempfile.TemporaryDirectory(prefix='lime_handle_read_') as folder:
        path=Path(folder)/'handle.igz';path.write_bytes(base64.b64decode(blob));igz.load_into(scene,path)
    root=Group(name='把手');root.adopt([Group(scene.mesh),*scene.groups]);points_of([root]);return root


def handle_from_snapshot(record):
    handle_bytes(record);asset_name(record['name']);group=clone_tree(_decoded_handle(record['sha256'],record['blob_b64']));lo,hi=bounds([group])
    if not np.allclose((hi-lo)*1000,record['size_mm'],atol=.05,rtol=1e-6):raise ValueError('把手快照尺寸與紀錄不符。')
    return group


def validate_accessory_parameters(p):
    for key in ('profile_snapshot','leaf_profile_snapshot','frame_profile_snapshot'):
        if p.get(key):section_points(p[key])
    if p.get('handle_asset'):handle_bytes(p['handle_asset']);asset_name(p['handle_asset']['name'])
    for key in ('leaf_profile_side','frame_profile_side','handle_side'):
        if key in p and p[key] not in ('front','back','both'):raise ValueError('正反面設定無效。')
    for key,options in [('handle_mount',('manual','rail_top','rail_middle','rail_bottom')),('handle_rail_x',('left','center','right')),('handle_reference',('left','right','keep'))]:
        if key in p and p[key] not in options:raise ValueError('把手安裝方式或方向基準無效。')
    if 'handle_edge' in p and p['handle_edge'] not in ('left','right'):raise ValueError('把手左右位置無效。')
    if 'handle_position' in p and not re.fullmatch('(top|center|bottom)_(left|center|right)',p['handle_position']):raise ValueError('把手位置無效。')
    for key in ('handle_x','handle_z','handle_height','handle_margin','handle_angle','handle_stand'):
        if key in p:
            p[key]=float(p[key])
            if not math.isfinite(p[key]) or (key in ('handle_x','handle_z','handle_margin') and p[key]<0):raise ValueError('把手尺寸無效，邊距不得為負。')
    if 'handle_pair' in p and not isinstance(p['handle_pair'],bool):raise ValueError('雙門把手配對設定無效。')


def sweep_section(rect,section,depth,name,back=False,u_frame=False):
    section=np.asarray(section,float);x,z,x2,z2=rect
    if not np.isfinite(section).all() or len(section)<3:raise ValueError('剖面設定無效。')
    if not u_frame and 2*section[:,0].max()>=min(x2-x,z2-z)-.01:raise ValueError('剖面寬度超過內框一半。')
    rings=[]
    for s,h in section:
        y=depth+h if back else depth-h
        if u_frame:ring=[[x-s,y,z],[x-s,y,z2+s],[x2+s,y,z2+s],[x2+s,y,z]]
        else:ring=[[x+s,y,z+s],[x2-s,y,z+s],[x2-s,y,z2-s],[x+s,y,z2-s]]
        rings.append(np.array(ring)/1000)
    polys=[]
    for i,a in enumerate(rings):
        b=rings[(i+1)%len(rings)]
        for k in range(3 if u_frame else 4):polys.append([a[k],a[(k+1)%4],b[(k+1)%4],b[k]])
    if u_frame:polys.extend([[r[0] for r in rings],list(reversed([r[3] for r in rings]))])
    volume=sum(np.dot(poly[0],np.cross(poly[j],poly[j+1])) for poly in polys for j in range(1,len(poly)-1))/6
    if volume<0:polys=[list(reversed(poly)) for poly in polys]
    group=Group(name=name)
    for poly in polys:group.mesh.add_face([QVector3D(*map(float,v)) for v in poly])
    from core.solids import solid_report
    if not solid_report(group.mesh)[0]:raise ValueError('剖面生成的線板無法閉合，請檢查剖面與基準點。')
    stamp_surface_keys(group);return group


def placed_handle(snapshot,x,y,z,angle=0,back=False,name='把手',flip_x=False):
    group=handle_from_snapshot(snapshot);local=QMatrix4x4(group.xform or QMatrix4x4());move=QMatrix4x4();move.translate(x/1000,y/1000,z/1000)
    mirror=QMatrix4x4()
    if back:mirror.scale(1,-1,1)
    rotate=QMatrix4x4();rotate.rotate(-float(angle),0,1,0);handing=QMatrix4x4()
    if flip_x:handing.scale(-1,1,1)
    if flip_x or back:
        # Reflect only depth for the back face. Bake all reflections and repair
        # outward winding without exchanging front/back material recipes.
        def bake(node,parent):
            matrix=parent*(node.xform if node.xform is not None else QMatrix4x4())
            node.mesh=transformed_mesh(node.mesh,matrix)
            if matrix.determinant()<0:
                for face in node.mesh.faces:
                    face.loop.reverse()
                    for hole in face.hole_loops:hole.reverse()
            node.xform=QMatrix4x4()
            for child in node.children or ():bake(child,matrix)
        group.xform=local;bake(group,mirror*handing*rotate);group.xform=move
    else:group.xform=move*rotate*local
    group.name=name;group.ext={**(group.ext or {}),'lime_handle_id':snapshot['id'],'lime_handle_flip_x':bool(flip_x)}
    return group


def cabinet_handle_locations(layout):
    p=layout['params'];row,col=p['handle_position'].split('_');result=[]
    if p.get('door_style')=='kitchen_c':return result
    for i,(rect,field) in enumerate(layout['fronts']):
        current=col
        if p['handle_count']==1 and p.get('handle_pair') and p['face_mode']=='doors' and len(layout['fronts'])==2:current='right' if i==0 else 'left'
        x=rect[0]+p['handle_x'] if current=='left' else rect[2]-p['handle_x'] if current=='right' else (rect[0]+rect[2])/2
        if p['handle_count']==2:x=(rect[0]+rect[2])/2
        z=rect[1]+p['handle_z'] if row=='bottom' else rect[3]-p['handle_z'] if row=='top' else (rect[1]+rect[3])/2
        z=p.get('handle_z_overrides',{}).get(str(i+1),z)
        if not rect[0]<=x<=rect[2] or not rect[1]<=z<=rect[3]:raise ValueError(f'把手 {i+1:02d} 安裝點超出面板，請調整離地高度或邊距。')
        y=cabinet_front_depth(p,rect,field,x,z)
        result.append((str(i+1),x,y-p['handle_stand'],z,rect,field))
    return result


def cabinet_handle_floor_heights(model,floor_mm=0.):
    matrix=model.xform if model.xform is not None else QMatrix4x4();result={}
    for child in model.children or ():
        if not child.name.startswith('把手 '):continue
        copy=clone_tree(child);copy.xform=matrix*(copy.xform if copy.xform is not None else QMatrix4x4())
        lo,hi=bounds([copy]);result[str(int(child.name.split()[-1]))]=float((lo[2]+hi[2])*500-floor_mm)
    return result


def set_cabinet_handle_floor_height(params,rigid,floor_mm,height_mm,target='all'):
    """Convert a world bbox-centre height into persistent cabinet-local Z.

    The stored offset follows the cabinet when moved; the floor readout is
    measured again, rather than freezing the cabinet handle in world space.
    """
    if not math.isfinite(float(height_mm)) or not math.isfinite(float(floor_mm)):raise ValueError('把手離地高度必須是有限數值。')
    layout=cabinet_layout(params);p=layout['params'];asset=p.get('handle_asset')
    if not asset or not layout['fronts']:raise ValueError('請先選用把手，且櫃體須有門片或抽屜面板。')
    matrix=np.array(rigid.data()).reshape(4,4).T;row=matrix[2,:3]
    if abs(row[2])<1e-5:raise ValueError('櫃體高度軸接近水平，無法只調整垂直位置來設定離地高度。')
    points=points_of([placed_handle(asset,0,0,0,p['handle_angle'])]);projected=points@row
    base=float((projected.min()+projected.max())*500+matrix[2,3]*1000)
    overrides=deepcopy(p.get('handle_z_overrides',{}));found=False
    if p['handle_count']==2:
        metrics=cabinet_handle_metrics(p);defaults=deepcopy(p);defaults['handle_z_overrides']={}
        for index,x,y,z,rect,field in cabinet_handle_locations(cabinet_layout(defaults)):
            if target!='all' and str(target)!=index:continue
            found=True;overrides[index]=cabinet_pair_height_z(p,rect,field,row,base,height_mm+floor_mm,metrics,index)
        if not found:raise ValueError('找不到指定面板，請重新選取。')
        return overrides
    # Read default X without a stale override blocking a new valid height.
    defaults=deepcopy(p);defaults['handle_z_overrides']={}
    for index,x,y,z,rect,field in cabinet_handle_locations(cabinet_layout(defaults)):
        if target!='all' and str(target)!=index:continue
        found=True;solutions=[]
        for depth in cabinet_depth_candidates(p,rect,field,x,row,height_mm+floor_mm-base):
            local_y=depth-p['handle_stand'];local_z=(height_mm+floor_mm-base-row[0]*x-row[1]*local_y)/row[2]
            recessed=field[0]<x<field[2] and field[1]<local_z<field[3]
            actual_depth=cabinet_front_depth(p,rect,field,x,local_z)
            if rect[1]-1e-5<=local_z<=rect[3]+1e-5 and abs(actual_depth-depth)<1e-7:solutions.append(float(np.clip(local_z,rect[1],rect[3])))
        if not solutions:raise ValueError(f'把手 {int(index):02d} 的目標高度超出其面板；多層抽屜請選擇個別把手。')
        overrides[index]=solutions[0]
    if not found:raise ValueError('找不到指定把手，請重新選取。')
    return overrides


def cabinet_handle_metrics(p):
    lo,hi=bounds([placed_handle(p['handle_asset'],0,0,0,p['handle_angle'])]);return lo*1000,hi*1000


def cabinet_pair_points(p,rect,field,z,metrics,index,check_height=True):
    lo,hi=metrics;spacing=p['handle_spacing'];cx=(rect[0]+rect[2])/2;offset=(lo[0]+hi[0])/2
    if spacing<hi[0]-lo[0]+1:raise ValueError(f'第 {index} 片面板的雙把手間距不足，兩顆把手外框會重疊。')
    xs=[cx-spacing/2-offset,cx+spacing/2-offset]
    if any(x+lo[0]<rect[0]-1e-4 or x+hi[0]>rect[2]+1e-4 or not rect[0]<=x<=rect[2] for x in xs):raise ValueError(f'第 {index} 片面板放不下雙把手，請縮小間距、配件或增加面板寬度。')
    if check_height and (z+lo[2]<rect[1]-1e-4 or z+hi[2]>rect[3]+1e-4):raise ValueError(f'第 {index} 片面板的雙把手外框超出上下邊界，請調整高度或邊距。')
    return [(x,cabinet_front_depth(p,rect,field,x,z)-p['handle_stand'],z) for x in xs]


def cabinet_pair_height_z(p,rect,field,row,base,target,metrics,index):
    lo,hi=metrics;low=rect[1]-lo[2];high=rect[3]-hi[2]
    if low>high:raise ValueError(f'第 {index} 片面板放不下雙把手。')
    def error(z):
        points=cabinet_pair_points(p,rect,field,z,metrics,index)
        return base+float(np.mean([row@np.array(point) for point in points]))-target
    guess=(target-base-row[0]*((rect[0]+rect[2])/2-(lo[0]+hi[0])/2)+row[1]*p['handle_stand'])/row[2]
    if low<=guess<=high and abs(error(guess))<.002:return float(guess)
    cuts=[low,high,*np.linspace(low,high,49),field[1],field[3]]
    rectangle=cabinet_rectangle(p)
    if rectangle is not None:cuts.extend([field[1]+rectangle[0],field[3]-rectangle[0]])
    if p['door_style']=='classical':cuts.extend(classical_triangles(rect[2]-rect[0],rect[3]-rect[1],classical_frame(p))[:,:,2].ravel()+rect[1])
    cuts=sorted(set(float(v) for v in cuts if low<=v<=high));values=[error(z) for z in cuts]
    for z,e in zip(cuts,values):
        if abs(e)<.002:return z
    for i in range(len(cuts)-1):
        left,right=cuts[i:i+2];a,b=values[i:i+2]
        if a*b>0:continue
        for _ in range(45):
            z=(left+right)/2;e=error(z)
            if abs(e)<.002:return z
            if a*e<=0:right=z;b=e
            else:left=z;a=e
    raise ValueError(f'第 {index} 片面板的雙把手目標高度超出可用範圍，請選個別面板或調整高度。')


def add_cabinet_handles(root,layout):
    p=layout['params'];asset=p.get('handle_asset')
    if not asset or p.get('door_style')=='kitchen_c':return
    metrics=cabinet_handle_metrics(p) if p['handle_count']==2 else None
    handles=[]
    for index,x,y,z,rect,field in cabinet_handle_locations(layout):
        if p['handle_count']==1:handles.append(placed_handle(asset,x,y,z,p['handle_angle'],name=f'把手 {int(index):02d}'))
        else:
            pair=Group(name=f'把手 {int(index):02d}');pair.ext={'lime_cabinet_handle_pair':True,'lime_cabinet_panel':index}
            points=cabinet_pair_points(p,rect,field,z,metrics,index)
            pair.adopt([placed_handle(asset,*point,p['handle_angle'],name=side+'把手') for point,side in zip(points,('左','右'))]);handles.append(pair)
    root.adopt([*root.children,*handles])


def decorative_handle_location(layout):
    """Mount in leaf coordinates. Rail heights follow the actual solid bands."""
    p=layout['params'];mount=p.get('handle_mount','manual');edge=p['handle_edge']
    if mount=='manual':z=p['handle_height']
    else:
        bands={'rail_top':(p['height']-p['top'],p['height']),
               'rail_middle':(p['bottom']+p['lower_height'],p['bottom']+p['lower_height']+p['middle']),
               'rail_bottom':(0.,p['bottom'])}
        lo,hi=bands[mount];z=(lo+hi)/2;edge=p.get('handle_rail_x','center')
    x=p['handle_margin'] if edge=='left' else p['width']-p['handle_margin'] if edge=='right' else (p['left']+p['width']-p['right'])/2
    if not 0<=x<=p['width'] or not 0<=z<=p['height']:raise ValueError('把手安裝點超出門片。')
    reference=p.get('handle_reference','left');flip=edge in ('left','right') and reference!='keep' and edge!=reference
    return {'x':x,'z':z,'flip_x':flip,'edge':edge,'mount':mount}


def add_decorative_accessories(root,layout):
    p=layout['params'];leaf=next(c for c in root.children if (c.ext or {}).get('lime_decorative_role')=='leaf')
    parts=[]
    record=p.get('leaf_profile_snapshot')
    if record:
        section=section_points(record)
        for index,rect in enumerate(layout['fields']):
            for side in ('front','back'):
                if p.get('leaf_profile_side','both') not in (side,'both'):continue
                depth=p['recess_front'] if side=='front' else layout['leaf_thickness']-p['recess_back']
                parts.append(sweep_section(rect,section,depth,f'門片線板 {index+1}｜'+('正面' if side=='front' else '反面'),side=='back'))
    asset=p.get('handle_asset')
    if asset:
        mount=decorative_handle_location(layout);x,z=mount['x'],mount['z']
        recessed=any(r[0]<x<r[2] and r[1]<z<r[3] for r in layout['fields'])
        for side in ('front','back'):
            if p.get('handle_side','both') not in (side,'both'):continue
            y=(p['recess_front'] if recessed else 0)-p['handle_stand'] if side=='front' else layout['leaf_thickness']-(p['recess_back'] if recessed else 0)+p['handle_stand']
            parts.append(placed_handle(asset,x,y,z,p['handle_angle'],side=='back','正面把手' if side=='front' else '反面把手',flip_x=mount['flip_x']))
    if parts:
        # Keep leaf geometry in its own mesh; child placements are local to it.
        leaf.adopt(parts)
    record=p.get('frame_profile_snapshot')
    if record:
        if not p['with_frame']:raise ValueError('請先建立門框，或關閉門框裝飾線板。')
        frame=next(c for c in root.children if (c.ext or {}).get('lime_decorative_role')=='frame');section=section_points(record);section[:,0]=section[:,0].min()+section[:,0].max()-section[:,0]
        rect=[p['frame_left'],0,layout['outer_width']-p['frame_right'],layout['outer_height']-p['frame_top']]
        for side in ('front','back'):
            if p.get('frame_profile_side','both') not in (side,'both'):continue
            depth=(p['frame_rebate_front_depth'] if p['frame_rebate_front_width']>0 else 0) if side=='front' else p['frame_depth']-(p['frame_rebate_back_depth'] if p['frame_rebate_back_width']>0 else 0)
            frame.adopt([*frame.children,sweep_section(rect,section,depth,'門框裝飾｜'+('正面' if side=='front' else '反面'),side=='back',True)])


class AssetPicker(QWidget):
    def __init__(self,app,kind,callback):
        super().__init__();self.app=app;self.kind=kind;self.pinned=None;self.callback=callback
        box=QVBoxLayout(self);box.setContentsMargins(0,0,0,0);self.combo=QComboBox();self.note=label('','muted');box.addWidget(self.combo);box.addWidget(self.note)
        self.refresh();self.combo.currentIndexChanged.connect(self.changed)
    def refresh(self):
        old=self.combo.currentData() if self.combo.count() else None;self.combo.blockSignals(True);self.combo.clear();self.combo.addItem('不加／不安裝',None)
        try:
            for row in store_for(self.app).load()[self.kind]:self.combo.addItem(row['name'],{'record':row})
            if self.pinned:self.combo.addItem(self.pinned['name']+'（物件快照）',{'snapshot':self.pinned})
            if old:
                wanted=old.get('snapshot') or old.get('record');key='snapshot' if 'snapshot' in old else 'record'
                for i in range(1,self.combo.count()):
                    entry=self.combo.itemData(i)
                    if key in entry and entry[key]['id']==wanted['id']:self.combo.setCurrentIndex(i);break
        except Exception as exc:self.note.setText(str(exc))
        self.combo.blockSignals(False);self.describe()
    def load(self,snapshot):
        self.pinned=deepcopy(snapshot);self.refresh();self.combo.blockSignals(True)
        self.combo.setCurrentIndex(self.combo.count()-1 if snapshot else 0);self.combo.blockSignals(False);self.describe()
    def value(self):
        entry=self.combo.currentData()
        if not entry:return None
        if 'snapshot' in entry:return deepcopy(entry['snapshot'])
        record=entry['record'];return profile_snapshot(record) if self.kind=='profiles' else store_for(self.app).snapshot_handle(record)
    def describe(self):
        entry=self.combo.currentData()
        if not entry:self.note.setText('未選用配件。');return
        try:
            row=entry.get('snapshot') or entry['record']
            if self.kind=='profiles':a=section_points(row);size=np.ptp(a,axis=0)/10;self.note.setText(f'剖面 {size[0]:g} × {size[1]:g} cm｜鏡像／基準點沿用所選快照。')
            else:self.note.setText('把手寬×深×高：'+' × '.join(f'{v/10:g}' for v in row['size_mm'])+' cm')
        except Exception as exc:self.note.setText(str(exc))
    def changed(self,*_):self.describe();self.callback()


def accessory_buttons(window,layout,pickers):
    row=QHBoxLayout();row.addWidget(button('配件資料庫',lambda:show_window(window.app,'profile_library')));row.addWidget(button('重新整理配件',lambda:[picker.refresh() for picker in pickers]));layout.addLayout(row)


def side_combo():
    combo=QComboBox()
    for title,key in [('正反兩面','both'),('只正面','front'),('只反面','back')]:combo.addItem(title,key)
    return combo


class CabinetAccessories:
    def __init__(self,window):
        self.window=window;self._z_overrides={};self._height_request=None;c=card(window.body);c.addWidget(label('資料庫剖面'));self.profile=AssetPicker(window.app,'profiles',self.profile_changed);c.addWidget(self.profile)
        c.addWidget(label('把手'));self.handle=AssetPicker(window.app,'handles',window.changed);c.addWidget(self.handle);form=QFormLayout();c.addLayout(form)
        self.position=QComboBox()
        for row,title in [('top','上'),('center','中'),('bottom','下')]:
            for col,other in [('left','左'),('center','中'),('right','右')]:self.position.addItem(title+other,row+'_'+col)
        self.position.setCurrentIndex(self.position.findData('center_right'));form.addRow('把手位置',self.position)
        self.count=QComboBox();self.count.addItem('單把手',1);self.count.addItem('雙把手（每片兩顆）',2);form.addRow('每片面板把手數',self.count);self.count.currentIndexChanged.connect(window.changed)
        self.numbers={}
        for key,title in [('handle_spacing','兩把手中心間距'),('handle_x','水平邊距'),('handle_z','垂直邊距'),('handle_stand','離安裝面'),('handle_angle','旋轉角度')]:
            f=number(0) if key=='handle_angle' else length_number(CABINET_DEFAULTS[key],1 if key=='handle_spacing' else 0 if key in ('handle_x','handle_z') else -1e9)
            if key=='handle_angle':f.setSuffix(' °')
            self.numbers[key]=f;form.addRow(title,f);f.valueChanged.connect(window.changed)
        self.pair=QCheckBox('雙門把手對向靠中縫');self.pair.setChecked(True);c.addWidget(self.pair);self.pair.toggled.connect(window.changed);self.position.currentIndexChanged.connect(window.changed)
        self.height_target=QComboBox();self.height_target.addItem('全部把手（相同離地高度）','all');form.addRow('離地高度調整對象',self.height_target)
        self.height=length_number(0);form.addRow('把手中心離地高度',self.height);self.height.valueChanged.connect(self.request_floor_height);self.height_target.currentIndexChanged.connect(self.change_height_target)
        self.height_note=label('未安裝把手。','muted');c.addWidget(self.height_note)
        self.reset_height=button('恢復垂直位置／邊距控制',self.clear_floor_height);c.addWidget(self.reset_height)
        c.addWidget(label('雙把手以面板中央左右對稱，位置選項僅決定上／中／下；高度同步、中心間距固定。每片兩顆與雙門靠中縫是不同設定。','muted'));accessory_buttons(window,c,[self.profile,self.handle])
    def sync_fields(self):
        active=self.window.door_style.currentData()!='kitchen_c';dual=self.count.currentData()==2
        self.handle.setEnabled(active);self.position.setEnabled(active);self.count.setEnabled(active)
        for field in self.numbers.values():field.setEnabled(active)
        self.numbers['handle_spacing'].setEnabled(active and dual);self.numbers['handle_x'].setEnabled(active and not dual);self.pair.setEnabled(active and not dual)
        if not active:self._height_request=None
    def floor_mm(self):return float(self.window.app.document_data(default={}).get('floor_m',0.))*1000
    def request_floor_height(self,*_):
        if self.height.isEnabled():self._height_request=(self.height_target.currentData(),self.height.value());self.window.changed()
    def change_height_target(self,*_):
        # Commit the previous valid draft before displaying another handle.
        try:
            self.window.parameters()
            if self.window.preview_group is not None:self.show_floor_heights(self.window.preview_group,force=True)
            self.window.preview()
        except Exception as exc:self.window.message(str(exc),True)
    def clear_floor_height(self):
        self._height_request=None;target=self.height_target.currentData()
        if target=='all':self._z_overrides={}
        else:self._z_overrides.pop(str(target),None)
        self.window.preview()
    def show_floor_heights(self,model,force=False):
        heights=cabinet_handle_floor_heights(model,self.floor_mm());current=self.height_target.currentData()
        self.height_target.blockSignals(True);self.height_target.clear();self.height_target.addItem('全部把手（相同離地高度）','all')
        for key in heights:self.height_target.addItem((f'面板 {int(key):02d}（雙把手同高）' if parametric_info(model)['params'].get('handle_count',1)==2 else f'把手 {int(key):02d}'),key)
        index=self.height_target.findData(current);self.height_target.setCurrentIndex(max(0,index));self.height_target.blockSignals(False)
        self.height.setEnabled(bool(heights));self.height_target.setEnabled(bool(heights));self.reset_height.setEnabled(bool(heights))
        if not heights:self.height_note.setText('C 把手固定於每片上緣，隨面板高度移動；選取門板內的「C形把手」子群組可獨立上材質。' if parametric_info(model)['params'].get('door_style')=='kitchen_c' else '未安裝把手；選用配件後顯示中心離地高度。');return
        selected=self.height_target.currentData();value=next(iter(heights.values())) if selected=='all' else heights[selected]
        if force or self._height_request is None:
            self.height.blockSignals(True);self.height.setValue(value);self.height.blockSignals(False)
        rows=[f'{int(key):02d}：{v/10:g} cm' for key,v in list(heights.items())[:12]]
        extra=f'；另有 {len(heights)-12} 個' if len(heights)>12 else ''
        different=selected=='all' and max(heights.values())-min(heights.values())>.01
        self.height_note.setText(f'地板標高 {self.floor_mm()/10:g} cm｜目前離地 '+ '／'.join(rows)+extra+('。各把手高度不同，上方欄位顯示第一個；輸入將設定全部。' if different else '')+('。雙把手以整組外框中心量測，高度調整同步套用該片兩顆把手。' if parametric_info(model)['params'].get('handle_count',1)==2 else '。中心以把手實際外框計算；移動櫃體後讀值跟著更新。'))

    def profile_changed(self):
        record=self.profile.value()
        if record and record.get('kind')=='recess_rectangle':
            dims=np.ptp(section_points(record),axis=0)
            for key,value in zip(('rect_width','rect_depth'),dims):
                field=self.window.fields[key];field.blockSignals(True);field.setValue(float(value));field.blockSignals(False)
        if self.profile.combo.currentData():self.window.profile_mode.setCurrentIndex(self.window.profile_mode.findData('library'))
        self.window.changed()
    def parameters(self,base=None):
        result={'profile_snapshot':self.profile.value(),'handle_asset':self.handle.value(),'handle_position':self.position.currentData(),'handle_count':self.count.currentData(),'handle_pair':self.pair.isChecked(),**{k:f.value() for k,f in self.numbers.items()},'handle_z_overrides':deepcopy(self._z_overrides)}
        if self._height_request is not None and base is not None:
            target,value=self._height_request;params={**base,**result}
            overrides=set_cabinet_handle_floor_height(params,self.window.rigid,self.floor_mm(),value,target)
            self._z_overrides=overrides;self._height_request=None;result['handle_z_overrides']=deepcopy(overrides)
        return result
    def load(self,p):
        self._z_overrides=deepcopy(p.get('handle_z_overrides',{}));self._height_request=None
        self.profile.load(p.get('profile_snapshot'));self.handle.load(p.get('handle_asset'));self.position.blockSignals(True);self.position.setCurrentIndex(self.position.findData(p.get('handle_position','center_right')));self.position.blockSignals(False)
        self.count.blockSignals(True);self.count.setCurrentIndex(self.count.findData(p.get('handle_count',1)));self.count.blockSignals(False)
        self.pair.blockSignals(True);self.pair.setChecked(p.get('handle_pair',True));self.pair.blockSignals(False)
        for key,f in self.numbers.items():f.blockSignals(True);f.setValue(p.get(key,CABINET_DEFAULTS[key]));f.blockSignals(False)


class DecorativeAccessories:
    def __init__(self,window):
        self.window=window;c=card(window.body);c.addWidget(label('配件資料庫'));self.leaf=AssetPicker(window.app,'profiles',window.changed);self.frame=AssetPicker(window.app,'profiles',window.changed);self.handle=AssetPicker(window.app,'handles',window.changed)
        self.leaf_side=side_combo();self.frame_side=side_combo();self.handle_side=side_combo()
        for title,picker,side in [('門片內框線板',self.leaf,self.leaf_side),('三邊門框裝飾',self.frame,self.frame_side),('把手',self.handle,self.handle_side)]:c.addWidget(label(title));c.addWidget(picker);c.addWidget(side);side.currentIndexChanged.connect(window.changed)
        form=QFormLayout();c.addLayout(form)
        self.mount=QComboBox()
        for title,key in [('手動高度與側邊距','manual'),('上橫料中央','rail_top'),('中橫料中央','rail_middle'),('下橫料中央','rail_bottom')]:self.mount.addItem(title,key)
        form.addRow('把手安裝方式',self.mount);self.mount.currentIndexChanged.connect(window.changed)
        self.edge=QComboBox();self.edge.addItem('右側','right');self.edge.addItem('左側','left');form.addRow('把手側邊',self.edge);self.edge.currentIndexChanged.connect(window.changed)
        self.rail_x=QComboBox()
        for title,key in [('橫料水平置中','center'),('靠左（依側邊距）','left'),('靠右（依側邊距）','right')]:self.rail_x.addItem(title,key)
        form.addRow('橫料橫向位置',self.rail_x);self.rail_x.currentIndexChanged.connect(window.changed)
        self.reference=QComboBox()
        for title,key in [('左側把手樣式（右側自動鏡像）','left'),('右側把手樣式（左側自動鏡像）','right'),('維持配件方向，不自動鏡像','keep')]:self.reference.addItem(title,key)
        form.addRow('配件原始方向',self.reference);self.reference.currentIndexChanged.connect(window.changed)
        self.reference.setToolTip('以資料庫配件原本適用的一側為基準。鏡像保留上、下方向及配件安裝原點，正反面分別貼齊門片。')
        self.numbers={}
        for key,title in [('handle_margin','距門片側邊'),('handle_height','安裝點離門底'),('handle_stand','離安裝面'),('handle_angle','旋轉角度')]:
            f=number(0) if key=='handle_angle' else length_number(DECORATIVE_DEFAULTS[key],0 if key!='handle_stand' else -1e9)
            if key=='handle_angle':f.setSuffix(' °')
            self.numbers[key]=f;form.addRow(title,f);f.valueChanged.connect(window.changed)
        self.position_note=label('','muted');c.addWidget(self.position_note)
        c.addWidget(label('橫料中央以左右直料之間的淨寬置中；也可選靠左／靠右。手動高度保留，切回手動可繼續使用。','muted'))
        accessory_buttons(window,c,[self.leaf,self.frame,self.handle])
    def sync_fields(self):
        rail=self.mount.currentData()!='manual';center=rail and self.rail_x.currentData()=='center'
        self.edge.setEnabled(not rail);self.rail_x.setEnabled(rail);self.numbers['handle_height'].setEnabled(not rail);self.numbers['handle_margin'].setEnabled(not center)
        try:
            params={k:f.isChecked() if isinstance(f,QCheckBox) else f.value() for k,f in self.window.fields.items()};params.update(self.parameters());location=decorative_handle_location(decorative_layout(params))
            self.position_note.setText(f'安裝點距門左 {location["x"]/10:g} cm｜距門底 {location["z"]/10:g} cm'+('｜已左右鏡像' if location['flip_x'] else ''))
        except Exception:self.position_note.setText('請先確認門片及把手尺寸；預覽會顯示原因。')
    def parameters(self):return {'leaf_profile_snapshot':self.leaf.value(),'frame_profile_snapshot':self.frame.value(),'handle_asset':self.handle.value(),'leaf_profile_side':self.leaf_side.currentData(),'frame_profile_side':self.frame_side.currentData(),'handle_side':self.handle_side.currentData(),'handle_edge':self.edge.currentData(),'handle_mount':self.mount.currentData(),'handle_rail_x':self.rail_x.currentData(),'handle_reference':self.reference.currentData(),**{k:f.value() for k,f in self.numbers.items()}}
    def load(self,p):
        for picker,key in [(self.leaf,'leaf_profile_snapshot'),(self.frame,'frame_profile_snapshot'),(self.handle,'handle_asset')]:picker.load(p.get(key))
        for combo,key,default in [(self.leaf_side,'leaf_profile_side','both'),(self.frame_side,'frame_profile_side','both'),(self.handle_side,'handle_side','both'),(self.edge,'handle_edge','right'),(self.mount,'handle_mount','manual'),(self.rail_x,'handle_rail_x','center'),(self.reference,'handle_reference','left')]:combo.blockSignals(True);combo.setCurrentIndex(combo.findData(p.get(key,default)));combo.blockSignals(False)
        for key,f in self.numbers.items():f.blockSignals(True);f.setValue(p.get(key,DECORATIVE_DEFAULTS[key]));f.blockSignals(False)
        self.sync_fields()


def render_handle_preview(root,width,height):
    """CPU depth-buffer thumbnail: complete faces, including softened surfaces.

    The active scene, camera, materials and mesh are never touched. Textures
    use a representative colour; this thumbnail is not a render engine.
    """
    width=max(1,min(int(width),700));height=max(1,min(int(height),700))
    background=np.array([244,247,250],dtype=np.uint8)
    pixels=np.empty((height,width,3),dtype=np.uint8);pixels[:]=background
    zbuffer=np.full((height,width),-np.inf)
    right=np.array([.94,.342,0.]);right/=np.linalg.norm(right)
    eye=np.array([.3,-.82,.49]);eye/=np.linalg.norm(eye)
    right=np.cross(eye,[0,0,1.]);right/=np.linalg.norm(right)
    up=np.cross(right,eye);up/=np.linalg.norm(up)
    camera=np.stack([right,up,eye],axis=1)
    triangles=[];texture_colors={}
    def color_of(attrs):
        if attrs.get('color') is not None:
            rgb=np.asarray(attrs['color'],float)[:3]
            return np.clip(rgb,0,1)
        texture=attrs.get('texture') or {};path=texture.get('path')
        if path:
            if path not in texture_colors:
                image=QImage(str(path)).scaled(16,16,Qt.IgnoreAspectRatio,Qt.SmoothTransformation)
                texture_colors[path]=np.mean([[image.pixelColor(x,y).redF(),image.pixelColor(x,y).greenF(),image.pixelColor(x,y).blueF()] for y in range(image.height()) for x in range(image.width())],axis=0) if not image.isNull() else np.ones(3)
            return texture_colors[path]
        return np.ones(3)
    def key(point):return tuple(np.round(xyz(point),9))
    def visit(node,parent,inherited=None):
        if getattr(node,'hidden',False):return
        matrix=parent*(node.xform if node.xform is not None else QMatrix4x4())
        inherited=paint_recipe(node.material) or inherited or {}
        mesh=transformed_mesh(node.mesh,matrix)
        soft={key(p) for edge in mesh.edges if edge.soft for p in (edge.a,edge.b)}
        summed={}
        for face in mesh.faces:
            normal=xyz(face.normal())
            for vertex in face.vertices:
                k=key(vertex)
                if k in soft:summed[k]=summed.get(k,np.zeros(3))+normal
        for face in mesh.faces:
            normal=xyz(face.normal());attrs=paint_recipe(face.attrs) or inherited
            if normal@eye<0 and isinstance(face.attrs.get('back'),dict):attrs=face.attrs['back']
            color=color_of(attrs)
            for triangle in face.triangulate():
                points=np.array([xyz(p) for p in triangle]);normals=np.array([summed.get(key(p),normal) for p in triangle])
                normals/=np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-12)
                normals=np.where((normals@eye)[:,None]<0,-normals,normals)
                triangles.append((points@camera,normals,color))
        for child in node.children or ():visit(child,matrix,inherited)
    visit(root,QMatrix4x4())
    if not triangles:
        # Edge-only assets still get a useful outline.
        image=QImage(width,height,QImage.Format_RGB888);image.fill(QColor('#f4f7fa'))
        lines=parametric_preview(root,12000)
        if lines.size:
            projected=lines.reshape(-1,3)@camera[:,:2];lo=projected.min(0);hi=projected.max(0)
            scale=min(max(1,width-40)/max(hi[0]-lo[0],1e-9),max(1,height-40)/max(hi[1]-lo[1],1e-9))
            projected=(projected-(lo+hi)/2)*scale;projected[:,0]+=width/2;projected[:,1]=height/2-projected[:,1]
            painter=QPainter(image);painter.setRenderHint(QPainter.Antialiasing);painter.setPen(QPen(QColor('#356575'),1))
            for a,b in projected.reshape(-1,2,2):painter.drawLine(QPointF(*map(float,a)),QPointF(*map(float,b)))
            painter.end()
        return image
    all_points=np.concatenate([t[0] for t in triangles]);lo=all_points.min(0);hi=all_points.max(0)
    center=(lo+hi)/2;span=np.maximum(hi-lo,1e-9);scale=min(max(1,width-40)/span[0],max(1,height-40)/span[1])
    light=np.array([-.35,-.6,.72]);light/=np.linalg.norm(light)
    for points,normals,color in triangles:
        points=points.copy();points[:,:2]=(points[:,:2]-center[:2])*scale;points[:,0]+=width/2;points[:,1]=height/2-points[:,1]
        a,b,c=points;den=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
        if abs(den)<1e-10:continue
        x0=max(0,int(np.floor(points[:,0].min())));x1=min(width-1,int(np.ceil(points[:,0].max())))
        y0=max(0,int(np.floor(points[:,1].min())));y1=min(height-1,int(np.ceil(points[:,1].max())))
        if x1<x0 or y1<y0:continue
        xx,yy=np.meshgrid(np.arange(x0,x1+1)+.5,np.arange(y0,y1+1)+.5)
        u=((b[1]-c[1])*(xx-c[0])+(c[0]-b[0])*(yy-c[1]))/den
        v=((c[1]-a[1])*(xx-c[0])+(a[0]-c[0])*(yy-c[1]))/den;w=1-u-v
        depth=u*a[2]+v*b[2]+w*c[2];old=zbuffer[y0:y1+1,x0:x1+1]
        mask=(u>=-1e-7)&(v>=-1e-7)&(w>=-1e-7)&(depth>old)
        if not mask.any():continue
        strength=.32+.68*np.maximum(0,normals@light)
        shade=u*strength[0]+v*strength[1]+w*strength[2]
        rgb=np.clip(shade[:,:,None]*color*255,0,255).astype(np.uint8)
        pixels[y0:y1+1,x0:x1+1][mask]=rgb[mask];old[mask]=depth[mask]
    return QImage(pixels.data,width,height,pixels.strides[0],QImage.Format_RGB888).copy()


class AssetPreview(QWidget):
    def __init__(self):
        super().__init__();self.record=None;self.group=None;self._render_cache=None;self.setMinimumSize(280,220)
    def profile(self,record):self.record=record;self.group=None;self.update()
    def handle(self,group):self.record=None;self.group=group;self._render_cache=None;self.update()
    def clear(self):self.record=None;self.group=None;self.update()
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing);painter.fillRect(self.rect(),QColor('#f4f7fa'));margin=25.;w,h=self.width(),self.height()
        try:
            if self.record:
                points=section_points(self.record);lo,hi=points.min(0),points.max(0);span=np.maximum(hi-lo,1e-6);scale=min((w-2*margin)/span[0],(h-2*margin)/span[1]);center=(lo+hi)/2
                def pixel(p):return QPointF(float(w/2+(p[0]-center[0])*scale),float(h/2-(p[1]-center[1])*scale))
                painter.setPen(QPen(QColor('#366c79'),1.5));painter.setBrush(QColor('#b4ccd0'));painter.drawPolygon(QPolygonF([pixel(p) for p in points]));painter.setBrush(QColor('#db3942'));painter.setPen(QPen(QColor('white'),1));painter.drawEllipse(pixel([0,0]),5.,5.)
            elif self.group:
                key=(w,h)
                if self._render_cache is None or self._render_cache[0]!=key:
                    self._render_cache=(key,render_handle_preview(self.group,w,h))
                painter.drawImage(self.rect(),self._render_cache[1])
            else:painter.setPen(QColor('#6b8197'));painter.drawText(self.rect(),Qt.AlignCenter,'選取配件以預覽')
        except Exception:painter.setPen(QColor('#a34f47'));painter.drawText(self.rect(),Qt.AlignCenter,'無法預覽此配件')
        painter.end()


class AccessoryWindow(StyledWindow):
    def __init__(self,app):
        super().__init__(app,'配件資料庫',850,900);self.store=store_for(app);self.rows=[]
        self.body.addWidget(label('配件資料庫','title'));self.body.addWidget(label('資料獨立儲存，更新套件會保留。尺寸 cm；紅點為剖面基準點。','muted'))
        row=QHBoxLayout();self.kind=QComboBox();self.kind.addItem('線板剖面','profiles');self.kind.addItem('把手','handles');row.addWidget(self.kind);self.search=QLineEdit();self.search.setPlaceholderText('搜尋配件名稱');row.addWidget(self.search);row.addWidget(button('重新整理',self.refresh));self.body.addLayout(row)
        c=card(self.body);row=QHBoxLayout();self.list=QListWidget();self.list.setMinimumSize(230,240);row.addWidget(self.list,1);self.preview=AssetPreview();row.addWidget(self.preview,2);c.addLayout(row);self.dimensions=label('','muted');c.addWidget(self.dimensions)
        form=QFormLayout();self.name=QLineEdit();self.name.setPlaceholderText('輸入新增或修改後的名稱');form.addRow('名稱',self.name);c.addLayout(form)
        self.profile_controls=QWidget();profile_layout=QFormLayout(self.profile_controls);profile_layout.setContentsMargins(0,0,0,0)
        self.anchor=QComboBox()
        for key,title in [('top_left','左上'),('top_center','中上'),('top_right','右上'),('center_left','左中'),('center','正中'),('center_right','右中'),('bottom_left','左下'),('bottom_center','中下'),('bottom_right','右下')]:self.anchor.addItem(title,key)
        self.anchor.setCurrentIndex(self.anchor.findData('bottom_left'));profile_layout.addRow('剖面基準點',self.anchor);self.mirror_x=QCheckBox('水平鏡像');self.mirror_y=QCheckBox('垂直鏡像');profile_layout.addRow(self.mirror_x,self.mirror_y)
        self.rotation=number(0);self.rotation.setSuffix(' °');profile_layout.addRow('剖面旋轉',self.rotation);c.addWidget(self.profile_controls)
        self.handle_controls=QWidget();handle_layout=QFormLayout(self.handle_controls);handle_layout.setContentsMargins(0,0,0,0);self.front=QComboBox()
        for key in ('-Y','+Y','+Z','-Z','+X','-X'):self.front.addItem('原模型正面 '+key,key)
        self.mount=QComboBox();self.mount.addItem('後側中心貼齊安裝面','center');self.mount.addItem('保留原點作為安裝點','origin');handle_layout.addRow('正面方向',self.front);handle_layout.addRow('把手安裝基準',self.mount);c.addWidget(self.handle_controls)
        c.addWidget(label('正面方向與安裝基準在「收錄／匯入把手」時生效。已收錄把手要改軸向，請重新收錄。','muted'))
        row=QHBoxLayout();row.addWidget(button('收錄目前選取',self.capture,'primary'));row.addWidget(button('匯入 SKP／IGZ',self.import_asset));row.addWidget(button('儲存名稱／基準設定',self.save_record));c.addLayout(row)
        row=QHBoxLayout();row.addWidget(button('插入目前模型',self.insert));row.addWidget(button('移除資料庫項目',self.delete));c.addLayout(row)
        self.body.addWidget(button('舊把手資料批次轉入（試驗中）',lambda:show_window(self.app,'legacy_handles')))
        c=card(self.body);row=QHBoxLayout();row.addWidget(button('匯出配件備份',self.export_backup));row.addWidget(button('匯入備份／舊版剖面 JSON',self.import_backup));row.addWidget(button('開啟配件資料夾',self.open_folder));c.addLayout(row)
        self.body.addStretch();self.kind.currentIndexChanged.connect(self.refresh);self.search.textChanged.connect(self.rebuild_list);self.list.currentRowChanged.connect(self.select_record)
        self.anchor.currentIndexChanged.connect(self.preview_settings);self.rotation.valueChanged.connect(self.preview_settings);self.mirror_x.toggled.connect(self.preview_settings);self.mirror_y.toggled.connect(self.preview_settings);self.refresh()
    def config(self):return {'anchor':self.anchor.currentData(),'mirror_x':self.mirror_x.isChecked(),'mirror_y':self.mirror_y.isChecked(),'rotation':self.rotation.value()}
    def current(self):
        item=self.list.currentItem()
        if item is None:raise ValueError('請先選取資料庫項目。')
        return deepcopy(item.data(Qt.UserRole))
    def refresh(self,*_):
        def refresh():
            self.rows=self.store.load()[self.kind.currentData()];self.rebuild_list();self.profile_controls.setVisible(self.kind.currentData()=='profiles');self.handle_controls.setVisible(self.kind.currentData()=='handles')
            return f'已讀取 {len(self.rows)} 個'+('剖面。' if self.kind.currentData()=='profiles' else '把手。')
        self.run(refresh)
    def rebuild_list(self,*_):
        old=self.list.currentItem().data(Qt.UserRole)['id'] if self.list.currentItem() else None;self.list.blockSignals(True);self.list.clear();needle=self.search.text().strip().casefold()
        for r in self.rows:
            if needle and needle not in r['name'].casefold():continue
            item=QListWidgetItem(r['name']);item.setData(Qt.UserRole,r);self.list.addItem(item)
            if r['id']==old:self.list.setCurrentItem(item)
        if self.list.currentRow()<0 and self.list.count():self.list.setCurrentRow(0)
        self.list.blockSignals(False);self.select_record()
    def select_record(self,*_):
        if self.list.currentItem() is None:self.preview.clear();self.dimensions.setText('尚未收錄配件，可選取模型或匯入檔案。');return
        record=self.current();self.name.setText(record['name'])
        try:
            if self.kind.currentData()=='profiles':
                config=placement(record.get('placement'))
                for widget,value in [(self.anchor,config['anchor']),(self.mirror_x,config['mirror_x']),(self.mirror_y,config['mirror_y']),(self.rotation,config['rotation'])]:
                    widget.blockSignals(True)
                    if isinstance(widget,QComboBox):widget.setCurrentIndex(widget.findData(value))
                    elif isinstance(widget,QCheckBox):widget.setChecked(value)
                    else:widget.setValue(value)
                    widget.blockSignals(False)
                self.preview_settings()
            else:
                snapshot=self.store.snapshot_handle(record);self.preview.handle(handle_from_snapshot(snapshot));self.dimensions.setText('寬 × 深 × 高：'+' × '.join(f'{v/10:g}' for v in record['size_mm'])+' cm｜實體預覽')
        except Exception as exc:self.preview.clear();self.message(str(exc),True)
    def preview_settings(self,*_):
        if self.kind.currentData()!='profiles' or not self.list.currentItem():return
        try:
            record=self.current();record['placement']=self.config();points=section_points(record);size=np.ptp(points,axis=0)/10;self.preview.profile(record);self.dimensions.setText(f'寬 {size[0]:g} cm｜高 {size[1]:g} cm｜紅點為安裝基準')
        except Exception as exc:self.message(str(exc),True)
    def capture(self):
        def capture():
            if self.kind.currentData()=='profiles':r=self.store.add_profile(self.name.text(),selected_profile(self.app.scene),self.config())
            else:
                if self.app.scene.edit_group is not None:raise ValueError('收錄把手前請退出群組編輯。')
                items=selection(self.app.scene,True)
                if len(items)!=1:raise ValueError('請只選取一個把手群組／元件。')
                r=self.store.add_handle(self.name.text(),items[0],self.front.currentData(),self.mount.currentData())
            self.refresh();return '已收錄：'+r['name']+'。既有模型未變更。'
        self.run(capture)
    def import_asset(self):
        path,_=QFileDialog.getOpenFileName(self,'匯入配件','','模型檔案 (*.skp *.igz)')
        if not path:return
        def apply():
            group=read_asset_group(path);name=self.name.text().strip() or Path(path).stem
            if self.kind.currentData()=='profiles':r=self.store.add_profile(name,profile_from_group(group),self.config())
            else:r=self.store.add_handle(name,group,self.front.currentData(),self.mount.currentData())
            self.refresh();return '已匯入：'+r['name']
        self.run(apply)
    def save_record(self):
        def apply():
            record=self.current();self.store.update(self.kind.currentData(),record['id'],self.name.text(),self.config() if self.kind.currentData()=='profiles' else None);self.refresh();return '已儲存名稱與設定。已放入模型的配件保留原快照。'
        self.run(apply)
    def delete(self):
        try:r=self.current()
        except Exception as exc:self.message(str(exc),True);return
        if QMessageBox.question(self,'移除配件','從資料庫移除「'+r['name']+'」？\n已放入模型的配件會保留。')!=QMessageBox.Yes:return
        self.run(lambda:(self.store.delete(self.kind.currentData(),r['id']),self.refresh(),'已移除資料庫項目。')[2])
    def insert(self):
        def apply():
            row=self.current()
            if self.kind.currentData()=='profiles':
                row['placement']=self.config();points=section_points(row)/1000;group=Group(name='剖面｜'+row['name']);group.mesh.add_face([QVector3D(float(x),float(y),0) for x,y in points])
            else:group=handle_from_snapshot(self.store.snapshot_handle(row));group.name='把手｜'+row['name']
            execute(self.app.viewport,AddGenerated(group));return '已插入目前編輯層級原點，Ctrl＋Z 可復原。'
        self.run(apply)
    def export_backup(self):
        path,_=QFileDialog.getSaveFileName(self,'匯出配件備份','Lime_Accessories_Backup.json','配件備份 (*.json)')
        if path:self.run(lambda:(self.store.export(path),'已匯出配件備份，包含剖面、把手與貼圖。')[1])
    def import_backup(self):
        path,_=QFileDialog.getOpenFileName(self,'匯入配件備份或原版剖面 JSON','','配件備份 (*.json)')
        if path:
            def apply():
                count=self.store.import_json(path);self.refresh();return f'已匯入 {count} 個配件；相同剖面已略過。'
            self.run(apply)
    def open_folder(self):
        self.store.directory.mkdir(parents=True,exist_ok=True);QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.directory.resolve())))


# ---- Experimental exports, legacy handles, surface snap and composite cabinet ----
def batch_export_groups(scene,items,directory,mode='separate',format='igz',at_origin=False):
    """Stage an entire batch, then publish one new folder; never overwrite."""
    import shutil
    from datetime import datetime
    from core.scene import Scene
    if mode not in ('separate','merged') or format not in ('igz','skp'):raise ValueError('匯出模式或格式無效。')
    if not items or any(not isinstance(g,Group) or not editable(scene,g) for g in items):raise ValueError('請選取可用的群組／元件。')
    directory=Path(directory)
    if not directory.is_dir():raise ValueError('請選擇已存在的輸出資料夾。')
    groups=[clone_tree(g) for g in items]
    inherited=next((paint_recipe(level['group'].material) for level in reversed(scene._edit_stack) if paint_recipe(level['group'].material)),None)
    for g in groups:
        if not g.material and inherited:g.material=deepcopy(inherited)
        bind_face_materials(g)
    batches=[groups] if mode=='merged' else [[g] for g in groups]
    stage=Path(tempfile.mkdtemp(prefix='.lime_export_',dir=directory));used=set();entries=[]
    try:
        for index,batch in enumerate(batches,1):
            export_scene=Scene();export_scene.materials=deepcopy(scene.materials);export_scene.layers=deepcopy(scene.layers)
            if at_origin:
                lo,hi=bounds(batch);move=QMatrix4x4();move.translate(*map(float,-lo))
                for g in batch:g.xform=move*(g.xform if g.xform is not None else QMatrix4x4())
            export_scene.groups=batch
            name='合併物件' if mode=='merged' else batch[0].name
            safe=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',str(name)).strip(' .')[:70] or '物件'
            if safe.upper().split('.')[0] in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:safe='物件_'+safe
            base=safe;number=2
            while safe.casefold() in used:safe=f'{base}_{number}';number+=1
            used.add(safe.casefold());path=stage/(safe+'.'+format)
            if format=='igz':
                from formats.igz import save_scene
                save_scene(export_scene,path)
            else:
                from formats.skp_out import save_skp
                save_skp(export_scene,path)
            if not path.is_file() or path.stat().st_size==0:raise ValueError('匯出沒有產生有效檔案：'+safe)
            entries.append({'file':path.name,'source':[g.name for g in batch]})
        _atomic_json(stage/'匯出清單.json',{'version':VERSION,'experimental':True,'format':format,'coordinates':'origin' if at_origin else 'model','files':entries})
        final=directory/('Lime_Export_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
        os.rename(stage,final);return final,entries
    except Exception:
        shutil.rmtree(stage,ignore_errors=True);raise


class BatchExportWindow(StyledWindow):
    def __init__(self,app):
        super().__init__(app,'元件批次匯出｜試驗中',560,650)
        self.body.addWidget(label('元件批次匯出 · 試驗中','title'));self.body.addWidget(label('選取群組／元件，另存到新的批次資料夾。原模型不變，不覆寫舊檔。','muted'))
        c=card(self.body);self.mode=QComboBox();self.mode.addItem('各自匯出檔案','separate');self.mode.addItem('合併為一個檔案','merged');c.addWidget(self.mode)
        self.format=QComboBox();self.format.addItem('IGZ（保留本工具參數與貼圖）','igz');self.format.addItem('SKP（原生寫入器 v17，試驗中）','skp');c.addWidget(self.format)
        self.origin=QCheckBox('移至原點（各檔以外框最小點定位）');c.addWidget(self.origin)
        self.path=QLineEdit();self.path.setReadOnly(True);c.addWidget(self.path);c.addWidget(button('選擇輸出資料夾',self.choose));c.addWidget(button('匯出目前選取',self.export,'primary'))
        self.body.addWidget(label('SKP 需要 IngeTrazo 原生 SKP 寫入器。失敗會取消整批暫存，不會改動原模型。','muted'));self.body.addStretch()
    def choose(self):
        path=QFileDialog.getExistingDirectory(self,'選擇輸出資料夾')
        if path:self.path.setText(path)
    def export(self):
        def run():
            if not self.path.text().strip():raise ValueError('請先選擇輸出資料夾。')
            groups=selection(self.app.scene,True);folder,rows=batch_export_groups(self.app.scene,groups,self.path.text(),self.mode.currentData(),self.format.currentData(),self.origin.isChecked())
            return f'已匯出 {len(rows)} 個檔案：{folder}'
        self.run(run)


def scan_legacy_handles(store,path):
    path=Path(path)
    if path.stat().st_size>32*1024*1024:raise ValueError('舊目錄 JSON 超過 32 MiB。')
    rows=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(rows,list) or len(rows)>500:raise ValueError('請選原版 handles.json 陣列，最多 500 項。')
    current=store.load();ids={r['id'] for r in current['handles']};names={r['name'].casefold() for r in current['handles']};records=[];report=[];total=0
    for row in rows:
        name=str(row.get('name','')) if isinstance(row,dict) else '無效項目'
        try:
            identity=_asset_id(row['id'],'h');name=asset_name(row['name'])
            if identity in ids:report.append((name,'已存在，略過'));continue
            model=path.parent/(identity+'.skp')
            if not model.is_file():raise ValueError('缺少 '+model.name)
            # Original SKP definitions ALREADY contain front and mount transforms.
            group=read_asset_group(model);group=normalize_handle(group,'-Y','origin');raw=group_igz_bytes(group)
            if len(raw)>ASSET_MAX_BYTES or total+len(raw)>96*1024*1024:raise ValueError('模型或整批資料過大，請分批匯入。')
            original=name;counter=2
            while name.casefold() in names:
                suffix=f'（舊版 {counter}）';name=original[:80-len(suffix)]+suffix;counter+=1
            lo,hi=bounds([group]);record={'id':identity,'name':name,'size_mm':((hi-lo)*1000).tolist(),'sha256':hashlib.sha256(raw).hexdigest(),'blob_b64':base64.b64encode(raw).decode(),'legacy_id':identity}
            handle_from_snapshot(record);records.append(record);total+=len(raw);ids.add(identity);names.add(name.casefold());report.append((name,'可轉入'))
        except Exception as exc:report.append((name,'失敗：'+str(exc)))
    return {'schema':2,'units':'mm','profiles':[],'handles':records},report


class LegacyHandlesWindow(StyledWindow):
    def __init__(self,app):
        super().__init__(app,'舊把手批次轉入｜試驗中',700,800);self.store=store_for(app);self.plan=None
        self.body.addWidget(label('舊把手批次轉入 · 試驗中','title'));self.body.addWidget(label('選擇原版 handles.json；同資料夾需有對應 h_… .skp。先掃描，按轉入才寫入。','muted'))
        self.body.addWidget(button('選擇 handles.json 並掃描',self.choose,'primary'));self.list=QListWidget();self.list.setMinimumHeight(180);self.body.addWidget(self.list);self.preview=AssetPreview();self.body.addWidget(self.preview)
        self.apply_button=button('轉入可用項目',self.apply,'primary');self.apply_button.setEnabled(False);self.body.addWidget(self.apply_button);self.body.addStretch()
    def scan(self,path):
        self.plan=None;self.apply_button.setEnabled(False);self.list.clear();self.preview.clear()
        def run():
            self.plan,report=scan_legacy_handles(self.store,path)
            for name,status in report:self.list.addItem(name+'｜'+status)
            rows=self.plan['handles'];self.apply_button.setEnabled(bool(rows))
            if rows:self.preview.handle(handle_from_snapshot(rows[0]))
            return f'可轉入 {len(rows)} 項；其餘 {len(report)-len(rows)} 項請見清單。原資料未變更。'
        self.run(run)
    def choose(self):
        path,_=QFileDialog.getOpenFileName(self,'選擇舊把手目錄','','舊目錄 (handles.json *.json)')
        if path:self.scan(path)
    def apply(self):
        def run():
            if not self.plan or not self.plan['handles']:raise ValueError('請先掃描可用項目。')
            with tempfile.TemporaryDirectory(prefix='lime_legacy_') as folder:
                path=Path(folder)/'backup.json';_atomic_json(path,self.plan);count=self.store.import_json(path)
            self.plan=None;self.apply_button.setEnabled(False)
            return f'已轉入 {count} 項，原 JSON／SKP 未修改。請到配件選單重新整理。'
        self.run(run)


def snap_ffd_controls(engine,selected,face,placement,axis=3):
    movable=sorted(set(selected)-engine.locked)
    if not movable:raise ValueError('請先選取未鎖定的控制點。')
    matrix=getattr(placement,'xform',None)
    def world(point):return xyz(matrix.map(point) if matrix is not None else point)
    triangles=np.array([[world(p) for p in triangle] for triangle in face.triangulate()])
    if not len(triangles):raise ValueError('目標面沒有有效三角面。')
    normal=np.cross(triangles[0,1]-triangles[0,0],triangles[0,2]-triangles[0,0]);length=np.linalg.norm(normal)
    if length<1e-12:raise ValueError('目標面退化。')
    normal/=length
    direction=normal if axis==3 else np.eye(3)[axis];den=float(direction@normal)
    if abs(den)<1e-8:raise ValueError('目前方向平行目標面，請換軸或選自由方向。')
    source=engine.controls[movable];dist=(triangles[0,0]-source)@normal/den;points=source+dist[:,None]*direction
    def inside(point):
        a=triangles[:,0];v0=triangles[:,1]-a;v1=triangles[:,2]-a;v2=point-a
        d00=np.sum(v0*v0,1);d01=np.sum(v0*v1,1);d11=np.sum(v1*v1,1);d20=np.sum(v2*v0,1);d21=np.sum(v2*v1,1);det=d00*d11-d01*d01
        valid=det>1e-18;den=np.where(valid,det,1);u=(d11*d20-d01*d21)/den;v=(d00*d21-d01*d20)/den
        return np.any(valid&(u>=-1e-7)&(v>=-1e-7)&(u+v<=1+1e-7))
    if not all(inside(point) for point in points):raise ValueError('投影點超出目標面或落在孔洞，未套用吸附。')
    before=engine.controls.copy()
    try:engine.controls[movable]=points;engine.validate()
    except Exception:engine.controls=before;raise
    return len(movable)


def split_grid_sizes(total,count,values):
    if not 1<=count<=6 or not isinstance(values,list) or len(values)>count:raise ValueError('分欄／分層數需為 1～6；尺寸項目不能超過數量。')
    sizes=[float(values[i]) if i<len(values) and values[i] is not None else None for i in range(count)]
    if any(v is not None and (not math.isfinite(v) or v<=0) for v in sizes):raise ValueError('分區尺寸必須大於零。')
    flex=sizes.count(None);remaining=total-sum(v for v in sizes if v is not None)
    if not flex and abs(remaining)>.01:raise ValueError('指定尺寸總和與櫃體尺寸不同。')
    result=[v if v is not None else remaining/flex for v in sizes]
    if min(result)<80:raise ValueError('分區寬高至少 8 cm；還需放得下門片框料。')
    return result


def composite_layout(values=None):
    p={'width':1200.,'height':1800.,'depth':600.,'columns':2,'rows':2,'widths':[],'heights':[],'zones':[],'style':{}}
    p.update(deepcopy(values or {}))
    for key in ('columns','rows'):
        v=float(p[key])
        if not math.isfinite(v) or v!=int(v):raise ValueError('分欄／分層數必須為整數。')
        p[key]=int(v)
    for key in ('width','height','depth'):
        p[key]=float(p[key])
        if not math.isfinite(p[key]) or not 100<=p[key]<=20000:raise ValueError('櫃體尺寸須介於 10～2000 cm。')
    widths=split_grid_sizes(p['width'],p['columns'],p['widths']);heights=split_grid_sizes(p['height'],p['rows'],p['heights'])
    if not isinstance(p['zones'],list) or len(p['zones'])>len(widths)*len(heights):raise ValueError('分區設定數量無效。')
    if not isinstance(p['style'],dict):raise ValueError('面板樣式設定無效。')
    style={**deepcopy(CABINET_DEFAULTS),**p['style']};style['handle_z_overrides']={};zones=[];top=p['height']
    for row,h in enumerate(heights):
        z=top-h;x=0.
        for col,w in enumerate(widths):
            index=row*p['columns']+col;config=p['zones'][index] if index<len(p['zones']) else {}
            if not isinstance(config,dict):raise ValueError('分區設定無效。')
            cell={**style,'width':w,'height':h,'depth':p['depth'],'face_mode':config.get('face_mode','doors'),'panels':config.get('panels',1),'drawer_columns':config.get('drawer_columns',1),'drawer_rows':config.get('drawer_rows',3),'drawer_heights':[]}
            cell=cabinet_layout(cell)['params'];zones.append((index,row,col,x,z,cell));x+=w
        top=z
    p['zones']=[{k:cell[k] for k in ('face_mode','panels','drawer_columns','drawer_rows')} for _,_,_,_,_,cell in zones]
    p['style']=style;return p,widths,heights,zones


def composite_model(values=None):
    p,widths,heights,zones=composite_layout(values);shell=cabinet_model({'width':p['width'],'height':p['height'],'depth':p['depth'],'face_mode':'open'});body=shell.children[0];boards=[];x=0.
    for i,w in enumerate(widths[:-1]):
        x+=w;r=[x-15,30,x+15,p['height']-30];cuts=[[x-15,30,x-2,p['height']-30],[x+2,30,x+15,p['height']-30]]
        boards.append((f'分欄隔板 {i+1}',r,cuts))
    top=p['height']
    for row,h in enumerate(heights[:-1]):
        top-=h;x=0.
        for col,w in enumerate(widths):
            left=30 if col==0 else x+15;right=p['width']-30 if col==len(widths)-1 else x+w-15
            r=[left,top-15,right,top+15];cuts=[[left,top-15,right,top-2],[left,top+2,right,top+15]];boards.append((f'分層板 {row+1}-{col+1}',r,cuts));x+=w
    for name,rect,cuts in boards:
        g=recessed_solid(rect,cuts,[],10,0,p['depth']-50,name);g.xform=QMatrix4x4();g.xform.translate(0,.02,0);body.adopt([*body.children,g])
    children=[body]
    for index,row,col,x,z,cell in zones:
        source=cabinet_model(cell);zone=Group(name=f'分區 {row+1}-{col+1}');zone.adopt([g for g in source.children if (g.ext or {}).get('lime_cabinet_part')!='body']);zone.xform=QMatrix4x4();zone.xform.translate(x/1000,0,z/1000);children.append(zone)
        # Interior zone edges share 3 cm grid boards, so drawer boards extend
        # to 1.5 cm from those joints instead of a separate 3 cm carcass edge.
        for name,rect,cuts in cabinet_layout(cell)['drawer_boards']:
            shapes=[list(rect),*[list(cut) for cut in cuts]]
            for r in shapes:
                for j in (0,2):
                    if col>0 and abs(r[j]-30)<1e-7:r[j]=15
                    if col<len(widths)-1 and abs(r[j]-(cell['width']-30))<1e-7:r[j]=cell['width']-15
                for j in (1,3):
                    if row<len(heights)-1 and abs(r[j]-30)<1e-7:r[j]=15
                    if row>0 and abs(r[j]-(cell['height']-30))<1e-7:r[j]=cell['height']-15
            board=recessed_solid(shapes[0],shapes[1:],[],10,0,p['depth']-50,f'分區 {row+1}-{col+1}｜'+name)
            board.xform=QMatrix4x4();board.xform.translate(x/1000,.02,z/1000);body.adopt([*body.children,board])
    root=Group(name=f'組合櫃 {p["width"]/10:g}×{p["height"]/10:g}×{p["depth"]/10:g} cm（試驗中）');root.adopt(children);root.ext={PARAM_KEY:{'kind':'composite','params':p,'version':1},'lime_suite_generated':'composite','experimental':True}
    return bind_face_materials(root)


class CompositeWindow(GeometryWindow):
    def __init__(self,app):
        super().__init__(app,'組合櫃｜試驗中',920,900);self.target=None;self.editing=False;self.rigid=QMatrix4x4();self.style={};self.cells=[]
        self.body.addWidget(label('試驗中：以矩形網格分區，每區可選門片、抽屜或開放。共用隔板厚 3 cm。','muted'))
        c=card(self.body);form=QFormLayout();c.addLayout(form);self.fields={}
        for key,title,value in [('width','總寬',1200),('height','總高',1800),('depth','總深',600)]:field=length_number(value,100);field.setMaximum(20000);form.addRow(title,field);self.fields[key]=field
        self.columns=LockedSpinBox();self.columns.setRange(1,6);self.columns.setValue(2);self.rows=LockedSpinBox();self.rows.setRange(1,6);self.rows.setValue(2);form.addRow('分欄數',self.columns);form.addRow('分層數',self.rows)
        self.widths=LockedNumericText();self.widths.setPlaceholderText('例如 50, auto；單位 cm，左到右');self.heights=LockedNumericText();self.heights.setPlaceholderText('例如 60, auto；單位 cm，上到下');form.addRow('欄寬（留空均分）',self.widths);form.addRow('層高（留空均分）',self.heights)
        c.addWidget(button('更新分區表格（保留同位置設定）',self.rebuild_table));c.addWidget(button('沿用選取參數櫃體的面板／配件樣式',self.read_style));c.addWidget(button('讀取選取組合櫃',self.read_target))
        self.table=QTableWidget();self.table.setColumnCount(5);self.table.setHorizontalHeaderLabels(['分區（層由上而下）','配置','門片數','抽屜欄','抽屜層']);self.table.setMinimumHeight(320);self.body.addWidget(self.table)
        footer=QHBoxLayout();footer.addWidget(button('預覽',self.preview));self.apply_button=button('建立組合櫃',self.apply,'primary');footer.addWidget(self.apply_button);self.outer.insertLayout(self.outer.count()-1,footer);self.rebuild_table()
        items=list(self.scene.selection)
        if len(items)==1 and (items[0].ext or {}).get(PARAM_KEY,{}).get('kind')=='composite':self.read_target()
    def rebuild_table(self):
        old={(r,c):(mode.currentData(),*[f.value() for f in nums]) for r,c,mode,nums in self.cells};self.cells=[];self.table.setRowCount(self.columns.value()*self.rows.value())
        index=0
        for row in range(self.rows.value()):
            for col in range(self.columns.value()):
                self.table.setItem(index,0,QTableWidgetItem(f'{row+1} 層／{col+1} 欄'));mode=QComboBox()
                for name,key in [('門片','doors'),('抽屜','drawers'),('開放','open')]:mode.addItem(name,key)
                config=old.get((row,col),('doors',1,1,3));mode.setCurrentIndex(mode.findData(config[0]));self.table.setCellWidget(index,1,mode);nums=[]
                for j,value in enumerate(config[1:],2):
                    cell=QWidget();layout=QHBoxLayout(cell);layout.setContentsMargins(0,0,0,0);f=LockedSpinBox();f.setRange(1,20);f.setValue(value);layout.addWidget(f);f.attach_lock_button();self.table.setCellWidget(index,j,cell);nums.append(f)
                self.cells.append((row,col,mode,nums));self.table.setRowHeight(index,46);index+=1
        self.table.setColumnWidth(0,150);self.table.setColumnWidth(1,100)
        for i in range(2,5):self.table.setColumnWidth(i,185)
    def parameters(self):
        if len(self.cells)!=self.columns.value()*self.rows.value():raise ValueError('分欄／分層已變更，請先更新分區表格。')
        def sizes(widget):return [None if text.strip().lower()=='auto' else float(text)*10 for text in widget.text().replace('，',',').split(',')] if widget.text().strip() else []
        return {**{k:f.value() for k,f in self.fields.items()},'columns':self.columns.value(),'rows':self.rows.value(),'widths':sizes(self.widths),'heights':sizes(self.heights),'style':deepcopy(self.style),'zones':[dict(face_mode=mode.currentData(),panels=nums[0].value(),drawer_columns=nums[1].value(),drawer_rows=nums[2].value()) for r,c,mode,nums in self.cells]}
    def read_style(self):
        def run():
            items=selection(self.scene,True)
            if len(items)!=1:raise ValueError('請選一個參數櫃體。')
            self.style=parametric_info(items[0],'cabinet')['params'];self.style['handle_z_overrides']={};self.message('已沿用面板與配件；不沿用原把手高度覆寫。');return '已沿用面板與配件樣式。'
        self.run(run)
    def read_target(self):
        def run():
            self.check();items=selection(self.scene,True)
            if len(items)!=1:raise ValueError('請選一個組合櫃。')
            _,p,rigid=effective_parameters(items[0],'composite');self.target=items[0];self.editing=True;self.rigid=rigid;self.style=p['style'];self.version=self.scene.content_version
            for key,f in self.fields.items():f.setValue(p[key])
            self.columns.setValue(p['columns']);self.rows.setValue(p['rows']);self.widths.setText(', '.join('auto' if v is None else f'{v/10:g}' for v in p['widths']));self.heights.setText(', '.join('auto' if v is None else f'{v/10:g}' for v in p['heights']));self.rebuild_table()
            for (_,_,mode,nums),zone in zip(self.cells,p['zones']):mode.setCurrentIndex(mode.findData(zone['face_mode']));[f.setValue(zone[k]) for f,k in zip(nums,('panels','drawer_columns','drawer_rows'))]
            self.apply_button.setText('更新組合櫃');self.preview();return '已讀取組合櫃。'
        self.run(run)
    def preview(self):
        try:
            self.check(strict=self.editing);model=composite_model(self.parameters());model.xform=QMatrix4x4(self.rigid);self.preview_lines=parametric_preview(model,12000);self.version=self.scene.content_version;self.message('試驗預覽已更新；按建立／更新才寫入模型。')
        except Exception as exc:self.preview_lines=None;self.message(str(exc),True)
        self.vp.update()
    def apply(self):
        def run():
            self.check(strict=self.editing);model=composite_model(self.parameters())
            if self.editing:
                if selection(self.scene,True)!=[self.target]:raise ValueError('選取已變更，請重新讀取。')
                _,_,rigid=effective_parameters(self.target,'composite');execute(self.vp,UpdateParametric(self.target,model,rigid))
            else:execute(self.vp,AddGenerated(model));self.target=model;self.editing=True;self.apply_button.setText('更新組合櫃')
            self.version=self.scene.content_version;self.preview_lines=None;return '已套用組合櫃（試驗中），Ctrl＋Z 可復原。'
        self.run(run)


