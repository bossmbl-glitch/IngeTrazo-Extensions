# SPDX-License-Identifier: MIT
"""萊姆兩點出風口／燈條 v0.2.0 — IngeTrazo 0.6.0; internal metres, UI cm."""
import math
import json
from functools import lru_cache
from copy import deepcopy
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt, QSize, QPointF
from PySide6.QtGui import QVector3D, QMatrix4x4, QIcon, QColor, QPen
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel,
    QPushButton, QDoubleSpinBox, QToolBar)
from core.mesh import Mesh
from core.group import Group
from core.history import Command
from tools.base import Tool

VERSION = '0.2.0'
KEY = 'lime_two_point_vent'
ROOT = Path(__file__).resolve().parent
# AA01 actual geometry, removing the two overlapping duplicate strips.
STRIP_Y = [-.01059486065, -.020440083, -.03028530627, -.04013052955,
           -.04997574911, -.05982097238, -.06966619939, -.07951141894,
           -.08982097358, -.09982097149]
DEFAULT = dict(length=103., width=12., strip_t=.5845222622,
               strip_h=3.5071335733, baffle_t=.5845222622,
               baffle_h=1.9224002957, max_gap=25.75)
BFL_Z = .022999999
EDGES = [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),
         (0,4),(1,5),(2,6),(3,7)]
FACES = [(3,2,1,0),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]


def layout(p):
    p = {k: float(p[k]) for k in DEFAULT}
    if any(not math.isfinite(v) or v <= 0 for v in p.values()):
        raise ValueError('所有尺寸必須是大於零的有限數值。')
    if p['length'] < .1 or p['length'] > 100000:
        raise ValueError('長度範圍為 0.1～100000 cm。')
    if p['strip_t'] >= .984522 * p['width']/12:
        raise ValueError('長料厚度超過目前寬度可容納的間距，請減小厚度或加寬。')
    if p['strip_t'] >= p['width'] * .168:
        raise ValueError('寬度過小，最外側長料會超出範圍。')
    L, t, g = (p[k] / 100 for k in ('length','baffle_t','max_gap'))
    # n bays; n-1 baffles. Every bay, including both ends, has equal clear width.
    n = max(1, math.ceil((L+t)/(g+t) - 1e-10))
    gap = (L-(n-1)*t)/n
    if gap <= 1e-7:
        raise ValueError('長度不足以放入指定厚度的擋板。')
    if n > 1001:
        raise ValueError('擋板超過 1000 片，請增大最大淨距或縮短長度。')
    return p, gap, [gap+i*(gap+t) for i in range(n-1)]


def boxes(p):
    p, gap, positions = layout(p)
    L, W, st, sh, bt, bh = [p[k]/100 for k in
        ('length','width','strip_t','strip_h','baffle_t','baffle_h')]
    parts = []
    for i, y in enumerate(STRIP_Y):
        high = y * W / .12
        parts.append(('長料_%02d' % (i+1), [0,high-st,0], [L,high,sh]))
    for i, x in enumerate(positions):
        parts.append(('擋板_%02d' % (i+1), [x,-W,BFL_Z], [x+bt,0,BFL_Z+bh]))
    return parts, gap, len(positions)


def corners(lo, hi):
    x,y,z = lo; X,Y,Z = hi
    return np.array([(x,y,z),(X,y,z),(X,Y,z),(x,Y,z),
                     (x,y,Z),(X,y,Z),(X,Y,Z),(x,Y,Z)], dtype=float)


def placement(start, end):
    start, end = np.asarray(start, float), np.asarray(end, float)
    d = end-start; length = np.linalg.norm(d)
    if length < .001:
        raise ValueError('起點與終點距離至少需要 0.1 cm。')
    x = d/length
    up = np.array([0.,0.,1.])
    if abs(np.dot(x,up)) > .999: up = np.array([0.,1.,0.])
    z = up-x*np.dot(up,x); z /= np.linalg.norm(z)
    y = np.cross(z,x)
    m = np.eye(4); m[:3,:3] = np.column_stack((x,y,z)); m[:3,3] = start
    return m, length*100


def qmatrix(m):
    return QMatrix4x4(*map(float, np.asarray(m).ravel()))


