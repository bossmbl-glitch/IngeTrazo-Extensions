# SPDX-License-Identifier: MIT
"""Lime 線段補面 0.1.1 — IngeTrazo 0.6.0, metres internally."""
from collections import defaultdict
import math
from PySide6.QtCore import QPointF, Qt, QSize, QByteArray
from PySide6.QtGui import QVector3D, QColor, QPen, QIcon, QPixmap, QAction
from PySide6.QtWidgets import QMessageBox, QToolBar
from tools.base import Tool
from core.mesh import Edge
from core.group import Group
from core.history import Command, _dirty_group_chunks

VERSION = '0.1.1'
PLANAR_TOL = 1e-5  # 0.01 mm; never projects or moves the source geometry
_APP = None
_MARKS = []


class OutlineError(ValueError):
    def __init__(self, message, points=()):
        super().__init__(message)
        self.points = list(points)


def xyz(v):
    return tuple(float(x) for x in v.toTuple())


def sub(a, b):
    return tuple(x-y for x, y in zip(a, b))


def dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def orient(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def on_segment(a, b, p, eps):
    return (abs(orient(a, b, p)) <= eps and
            min(a[0], b[0])-1e-10 <= p[0] <= max(a[0], b[0])+1e-10 and
            min(a[1], b[1])-1e-10 <= p[1] <= max(a[1], b[1])+1e-10)


def intersect(a, b, c, d, eps):
    x, y, z, w = orient(a,b,c), orient(a,b,d), orient(c,d,a), orient(c,d,b)
    return ((x > eps and y < -eps or x < -eps and y > eps) and
            (z > eps and w < -eps or z < -eps and w > eps) or
            on_segment(a,b,c,eps) or on_segment(a,b,d,eps) or
            on_segment(c,d,a,eps) or on_segment(c,d,b,eps))


def inside(p, poly):
    result = False
    for a, b in zip(poly, poly[1:]+poly[:1]):
        if (a[1] > p[1]) != (b[1] > p[1]):
            if p[0] < (b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0]:
                result = not result
    return result


def describe_loop(vertices):
    pts = [xyz(v.position) for v in vertices]
    origin = pts[0]
    local = [sub(p, origin) for p in pts]
    # Maximum cross product finds a reliable plane even for a bow-tie.
    axis = max(local, key=lambda p: dot(p,p))
    normal = max((cross(axis,p) for p in local), key=lambda p: dot(p,p))
    length = math.sqrt(dot(normal,normal))
    scale = max(math.sqrt(dot(p,p)) for p in local)
    if length <= max(1e-16, scale*scale*1e-10):
        raise OutlineError('輪廓共線或面積太小，無法建立面。', pts)
    normal = tuple(x/length for x in normal)
    drop = max(range(3), key=lambda i: abs(normal[i]))
    if normal[drop] < 0:
        normal = tuple(-x for x in normal)
    bad = [p for p in pts if abs(dot(sub(p,origin),normal)) > PLANAR_TOL]
    if bad:
        raise OutlineError('輪廓不共面（容許偏差 0.01 mm），請先修正高度。', bad)
    axes = [i for i in range(3) if i != drop]
    poly = [(p[axes[0]],p[axes[1]]) for p in local]
    eps = max(1e-18, scale*scale*1e-12)
    n = len(poly)
    for i in range(n):
        # Reject backtracking at consecutive edges as well as crossings.
        a, b, c = poly[i-1], poly[i], poly[(i+1)%n]
        if abs(orient(a,b,c)) <= eps and (a[0]-b[0])*(c[0]-b[0])+(a[1]-b[1])*(c[1]-b[1]) > eps:
            raise OutlineError('輪廓有重疊或折返線段。', [pts[i]])
        for j in range(i+1,n):
            if j == i+1 or (i == 0 and j == n-1):
                continue
            if intersect(poly[i],poly[(i+1)%n],poly[j],poly[(j+1)%n],eps):
                raise OutlineError('輪廓有自交或重疊線段。', [pts[i],pts[j]])
    # Consistent winding, including vertical and tilted planes.
    area_vec = (0.,0.,0.)
    for a,b in zip(local,local[1:]+local[:1]):
        q = cross(a,b)
        area_vec = tuple(x+y for x,y in zip(area_vec,q))
    if abs(dot(area_vec,normal)) <= eps:
        raise OutlineError('輪廓面積太小。', pts)
    if dot(area_vec,normal) < 0:
        vertices = list(reversed(vertices))
    return dict(vertices=vertices, points=pts, origin=origin, normal=normal, axes=axes, eps=eps)


def project(loop, frame):
    return [tuple(sub(p,frame['origin'])[i] for i in frame['axes']) for p in loop['points']]


def plan_faces(mesh, edges):
    """Read-only validation: all supplied edges must form simple closed loops."""
    edges = set(edges)
    if not edges:
        return []
    if len(edges) > 3000:
        raise OutlineError('單一範圍超過 3000 條線，請分批選取補面。')
    adj = defaultdict(list)
    for edge in edges:
        adj[edge.v0].append(edge)
        adj[edge.v1].append(edge)
    bad = [v for v, es in adj.items() if len(es) != 2]
    if bad:
        ends = sum(len(adj[v]) == 1 for v in bad)
        branches = len(bad)-ends
        raise OutlineError(f'輪廓未封閉或有分岔：斷點 {ends} 個、分岔 {branches} 個。\n請連接斷點或只選取外框線。', [xyz(v.position) for v in bad])
    remaining = set(edges)
    loops = []
    while remaining:
        first = next(iter(remaining))
        start, current = first.v0, first.v0
        ordered = []
        edge = first
        while True:
            ordered.append(current)
            remaining.remove(edge)
            current = edge.other(current)
            if current is start:
                break
            edge = next(e for e in adj[current] if e in remaining)
        if len(ordered) < 3:
            raise OutlineError('輪廓至少需要三個不同頂點。')
        loops.append(describe_loop(ordered))
    containers = [set() for _ in loops]
    for i,a in enumerate(loops):
        pa = project(a,a)
        for j,b in enumerate(loops):
            if j <= i:
                continue
            if abs(dot(a['normal'],b['normal'])) < 1-1e-8:
                continue
            if max(abs(dot(sub(p,a['origin']),a['normal'])) for p in b['points']) > PLANAR_TOL:
                continue
            pb = project(b,a)
            if any(intersect(c,d,e,f,max(a['eps'],b['eps']))
                   for c,d in zip(pa,pa[1:]+pa[:1])
                   for e,f in zip(pb,pb[1:]+pb[:1])):
                raise OutlineError('兩個輪廓相交或接觸，請分開或整理交點。', a['points']+b['points'])
            if inside(pa[0],pb):
                containers[i].add(j)
            if inside(pb[0],pa):
                containers[j].add(i)
    plans = []
    for i,loop in enumerate(loops):
        if len(containers[i]) % 2:
            continue
        holes = [list(reversed(b['vertices'])) for j,b in enumerate(loops)
                 if i in containers[j] and len(containers[j]) == len(containers[i])+1]
        rim = set(loop['vertices'])
        hole_sets = {frozenset(h) for h in holes}
        existing = any(set(f.loop) == rim and {frozenset(h) for h in f.hole_loops} == hole_sets for f in mesh.faces)
        if not existing:
            plans.append((loop['vertices'],holes))
    return plans


class FillFacesCommand(Command):
    def __init__(self, jobs):
        self.jobs = jobs
        self.before = []
        self.after = []
        self.count = sum(len(plans) for mesh,plans in jobs)

    def do(self, scene):
        if self.after:
            for (mesh,_),state in zip(self.jobs,self.after):
                mesh.restore_state(state)
        else:
            self.before = [mesh.capture_state() for mesh,_ in self.jobs]
            try:
                for mesh,plans in self.jobs:
                    for outer,holes in plans:
                        face = mesh.add_face([v.position for v in outer],
                                             [[v.position for v in h] for h in holes] or None)
                        layers = {e.layer for e in mesh.edges if e.v0 in outer and e.v1 in outer}
                        if len(layers) == 1:
                            layer = next(iter(layers))
                            if layer is not None:
                                face.attrs['layer'] = layer
                self.after = [mesh.capture_state() for mesh,_ in self.jobs]
            except Exception:
                for (mesh,_),state in zip(self.jobs,self.before):
                    mesh.restore_state(state)
                raise
        _dirty_group_chunks(scene)
        scene.version += 1

    def undo(self, scene):
        for (mesh,_),state in zip(self.jobs,self.before):
            mesh.restore_state(state)
        _dirty_group_chunks(scene)
        scene.version += 1


def selected_targets(scene):
    selected = list(scene.selection)
    jobs = []
    direct = [e for e in selected if isinstance(e,Edge) and e in scene.mesh.edges]
    if direct:
        jobs.append(('選取線段',scene.mesh,direct,None))
    for group in selected:
        if not isinstance(group,Group):
            continue
        if group.locked:
            raise OutlineError(f'「{group.name}」已鎖定，請先解鎖。')
        if group.is_component():
            raise OutlineError(f'「{group.name}」是元件。請進入元件編輯後選取輪廓線補面。')
        if group.children:
            raise OutlineError(f'「{group.name}」含巢狀群組。請進入需要補面的那一層再選取。')
        jobs.append((group.name,group.mesh,[e for e in group.mesh.edges if not e.hidden],group.xform))
    if not jobs:
        raise OutlineError('請先選取封閉輪廓線，或選取只含輪廓線的群組。\n可進入群組後選取線段，再執行本工具。')
    unique = {}
    for name,mesh,edges,matrix in jobs:
        if id(mesh) not in unique:
            unique[id(mesh)] = (name,mesh,edges,matrix)
    return list(unique.values())


def fill(viewport):
    global _MARKS
    _MARKS = []
    try:
        jobs = []
        for name,mesh,edges,matrix in selected_targets(viewport.scene):
            if not edges:
                continue
            try:
                plans = plan_faces(mesh,edges)
            except OutlineError as exc:
                for p in exc.points:
                    q = QVector3D(*p)
                    _MARKS.append(matrix.map(q) if matrix is not None else q)
                raise OutlineError(f'【{name}】\n{exc}') from exc
            if plans:
                jobs.append((mesh,plans))
        if not jobs:
            viewport.flash_status('沒有需要新增的面：可能已補面，或群組沒有線段。',5000)
            viewport.update()
            return
        cmd = FillFacesCommand(jobs)
        viewport.history.execute(cmd)
        if viewport.history.last_error or getattr(viewport.history,'last_rejected',None):
            raise RuntimeError(viewport.history.last_error or viewport.history.last_rejected)
        viewport.notify_scene_changed()
        viewport.flash_status(f'線段補面完成：新增 {cmd.count} 個面。Ctrl+Z 可復原。',6000)
    except OutlineError as exc:
        viewport.update()
        hint = '\n\n紅點為問題位置（再次執行或模型變更後清除）。' if _MARKS else ''
        QMessageBox.information(viewport,'萊姆｜線段補面',str(exc)+hint)
    except Exception as exc:
        QMessageBox.warning(viewport,'萊姆｜線段補面',f'補面未完成：{exc}')


class LimeFillFacesTool(Tool):
    name = '萊姆｜線段補面'
    shortcut = None
    description = '選取封閉共面輪廓線或群組，保留凹口與圓角建立面；支援孔洞與復原。'
    def on_activate(self,viewport):
        fill(viewport)
    def on_deactivate(self,viewport):
        pass



_ICON_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path d="M10 52V20h13v-7h25q7 0 7 7v24q0 8-8 8Z" fill="#42b7d0" stroke="#163d5b" stroke-width="4" stroke-linejoin="round"/><path d="m17 44 24-23m-14 23 20-19" stroke="#baf1f7" stroke-width="3" stroke-linecap="round"/><g fill="#fff" stroke="#163d5b" stroke-width="2"><circle cx="10" cy="52" r="3"/><circle cx="10" cy="20" r="3"/><circle cx="23" cy="13" r="3"/></g><circle cx="49" cy="48" r="13" fill="#f2aa39" stroke="#163d5b" stroke-width="3"/><path d="M49 41v14m-7-7h14" stroke="#163d5b" stroke-width="4" stroke-linecap="round"/></svg>'

def install_toolbar(app):
    """Register a persistent native toolbar; resolve the active tab on click."""
    window = app.window
    name = 'lime_fill_faces_toolbar'
    toolbar = window.findChild(QToolBar, name)
    if toolbar is not None:
        return toolbar
    toolbar = QToolBar('萊姆｜線段補面', window)
    toolbar.setObjectName(name)
    toolbar.setMovable(True)
    toolbar.setFloatable(True)
    toolbar.setAllowedAreas(Qt.AllToolBarAreas)
    toolbar.setIconSize(QSize(32, 32))
    toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
    pixmap = QPixmap()
    if not pixmap.loadFromData(QByteArray(_ICON_SVG), 'SVG'):
        raise RuntimeError('補面圖示載入失敗')
    action = QAction(QIcon(pixmap), '補面', toolbar)
    action.setObjectName('lime_fill_faces_action')
    action.setToolTip('線段補面：選取封閉輪廓線或群組後，按一下建立面')
    action.setStatusTip('保留圓角、凹口與內孔；Ctrl+Z 可復原')
    action.triggered.connect(lambda checked=False: fill(app.viewport))
    toolbar.addAction(action)
    window.addToolBar(Qt.TopToolBarArea, toolbar)
    return toolbar


def setup(app):
    global _APP
    _APP = app
    install_toolbar(app)
    def clear(*args):
        _MARKS.clear()
        app.viewport.update()
    app.on_document_changed(clear)
    def paint(vp,painter):
        painter.setPen(QPen(QColor('#ffffff'),1))
        painter.setBrush(QColor('#ed3c38'))
        for point in _MARKS[:500]:
            q = vp.world_to_pixel(point)
            if q is not None:
                painter.drawEllipse(QPointF(*q),5,5)
    app.add_overlay(paint)