def model(p):
    parts, _, _ = boxes(p)
    root = Group(name='兩點出風口'); root.xform = QMatrix4x4()
    root.ext = {KEY: dict(version=VERSION, params=deepcopy(p))}
    for name, lo, hi in parts:
        pts = corners(lo,hi); mesh = Mesh()
        for face in FACES:
            f = mesh.add_face([QVector3D(*map(float,pts[j])) for j in face])
            if f is None: raise ValueError('尺寸過小，無法建立有效面。')
        child = Group(mesh, name=name)
        child.material = {'color': [0.,0.,0.] if name.startswith('擋板_') else [1.,1.,1.]}
        root.children.append(child)
    return root


def root_context(scene):
    if scene.edit_group is not None:
        raise ValueError('請先退出群組編輯，再使用兩點繪製工具。')


def editable(scene, target):
    if target not in scene.groups or target.hidden or scene.entity_locked(target):
        raise ValueError('物件已移除、隱藏或鎖定，請重新選取。')
    visible, locked = scene._layer_state(target)
    if not visible or locked: raise ValueError('物件圖層已隱藏或鎖定。')


class WriteVent(Command):
    keeps_paint = True
    def __init__(self, new, target=None):
        self.new, self.target = new, target
        self.before = None
    def do(self, scene):
        root_context(scene)
        if self.before is None:
            if LAMP_KEY in (self.new.ext or {}): register_lamp_materials(scene,self.new)
            self.selection = set(scene.selection)
            if self.target:
                editable(scene,self.target)
                self.before = (self.target.mesh,self.target.children,self.target.ext)
                old = {c.name:c for c in self.target.children or ()}
                fallback = next((c for c in old.values() if c.name.startswith('擋板_')),None)
                for c in self.new.children:
                    prev = old.get(c.name, fallback if c.name.startswith('擋板_') else None)
                    if prev is not None:
                        if prev.material is not None: c.material = deepcopy(prev.material)
                        c.layer = prev.layer
                        for f, prior in zip(c.mesh.faces,prev.mesh.faces):
                            f.attrs = deepcopy(prior.attrs)
                self.new.ext = {**deepcopy(self.target.ext or {}), **self.new.ext}
            else: self.before = True
        if self.target:
            editable(scene,self.target)
            self.target.mesh,self.target.children,self.target.ext = self.new.mesh,self.new.children,self.new.ext
            obj = self.target
        else:
            if self.new not in scene.groups: scene.groups.append(self.new)
            obj = self.new
        scene.selection.clear(); scene.selection.add(obj); scene.version += 1
    def undo(self, scene):
        if self.target:
            self.target.mesh,self.target.children,self.target.ext = self.before
        elif self.new in scene.groups: scene.groups.remove(self.new)
        scene.selection.clear(); scene.selection.update(self.selection); scene.version += 1


def execute(vp, command):
    vp.history.execute(command)
    error = getattr(vp.history,'last_error',None) or getattr(vp.history,'last_rejected',None)
    if error: raise ValueError(str(error))
    vp.notify_scene_changed()


class VentTool(Tool):
    name = '兩點繪製出風口'
    uses_snap = True
    def __init__(self, window):
        self.window = window; self.start_point = None; self.end = None
        self.scene = window.app.scene; self.scene_version = self.scene.content_version
        self.lines = None; self.matrix = None; self.p = None
    def on_activate(self, vp):
        vp.flash_status('點選起點，再點終點。Enter／空白鍵確認預覽；Esc 取消。',7000)
    def on_deactivate(self, vp):
        self.lines = None; vp.update()
    def claims_key(self,key,modifiers):
        return key in (Qt.Key_Escape,Qt.Key_Return,Qt.Key_Enter,Qt.Key_Space)
    def on_key(self,vp,key,modifiers):
        if key == Qt.Key_Escape: self.window.stop(); return True
        if key in (Qt.Key_Return,Qt.Key_Enter,Qt.Key_Space):
            if self.lines is not None: self.finish()
            return True
        return False
    def on_cancel(self,vp): self.window.stop()
    def on_hover(self,ctx):
        self.end = np.array([ctx.world.x(),ctx.world.y(),ctx.world.z()])
        self.refresh()
    def refresh(self):
        self.lines = None
        if self.start_point is None or self.end is None: return
        try:
            a = self.start_point
            self.matrix, length = placement([a.x(),a.y(),a.z()],self.end)
            self.p = self.window.parameters(); self.p['length'] = length
            segments, description = self.window.preview(self.p)
            self.lines = np.asarray(segments) @ self.matrix[:3,:3].T + self.matrix[:3,3]
            self.window.info.setText(description)
        except Exception as exc: self.window.info.setText(str(exc))
        self.window.vp.update()
    def on_click(self,ctx):
        if self.start_point is None:
            self.start_point = QVector3D(ctx.world)
            self.window.info.setText('起點已設定，移動滑鼠預覽後點選終點。')
            self.window.vp.flash_status('起點已設定，請移動滑鼠並點選終點。',7000)
        else:
            self.on_hover(ctx)
            if self.lines is not None: self.finish()
    def finish(self):
        try:
            if self.window.app.scene is not self.scene or self.scene.content_version != self.scene_version:
                raise ValueError('模型已變更，請取消後重新開始繪製。')
            root_context(self.window.app.scene)
            group = self.window.build(self.p); group.xform = qmatrix(self.matrix)
            execute(self.window.vp,WriteVent(group))
            self.window.fields['length'].setValue(self.p['length'])
            self.window.stop()
            self.window.info.setText('已建立獨立群組。Ctrl+Z 可復原；選取整組後可修改設定。')
        except Exception as exc: self.window.info.setText(str(exc))


class NoWheelSpin(QDoubleSpinBox):
    def wheelEvent(self,event): event.ignore()


class VentWindow(QDialog):
    param_key = KEY
    def build(self,p): return model(p)
    def validate(self,p): return layout(p)
    def preview(self,p):
        parts,gap,count = boxes(p)
        lines=[]
        for _,lo,hi in parts:
            pts=corners(lo,hi)
            lines.extend([(pts[a],pts[b]) for a,b in EDGES])
        return lines,f'長度 {p["length"]:.2f} cm｜擋板 {count} 片｜每格淨距 {gap*100:.3f} cm'
    def __init__(self,app):
        super().__init__(app.window,Qt.Tool)
        self.app = app; self.vp = app.viewport; self.tool = None; self.target = None
        self.setWindowTitle('萊姆兩點出風口 v'+VERSION+'｜試用版')
        self.resize(400,570)
        self.setStyleSheet('QDialog{background:#f3f6f5;} QLabel{color:#24483d;font-size:14px;} '
            'QPushButton{padding:10px;background:#e4f1eb;border:1px solid #91b6a6;border-radius:6px;} '
            'QDoubleSpinBox{padding:5px;min-height:23px;}')
        body = QVBoxLayout(self)
        title = QLabel('兩點出風口 · 擋板自動均分');title.setStyleSheet('font-size:20px;font-weight:bold;');body.addWidget(title)
        note = QLabel('AA01 截面｜10 支長料｜兩端無封板\n點起點 → 移動預覽 → 點終點完成');note.setWordWrap(True);body.addWidget(note)
        form = QFormLayout(); self.fields = {}
        for key,label in [('length','總長度'),('width','總寬度'),('strip_t','長料厚度'),
            ('strip_h','長料高度'),('baffle_t','擋板厚度'),('baffle_h','擋板高度'),('max_gap','每格最大淨距')]:
            spin = NoWheelSpin();spin.setDecimals(6);spin.setRange(.001,100000.);spin.setSuffix(' cm');spin.setValue(DEFAULT[key]);spin.setSingleStep(.1)
            self.fields[key] = spin;form.addRow(label,spin);spin.valueChanged.connect(self.changed)
        body.addLayout(form)
        for text,fn in [('開始兩點繪製',self.start),('讀取選取的出風口',self.load),('套用設定至選取出風口',self.apply),('取消繪製',self.stop)]:
            btn = QPushButton(text);btn.setAutoDefault(False);btn.clicked.connect(fn);body.addWidget(btn)
        self.info = QLabel('最大淨距預設 25.75 cm；中間及兩端淨距相同。');self.info.setWordWrap(True);body.addWidget(self.info)
        tip = QLabel('總長度欄用於修改已建立物件；繪製時由兩點距離決定。\n以起點側邊為基準，寬度向右側展開，高度朝上。');tip.setWordWrap(True);body.addWidget(tip)
    def parameters(self): return {k:s.value() for k,s in self.fields.items()}
    def changed(self,*_):
        if self.tool is not None: self.tool.refresh()
    def selected(self):
        root_context(self.app.scene)
        items = list(self.app.scene.selection)
        if len(items)!=1 or not isinstance(items[0],Group) or self.param_key not in (items[0].ext or {}):
            raise ValueError('請只選取一個由此工具建立的對應物件整組。')
        editable(self.app.scene,items[0]);return items[0]
    def load(self):
        try:
            self.stop(); self.target = self.selected()
            p = self.target.ext[self.param_key]['params']
            for k,s in self.fields.items(): s.setValue(p[k])
            self.info.setText('已讀取選取物件，可修改後按「套用設定」。')
        except Exception as exc: self.info.setText(str(exc))
    def apply(self):
        try:
            self.stop(); target = self.selected(); p = self.parameters()
            execute(self.vp,WriteVent(self.build(p),target))
            self.info.setText('已更新，保留起點與方向。Ctrl+Z 可復原。')
        except Exception as exc: self.info.setText(str(exc))
    def start(self):
        try:
            self.stop(); root_context(self.app.scene); self.validate(self.parameters())
            self.vp = self.app.viewport;self.old_tool = self.vp.active_tool
            self.tool = VentTool(self);self.tool.name = '兩點繪製燈條' if self.param_key != KEY else '兩點繪製出風口';self.vp.set_active_tool(self.tool);self.vp.setFocus()
            self.info.setStyleSheet('color:#14633f;font-weight:bold;')
            self.info.setText('繪製已啟動：請在模型中點選第一個起點。')
        except Exception as exc:
            self.tool = None
            self.info.setStyleSheet('color:#b22222;font-weight:bold;')
            self.info.setText('無法開始：'+str(exc))
            self.app.viewport.flash_status('無法開始：'+str(exc),10000)
    def stop(self):
        if self.tool is not None and self.vp.active_tool is self.tool:
            self.vp.set_active_tool(self.old_tool)
        self.tool = None;self.vp.update()
    def closeEvent(self,event): self.stop();super().closeEvent(event)
    def reject(self): self.stop();super().reject()


def draw_window_overlay(w,vp,painter):
    if w is None or w.vp is not vp or w.tool is None or vp.active_tool is not w.tool: return
    tool = w.tool
    if tool.lines is None: return
    pts = tool.lines.reshape(-1,3);x,y,front = vp.world_to_pixels(pts)
    painter.save();painter.setPen(QPen(QColor('#0b9b77'),1.5))
    for i in range(0,len(pts),2):
        if front[i] and front[i+1]:
            painter.drawLine(QPointF(float(x[i]),float(y[i])),QPointF(float(x[i+1]),float(y[i+1])))
    painter.restore()


def overlay(app,vp,painter):
    for attr in ('_lime_two_point_vent_window','_lime_two_point_lamp_window'):
        draw_window_overlay(getattr(app.window,attr,None),vp,painter)


def show(app,edit=False):
    w = getattr(app.window,'_lime_two_point_vent_window',None)
    if w is None:
        w = VentWindow(app);app.window._lime_two_point_vent_window = w
    other = getattr(app.window,"_lime_two_point_lamp_window",None)
    if other is not None: other.stop()
    w.show();w.raise_();w.activateWindow()
    if edit: w.load()
    else: w.start()


def setup(app):
    if getattr(app.window,'_lime_two_point_vent_toolbar',None): return
    toolbar = QToolBar('萊姆兩點工具：出風口／燈條',app.window)
    toolbar.setObjectName('lime_two_point_vent_toolbar');toolbar.setIconSize(QSize(32,32))
    for title,icon,edit in [('兩點繪製出風口','draw.svg',False),('修改出風口設定','edit.svg',True)]:
        action = app.add_menu_action(title,lambda edit=edit:show(app,edit),tip='AA01 出風口：兩點繪製，依長度增減擋板並均分淨距')
        if action is not None:
            action.setIcon(QIcon(str(ROOT/'lime_two_point_vent_assets'/icon)))
            action.setAutoRepeat(False);toolbar.addAction(action)
    for title,icon,edit in [('兩點繪製燈條','lamp_draw.svg',False),('修改燈條設定','lamp_edit.svg',True)]:
        action=app.add_menu_action(title,lambda edit=edit:show_lamp(app,edit),tip='ddd 原型燈條：兩點延長中段，截面與端蓋及材質固定')
        if action is not None:
            action.setIcon(QIcon(str(ROOT/'lime_two_point_vent_assets'/icon)))
            action.setAutoRepeat(False);toolbar.addAction(action)
    app.window.addToolBar(toolbar);app.window._lime_two_point_vent_toolbar = toolbar
    app.add_overlay(lambda vp,painter:overlay(app,vp,painter))


LAMP_KEY = 'lime_two_point_lamp'


@lru_cache(maxsize=1)
def lamp_stock():
    return json.loads((ROOT/'lime_two_point_vent_assets'/'lamp_stock.json').read_text(encoding='utf-8'))


def lamp_validate(p):
    length=float(p['length'])
    if not math.isfinite(length) or not .5 <= length <= 100000:
        raise ValueError('燈條總長度需為 0.5～100000 cm；兩端端蓋合計 0.4 cm。')
    return length


def lamp_vertices(points,length):
    a=np.array(points,dtype=float,copy=True)
    # Piecewise stretch: same coordinates in both fixed caps, extend only middle.
    x=a[:,0].copy();L=length/100
    a[:,0]=np.where(x<=.00200001,x,
        np.where(x>=.11599999,x+(L-.118),.002+(x-.002)*(L-.004)/.114))
    return a


def lamp_model(p):
    length=lamp_validate(p)
    root=Group(name='兩點燈條');root.xform=QMatrix4x4()
    root.ext={LAMP_KEY:dict(version=VERSION,params={'length':length})}
    for part in lamp_stock()['parts']:
        child=Group(name=part['name'])
        for f in part['faces']:
            pts=lamp_vertices(f['vertices'],length)
            face=child.mesh.add_face([QVector3D(*map(float,v)) for v in pts])
            if face is None: raise ValueError('燈條尺寸過小，無法建立有效面。')
            face.attrs=deepcopy(f['attrs'])
        root.children.append(child)
    return root


def register_lamp_materials(scene,group):
    from core.materials import Material,register
    mapping={m['name']:register(scene.materials,Material.from_dict(m)) for m in lamp_stock()['materials']}
    # register() preserves existing different materials by choosing a unique name.
    for child in group.children:
        for face in child.mesh.faces:
            if face.attrs.get('mat') in mapping:face.attrs['mat']=mapping[face.attrs['mat']]


class LampWindow(VentWindow):
    param_key=LAMP_KEY
    def __init__(self,app):
        QDialog.__init__(self,app.window,Qt.Tool)
        self.app=app;self.vp=app.viewport;self.tool=None;self.target=None
        self.setWindowTitle('萊姆兩點燈條 v'+VERSION+'｜試用版');self.resize(405,410)
        self.setStyleSheet('QDialog{background:#f3f6f5;} QLabel{color:#24483d;font-size:14px;} '
            'QPushButton{padding:10px;background:#e4f1eb;border:1px solid #91b6a6;border-radius:6px;} '
            'QDoubleSpinBox{padding:5px;min-height:23px;}')
        body=QVBoxLayout(self)
        title=QLabel('兩點燈條 · 沿用原模型材質');title.setStyleSheet('font-size:20px;font-weight:bold;');body.addWidget(title)
        note=QLabel('ddd 原型｜截面 1.4 × 1.4 cm\n端蓋各 0.2 cm，燈罩寬 1.0、厚 0.2 cm\n只延長中段，保留原鐵件與燈罩材質。');note.setWordWrap(True);body.addWidget(note)
        form=QFormLayout();spin=NoWheelSpin();spin.setDecimals(3);spin.setRange(.5,100000);spin.setSuffix(' cm');spin.setValue(11.8);spin.valueChanged.connect(self.changed)
        self.fields={'length':spin};form.addRow('總長度',spin);body.addLayout(form)
        for text,fn in [('開始兩點繪製',self.start),('讀取選取的燈條',self.load),('套用設定至選取燈條',self.apply),('取消繪製',self.stop)]:
            btn=QPushButton(text);btn.setAutoDefault(False);btn.clicked.connect(fn);body.addWidget(btn)
        self.info=QLabel('點起點 → 移動預覽 → 點終點完成。');self.info.setWordWrap(True);body.addWidget(self.info)
        tip=QLabel('繪製時總長由兩點距離決定；此欄用於修改既有燈條。\n起點位於端蓋中心線的原模型基準高度。');tip.setWordWrap(True);body.addWidget(tip)
    def build(self,p):return lamp_model(p)
    def validate(self,p):return lamp_validate(p)
    def preview(self,p):
        length=lamp_validate(p);segments=[]
        for part in lamp_stock()['parts']:
            for face in part['faces']:
                pts=lamp_vertices(face['vertices'],length)
                segments.extend([(pts[i],pts[(i+1)%len(pts)]) for i in range(len(pts))])
        return segments,f'燈條長度 {length:.2f} cm｜中段 {length-.4:.2f} cm｜端蓋各 0.2 cm'


def show_lamp(app,edit=False):
    other=getattr(app.window,'_lime_two_point_vent_window',None)
    if other is not None:other.stop()
    w=getattr(app.window,'_lime_two_point_lamp_window',None)
    if w is None:
        w=LampWindow(app);app.window._lime_two_point_lamp_window=w
    w.show();w.raise_();w.activateWindow()
    if edit:w.load()
    else:w.start()
