# SPDX-License-Identifier: GPL-3.0-or-later
"""Quick Profile 2.2.4 — standalone IngeTrazo 0.6.0 extension.

Geometry uses metres internally. Install this package directory in the per-user plugins folder.
Scene geometry is committed only on generation or finishing a drawn path.
"""
from __future__ import annotations

import json
import copy
import math
import os
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QTimer, QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QVector3D, QIcon, QPixmap, QBrush, QPolygonF, QAction, QCursor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QPushButton, QDoubleSpinBox, QPlainTextEdit, QMessageBox,
    QFormLayout, QGroupBox, QCheckBox, QLineEdit, QInputDialog, QFileDialog,
    QToolButton, QDialog, QDialogButtonBox, QGridLayout, QSizePolicy, QFrame, QMenu, QColorDialog, QToolBar)
from core.history import SnapshotImport
from core.mesh import Edge, Face, Mesh
from core.group import Group
from tools.base import Tool
from .profile_browser import ProfileBrowser
from .profile_storage import save_profile, find_owned, archive_owned, rename_owned, read_owned
from .last_used_state import load as load_last_used
from .profile_preferences import identity as profile_identity, read as read_profile_preferences, write as write_profile_preferences, move as move_profile_preferences, forget as forget_profile_preferences

PLUGIN_KEY = "quick_profile"
EPS = 1e-8

# Profile coordinates in nominal millimetres. Local u=width; v=height.
PRESETS = {
    "001｜矩形": [[0, 0], [17, 0], [17, 17], [0, 17]],
    "012｜階梯線板": [[0, 0], [17, 0], [17, 5], [12, 5], [12, 10], [7, 10], [7, 17], [0, 17]],
    "058｜弧形線板": [[0, 0], [17, 0], [17, 3], [13, 3]] +
        [[13 - 10 * math.sin(i * math.pi / 24), 13 - 10 * math.cos(i * math.pi / 24)] for i in range(1, 13)] +
        [[3, 17], [0, 17]],
}


def v_add(a, b): return tuple(x+y for x,y in zip(a,b))
def v_sub(a, b): return tuple(x-y for x,y in zip(a,b))
def v_mul(a, s): return tuple(x*s for x in a)
def v_dot(a, b): return sum(x*y for x,y in zip(a,b))
def v_cross(a, b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def v_norm(a): return math.sqrt(v_dot(a,a))
def v_unit(a):
    length = v_norm(a)
    if length < EPS: raise ValueError("路徑包含長度為零的線段。")
    return v_mul(a, 1.0/length)

def as_tuple(p): return (float(p.x()), float(p.y()), float(p.z()))
def qvec(p): return QVector3D(*p)


def clean_polygon(poly):
    points = [(float(p[0]), float(p[1])) for p in poly]
    if len(points)>1 and abs(points[0][0]-points[-1][0])+abs(points[0][1]-points[-1][1])<1e-7:
        points.pop()
    compact=[]
    for p in points:
        if not compact or math.dist(p, compact[-1]) > 1e-7: compact.append(p)
    if len(compact)<3: raise ValueError("剖面至少需要三個不重複的頂點。")
    area=sum(compact[i][0]*compact[(i+1)%len(compact)][1]-compact[(i+1)%len(compact)][0]*compact[i][1] for i in range(len(compact)))
    if abs(area)<1e-7: raise ValueError("剖面面積不可為零。")
    return compact



def section_dimensions_mm(section):
    """Return unrotated 2D section extents in millimetres.

    The section loader and face capture both supply points in mm; width and
    height must come from full bounds, not just the first two vertices.
    """
    polygon = clean_polygon(section)
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    if not all(math.isfinite(v) and v > 1e-7 for v in (width, height)):
        raise ValueError('剖面寬度與高度必須大於零。')
    return width, height



def face_section_points_mm(vertices):
    """Project a planar face onto stable world-oriented profile axes.

    The previous capture used the first perimeter edge as local X and the
    face winding to derive local Y. Neither is stable: the same physical face
    can appear rotated or inverted depending on vertex order. The viewport
    preview uses the stored 2D points, so normalize at capture time.

    Upright faces use world +Z as profile up and a positive horizontal world
    axis as profile right. Horizontal faces use +X right and +Y up. No camera
    dependent transforms are used, and source geometry is never modified.
    Input world coordinates are in metres; output is millimetres.
    """
    points = [tuple(float(c) for c in point) for point in vertices]
    if len(points) < 3 or any(len(p) != 3 or not all(math.isfinite(x) for x in p) for p in points):
        raise ValueError('請選取有效的平面封閉剖面。')
    # Newell's method does not depend on a particular first edge and copes
    # with polygons whose initial vertices happen to be collinear.
    n = (0., 0., 0.)
    for a, b in zip(points, points[1:] + points[:1]):
        n = v_add(n, ((a[1]-b[1])*(a[2]+b[2]),
                      (a[2]-b[2])*(a[0]+b[0]),
                      (a[0]-b[0])*(a[1]+b[1])))
    if v_norm(n) < EPS:
        raise ValueError('選取面積過小，無法建立剖面。')
    n = v_unit(n)
    a = points[0]
    if any(abs(v_dot(v_sub(p,a), n)) > 1e-5 for p in points):
        raise ValueError('選取的剖面不在同一個平面。')
    z = (0.,0.,1.)
    up_proj = v_sub(z,v_mul(n,v_dot(z,n)))
    if v_norm(up_proj) >= .35:
        # The face has a meaningful vertical direction. Keep that direction
        # upward, independently of clockwise/counter-clockwise vertex order.
        up = v_unit(up_proj)
        horizontal_candidates = ((1.,0.,0.),(0.,1.,0.))
        candidate = max(horizontal_candidates, key=lambda axis:
                        v_norm(v_sub(axis, v_mul(n,v_dot(axis,n)))))
        right = v_unit(v_cross(up,n))
        if v_dot(right,candidate) < 0:
            right = v_mul(right,-1.)
    else:
        # A nearly horizontal plane has no stable projected +Z axis.
        # Use stable XY directions instead of a random perimeter edge.
        right = v_unit(v_sub((1.,0.,0.),v_mul(n,n[0])))
        up = v_unit(v_sub((0.,1.,0.),v_mul(n,n[1])))
        up = v_unit(v_sub(up,v_mul(right,v_dot(up,right))))
    polygon = [[1000*v_dot(v_sub(point,a),right),
                1000*v_dot(v_sub(point,a),up)] for point in points]
    return clean_polygon(polygon)


def ordered_paths(edges):
    """Convert selected edge networks into unambiguous open/closed trails."""
    incident={}
    for edge in edges:
        for vertex in (edge.v0, edge.v1): incident.setdefault(vertex,set()).add(edge)
    if any(len(es)>2 for es in incident.values()):
        raise ValueError("所選線段有分岔；請只選擇一條沒有分岔的路徑。")
    if not edges: raise ValueError("請先選取線段。")
    paths=[]
    remaining=set(edges)
    while remaining:
        seed=next(iter(remaining))
        component={seed}
        queue=[seed]
        while queue:
            e=queue.pop()
            for vert in (e.v0,e.v1):
                for other in incident[vert]:
                    if other not in component:
                        component.add(other); queue.append(other)
        endpoints=[v for v in incident if len(incident[v])==1 and (incident[v] & component)]
        start=endpoints[0] if endpoints else seed.v0
        vertex=start
        used=set()
        pts=[as_tuple(start.position)]
        while True:
            choices=[e for e in incident[vertex] if e in component and e not in used]
            if not choices: break
            e=choices[0]; used.add(e); vertex=e.other(vertex)
            pts.append(as_tuple(vertex.position))
        if used != component: raise ValueError("無法將選取線段整理為連續路徑。")
        if len(pts)>2 and math.dist(pts[0],pts[-1])<EPS: pts.pop(); closed=True
        else: closed=False
        remaining.difference_update(component)
        paths.append((pts,closed))
    return paths


def sweep_faces(path, section, closed=False, width_mm=17, height_mm=17,
                rotate_deg=0, mirror=False, off_u_mm=0, off_v_mm=0, miter=True,
                anchor="左下", closed_side="內側", reverse_depth=True, preview_side=False, side_hint_path=None, stable_mouse_plane=False,
                plane_normal_override=None, horizontal=False, fixed_side=1.0,
                wall_frame=False, wall_depth_sign=1.0):
    """Return quad side faces and planar caps as 3D coordinate lists.

    Planar frames use one canonical normal and one route-wide side choice;
    open-path preview may infer the eventual closed-loop side from its chord. A miter joint is a
    projection onto the angle-bisector plane, clamped for acute corners.
    """
    if wall_frame:
        if plane_normal_override is None:
            raise ValueError('牆面線板需要固定牆面方向。')
        if fixed_side not in (-1., 1.) or wall_depth_sign not in (-1., 1.):
            raise ValueError('牆面線板方向無效。')
    if horizontal:
        # Horizontal skirting has an explicit up axis, even on its first span.
        # Do not infer a new plane or inside/outside from the growing route.
        plane_normal_override = (0., 0., 1.)
        if fixed_side not in (-1.0, 1.0):
            raise ValueError('水平踢腳方向必須為正向或反向。')
        if path and any(abs(p[2]-path[0][2]) > 1e-5 for p in path):
            raise ValueError('水平踢腳的節點高度必須一致；請吸附同高端點，或改用牆面／空間模式。')
    if len(path)<2: raise ValueError("路徑至少需要兩個點。")
    if closed and len(path)<3: raise ValueError("封閉路徑至少需要三個點。")
    if any(math.dist(path[i], path[(i + 1) % len(path)]) < EPS for i in range(len(path) if closed else len(path)-1)):
        raise ValueError('路徑含重複節點或零長度線段。')
    section=clean_polygon(section)
    min_u=min(p[0] for p in section); max_u=max(p[0] for p in section)
    min_v=min(p[1] for p in section); max_v=max(p[1] for p in section)
    if max_u-min_u<EPS or max_v-min_v<EPS: raise ValueError("剖面的寬度與高度不可為零。")
    anchors = {"左下": (0., 0.), "下中": (.5, 0.), "右下": (1., 0.),
               "左中": (0., .5), "中心": (.5, .5), "右中": (1., .5),
               "左上": (0., 1.), "上中": (.5, 1.), "右上": (1., 1.)}
    if anchor not in anchors:
        raise ValueError("無效的剖面基準點。")
    anchor_u, anchor_v = anchors[anchor]
    ang=math.radians(rotate_deg)
    ca,sa=math.cos(ang),math.sin(ang)
    scaled=[]
    for u,v in section:
        u=(u-min_u)/(max_u-min_u)*width_mm/1000.
        v=(v-min_v)/(max_v-min_v)*height_mm/1000.
        if mirror: u=width_mm/1000.-u
        u-=anchor_u*width_mm/1000.
        v-=anchor_v*height_mm/1000.
        u,v=u*ca-v*sa,v*ca+u*sa
        scaled.append((u+off_u_mm/1000.,v+off_v_mm/1000.))
    n=len(path)
    seg_count=n if closed else n-1
    directions=[v_unit(v_sub(path[(i+1)%n],path[i])) for i in range(seg_count)]
    # Use a single, stable frame for planar paths (including XZ vertical
    # outlines). Rotating the reference up independently per segment used to
    # twist rings at 90-degree bends, producing crossing/overlapping faces.
    plane_normal = None
    for ia in range(len(directions)):
        for ib in range(ia + 1, len(directions)):
            cross = v_cross(directions[ia], directions[ib])
            if v_norm(cross) > 1e-5:
                plane_normal = v_unit(cross)
                break
        if plane_normal is not None:
            break
    if plane_normal_override is not None:
        candidate = v_unit(plane_normal_override)
        if all(abs(v_dot(d, candidate)) < 1e-4 for d in directions):
            plane_normal = candidate
        else:
            raise ValueError("繪製路徑已離開所選牆面，請取消牆面鎖定後重新繪製。")
    # A two-node mouse path has no corner from which to derive its plane.
    # Prefer the wall plane for X/Z wall mouldings, rather than using the
    # nonplanar fallback (which places the section 90 degrees differently
    # until the next point is placed). The later XZ plane must use this same
    # orientation. Other single-axis paths use their customary world plane.
    if plane_normal is None and stable_mouse_plane:
        direction = directions[0]
        ax, ay, az = (abs(value) for value in direction)
        if ay < 1e-6:
            plane_normal = (0., -1., 0.)  # XZ; wall across X or up Z
        elif ax < 1e-6:
            plane_normal = (1., 0., 0.) if az > 1e-6 else (0., 0., 1.)
        elif az < 1e-6:
            plane_normal = (0., 0., 1.)  # XY floor path
    planar = (plane_normal is not None and all(
        abs(v_dot(d, plane_normal)) < 1e-5 for d in directions))
    if planar:
        # Choose a canonical reference normal. Reversing the route (or making
        # the first turn vertical) must not silently invert the section's
        # front/back orientation. XY -> +Z; XZ -> -Y; YZ -> +X.
        canonical = ((0., 0., 1.), (0., -1., 0.), (1., 0., 0.))
        preferred = max(canonical, key=lambda axis: abs(v_dot(plane_normal, axis)))
        if plane_normal_override is None and v_dot(plane_normal, preferred) < 0:
            plane_normal = v_mul(plane_normal, -1.)
        frames = [(v_unit(v_cross(d, plane_normal)), plane_normal)
                  for d in directions]
    else:
        # Nonplanar route: use a stable moving frame. At a sudden 3-D change
        # we preserve each ring orientation; abrupt 180-degree reversals are
        # rejected below rather than producing inverted geometry.
        initial = directions[0]
        ref = (0., 0., 1.) if abs(v_dot(initial, (0.,0.,1.))) < 0.92 else (0.,1.,0.)
        up = v_unit(v_sub(ref, v_mul(initial, v_dot(ref, initial))))
        frames=[]
        previous=initial
        for direction in directions:
            projected=v_sub(up,v_mul(direction,v_dot(up,direction)))
            if v_norm(projected)<1e-5:
                ref=(1.,0.,0.) if abs(direction[0])<0.9 else (0.,1.,0.)
                projected=v_sub(ref,v_mul(direction,v_dot(ref,direction)))
            up=v_unit(projected)
            side=v_unit(v_cross(direction,up))
            frames.append((side,up))
            previous=direction
    # Keep the section on ONE chosen side for an entire planar path.  Prior
    # versions used side_sign=+1 while drawing, then changed it upon closing.
    # That swapped intermediate sections at the exact moment the frame closed.
    # For a partially drawn polygon, the implicit last->first chord provides
    # a preview-only winding.  The same plane normal and same rule are applied
    # by the final closed sweep, so no section flips at completion.
    side_sign = fixed_side if (horizontal or wall_frame) else 1.0
    if not (horizontal or wall_frame) and planar and (closed or (preview_side and n >= 3)):
        # Hover position must not change the direction of already confirmed
        # sections. Use the fixed, clicked vertices to infer the potential
        # closure direction while the last span is only a mouse preview.
        winding_path = side_hint_path if (preview_side and not closed and
                                         side_hint_path is not None and
                                         len(side_hint_path) >= 3) else path
        winding_count = len(winding_path)
        signed_double_area = sum(
            v_dot(v_cross(winding_path[i], winding_path[(i + 1) % winding_count]),
                  plane_normal)
            for i in range(winding_count)
        )
        if closed and abs(signed_double_area) < 1e-10:
            raise ValueError('封閉路徑的面積太小，無法判定內外側。')
        if abs(signed_double_area) > 1e-10:
            # side = tangent x normal; an anticlockwise loop has its inside
            # on the opposite side.  Match the inside/outside selection even
            # before the final closing edge exists.
            side_sign = -1.0 if signed_double_area > 0 else 1.0
            if closed_side == '外側':
                side_sign *= -1.0
    # The section depth is independently reversible.  In vertical XY-wall
    # outlines, world +Y is normally the visible front while the legacy code
    # forced -Y.  Keep the path-side direction stable while reversing depth.
    depth_sign = -1.0 if reverse_depth and planar and abs(plane_normal[1]) > 0.90 else 1.0
    if wall_frame:
        depth_sign = wall_depth_sign
    rings=[]
    for i,p in enumerate(path):
        prev=(i-1)%seg_count if (closed or i>0) else 0
        nxt=i%seg_count if (closed or i<seg_count) else seg_count-1
        if prev!=nxt and v_dot(directions[prev],directions[nxt]) < -0.97:
            raise ValueError('路徑有接近 180 度的折返；請調整路徑節點。')
        if miter and prev!=nxt and planar:
            # For planar corners the ring plane bisects the turn. Its up
            # vector is identical on both adjoining spans, preventing twisted
            # nonplanar quads and the dense triangle fan seen at corners.
            s0=frames[prev][0]; s1=frames[nxt][0]
            half=v_add(s0,s1)
            if v_norm(half) < 0.15:
                raise ValueError('轉角過於尖銳，無法建立安全的斜接。')
            side=v_unit(half)
            factor=1.0/v_dot(side,s1)
            if factor > 3.0:
                raise ValueError('轉角過於尖銳，請分段建立線板。')
            side=v_mul(side,factor)
            vertical=plane_normal
        elif miter and prev!=nxt:
            side=frames[nxt][0]; vertical=frames[nxt][1]
        else:
            side,vertical=frames[nxt]
        side = v_mul(side, side_sign)
        vertical = v_mul(vertical, depth_sign)
        rings.append([v_add(p,v_add(v_mul(side,u),v_mul(vertical,v))) for u,v in scaled])
    faces=[]
    for i in range(seg_count):
        a=rings[i]; b=rings[(i+1)%n]
        for j in range(len(section)):
            k=(j+1)%len(section)
            faces.append([a[j],a[k],b[k],b[j]])
    if not closed:
        faces.append(list(reversed(rings[0])))
        faces.append(list(rings[-1]))
    return faces



def experimental_sweep_faces(path, section, closed=False, width_mm=17, height_mm=17,
                             rotate_deg=0, mirror=False, off_u_mm=0, off_v_mm=0,
                             miter=True, anchor="左下", closed_side="內側",
                             reverse_depth=True, preview_side=False,
                             side_hint_path=None, stable_mouse_plane=False,
                             wall_normal=None, horizontal=False, fixed_side=1.0):
    """Experimental mouse sweep: parallel-transport frame for spatial routes.

    Production planar geometry is retained as a reference baseline. Nonplanar
    routes use minimal-rotation transport at each corner instead of repeatedly
    projecting world-up into the new tangent (which can abruptly flip).
    This is deliberately isolated from the production sweep algorithm.
    """
    if horizontal:
        return sweep_faces(path, section, closed, width_mm, height_mm, rotate_deg,
                           mirror, off_u_mm, off_v_mm, miter, anchor, closed_side,
                           reverse_depth, preview_side, side_hint_path,
                           stable_mouse_plane, horizontal=True, fixed_side=fixed_side)
    if len(path) < 2:
        raise ValueError('路徑至少需要兩個點。')
    if wall_normal is not None:
        wall_normal=v_unit(wall_normal)
    count=len(path)
    if closed and count < 3:
        raise ValueError('封閉路徑至少需要三個點。')
    directions=[v_unit(v_sub(path[(i+1)%count], path[i]))
                for i in range(count if closed else count-1)]
    normal=None
    for a in directions:
        for b in directions:
            c=v_cross(a,b)
            if v_norm(c)>1e-5:
                normal=v_unit(c)
                break
        if normal is not None: break
    wall_planar = (wall_normal is not None and all(
        abs(v_dot(d,wall_normal)) < 1e-4 for d in directions))
    if wall_normal is not None and not wall_planar:
        raise ValueError('路徑已偏離鎖定牆面。請返回牆面，或重新啟動實驗工具。')
    if wall_planar or (normal is not None and all(abs(v_dot(d,normal))<1e-5 for d in directions)):
        # Experimental wall mode uses the selected face normal instead of
        # canonical world X/Y. The production path never passes this override.
        return sweep_faces(path,section,closed,width_mm,height_mm,rotate_deg,
                           mirror,off_u_mm,off_v_mm,miter,anchor,closed_side,
                           reverse_depth,preview_side,side_hint_path,stable_mouse_plane,
                           plane_normal_override=wall_normal if wall_planar else None)
    if normal is None and stable_mouse_plane:
        return sweep_faces(path,section,closed,width_mm,height_mm,rotate_deg,
                           mirror,off_u_mm,off_v_mm,miter,anchor,closed_side,
                           reverse_depth,preview_side,side_hint_path,stable_mouse_plane)
    section=clean_polygon(section)
    us=[p[0] for p in section]; vs=[p[1] for p in section]
    u0,u1=min(us),max(us); v0,v1=min(vs),max(vs)
    if u1-u0<EPS or v1-v0<EPS:
        raise ValueError('剖面寬高不可為零。')
    anchors={'左下':(0.,0.),'下中':(.5,0.),'右下':(1.,0.),
             '左中':(0.,.5),'中心':(.5,.5),'右中':(1.,.5),
             '左上':(0.,1.),'上中':(.5,1.),'右上':(1.,1.)}
    if anchor not in anchors:
        raise ValueError('無效的剖面基準點。')
    au,av=anchors[anchor]
    ca=math.cos(math.radians(rotate_deg)); sa=math.sin(math.radians(rotate_deg))
    scaled=[]
    for u,v in section:
        u=(u-u0)/(u1-u0)*width_mm/1000.
        v=(v-v0)/(v1-v0)*height_mm/1000.
        if mirror: u=width_mm/1000.-u
        u-=au*width_mm/1000.;v-=av*height_mm/1000.
        scaled.append((u*ca-v*sa+off_u_mm/1000.,u*sa+v*ca+off_v_mm/1000.))
    first=directions[0]
    reference=(0.,0.,1.) if abs(first[2]) < .92 else (0.,1.,0.)
    up=v_unit(v_sub(reference,v_mul(first,v_dot(first,reference))))
    segment_up=[up]
    for i in range(1,len(directions)):
        before,after=directions[i-1],directions[i]
        c=v_cross(before,after); sinv=v_norm(c); cosv=v_dot(before,after)
        if cosv < -.97:
            raise ValueError('實驗模式不支援接近 180 度的折返。')
        if sinv > 1e-8:
            axis=v_mul(c,1./sinv)
            # Rodrigues minimal rotation from incoming to outgoing tangent.
            up=v_add(v_add(v_mul(up,cosv),v_mul(v_cross(axis,up),sinv)),
                     v_mul(axis,v_dot(axis,up)*(1.-cosv)))
        up=v_unit(v_sub(up,v_mul(after,v_dot(up,after))))
        segment_up.append(up)
    if closed:
        # Closure with mismatched transported frames would create a seam.
        # Reject rather than silently write invalid geometry.
        before,after=directions[-1],directions[0]
        cross=v_cross(before,after)
        sinv=v_norm(cross); cosv=v_dot(before,after)
        end_up=segment_up[-1]
        if cosv < -.97: raise ValueError('封閉路徑含 180 度折返。')
        if sinv > 1e-8:
            axis=v_mul(cross,1./sinv)
            end_up=v_add(v_add(v_mul(end_up,cosv),v_mul(v_cross(axis,end_up),sinv)),
                         v_mul(axis,v_dot(axis,end_up)*(1.-cosv)))
        if v_dot(v_unit(end_up),segment_up[0]) < .999:
            raise ValueError('這條三維封閉路徑會造成剖面扭轉；實驗模式暫不生成。')
    frames=[(v_unit(v_cross(d,u)),u) for d,u in zip(directions,segment_up)]
    rings=[]; edge_count=len(directions)
    for i,point in enumerate(path):
        prev=(i-1)%edge_count if (closed or i>0) else 0
        nxt=i%edge_count if (closed or i<edge_count) else edge_count-1
        side,vertical=frames[nxt]
        if prev!=nxt and miter:
            before,after=directions[prev],directions[nxt]
            cosine=v_dot(before,after)
            if cosine < -.97: raise ValueError('轉角過於尖銳。')
            # Bisect side vectors and keep vertical orthogonal to the outgoing
            # tangent. This minimizes discontinuity for spatial miter joints.
            bisector=v_add(frames[prev][0],frames[nxt][0])
            if v_norm(bisector)<.15: raise ValueError('轉角斜接方向不明確。')
            bisector=v_unit(bisector)
            divisor=v_dot(bisector,frames[nxt][0])
            if divisor < 1./3.: raise ValueError('轉角斜接延伸過長。')
            side=v_mul(bisector,1./divisor)
        rings.append([v_add(point,v_add(v_mul(side,u),v_mul(vertical,v))) for u,v in scaled])
    faces=[]
    for i in range(edge_count):
        a=rings[i];b=rings[(i+1)%count]
        for j in range(len(section)):
            k=(j+1)%len(section)
            faces.append([a[j],a[k],b[k],b[j]])
    if not closed:
        faces.extend((list(reversed(rings[0])),list(rings[-1])))
    return faces


def mesh_ready_faces(polygons):
    """Triangulate non-planar sweep quads and concave end caps explicitly.

    A miter at a corner stretches each section coordinate by a different
    amount. On a curved profile, a quad between two miter rings can therefore
    be NON-PLANAR. Giving it to the mesh as an undivided quad makes the renderer
    choose an arbitrary diagonal and can create the crossed, reversed-looking
    surfaces seen at 90-degree corners.

    All polygons are triangulated deterministically. Ear clipping, rather
    than triangle fans, is required for the concave 058 and 012 end caps.
    Returns coordinate polygons with consistent original winding.
    """
    result = []
    for face in polygons:
        if len(face) < 3:
            raise ValueError('生成幾何包含不完整的面。')
        if len(face) == 3:
            result.append(face)
            continue
        if len(face) == 4:
            # Two triangles across a stable diagonal. Check both candidates
            # and select the one with the better smallest triangle area.
            variants = (([0, 1, 2], [0, 2, 3]),
                        ([0, 1, 3], [1, 2, 3]))
            def quality(pair):
                vals = []
                for a,b,c in pair:
                    vals.append(v_norm(v_cross(v_sub(face[b],face[a]),
                                               v_sub(face[c],face[a]))))
                return min(vals)
            tris=max(variants,key=quality)
            if quality(tris) < 1e-15:
                raise ValueError('剖面轉角產生退化三角面；請調整路徑或剖面尺寸。')
            result.extend([[face[k] for k in indices] for indices in tris])
            continue
        # Ear clipping for a planar polygon (usually an open-path end cap).
        # Newell normal is stable even for a concave outline.
        normal=(0.,0.,0.)
        for j,p in enumerate(face):
            q=face[(j+1)%len(face)]
            normal=v_add(normal,((p[1]-q[1])*(p[2]+q[2]),
                                  (p[2]-q[2])*(p[0]+q[0]),
                                  (p[0]-q[0])*(p[1]+q[1])))
        if v_norm(normal)<1e-12:
            raise ValueError('剖面端面面積為零。')
        drop=max(range(3), key=lambda k:abs(normal[k]))
        ax=[k for k in range(3) if k!=drop]
        coords=[(p[ax[0]],p[ax[1]]) for p in face]
        def area2(a,b,c):
            return ((b[0]-a[0])*(c[1]-a[1])-
                    (b[1]-a[1])*(c[0]-a[0]))
        total=sum(coords[i][0]*coords[(i+1)%len(face)][1]-
                  coords[(i+1)%len(face)][0]*coords[i][1] for i in range(len(face)))
        if abs(total)<1e-14:
            raise ValueError('剖面端面投影面積為零。')
        sign=1 if total>0 else -1
        indices=list(range(len(face)))
        while len(indices)>3:
            ear_found=False
            for pos in range(len(indices)):
                a,b,c=(indices[(pos-1)%len(indices)], indices[pos],
                       indices[(pos+1)%len(indices)])
                if area2(coords[a],coords[b],coords[c])*sign<=1e-13:
                    continue
                # No other polygon vertex may lie in the proposed ear.
                occupied=False
                for test in indices:
                    if test in (a,b,c): continue
                    t=coords[test]
                    v1=area2(coords[a],coords[b],t)*sign
                    v2=area2(coords[b],coords[c],t)*sign
                    v3=area2(coords[c],coords[a],t)*sign
                    if min(v1,v2,v3)>=-1e-13:
                        occupied=True
                        break
                if occupied: continue
                result.append([face[a],face[b],face[c]])
                indices.pop(pos)
                ear_found=True
                break
            if not ear_found:
                raise ValueError('剖面端面無法安全三角化，可能包含自交或重複頂點。')
        result.append([face[k] for k in indices])
    return result




def _mesh_vertex_key(point):
    """Quantize to IngeTrazo's welded vertex grid (metres -> micro-metres).

    QVector3D uses single-precision floats. Use the *same* converted input
    coordinates for the rules as for Mesh.add_face so shared vertices match.
    """
    if hasattr(point, 'toTuple'):
        values = point.toTuple()
    else:
        values = point
    # Raw sweep points are float64 but Mesh.add_face stores float32 QVector3D.
    # Round only AFTER the same float32 conversion, or points near a micron
    # boundary receive different keys and their edges never get cleaned.
    import struct
    return tuple(round(struct.unpack('f',struct.pack('f',float(c)))[0] * 1000000) for c in values)


def _mesh_edge_key(first, second):
    a, b = _mesh_vertex_key(first), _mesh_vertex_key(second)
    return (a, b) if a <= b else (b, a)


def edge_display_rules(raw_polygons, triangulated_faces, closed):
    """Return tessellation diagonals and longitudinal seams to soften.

    The original sweep's quad borders are *true* section boundaries. All
    triangle edges not on an original border are implementation-only and must
    be hidden. Along-path edges which are present on adjacent curved profile
    facets may be softened, subject to a conservative dihedral-angle check.
    Corner miter seam edges span the section rings, and stay visible.
    """
    original_edges = set()
    along_path = set()
    side_count = len(raw_polygons) if closed else len(raw_polygons) - 2
    if side_count < 0:
        raise ValueError('輪廓面資料不完整。')
    for index, poly in enumerate(raw_polygons):
        for j, pt in enumerate(poly):
            original_edges.add(_mesh_edge_key(pt, poly[(j + 1) % len(poly)]))
        if index < side_count and len(poly) == 4:
            # Sweep quads: [section_j_at_A, section_j+1_at_A,
            #               section_j+1_at_B, section_j_at_B].
            # Only long edges run *along the path*. The other two belong
            # to end rings and are also the visible 45-degree miter seams.
            along_path.add(_mesh_edge_key(poly[0], poly[3]))
            along_path.add(_mesh_edge_key(poly[1], poly[2]))
    generated_edges = set()
    for face in triangulated_faces:
        for j, pt in enumerate(face):
            generated_edges.add(_mesh_edge_key(pt, face[(j+1) % len(face)]))
    internal = generated_edges - original_edges
    return internal, along_path


def apply_edge_display(mesh, internal_edges, along_path_edges, smooth_angle_deg=30.0):
    """Mark display-only mesh splits without destroying face geometry.

    - Hidden + soft: internal triangulation diagonals, including cap ears.
    - Soft: small-angle seams running down the tessellated arc of a profile.
    - Visible: silhouette, sharp steps, end rings and miter join lines.
    Only affect edges belonging to exactly two faces; boundary edges must
    remain visible even when they happen to resemble generated diagonals.
    """
    cos_threshold = math.cos(math.radians(smooth_angle_deg))
    stats = {'hidden': 0, 'soft': 0, 'visible': 0}
    for edge in mesh.edges:
        key = _mesh_edge_key(edge.v0.position, edge.v1.position)
        if len(edge.faces) == 2 and key in internal_edges:
            edge.hidden = True
            # Group copying in 0.6.0 preserves soft flags on paths where
            # hidden flags can be dropped. Coplanar splits must remain soft.
            edge.soft = True
            stats['hidden'] += 1
        elif len(edge.faces) == 2 and key in along_path_edges:
            try:
                a = as_tuple(edge.faces[0].normal())
                b = as_tuple(edge.faces[1].normal())
                denom = v_norm(a) * v_norm(b)
                # Do not soften a sharp joint or a face with invalid normal.
                if denom > EPS and abs(v_dot(a, b) / denom) >= cos_threshold:
                    edge.soft = True
                    stats['soft'] += 1
                else:
                    stats['visible'] += 1
            except (TypeError, AttributeError, ValueError, ZeroDivisionError):
                stats['visible'] += 1
        else:
            stats['visible'] += 1
    return stats


def read_profiles():
    base=Path(os.environ.get("APPDATA", str(Path.home()/".local/share")))/"ingetrazo"/"quick_profile"
    file=base/"profiles.json"
    data={k:list(v) for k,v in PRESETS.items()}
    if file.exists():
        try:
            loaded=json.loads(file.read_text(encoding="utf-8"))
            for k,p in loaded.items(): data[k]=clean_polygon(p)
        except Exception: pass
    return data,file


def make_icon(kind,size=36):
    """Original high-contrast outline symbols; independent of installed icon fonts."""
    from PySide6.QtCore import QPointF, QRectF
    pm=QPixmap(size,size); pm.fill(Qt.transparent)
    p=QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor('#283c4e'), 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    scale=size/36.; p.scale(scale,scale)
    if kind in ('draw','profile'):
        p.drawLine(6,28,12,8); p.drawLine(12,8,29,24); p.drawLine(6,28,29,24)
        p.setBrush(QColor('#f0ad42')); p.drawEllipse(QPointF(12,8),3,3)
    elif kind=='route':
        path=QPainterPath(QPointF(6,28)); path.lineTo(7,9); path.lineTo(28,10)
        p.drawPath(path); p.setBrush(QColor('#ff9c40'))
        for x,y in ((6,28),(7,9),(28,10)): p.drawEllipse(QPointF(x,y),3,3)
    elif kind=='frame':
        p.drawRect(7,7,22,22); p.drawRect(12,12,12,12)
    elif kind=='edit':
        p.drawRect(5,7,18,22)
        p.setPen(QPen(QColor('#26875e'), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(16,24,22,29); p.drawLine(22,29,32,15)
    elif kind=='stamp':
        p.drawRoundedRect(13,5,10,13,3,3)
        p.drawLine(18,18,18,23)
        p.drawRoundedRect(7,23,22,6,2,2)
        p.setPen(QPen(QColor('#cf8232'), 2))
        p.drawLine(6,32,30,32)
    elif kind=='pipette':
        # Original eyedropper pictogram, not copied from Profile Builder.
        p.drawLine(8,29,25,12); p.drawLine(12,31,29,14)
        p.drawLine(8,29,12,31); p.drawLine(25,12,29,14)
        p.drawLine(23,11,28,6); p.drawLine(28,6,32,10)
        p.drawLine(32,10,27,15)
        p.setBrush(QColor('#55a9db'))
        p.drawEllipse(QPointF(8,30),3,3)
    elif kind=='save':
        p.drawRoundedRect(QRectF(6,5,24,26),3,3); p.drawRect(11,6,13,9); p.drawRect(12,21,12,9)
    elif kind=='import':
        p.drawLine(18,5,18,23); p.drawLine(11,16,18,24); p.drawLine(25,16,18,24); p.drawLine(7,30,29,30)
    elif kind=='export':
        p.drawLine(18,9,18,30); p.drawLine(11,16,18,8); p.drawLine(25,16,18,8); p.drawLine(7,31,29,31)
    elif kind=='direction':
        p.drawLine(4,18,32,18); p.drawLine(4,18,12,11); p.drawLine(4,18,12,25); p.drawLine(32,18,24,11); p.drawLine(32,18,24,25)
    elif kind=='settings':
        p.drawEllipse(QPointF(18,18),10,10); p.drawEllipse(QPointF(18,18),3.5,3.5)
        for a in range(0,360,45):
            t=math.radians(a); p.drawLine(int(18+11*math.cos(t)),int(18+11*math.sin(t)),int(18+15*math.cos(t)),int(18+15*math.sin(t)))
    elif kind=='finish':
        p.drawLine(7,18,15,26); p.drawLine(15,26,30,9)
    elif kind=='more':
        p.setBrush(QColor('#283c4e'))
        for x in (9,18,27): p.drawEllipse(QPointF(x,18),2.5,2.5)
    p.end(); return QIcon(pm)

def make_header_icon(kind, size=28):
    """Independent crisp vector icons, not copied from commercial assets."""
    from PySide6.QtCore import QRectF
    pm=QPixmap(size,size); pm.fill(Qt.transparent)
    p=QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor('#30363b'),2.0,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    z=size/28.; p.scale(z,z)
    if kind=='search':
        p.drawEllipse(QRectF(5,4,14,14)); p.drawLine(17,17,24,24)
    elif kind=='save':
        p.drawRoundedRect(QRectF(5,4,18,20),1.8,1.8)
        p.drawRect(QRectF(8,4,12,7))
        p.drawRect(QRectF(9,16,10,8))
    elif kind=='plus':
        p.drawLine(14,4,14,24); p.drawLine(4,14,24,14)
    p.end(); return QIcon(pm)



def make_corner_icon(kind, size=32):
    """Original crisp corner-joint pictograms, not copied from commercial assets.

    Render a thick L-frame and distinguish the seam / end treatment. Output
    stays crisp at small display sizes through high-resolution QPixmap painting.
    """
    from PySide6.QtCore import QRectF, QPointF
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(size / 36.0, size / 36.0)
    outline = QPen(QColor('#34414a'), 1.5, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin)
    painter.setPen(outline)
    painter.setBrush(QColor('#a8b3bd'))
    # L-shaped timber members: vertical member at left, horizontal at top.
    if kind == 'miter':
        shape = QPainterPath()
        shape.moveTo(5, 31); shape.lineTo(5, 5); shape.lineTo(31, 5)
        shape.lineTo(31, 13); shape.lineTo(13, 13); shape.lineTo(13, 31)
        shape.closeSubpath()
        painter.drawPath(shape)
        # 45 degree seam: same joint treatment seen on both legs.
        painter.drawLine(5, 5, 13, 13)
    elif kind == 'flat':
        painter.drawRect(QRectF(5, 5, 8, 26))
        painter.drawRect(QRectF(13, 5, 18, 8))
        # Visible straight butt seam.
        painter.drawLine(13, 5, 13, 13)
    elif kind == 'continuous':
        shape = QPainterPath()
        shape.moveTo(5, 31); shape.lineTo(5, 5); shape.lineTo(31, 5)
        shape.lineTo(31, 13); shape.lineTo(13, 13); shape.lineTo(13, 31)
        shape.closeSubpath()
        painter.drawPath(shape)
        # Unbroken right-angled edge at the inside corner.
        painter.setPen(QPen(QColor('#f4f6f7'), 1.3))
        painter.drawLine(13, 14, 14, 13)
    elif kind == 'separate':
        painter.drawRect(QRectF(5, 15, 8, 16))
        painter.drawRect(QRectF(16, 5, 15, 8))
        painter.setPen(QPen(QColor('#64748b'), 1.4, Qt.DashLine))
        painter.drawLine(13, 15, 16, 13)
    painter.end()
    return QIcon(pm)


class ProfilePreview(QWidget):
    """Original vector preview with a visible adjustable red anchor and live mirror."""
    ANCHORS = {"左下": (0.,0.), "下中": (.5,0.), "右下": (1.,0.),
               "左中": (0.,.5), "中心": (.5,.5), "右中": (1.,.5),
               "左上": (0.,1.), "上中": (.5,1.), "右上": (1.,1.)}
    def __init__(self, parent=None):
        super().__init__(parent)
        self.points=PRESETS["058｜弧形線板"]
        self.anchor="左上"; self.mirror=False; self.rotation=0.
        self.setMinimumHeight(145)
    def set_points(self,pts): self.points=pts; self.update()
    def set_orientation(self, anchor=None, mirror=None, rotation=None):
        if anchor is not None: self.anchor=anchor
        if mirror is not None: self.mirror=bool(mirror)
        if rotation is not None: self.rotation=float(rotation)
        self.update()
    def paintEvent(self,event):
        from PySide6.QtCore import QPointF
        pa=QPainter(self); pa.setRenderHint(QPainter.Antialiasing)
        pa.fillRect(self.rect(), QColor('#fafafa'))
        pts=self.points
        if not pts: pa.end(); return
        xmin=min(v[0] for v in pts); xmax=max(v[0] for v in pts)
        ymin=min(v[1] for v in pts); ymax=max(v[1] for v in pts)
        w=max(xmax-xmin, 0.00001); h=max(ymax-ymin, 0.00001)
        ax,ay=self.ANCHORS.get(self.anchor,(0,1))
        theta=math.radians(self.rotation)
        cos,sin=math.cos(theta), math.sin(theta)
        def transform(x,y):
            # Keep both axes in their original millimetre scale.  Normalising
            # X by width and Y by height independently made every section
            # look almost square, irrespective of its actual aspect ratio.
            # Place the anchor in the same physical coordinate system and use
            # just one common pixel-per-mm scale below.
            u=(x-xmin)-ax*w; v=(y-ymin)-ay*h
            if self.mirror: u=-u
            return (u*cos-v*sin, u*sin+v*cos)
        poly=[transform(x,y) for x,y in pts]
        anchor_x,anchor_y=0.,0.
        # Keep red point visible even when the cross section rotates.
        xx=[a for a,b in poly]+[anchor_x]; yy=[b for a,b in poly]+[anchor_y]
        scale=min((self.width()-32)/max(max(xx)-min(xx),.1),
                  (self.height()-32)/max(max(yy)-min(yy),.1))
        cx=self.width()/2-(max(xx)+min(xx))/2*scale
        cy=self.height()/2+(max(yy)+min(yy))/2*scale
        def xy(x,y): return QPointF(cx+x*scale,cy-y*scale)
        path=QPainterPath()
        for idx,(x,y) in enumerate(poly):
            if idx==0: path.moveTo(xy(x,y))
            else: path.lineTo(xy(x,y))
        path.closeSubpath()
        pa.setPen(QPen(QColor('#707070'),1))
        pa.setBrush(QColor('#929292')); pa.drawPath(path)
        marker=xy(0,0)
        # Mark the exact selected bounding-box anchor. If it lies outside the
        # silhouette (e.g. an L-shaped profile), use a fine locator line to
        # the nearest outline point so the red mark is still intelligible.
        candidates=[xy(x,y) for x,y in poly]
        nearest=min(candidates,key=lambda pt:(pt.x()-marker.x())**2+(pt.y()-marker.y())**2)
        if (nearest.x()-marker.x())**2+(nearest.y()-marker.y())**2 > 64:
            pa.setPen(QPen(QColor('#c54c4c'),1,Qt.DashLine))
            pa.drawLine(marker,nearest)
        pa.setBrush(QColor('#e52d2d')); pa.setPen(QPen(QColor('#fff'),1.4))
        pa.drawEllipse(marker,5,5)
        pa.end()



class SceneComboBox(QComboBox):
    """A combo that re-reads the active scene immediately before opening."""
    def __init__(self, owner=None):
        super().__init__(owner)
        self._owner=owner
    def showPopup(self):
        if self._owner is not None:
            self._owner.refresh_scene_options()
        super().showPopup()

class ProfilePanel(QWidget):
    """Compact floating tool window in the spirit of a profile editor.

    This UI is original and does not reuse Profile Builder's proprietary assets.
    """
    def __init__(self, app):
        # The main IngeTrazo QMainWindow must own this floating tool window.
        # A parentless Qt.Window appears as another taskbar application on
        # Windows, while parented Qt.Tool remains alongside the modeller.
        host = app.window
        super().__init__(host, Qt.Tool | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.app = app
        self.profiles, self.file = read_profiles()
        self._temporary_profiles = set()
        self._profile_browser = None
        self._profile_key_override = None
        self._active_profile_key = None
        self._restoring_profile_preferences = False
        self._profile_preferences = self._load_profile_preferences()
        self.setWindowTitle("輪廓外形面板 v2.2.4｜IngeTrazo")
        self.setMinimumSize(430, 600)
        self.resize(445, 710)
        self.setStyleSheet("""
            QWidget { font-size: 12px; }
            QToolButton { min-width: 24px; min-height: 24px; padding: 2px; }
            QToolButton:checked { background: #bcd8ef; border: 1px solid #729ec3; }
            QDoubleSpinBox { min-height: 20px; }
            QFrame#rule { background: #bbbbbb; max-height: 1px; }
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(5)

        # Compact original-style top row: short editable name + frameless icons.
        # The hidden combo retains existing geometry/profile-selection contracts.
        self.profile = QComboBox(self)
        self.profile.addItems(sorted(self.profiles))
        self.profile.setCurrentText("058｜弧形線板")
        self.profile.hide()
        top = QHBoxLayout()
        top.setSpacing(9)
        self.profile_name = QLineEdit(self)
        self.profile_name.setFixedWidth(142)
        self.profile_name.setFixedHeight(27)
        self.profile_name.setMaxLength(90)
        self.profile_name.setToolTip('剖面名稱或編號。輸入後按 Enter 選取現有剖面。')
        self.profile_name.returnPressed.connect(self.select_profile_by_name)
        top.addWidget(self.profile_name)
        top.addStretch(1)
        self.search_button = self.header_button('search', '搜尋並選擇剖面', self.search_profile)
        self.save_button = self.header_button('save', '只儲存目前剖面的個別參數（不修改資料庫）', self.save_last_used_state)
        self.new_button = self.header_button('plus', '從選取面擷取暫存剖面，立即使用；另存圖庫時才命名', self.save_face)
        for btn in (self.search_button,self.save_button,self.new_button):
            top.addWidget(btn)
        lay.addLayout(top)

        upper = QHBoxLayout()
        upper.setSpacing(7)
        self.preview = ProfilePreview()
        self.preview.setFixedSize(147, 147)
        self.preview.setStyleSheet('border: 1px solid #999999;')
        upper.addWidget(self.preview, 0, Qt.AlignTop)

        right = QVBoxLayout()
        right.setSpacing(4)
        self.anchor = QComboBox()
        self.anchor.addItems(('左上', '上中', '右上', '左中', '中心', '右中', '左下', '下中', '右下'))
        right.addWidget(self.anchor)
        self.anchor.currentTextChanged.connect(lambda v: self.preview.set_orientation(anchor=v))
        self.rotation = self.spin(0, -360, 360)
        right.addWidget(self.named_spin('旋轉', self.rotation))
        self.rotation.valueChanged.connect(lambda v: self.preview.set_orientation(rotation=v))
        self.mirror = QCheckBox('鏡像剖面')
        right.addWidget(self.mirror)
        self.mirror.toggled.connect(lambda v: self.preview.set_orientation(mirror=v))
        self.reverse_depth = QCheckBox('垂直路徑：反轉剖面正反面')
        self.reverse_depth.setChecked(True)
        self.reverse_depth.setToolTip('修正 XZ 垂直平面線板的前後方向；取消勾選可回到舊版方向。')
        # Advanced orientation flag is managed from the bottom settings menu.
        self.offsetu = self.spin(0, -100000, 100000)
        self.offsetv = self.spin(0, -100000, 100000)
        right.addWidget(self.named_spin('水平偏移', self.offsetu))
        right.addWidget(self.named_spin('垂直偏移', self.offsetv))
        right.addStretch(1)
        upper.addLayout(right, 1)
        lay.addLayout(upper)

        lay.addWidget(self.heading('屬性'))
        self.rule(lay)
        self.width = self.spin(17, 0.1, 100000)
        self.height = self.spin(17, 0.1, 100000)
        self.width.setSuffix(' mm'); self.height.setSuffix(' mm')
        props = QGridLayout()
        props.setHorizontalSpacing(8)
        props.setVerticalSpacing(5)
        props.addWidget(QLabel('寬度'), 0, 0)
        props.addWidget(self.width, 0, 2)
        props.addWidget(QLabel('高度'), 1, 0)
        props.addWidget(self.height, 1, 2)
        self.link_dimensions = QCheckBox('等比例')
        self.link_dimensions.setToolTip('鎖定勾選當下的寬高比例，不會強制改成 1:1')
        props.addWidget(self.link_dimensions, 0, 1, 2, 1)
        reset = QPushButton('重置')
        reset.setFixedWidth(56)
        reset.clicked.connect(self.reset_dimensions)
        props.addWidget(reset, 2, 1)
        self.material = SceneComboBox(self)
        self.material.addItems(['預設材質'])
        
        self.material.setToolTip('選擇目前模型的材質，產生新輪廓時套用。')
        props.addWidget(QLabel('材質'), 3, 0); props.addWidget(self.material, 3, 1, 1, 2)
        self.tag = SceneComboBox(self)
        self.tag.addItems(['預設標籤'])
        self.tag.setToolTip('選擇模型中的標籤，產生輪廓時套用。')
        props.addWidget(QLabel('標籤'), 4, 0); props.addWidget(self.tag, 4, 1, 1, 2)
        lay.addLayout(props)
        self.refresh_scene_options()
        self._dim_update = False
        self._locked_aspect_ratio = 1.0
        self.link_dimensions.toggled.connect(self.on_dimension_link_toggled)
        self.width.valueChanged.connect(lambda v: self.sync_dimension('w', v))
        self.height.valueChanged.connect(lambda v: self.sync_dimension('h', v))
        self.profile.currentTextChanged.connect(self.on_profile_changed)
        self.on_profile_changed(self.profile.currentText())

        self.rule(lay)
        side_row = QHBoxLayout()
        side_row.addWidget(QLabel('封閉路徑方向'))
        self.closed_side = QComboBox()
        self.closed_side.addItems(['內側', '外側'])
        self.closed_side.setToolTip('封閉框自動辨識順時針／逆時針，維持剖面朝向指定的內側或外側。')
        side_row.addWidget(self.closed_side)
        # Keep this geometric parameter but expose it through direction/settings.
        corners = QHBoxLayout()
        corners.addWidget(QLabel('轉角連接'))
        corners.addStretch(1)
        self.miter = QCheckBox()
        self.miter.setChecked(True)
        # Four clearly distinguishable original pictograms, mirroring the
        # layout of a traditional profile editor without reusing its artwork.
        self.corner_miter = self.corner_tool('miter', '斜接｜轉角斜切接合（已支援）', lambda: self.set_corner(True), True)
        self.corner_flat = self.corner_tool('flat', '平接｜兩段平切接合（已支援）', lambda: self.set_corner(False), True)
        self.corner_continuous = self.corner_tool('continuous', '連續轉角｜尚未支援')
        self.corner_separate = self.corner_tool('separate', '獨立分段｜尚未支援')
        for btn in (self.corner_miter, self.corner_flat,
                    self.corner_continuous, self.corner_separate):
            corners.addWidget(btn)
        self.corner_continuous.setEnabled(False)
        self.corner_separate.setEnabled(False)
        lay.addLayout(corners)
        self.set_corner(True)
        extrusion = QHBoxLayout()
        extrusion.addWidget(QLabel('擠出模式'))
        extrusion.addStretch(1)
        self.extrude = QComboBox()
        self.extrude.addItems(['一般'])
        self.extrude.setToolTip('其他擠出方式待後續版本開發')
        extrusion.addWidget(self.extrude)
        lay.addLayout(extrusion)
        orientation_row = QHBoxLayout()
        orientation_row.addWidget(QLabel('滑鼠方向'))
        self.mouse_orientation = QComboBox()
        self.mouse_orientation.addItems(['水平踢腳（高度沿 Z）', '牆面線板（固定牆面）', '空間路徑（原方式）'])
        self.mouse_orientation.setToolTip('水平踢腳：F 反向。牆面線板：F 切換線板側、R 切換凸出面；無面吸附且垂直起筆時，第二點前按 Tab 選牆向。模式在起筆時固定。')
        orientation_row.addWidget(self.mouse_orientation, 1)
        lay.addLayout(orientation_row)
        lay.addStretch(1)
        self.rule(lay)
        footer = QHBoxLayout()
        footer.setSpacing(3)
        actions = [
            ('draw', '滑鼠繪製｜Space／Enter 完成', self.activate_draw, '滑鼠繪製'),
            ('route', '沿選取路徑生成輪廓', self.generate_selected, '沿路徑'),
            ('frame', '沿選取面的外框生成', self.generate_perimeter, '沿面外框'),
            ('pipette', '吸取既有輪廓群組的剖面與參數（Esc 離開）', self.activate_pipette, '吸取剖面'),
            ('edit', '將面板指定屬性套用至選取的輪廓群組', self.edit_selected_profiles, '編輯輪廓'),
            ('stamp', '放置含面的剖面；可連續蓋印，Esc 結束', self.activate_stamp, '放置剖面'),
            ('direction', '踢腳／牆面線板繪製中：切換線板側；其他模式：切換封閉輪廓內側／外側', self.toggle_closed_side, '方向切換'),
            ('settings', '進階設定與資料管理', self.open_manage_menu, '進階設定'),
        ]
        self.footer_buttons=[]
        for kind,tip,action,label in actions:
            b=self.icon_tool(kind,tip,action,label)
            if len(self.footer_buttons) == 4:
                lay.addLayout(footer)
                footer = QHBoxLayout()
                footer.setSpacing(3)
            footer.addWidget(b, 1)
            self.footer_buttons.append(b)
        lay.addLayout(footer)
        self.setToolTip('操作圖示可將滑鼠移入查看繁體中文說明')
        self.restore_last_used_state()

    def tool(self, glyph, tip, callback=None, checkable=False):
        btn = QToolButton(self)
        btn.setText(glyph)
        btn.setToolTip(tip)
        btn.setCheckable(checkable)
        btn.setFixedSize(30, 29)
        if callback is not None:
            btn.clicked.connect(callback)
        return btn

    def corner_tool(self, kind, tip, callback=None, checkable=False):
        button = QToolButton(self)
        button.setIcon(make_corner_icon(kind, 36))
        button.setIconSize(QSize(28, 28))
        button.setFixedSize(36, 36)
        button.setToolTip(tip)
        button.setAccessibleName(tip)
        button.setCheckable(checkable)
        button.setAutoRaise(False)
        button.setStyleSheet("""
            QToolButton { border: 1px solid transparent; border-radius: 2px;
                          background: transparent; padding: 2px; }
            QToolButton:hover:enabled { background: #e7eff6; border-color: #b2c8da; }
            QToolButton:checked { background: #bedaf2; border-color: #6c9ac2; }
            QToolButton:disabled { background: transparent; border-color: transparent; }
        """)
        if callback is not None:
            button.clicked.connect(callback)
        return button

    def icon_tool(self, kind, tip, callback, label=''):
        b=QToolButton(self)
        b.setIcon(make_icon(kind,36))
        b.setIconSize(QSize(29,29))
        b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        b.setText(label)
        b.setMinimumHeight(60)
        b.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
        b.setToolTip(tip)
        b.clicked.connect(callback)
        return b

    def toggle_closed_side(self):
        tool = getattr(self.app.viewport, 'active_tool', None)
        if isinstance(tool, ProfileDrawTool) and (tool._is_horizontal() or tool._is_wall()):
            tool.on_key(self.app.viewport, int(Qt.Key_F), None)
            return
        self.closed_side.setCurrentIndex(1-self.closed_side.currentIndex())
        self.app.viewport.flash_status('封閉路徑方向：'+self.closed_side.currentText(), 2500)

    def refresh_scene_options(self):
        """Refresh directly from the active project, preserving selections.

        Do not silently suppress failures: they should be visible to the user.
        """
        try:
            scene=self.app.scene
            registry=getattr(scene,'materials',None) or {}
            material_names=[str(name) for name in registry.keys()]
            # Some imported documents have face material labels without a
            # registry record. Expose their names, but reject applying them
            # until the corresponding recipe is present.
            for mesh in [getattr(scene,'loose_mesh',None)]:
                if mesh is None: continue
                for face in getattr(mesh,'faces',[]):
                    name=(getattr(face,'attrs',None) or {}).get('mat')
                    if name and str(name) not in material_names:
                        material_names.append(str(name))
            names=sorted(set(material_names), key=str.casefold)
            layers=sorted(set(str(layer.name) for layer in getattr(scene,'layers',[])
                              if getattr(layer,'name',None)), key=str.casefold)
            for combo, default, values in ((self.material,'預設材質',names),
                                           (self.tag,'預設標籤',layers)):
                current=combo.currentText()
                new=[default]+[v for v in values if v!=default]
                old=[combo.itemText(i) for i in range(combo.count())]
                if new != old:
                    combo.blockSignals(True)
                    combo.clear(); combo.addItems(new)
                    combo.setCurrentText(current if current in new else default)
                    combo.blockSignals(False)
            self._scene_options_error=None
        except Exception as exc:
            msg=f'讀取專案材質／標籤時發生錯誤：{type(exc).__name__}: {exc}'
            if getattr(self,'_scene_options_error',None)!=msg:
                self._scene_options_error=msg
                QMessageBox.warning(self,'快速輪廓生成器',msg)

    def showEvent(self,event):
        super().showEvent(event)
        self.refresh_scene_options()

    def edit_selected_profiles(self):
        from .profile_operations import edit_selected_profiles
        edit_selected_profiles(self)

    def activate_stamp(self):
        from .profile_operations import ProfileStampTool
        viewport = self.app.viewport
        if getattr(viewport.scene, 'edit_group', None) is not None:
            self.report('請先結束群組編輯，再放置剖面。')
            return
        viewport.set_active_tool(ProfileStampTool(self))
        viewport.setFocus(Qt.OtherFocusReason)
        viewport.flash_status('放置剖面：每點一次蓋印；方向鍵選平面、向下恢復自動；Esc 結束。', 6000)

    def activate_pipette(self):
        """Read-only pipette: clicking a supported group updates this panel."""
        viewport = self.app.viewport
        if getattr(viewport.scene, 'edit_group', None) is not None:
            self.report('請先結束群組編輯，再吸取完整線板群組。')
            return
        viewport.set_active_tool(ProfilePipetteTool())
        viewport.flash_status('吸取剖面：點擊已生成的輪廓群組；可連續吸取，Esc 結束。', 5000)

    def apply_picked_group(self, group):
        """Restore a saved profile without modifying the selected group.

        Old v1.8.2 groups have a name/settings but no embedded section;
        those are resolved from the present library instead. New groups
        embed the section for resilient re-use after moving the library.
        """
        ext = getattr(group, 'ext', None) or {}
        data = ext.get(PLUGIN_KEY) if isinstance(ext, dict) else None
        if not isinstance(data, dict):
            return False, '這不是快速輪廓生成器建立的群組。'
        name = data.get('profile')
        if not isinstance(name, str) or not name:
            return False, '線板未記錄剖面名稱。'
        section = data.get('section')
        if section is not None:
            try:
                section = clean_polygon(section)
            except (TypeError, ValueError, IndexError):
                return False, '群組內記錄的剖面形狀無效。'
        else:
            section = self.profiles.get(name)
            if section is None:
                return False, ('舊版線板沒有保存剖面幾何，且目前資料庫找不到「'
                               + name + '」。請先匯入該剖面。')
        # Validate before mutating the UI; a malformed group must not
        # partially overwrite the current settings.
        settings = data.get('settings') or {}
        if not isinstance(settings, dict):
            return False, '群組的參數資料無效。'
        if name not in self.profiles or self.profiles[name] != section:
            # The group carries its own copy: do not persist or overwrite the
            # user's on-disk library. Register it for this session only.
            key = name
            if key in self.profiles and self.profiles[key] != section:
                key = name + '｜吸取'
            self.profiles[key] = [list(pt) for pt in section]
            if data.get('temporary_profile'):
                self._temporary_profiles.add(key)
        else:
            key = name
        self._refresh_profiles(key)
        for field, control in (('width_mm', self.width), ('height_mm', self.height),
                               ('rotate_deg', self.rotation), ('off_u_mm', self.offsetu),
                               ('off_v_mm', self.offsetv)):
            value = settings.get(field)
            if isinstance(value, (int, float)) and math.isfinite(value):
                control.setValue(float(value))
        if 'mirror' in settings:
            self.mirror.setChecked(bool(settings['mirror']))
        if 'reverse_depth' in settings:
            self.reverse_depth.setChecked(bool(settings['reverse_depth']))
        if 'miter' in settings:
            self.set_corner(bool(settings['miter']))
        for field, combo in (('anchor', self.anchor), ('closed_side', self.closed_side)):
            value = settings.get(field)
            if isinstance(value, str) and combo.findText(value) >= 0:
                combo.setCurrentText(value)
        self.refresh_scene_options()
        for value, combo, default in ((data.get('material'), self.material, '預設材質'),
                                      (data.get('tag') or getattr(group, 'layer', None),
                                       self.tag, '預設標籤')):
            if value and combo.findText(str(value)) >= 0:
                combo.setCurrentText(str(value))
            else:
                combo.setCurrentText(default)
        return True, '已吸取「' + getattr(group, 'name', name) + '」的剖面設定。'

    def finish_drawing(self):
        """Explicit finish button for users who prefer not to press a key."""
        viewport=self.app.viewport
        tool=getattr(viewport, 'active_tool', None)
        if not isinstance(tool, ProfileDrawTool):
            viewport.flash_status('請先使用左下角繪製工具選取至少兩個點。', 3000)
            return
        tool.finish(viewport)

    def activate_draw(self):
        """Activate tool through viewport API, not through extension menu text."""
        try:
            viewport=self.app.viewport
            self.refresh_scene_options()
            viewport.set_active_tool(ProfileDrawTool())
            viewport.setFocus(Qt.OtherFocusReason)
            viewport.flash_status('輪廓繪製已啟動：滑鼠點選路徑節點，Space／Enter 完成，Esc 取消。',5000)
        except Exception as exc:
            self.report(f'啟動滑鼠繪製失敗：{type(exc).__name__}: {exc}')

    def heading(self, label):
        w=QLabel(label)
        w.setStyleSheet('font-weight: bold;')
        return w

    def rule(self, parent):
        rule=QFrame()
        rule.setObjectName('rule')
        rule.setFrameShape(QFrame.HLine)
        parent.addWidget(rule)

    def named_spin(self, name, spin):
        row=QWidget()
        layout=QHBoxLayout(row)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(3)
        label=QLabel(name)
        label.setMinimumWidth(57)
        layout.addWidget(label)
        layout.addWidget(spin,1)
        return row

    def set_corner(self, miter):
        self.miter.setChecked(miter)
        self.corner_miter.setChecked(miter)
        self.corner_flat.setChecked(not miter)

    def reset_dimensions(self):
        """Restore the selected section's native width and height, not 17 x 17."""
        try:
            width_mm, height_mm = section_dimensions_mm(self.profiles[self.profile.currentText()])
        except (ValueError, KeyError):
            return
        self._dim_update = True
        try:
            self.width.setValue(width_mm)
            self.height.setValue(height_mm)
        finally:
            self._dim_update = False
        self.on_dimension_link_toggled(self.link_dimensions.isChecked())

    def on_dimension_link_toggled(self, checked):
        """Capture the displayed ratio when locking, without changing either size."""
        if checked:
            h = self.height.value()
            if h > EPS:
                self._locked_aspect_ratio = self.width.value() / h

    def sync_dimension(self, which, value):
        if not self.link_dimensions.isChecked() or self._dim_update:
            return
        ratio = self._locked_aspect_ratio
        if not math.isfinite(ratio) or ratio <= EPS:
            return
        self._dim_update = True
        try:
            if which == 'w':
                self.height.setValue(value / ratio)
            else:
                self.width.setValue(value * ratio)
        finally:
            self._dim_update = False

    def on_profile_changed(self, name):
        if name in self.profiles:
            section = self.profiles[name]
            self.preview.set_points(section)
            # A newly captured/selected section carries real-size coordinates
            # in millimetres.  Set both dimensions atomically so the optional
            # proportion lock cannot overwrite the other measurement.
            try:
                width_mm, height_mm = section_dimensions_mm(section)
                previous = self._dim_update
                self._dim_update = True
                try:
                    self.width.setValue(width_mm)
                    self.height.setValue(height_mm)
                    # A new section has its own native aspect ratio.
                    self._locked_aspect_ratio = width_mm / height_mm
                finally:
                    self._dim_update = previous
            except ValueError:
                # Geometry validation is performed at import/capture time;
                # an invalid legacy record must not break the UI.
                pass
            # Display only the short identifier when the name is "058｜弧形線板".
            visible = name.split('｜', 1)[0].strip() if '｜' in name else name
            self.profile_name.setText(visible)
            if not getattr(self, '_restoring_profile_preferences', False) and hasattr(self, '_profile_preferences') and hasattr(self, 'closed_side'):
                self._active_profile_key = self._profile_key_override or profile_identity('saved', name)
                self._profile_key_override = None
                saved = self._profile_preferences['profiles'].get(self._active_profile_key)
                if saved is not None:
                    self._apply_saved_options(saved)
                else:
                    self._reset_profile_options()

    def select_profile_by_name(self):
        term = self.profile_name.text().strip()
        match = next((n for n in self.profiles if n == term), None)
        if match is None:
            match = next((n for n in sorted(self.profiles) if n.split('｜',1)[0].strip() == term),None)
        if match is None:
            QMessageBox.information(self, '剖面名稱', '找不到指定剖面，請按放大鏡搜尋。')
            self.on_profile_changed(self.profile.currentText())
            return
        self.profile.setCurrentText(match)
        self.on_profile_changed(match)

    def search_profile(self):
        """Show the database first; never silently fail when creating the window."""
        try:
            if self._profile_browser is None:
                self._profile_browser = ProfileBrowser(self)
            browser = self._profile_browser
            already_open = browser.isVisible()
            browser.show()
            browser.raise_()
            browser.activateWindow()
            # Refresh only an already-visible browser. First-show refresh is
            # scheduled by showEvent, avoiding redundant directory scans.
            if already_open:
                QTimer.singleShot(120, browser._safe_reload)
        except Exception as exc:
            import traceback
            details = traceback.format_exc()
            try:
                from pathlib import Path
                import tempfile
                log = Path(tempfile.gettempdir()) / 'ingetrazo_quick_profile_browser_error.log'
                log.write_text(details, encoding='utf-8')
                log_hint = f'\n\n錯誤紀錄：{log}'
            except OSError:
                log_hint = ''
            QMessageBox.warning(self, '無法開啟剖面資料庫',
                f'資料庫視窗發生錯誤：{type(exc).__name__}: {exc}' + log_hint)

    def browser_identity(self, item):
        key = item['key']
        if item.get('source') == 'external':
            return profile_identity('external', item['name'], key[1], item['name'])
        if item.get('source') == 'skp':
            return profile_identity('skp', item['name'], key[1], item['name'])
        return profile_identity('saved', item['name'])

    def apply_browser_profile(self, name, points=None, source_key=None):
        """Apply a profile from the built-ins or a readable user JSON file."""
        if points is not None:
            clean = clean_polygon(points)
            if name in self.profiles and self.profiles[name] != clean:
                answer = QMessageBox.question(self, '剖面同名',
                    f'剖面「{name}」已存在且形狀不同，是否取代目前的剖面？')
                if answer != QMessageBox.Yes:
                    return False
            self.profiles[name] = clean
            self._temporary_profiles.discard(name)
            self._persist_profiles()
            self._profile_key_override = source_key
            self._refresh_profiles(name)
        else:
            if name not in self.profiles:
                return False
            self._profile_key_override = source_key
            self.choose_profile(name)
        return True

    def protected_profile_names(self):
        return set(PRESETS)

    def _library_directory(self):
        """Use the actual folder chosen in the browser, including saved state."""
        if self._profile_browser is not None:
            return self._profile_browser.directory
        state=self.file.parent/'browser_state.json'
        try:
            directory=Path(json.loads(state.read_text(encoding='utf-8')).get('directory',''))
            if directory.is_dir(): return directory
        except (OSError,ValueError,TypeError):
            pass
        folder=self.file.parent/'library'
        folder.mkdir(parents=True,exist_ok=True)
        return folder

    def _refresh_browser(self):
        if self._profile_browser is not None:
            self._profile_browser.reload_items()

    def _save_named_to_library(self,name,points):
        if name in PRESETS:
            raise ValueError('內建剖面不可覆寫，請使用新的自訂名稱。')
        directory=self._library_directory()
        existing=find_owned(directory,name)
        if existing is not None:
            if QMessageBox.question(self,'覆寫線板',f'資料夾中已有「{name}」，確定覆寫嗎？') != QMessageBox.Yes:
                return False
        path=save_profile(directory,name,points,overwrite=existing is not None)
        # Keep the original legacy store as a backup for existing projects.
        # Do not discard this directory's individual file on legacy-store error.
        self.profiles[name]=[list(v) for v in points]
        self._temporary_profiles.discard(name)
        self._persist_profiles()
        self._refresh_profiles(name)
        self._refresh_browser()
        QMessageBox.information(self,'儲存成功',f'線板「{name}」已寫入：\n{path}\n\n原始 SKP 未修改。')
        return True

    def sync_legacy_profiles(self):
        """Explicitly copy older custom profiles (e.g. 059) into selected folder."""
        target=self._library_directory()
        customs={name:pts for name,pts in self.library_profiles().items() if name not in PRESETS}
        if not customs:
            QMessageBox.information(self,'同步完成','沒有需要同步的舊版自訂線板。')
            return
        if QMessageBox.question(self,'同步舊版線板',
            f'將 {len(customs)} 個自訂剖面複製至：\n{target}\n\n已有同名檔案會略過，來源資料不變。') != QMessageBox.Yes:
            return
        created=0; skipped=0; problems=[]
        for name,points in customs.items():
            try:
                if find_owned(target,name) is not None:
                    skipped+=1
                    continue
                save_profile(target,name,points)
                created+=1
            except Exception as exc:
                problems.append(f'{name}: {exc}')
        self._refresh_browser()
        text=f'新增 {created} 個；略過 {skipped} 個。'
        if problems: text+='\n問題：\n'+'\n'.join(problems[:10])
        QMessageBox.information(self,'同步結果',text)

    def rename_browser_profile(self,item,new_name):
        """Rename our owned files at their actual source, never external originals."""
        old = item['name']
        if old in PRESETS:
            raise ValueError('內建線板不能重新命名。')
        if new_name in PRESETS or new_name in self.profiles:
            raise FileExistsError('新的線板名稱已存在。')
        source = item.get('source')
        if source in ('skp', 'invalid-json'):
            raise ValueError('來源 SKP 或無效 JSON 不能直接改名。')
        old_key = tuple(item['key'])
        old_settings_key = profile_identity('external', old, old_key[1], old) if source == 'external' else profile_identity('saved', old)
        if source == 'external':
            old_file = Path(old_key[1])
            if read_owned(old_file) is None:
                raise ValueError('外部 JSON 不是本外掛建立的資料，不能直接重新命名。')
            new_file = rename_owned(old_file, new_name)
        else:
            old_file = find_owned(self._library_directory(), old)
            if old_file:
                new_file = rename_owned(old_file, new_name)
            else:
                points = item.get('points') or self.profiles.get(old)
                if points is None:
                    raise ValueError('找不到可儲存的線板幾何。')
                new_file = save_profile(self._library_directory(), new_name, points)
        if old in self.profiles and old not in PRESETS:
            self.profiles[new_name] = self.profiles.pop(old)
        else:
            self.profiles[new_name] = [list(v) for v in item['points']]
        self._persist_profiles()
        new_settings_key = profile_identity('external', new_name, new_file, new_name)
        self._rename_profile_preferences(old_settings_key, new_settings_key, new_name)
        # The browser updates its recent key after a successful rename.
        self._profile_key_override = new_settings_key
        self._refresh_profiles(new_name)
        self._refresh_browser()
        return ('external', str(new_file), new_name)

    def delete_browser_profile(self,item):
        name = item['name']
        if name in PRESETS:
            browser = self._profile_browser
            hidden = set(browser.state.get('hidden_builtins', []))
            hidden.add(name)
            browser.state['hidden_builtins'] = sorted(hidden)
            browser._save_state()
            browser.render_items()
            return '內建剖面已隱藏，可透過「顯示隱藏」復原。'
        source = item.get('source')
        if source == 'skp':
            raise ValueError('來源 SKP 不會被刪除。')
        if source in ('invalid-json', 'external'):
            file = Path(item['key'][1])
            if read_owned(file) is None:
                raise ValueError('外部 JSON 非本外掛建立，不能刪除原始檔案。')
            archive_owned(file)
            setting_key = profile_identity('external', name, file, name)
        else:
            target = find_owned(self._library_directory(), name)
            if target:
                archive_owned(target)
            setting_key = profile_identity('saved', name)
        if name in self.profiles and name not in PRESETS:
            # Other user profiles may share names; remove only matching geometry.
            if self.profiles[name] == item.get('points'):
                self.profiles.pop(name)
                self._persist_profiles()
                self._refresh_profiles()
        self._forget_profile_preferences(setting_key)
        self._refresh_browser()
        return '剖面已移除；本外掛管理的檔案已備份到 .quick_profile_removed。'

    def choose_profile(self, name):
        self.profile.setCurrentText(name)
        self.on_profile_changed(name)

    def _last_used_path(self):
        return self.file.parent / 'last_saved_use.json'

    def _preferences_path(self):
        return self.file.parent / 'profile_preferences_v2.json'

    def _load_profile_preferences(self):
        try:
            data = read_profile_preferences(self._preferences_path())
        except (ValueError, OSError, TypeError) as exc:
            # Corrupt settings must never prevent the modeling tool from opening.
            data = {'version': 2, 'last_key': None, 'profiles': {}}
        if not self._preferences_path().exists():
            try:
                old = load_last_used(self._last_used_path())
                if old is not None:
                    # Only the named legacy profile inherits the old settings.
                    # No other profile receives a copy.
                    key = profile_identity('saved', old['profile'])
                    data['profiles'][key] = old
                    data['last_key'] = key
                    write_profile_preferences(self._preferences_path(), data)
            except (ValueError, OSError, TypeError):
                pass
        return data

    def _write_preferences(self):
        write_profile_preferences(self._preferences_path(), self._profile_preferences)

    def _reset_profile_options(self):
        """Never carry the previous profile's unsaved rotation/mirror/offsets."""
        self._restoring_profile_preferences = True
        try:
            self.rotation.setValue(0)
            self.mirror.setChecked(False)
            self.offsetu.setValue(0)
            self.offsetv.setValue(0)
            self.reverse_depth.setChecked(True)
            self.set_corner(True)
            self.anchor.setCurrentIndex(0)
            self.closed_side.setCurrentText('內側')
            self.refresh_scene_options()
            self.material.setCurrentIndex(0)
            self.tag.setCurrentIndex(0)
        finally:
            self._restoring_profile_preferences = False

    def _apply_saved_options(self, saved):
        settings = saved['settings']
        self._restoring_profile_preferences = True
        try:
            self._dim_update = True
            try:
                self.width.setValue(settings['width_mm'])
                self.height.setValue(settings['height_mm'])
                self._locked_aspect_ratio = self.width.value() / max(self.height.value(), EPS)
            finally:
                self._dim_update = False
            for key, control in (('rotate_deg', self.rotation), ('off_u_mm', self.offsetu),
                                 ('off_v_mm', self.offsetv)):
                control.setValue(settings[key])
            self.mirror.setChecked(settings['mirror'])
            self.reverse_depth.setChecked(settings['reverse_depth'])
            self.set_corner(settings['miter'])
            for key, combo in (('anchor', self.anchor), ('closed_side', self.closed_side)):
                if combo.findText(settings[key]) >= 0:
                    combo.setCurrentText(settings[key])
            self.refresh_scene_options()
            for key, combo in (('material', self.material), ('tag', self.tag)):
                if combo.findText(saved[key]) >= 0:
                    combo.setCurrentText(saved[key])
                else:
                    combo.setCurrentIndex(0)
        finally:
            self._restoring_profile_preferences = False

    def save_last_used_state(self):
        """Save only the current profile's settings, never the source profile."""
        name = self.profile.currentText()
        if name in self._temporary_profiles:
            self.report('目前是暫存剖面；如需保留，請使用「進階設定 → 另存剖面到資料庫」，命名後再儲存個別參數。')
            return
        section = self.profiles.get(name)
        if section is None:
            self.report('請先選取有效的剖面。')
            return
        key = self._active_profile_key or profile_identity('saved', name)
        snapshot = dict(version=1, profile=name,
                        section=[list(p) for p in section], settings=self.opts(),
                        material=self.material.currentText(), tag=self.tag.currentText())
        try:
            from .last_used_state import validate
            self._profile_preferences['profiles'][key] = validate(snapshot)
            self._profile_preferences['last_key'] = key
            self._write_preferences()
        except Exception as exc:
            self.report('儲存個別剖面設定失敗：' + str(exc))
            return
        QMessageBox.information(self, '個別剖面設定已儲存',
            '只儲存「' + name + '」的尺寸與操作參數。\n其他剖面的設定不會改變。\n\n原始剖面資料庫未修改。')

    def restore_last_used_state(self):
        """Restore last selected profile and its own saved parameters only."""
        try:
            key = self._profile_preferences.get('last_key')
            saved = self._profile_preferences['profiles'].get(key)
            if saved is None:
                return
            name = saved['profile']
            # Never resurrect a deleted profile from obsolete settings.
            if name not in self.profiles:
                return
            self._profile_key_override = key
            self._refresh_profiles(name)
        except Exception as exc:
            QMessageBox.warning(self, '使用狀態還原失敗', str(exc))

    def _rename_profile_preferences(self, old_key, new_key, new_name):
        try:
            changed = move_profile_preferences(self._profile_preferences, old_key, new_key, new_name)
            if changed:
                self._write_preferences()
            if self._active_profile_key == old_key:
                self._active_profile_key = new_key
        except Exception as exc:
            self.report('剖面已重新命名，但個別設定同步失敗：' + str(exc))

    def _forget_profile_preferences(self, key):
        forget_profile_preferences(self._profile_preferences, key)
        if self._active_profile_key == key:
            self._active_profile_key = None
        self._write_preferences()

    def save_current_profile(self):
        current = self.profile.currentText()
        if current not in self.profiles:
            self.report('請先選取有效的剖面。')
            return
        name, ok = QInputDialog.getText(self, '儲存剖面', '輸入要儲存的剖面名稱：', text='' if current in self._temporary_profiles else current)
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in PRESETS:
            self.report('內建剖面不可覆寫，請輸入新的自訂名稱。')
            return
        if name in self.profiles and name not in self._temporary_profiles and QMessageBox.question(self,'覆寫確認',f'確定覆寫「{name}」嗎？') != QMessageBox.Yes:
            return
        try:
            self._save_named_to_library(name,self.profiles[current])
        except Exception as exc:
            self.report(exc)

    def header_button(self, kind, tip, callback):
        btn = QToolButton(self)
        btn.setAutoRaise(True)
        btn.setIcon(make_header_icon(kind, 28))
        btn.setIconSize(QSize(26, 26))
        btn.setFixedSize(33, 31)
        btn.setToolTip(tip)
        btn.clicked.connect(callback)
        btn.setStyleSheet('QToolButton {border: none; background: transparent;} QToolButton:hover { background:#e6edf4; border-radius:3px; }')
        return btn

    def open_manage_menu(self):
        menu=QMenu(self)
        act_reverse=menu.addAction('垂直路徑反轉剖面正反面')
        act_reverse.setCheckable(True)
        act_reverse.setChecked(self.reverse_depth.isChecked())
        act_direction=menu.addAction('封閉路徑：切換內側／外側')
        menu.addSeparator()
        act_manual=menu.addAction('輸入路徑座標')
        act_finish=menu.addAction('完成目前繪製（Space／Enter）')
        menu.addSeparator()
        act_delete=menu.addAction('刪除自訂剖面')
        act_import=menu.addAction('匯入剖面資料庫')
        act_export=menu.addAction('匯出剖面資料庫')
        act_save_library=menu.addAction('另存剖面到資料庫')
        chosen=menu.exec(self.mapToGlobal(self.rect().bottomRight()))
        if chosen is act_reverse: self.reverse_depth.setChecked(act_reverse.isChecked())
        elif chosen is act_direction: self.toggle_closed_side()
        elif chosen is act_manual: self.open_manual_path()
        elif chosen is act_finish: self.finish_drawing()
        elif chosen is act_delete: self.delete_profile()
        elif chosen is act_import: self.import_library()
        elif chosen is act_export: self.export_library()
        elif chosen is act_save_library: self.save_current_profile()

    def open_manual_path(self):
        dialog=QDialog(self)
        dialog.setWindowTitle('輸入路徑座標')
        dialog.resize(400,260)
        layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('每行 x,y,z（單位 mm），例如：0,0,0'))
        editor=QPlainTextEdit('0,0,0\n1000,0,0\n1000,1000,0')
        layout.addWidget(editor)
        closed=QCheckBox('封閉輸入路徑')
        layout.addWidget(closed)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('生成輪廓')
        buttons.button(QDialogButtonBox.Cancel).setText('取消')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec()!=QDialog.Accepted: return
        self.points=editor
        self.closed=closed
        self.generate_manual()

    def spin(self,val,low,high):
        x=QDoubleSpinBox(); x.setRange(low,high); x.setDecimals(2); x.setValue(val); return x
    def opts(self):
        return dict(width_mm=self.width.value(),height_mm=self.height.value(),
                    rotate_deg=self.rotation.value(),mirror=self.mirror.isChecked(),
                    off_u_mm=self.offsetu.value(),off_v_mm=self.offsetv.value(),
                    miter=self.miter.isChecked(),anchor=self.anchor.currentText(),
                    closed_side=self.closed_side.currentText(),
                    reverse_depth=self.reverse_depth.isChecked())
    def report(self,message): QMessageBox.warning(self,"快速輪廓生成器",str(message))
    def _produce(self, paths, mouse_draw=False, experimental=False, wall_normal=None, mouse_orientation=None):
        """Commit each sweep as a separate group, never into scene.mesh."""
        try:
            section = self.profiles.get(self.profile.currentText())
            if section is None:
                raise ValueError("請從剖面清單選擇有效的剖面。")
            prepared = []
            for pts, closed in paths:
                sweep_options=self.opts()
                if mouse_draw and mouse_orientation:
                    sweep_options.update(mouse_orientation)
                if experimental:
                    sweep_options['wall_normal']=wall_normal
                raw_polygons = (experimental_sweep_faces if experimental else sweep_faces)(pts, section, closed,
                                           preview_side=(mouse_draw and not closed),
                                           stable_mouse_plane=mouse_draw,
                                           **sweep_options)
                polygons = mesh_ready_faces(raw_polygons)
                if polygons:
                    hidden_keys, smooth_keys = edge_display_rules(raw_polygons, polygons, closed)
                    prepared.append((polygons, hidden_keys, smooth_keys,
                                     [list(pt) for pt in pts], closed))
            if not prepared:
                raise ValueError("沒有生成任何面。")
            self.refresh_scene_options()
            selected_mat = self.material.currentText()
            selected_tag = self.tag.currentText()
            profile_name = self.profile.currentText()
            settings = self.opts()
            def mutate(scene):
                if getattr(scene, 'edit_group', None) is not None:
                    raise ValueError('請先結束群組編輯模式。')
                materials = getattr(scene, 'materials', {}) or {}
                mat = materials.get(selected_mat) if selected_mat != '預設材質' else None
                if selected_mat != '預設材質' and mat is None:
                    raise ValueError(f'材質「{selected_mat}」不存在，請重新選擇。')
                if selected_tag != '預設標籤' and selected_tag not in [layer.name for layer in getattr(scene,'layers',[])]:
                    raise ValueError(f'標籤「{selected_tag}」不存在，請重新選擇。')
                existing = {g.name for g in scene.groups}
                for polygons, hidden_keys, smooth_keys, path, closed in prepared:
                    mesh = Mesh()
                    for polygon in polygons:
                        f = mesh.add_face([qvec(point) for point in polygon])
                        if mat is not None:
                            f.attrs.update(mat.face_attrs())
                        if selected_tag != '預設標籤':
                            f.attrs['layer'] = selected_tag
                    apply_edge_display(mesh, hidden_keys, smooth_keys)
                    prefix = '輪廓_' + profile_name.split('｜')[0].split('|')[0].strip()
                    num = 1
                    while f'{prefix}_{num:03d}' in existing:
                        num += 1
                    name = f'{prefix}_{num:03d}'
                    existing.add(name)
                    group = Group(mesh, name)
                    if selected_tag != '預設標籤':
                        group.layer = selected_tag
                    group.ext = {'quick_profile': {'profile': profile_name, 'path': path,
                                     'closed': closed, 'settings': dict(settings),
                                     'section': [list(point) for point in section],
                                     'material': selected_mat, 'tag': selected_tag,
                                     'mouse_orientation': dict(mouse_orientation or {}),
                                     'temporary_profile': profile_name in self._temporary_profiles,
                                     'mouse_draw': bool(mouse_draw),
                                     'experimental': bool(experimental)}}
                    scene.groups.append(group)
            self.app.viewport.history.execute(SnapshotImport(mutate))
            if getattr(self.app.viewport.history, 'last_error', None):
                raise RuntimeError(self.app.viewport.history.last_error)
            self.app.viewport.notify_scene_changed()
            count = sum(len(item[0]) for item in prepared)
            self.app.viewport.flash_status(f'{"實驗中" if experimental else "快速輪廓"}：已建立 {len(prepared)} 個獨立群組，共 {count} 個面', 4000)
            return True
        except Exception as exc:
            self.report(exc)
            return False
    def generate_manual(self):
        try:
            pts=[]
            for text in self.points.toPlainText().splitlines():
                if not text.strip(): continue
                items=[float(x.strip())/1000 for x in text.split(",")]
                if len(items)!=3: raise ValueError("每一行請輸入 x,y,z 三個座標（mm）。")
                pts.append(tuple(items))
            if self.closed.isChecked() and len(pts)>2 and math.dist(pts[0],pts[-1])<EPS: pts.pop()
            self._produce([(pts,self.closed.isChecked())])
        except Exception as exc: self.report(exc)
    def generate_selected(self):
        try:
            scene=self.app.viewport.scene
            if getattr(scene,"edit_group",None) is not None:
                raise ValueError("生成前請先結束群組編輯模式。")
            edges=[e for e in scene.selection if isinstance(e,Edge) and e in scene.mesh.edges]
            self._produce(ordered_paths(edges))
        except Exception as exc: self.report(exc)
    def generate_perimeter(self):
        try:
            scene=self.app.viewport.scene
            if getattr(scene,"edit_group",None) is not None:
                raise ValueError("請先結束群組編輯模式。")
            faces=[f for f in scene.selection if isinstance(f,Face) and f in scene.mesh.faces]
            if len(faces)!=1:
                raise ValueError("請只選取一個封閉面。")
            face=faces[0]
            if face.hole_loops:
                raise ValueError("目前僅支援無孔洞的面外框。")
            path=[as_tuple(v.position) for v in face.loop]
            if len(path)>2 and math.dist(path[0],path[-1])<EPS:
                path.pop()
            self._produce([(path, True)])
        except Exception as exc: self.report(exc)
    def _refresh_profiles(self,name=None):
        self.profile.blockSignals(True)
        self.profile.clear()
        self.profile.addItems(sorted(self.profiles))
        if name in self.profiles:
            self.profile.setCurrentText(name)
        self.profile.blockSignals(False)
        self.on_profile_changed(self.profile.currentText())
    def library_profiles(self):
        """Return named library entries, excluding unsaved session captures."""
        temporary = getattr(self, '_temporary_profiles', set())
        return {k:v for k,v in self.profiles.items() if k not in temporary}

    def _persist_profiles(self):
        custom={k:v for k,v in self.library_profiles().items() if k not in PRESETS or v!=PRESETS[k]}
        self.file.parent.mkdir(parents=True,exist_ok=True)
        self.file.write_text(json.dumps(custom,ensure_ascii=False,indent=2),encoding="utf-8")
    def import_library(self):
        filename,_=QFileDialog.getOpenFileName(self,"匯入剖面資料庫", "", "剖面資料庫 (*.json)")
        if not filename: return
        try:
            parsed=json.loads(Path(filename).read_text(encoding="utf-8"))
            if not isinstance(parsed,dict) or len(parsed)>1000:
                raise ValueError("資料庫必須是名稱對應頂點座標的 JSON 物件，且最多一千筆。")
            validated={}
            for name,polygon in parsed.items():
                if not isinstance(name,str) or not name.strip() or len(name)>90:
                    raise ValueError("剖面名稱不可空白且不可超過 90 字。")
                if not isinstance(polygon,list) or len(polygon)>2048:
                    raise ValueError("單一剖面的點數不可超過 2048。")
                validated[name]=clean_polygon(polygon)
            conflicts=[name for name in validated if name in self.profiles]
            if conflicts and QMessageBox.question(self,"覆寫確認",f"有 {len(conflicts)} 個同名剖面。是否覆寫？") != QMessageBox.Yes:
                return
            self.profiles.update(validated)
            self._temporary_profiles.difference_update(validated)
            self._persist_profiles()
            self._refresh_profiles(next(iter(validated),None))
            QMessageBox.information(self,"匯入完成",f"已匯入 {len(validated)} 個剖面。")
        except Exception as exc: self.report(exc)
    def export_library(self):
        filename,_=QFileDialog.getSaveFileName(self,"匯出剖面資料庫","quick_profile_library.json","剖面資料庫 (*.json)")
        if not filename:return
        try:
            Path(filename).write_text(json.dumps(self.library_profiles(),ensure_ascii=False,indent=2),encoding="utf-8")
            QMessageBox.information(self,"匯出完成","剖面資料庫已匯出。")
        except Exception as exc:self.report(exc)
    def delete_profile(self):
        name=self.profile.currentText()
        if name in PRESETS:
            self.report("內建剖面不可刪除。")
            return
        if QMessageBox.question(self,"刪除確認",f"確定刪除「{name}」嗎？") != QMessageBox.Yes:
            return
        owned=find_owned(self._library_directory(),name)
        if owned:
            archive_owned(owned)
        self.profiles.pop(name,None)
        self._forget_profile_preferences(self._active_profile_key or profile_identity('saved', name))
        self._persist_profiles()
        self._refresh_profiles()
        self._refresh_browser()
    def save_face(self):
        try:
            scene=self.app.viewport.scene
            faces=[e for e in scene.selection if isinstance(e,Face) and e in scene.mesh.faces]
            if len(faces)!=1: raise ValueError("請只選取一個平面，才能儲存為剖面。")
            face=faces[0]
            if face.hole_loops: raise ValueError("目前尚不支援含孔洞的面。")
            verts=[as_tuple(v.position) for v in face.loop]
            polygon=face_section_points_mm(verts)
            # Capture only into session memory. Generated groups embed their
            # own section so they remain editable after restarting IngeTrazo.
            name='暫存剖面'
            number=1
            while name in self.profiles:
                number+=1
                name=f'暫存剖面 {number}'
            self.profiles[name]=polygon
            self._temporary_profiles.add(name)
            self._refresh_profiles(name)
            self.app.viewport.flash_status('已擷取暫存剖面，可直接繪製或放置；需要保留時，使用進階設定的「另存剖面到資料庫」。',6000)
        except Exception as exc:self.report(exc)


_WINDOW = None

def _show_window(app):
    """Show the floating tool window without creating a dock/panel."""
    global _WINDOW
    if _WINDOW is None:
        _WINDOW = ProfilePanel(app)
    # Reuse a single window; restore a hidden/minimised tool instead of
    # manufacturing a second top-level application window.
    if _WINDOW.isMinimized():
        _WINDOW.showNormal()
    else:
        _WINDOW.show()
    _WINDOW.refresh_scene_options()
    _WINDOW.raise_()
    _WINDOW.activateWindow()

def _pipette_cursor(available=False):
    """Draw an original 48px pipette cursor with a precise tip/hotspot.

    The marker changes colour when hovering over a profile owned by this
    extension. No external assets and no modification to the viewport core.
    """
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    tip = QPointF(7, 39)
    # White halo plus dark outline keep the pipette visible on any material.
    outline = QPainterPath()
    outline.moveTo(tip)
    outline.lineTo(16, 30)
    outline.lineTo(12, 26)
    outline.lineTo(25, 13)
    outline.lineTo(29, 17)
    outline.lineTo(35, 11)
    outline.lineTo(28, 4)
    outline.lineTo(22, 10)
    outline.lineTo(25, 13)
    outline.lineTo(12, 26)
    outline.lineTo(16, 30)
    outline.lineTo(tip)
    painter.setPen(QPen(QColor('white'), 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(QBrush(QColor('#eef3f8')))
    painter.drawPath(outline)
    painter.setPen(QPen(QColor('#1c2935'), 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawPath(outline)
    # Droplet barrel; distinctive tool silhouette even at normal DPI.
    painter.setPen(QPen(QColor('#25364a'), 2))
    painter.setBrush(QColor('#dce5ef'))
    painter.drawRoundedRect(24, 5, 9, 15, 3, 3)
    painter.save()
    painter.translate(28.5, 12.5)
    painter.rotate(45)
    painter.drawRoundedRect(-4, -8, 8, 16, 2, 2)
    painter.restore()
    # Status marker, green check when the hovered group is sampleable.
    color = QColor('#219b60') if available else QColor('#67798b')
    painter.setPen(QPen(QColor('white'), 2))
    painter.setBrush(color)
    painter.drawEllipse(30, 29, 14, 14)
    painter.setPen(QPen(QColor('white'), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if available:
        painter.drawLine(33, 36, 36, 39)
        painter.drawLine(36, 39, 41, 33)
    else:
        painter.drawLine(34, 36, 40, 36)
    painter.end()
    return QCursor(pixmap, int(tip.x()), int(tip.y()))


class ProfilePipetteTool(Tool):
    """Non-destructive profile sampler with a prominent Qt pipette cursor."""
    name = '輪廓吸取剖面'
    description = '以滴管點擊線板群組；綠色游標表示可吸取；Esc 離開。'
    shortcut = None
    uses_snap = False

    def __init__(self):
        self._cursor_normal = None
        self._cursor_valid = None
        self._hover_valid = None

    def _paint_cursor(self, viewport, valid):
        """Restore on each hover because the host may reassign tool cursors."""
        cursor = self._cursor_valid if valid else self._cursor_normal
        if cursor is not None:
            viewport.setCursor(cursor)

    def on_activate(self, viewport):
        self._cursor_normal = _pipette_cursor(False)
        self._cursor_valid = _pipette_cursor(True)
        self._hover_valid = False
        self._paint_cursor(viewport, False)
        viewport.flash_status('吸取剖面：滴管游標點選線板；綠色表示可吸取，Esc 結束。', 4500)

    def on_deactivate(self, viewport):
        # Only affect the model viewport: not a global QApplication override.
        viewport.unsetCursor()
        self._hover_valid = None
        viewport.set_hover(None)
        viewport.update()

    def on_hover(self, ctx):
        viewport = ctx.viewport
        group = viewport.pick_group(ctx.screen.x(), ctx.screen.y())
        metadata = getattr(group, 'ext', None) if group is not None else None
        valid = isinstance(metadata, dict) and isinstance(metadata.get(PLUGIN_KEY), dict)
        self._paint_cursor(viewport, valid)
        if valid != self._hover_valid:
            self._hover_valid = valid
            if valid:
                viewport.flash_status('可吸取：' + str(getattr(group, 'name', '輪廓')), 1500)
            viewport.update()

    def on_click(self, ctx):
        viewport = ctx.viewport
        group = viewport.pick_group(ctx.screen.x(), ctx.screen.y())
        if group is None:
            viewport.flash_status('這裡沒有可吸取的線板群組，請點選線板本體。', 2500)
            return
        if _WINDOW is None:
            viewport.flash_status('請先開啟輪廓外形面板。', 2500)
            return
        success, message = _WINDOW.apply_picked_group(group)
        viewport.flash_status(message, 4000)
        self._paint_cursor(viewport, success)
        if success:
            _WINDOW.show()
            _WINDOW.raise_()
        viewport.update()

    def on_key(self, viewport, key, modifiers):
        if key == int(Qt.Key_Escape):
            self._exit(viewport)
            return True
        return False

    def on_cancel(self, viewport):
        self._exit(viewport)

    def _exit(self, viewport):
        from tools.select import SelectTool
        viewport.set_active_tool(SelectTool())
        viewport.flash_status('已結束吸取剖面。', 2000)
        viewport.update()

    def status_clause(self):
        return '吸取剖面：滴管游標｜綠色可吸取｜Esc 結束｜不修改模型'

class ProfileDrawTool(Tool):
    """In-viewport parametric sweep with locked axis, shaded preview and Space finish."""
    name = '輪廓滑鼠繪製'
    description = '左鍵新增節點；點回起點封閉；空白鍵／Enter 完成；Esc 取消。'
    shortcut = None
    wireframe_color = (0.9, 0.45, 0.1, 1.)

    def __init__(self):
        self.path = []
        self.hover = None
        self.axis = None
        self._preview_mesh = None
        self._preview_segments = []
        self._closed_requested = False
        self._horizontal_mode = None
        self._drawing_mode = None
        self._wall_plane = None
        self._wall_hover_normal = None
        self._wall_fallback_axis = 0
        self._wall_side = 1.0
        self._wall_depth_sign = 1.0
        self._horizontal_side = 1.0
        self._manual_side = 1.0
        self._start_face_normal = None
        self._side_locked = False

    def on_activate(self, viewport):
        self.path = []
        self.hover = None
        self.axis = None
        self._preview_mesh = None
        self._closed_requested = False
        self._horizontal_mode = None
        self._drawing_mode = None
        self._wall_plane = None
        self._wall_hover_normal = None
        self._wall_fallback_axis = 0
        self._wall_side = 1.0
        self._wall_depth_sign = 1.0
        self._horizontal_side = 1.0
        self._manual_side = 1.0
        self._start_face_normal = None
        self._side_locked = False
        viewport.flash_status('輪廓 v2.2.4：左鍵選點、Space／Enter 完成；F 換側；牆面模式 R 換凸出面、Esc 取消', 6000)

    def on_deactivate(self, viewport):
        self._clear()

    def _clear(self):
        self.path = []
        self.hover = None
        self.axis = None
        self._preview_mesh = None
        self._preview_segments = []
        self._closed_requested = False
        self._horizontal_mode = None
        self._drawing_mode = None
        self._wall_plane = None
        self._wall_hover_normal = None
        self._wall_fallback_axis = 0
        self._wall_side = 1.0
        self._wall_depth_sign = 1.0
        self._horizontal_side = 1.0
        self._manual_side = 1.0
        self._start_face_normal = None
        self._side_locked = False

    def _is_horizontal(self):
        if self._horizontal_mode is not None:
            return self._horizontal_mode
        combo = getattr(_WINDOW, 'mouse_orientation', None)
        return combo is not None and combo.currentIndex() == 0

    def _is_wall(self):
        if self._drawing_mode is not None:
            return self._drawing_mode == 1
        combo = getattr(_WINDOW, 'mouse_orientation', None)
        return combo is not None and combo.currentIndex() == 1

    def _wall_plane_for(self, points):
        if self._wall_plane is not None:
            return self._wall_plane
        if len(points) >= 2:
            d = v_sub(points[1], points[0])
            horizontal = (d[0], d[1], 0.)
            if v_norm(horizontal) > EPS:
                return v_unit(v_cross(horizontal, (0., 0., 1.)))
        return ((0., -1., 0.), (1., 0., 0.))[self._wall_fallback_axis]

    def _choose_horizontal_side(self, points):
        side = self._manual_side
        normal = self._start_face_normal
        if normal is not None and len(points) >= 2:
            delta = v_sub(points[1], points[0])
            lateral = v_cross(delta, (0., 0., 1.))
            if v_norm(lateral) > EPS:
                alignment = v_dot(v_unit(lateral), normal)
                if abs(alignment) > .5:
                    side *= 1.0 if alignment > 0. else -1.0
        return side

    def _mouse_orientation_options(self):
        if self._is_wall():
            return dict(wall_frame=True,
                        plane_normal_override=self._wall_plane_for(self._preview_points()),
                        fixed_side=self._wall_side, wall_depth_sign=self._wall_depth_sign)
        side = (self._horizontal_side if self._side_locked else
                self._choose_horizontal_side(self._preview_points()))
        return dict(horizontal=self._is_horizontal(), fixed_side=side)

    def _show_preview_error(self, viewport):
        if getattr(self, '_preview_error', None):
            viewport.flash_status(self._preview_error, 3000)

    def _constrain(self, p):
        if self.axis is None or not self.path:
            return p
        last = self.path[-1]
        i = 'xyz'.index(self.axis)
        result = list(last)
        result[i] = p[i]
        return tuple(result)

    def drag_plane(self, viewport):
        """Camera-facing vertical working plane for Z-constrained drawing.

        The usual XY picking plane never changes Z. The vertical plane
        contains the active point and the Z direction, so the world ray can
        move up/down while x/y remain locked in _constrain().
        """
        if self.path and self._is_wall():
            return (qvec(self.path[0]), qvec(self._wall_plane_for(self._preview_points())))
        if self.path and self._is_horizontal() and self.axis != 'z':
            return (qvec(self.path[0]), qvec((0., 0., 1.)))
        if not self.path or self.axis != 'z':
            return None
        origin = self.path[-1]
        camera = getattr(viewport, 'camera', None)
        normal = (0., 1., 0.)
        if camera is not None:
            try:
                # OrbitCamera exposes the eye via `eye()` in some builds and
                # `position` in others; use whatever is actually available.
                for name in ('eye', 'position', 'eye_position'):
                    eye = getattr(camera, name, None)
                    if callable(eye): eye = eye()
                    if eye is not None:
                        delta = v_sub(as_tuple(eye), origin)
                        planar = (delta[0], delta[1], 0.)
                        if v_norm(planar) > 1e-6:
                            normal = v_unit(planar)
                            break
            except (TypeError, ValueError, AttributeError):
                pass
        return (qvec(origin), qvec(normal))

    def _closure_tolerance(self):
        # World-space fallback for endpoint snapping (metres).  The first
        # vertex itself is used, not the approximate cursor position.
        if _WINDOW is None:
            return 0.01
        return max(0.003, min(0.02, _WINDOW.width.value() / 1000.0))

    def _near_start(self, point):
        return len(self.path) >= 3 and math.dist(point, self.path[0]) <= self._closure_tolerance()

    def on_click(self, ctx):
        if not self.path:
            self._horizontal_mode = self._is_horizontal()
            combo = getattr(_WINDOW, 'mouse_orientation', None)
            self._drawing_mode = combo.currentIndex() if combo is not None else 2
            if self._is_wall():
                normal = experimental_hit_wall_normal(ctx) or self._wall_hover_normal
                if normal is not None and abs(normal[2]) < 1e-4:
                    self._wall_plane = normal
            if self._horizontal_mode:
                self._start_face_normal = experimental_hit_wall_normal(ctx)
        point = self._constrain(as_tuple(ctx.world))
        if self._near_start(point):
            # Treat clicking the start as completing a CLOSED LOOP. No duplicate
            # end vertex: sweep_faces(...,closed=True) joins the last/first ring.
            self._closed_requested = True
            self.finish(ctx.viewport)
            return
        if self.path and math.dist(point, self.path[-1]) < EPS:
            return
        if self._is_horizontal() and len(self.path) == 1 and not self._side_locked:
            self._horizontal_side = self._choose_horizontal_side(self.path + [point])
            self._side_locked = True
        if self._is_wall() and len(self.path) == 1 and self._wall_plane is None:
            self._wall_plane = self._wall_plane_for(self.path + [point])
        self.path.append(point)
        self.hover = point
        self._refresh_preview()
        self._show_preview_error(ctx.viewport)
        ctx.viewport.update()

    def on_hover(self, ctx):
        if self._is_wall() and not self.path:
            normal = experimental_hit_wall_normal(ctx)
            if normal is not None and abs(normal[2]) < 1e-4:
                self._wall_hover_normal = normal
        point = self._constrain(as_tuple(ctx.world))
        self.hover = self.path[0] if self._near_start(point) else point
        self._refresh_preview()
        self._show_preview_error(ctx.viewport)
        if self._near_start(point):
            ctx.viewport.flash_status('輪廓：已吸附起點，點擊可封閉路徑。', 1500)
        ctx.viewport.update()

    def on_double_click(self, ctx):
        # Use the same mode/side initialization and closure as a normal click.
        self.on_click(ctx)

    def on_key(self, viewport, key, modifiers):
        if self._is_wall():
            handled = True
            if key == int(Qt.Key_F):
                self._wall_side *= -1.
                message = '牆面線板：已切換線板側；凸出方向保持不變。'
            elif key == int(Qt.Key_R):
                self._wall_depth_sign *= -1.
                message = '牆面線板：已反轉凸出面；線板側保持不變。'
            elif key == int(Qt.Key_Tab) and self._wall_plane is None and len(self.path) < 2:
                self._wall_fallback_axis = 1 - self._wall_fallback_axis
                message = '牆面線板：已切換垂直起筆的牆面基準。'
            else:
                handled = False
            if handled:
                self._refresh_preview()
                viewport.flash_status(message, 3000)
                viewport.update()
                return True
        if key == int(Qt.Key_F) and self._is_horizontal():
            self._horizontal_side *= -1.0
            self._manual_side *= -1.0
            self._refresh_preview()
            viewport.flash_status('水平踢腳：已反轉厚度方向；F 可切回。', 2500)
            viewport.update()
            return True
        if key in (int(Qt.Key_Space), int(Qt.Key_Return), int(Qt.Key_Enter)):
            self.finish(viewport)
            return True
        if key == int(Qt.Key_Backspace):
            self._closed_requested = False
            if self.path:
                self.path.pop()
            if len(self.path) < 2:
                self._side_locked = False
            if not self.path:
                self._horizontal_mode = None
                self._drawing_mode = None
                self._wall_plane = None
                self._wall_hover_normal = None
                self._start_face_normal = None
            self._refresh_preview()
            viewport.update()
            return True
        axes = {int(Qt.Key_Right): 'x', int(Qt.Key_Left): 'y', int(Qt.Key_Up): 'z'}
        if key in axes:
            requested = axes[key]
            self.axis = None if self.axis == requested else requested
            if self.hover is not None:
                self.hover = self._constrain(self.hover)
            self._refresh_preview()
            viewport.flash_status('輪廓：' + (self.axis.upper() + ' 軸鎖定' if self.axis else '已解除軸向鎖定'), 2500)
            viewport.update()
            return True
        if key == int(Qt.Key_Down):
            self.axis = None
            self._refresh_preview()
            viewport.update()
            return True
        return False

    def on_cancel(self, viewport):
        self._clear()
        viewport.update()

    def status_clause(self):
        return ('Space／Enter 完成 | Backspace 上一步 | ' +
                (f'{self.axis.upper()} 軸已鎖定' if self.axis else '方向鍵鎖定 X/Y/Z') +
                (' | 水平踢腳：F 反向' if self._is_horizontal() else
                 ' | 牆面：F 換側、R 凸出面' if self._is_wall() else ''))

    def _preview_points(self):
        points = list(self.path)
        if points and self.hover is not None and math.dist(points[-1], self.hover) > EPS:
            points.append(self.hover)
        # No duplicate endpoint is passed into the geometry generator.
        if len(points) > 2 and math.dist(points[-1], points[0]) <= EPS:
            points.pop()
        return points

    def _refresh_preview(self):
        self._preview_mesh = None
        self._preview_segments = []
        self._preview_error = None
        points = self._preview_points()
        if len(points) < 2 or _WINDOW is None:
            return
        try:
            section = _WINDOW.profiles.get(_WINDOW.profile.currentText())
            if not section:
                return
            closed = len(self.path) >= 3 and self.hover is not None and self._near_start(self.hover)
            raw_polygons = sweep_faces(
                points, section, closed, preview_side=not closed,
                side_hint_path=self.path if len(self.path) >= 3 else None,
                stable_mouse_plane=True, **self._mouse_orientation_options(), **_WINDOW.opts())
            polygons = mesh_ready_faces(raw_polygons)
            hidden_keys, smooth_keys = edge_display_rules(raw_polygons, polygons, closed)
            # Temporary isolated mesh: NEVER inserted into scene or history.
            mesh = Mesh()
            segments = []
            for polygon in polygons:
                mesh.add_face([qvec(p) for p in polygon])
            apply_edge_display(mesh, hidden_keys, smooth_keys)
            # Full wireframe repeats every tessellation edge and obscures the
            # solid. Only the two end-cap outlines are useful feedback.
            for cap in (() if closed else (raw_polygons[-2], raw_polygons[-1])):
                if len(cap) >= 3:
                    for j in range(len(cap)):
                        segments.append((qvec(cap[j]), qvec(cap[(j + 1) % len(cap)])))
            self._preview_mesh = mesh
            self._preview_segments = segments
        except (ValueError, ArithmeticError) as exc:
            self._preview_error = str(exc)

    def rubber_band_lines(self):
        pts = self._preview_points()
        path_lines = [(qvec(pts[i]), qvec(pts[i + 1])) for i in range(len(pts) - 1)]
        if len(self.path) >= 3 and self.hover is not None and self._near_start(self.hover):
            path_lines.append((qvec(pts[-1]), qvec(pts[0]))) if math.dist(pts[-1],pts[0])>EPS else None
        return path_lines + self._preview_segments

    def preview_faces(self):
        if self._preview_mesh is None:
            return []
        return list(self._preview_mesh.faces)

    def finish(self, viewport):
        if len(self.path) < 2:
            viewport.flash_status('至少需要兩個已確認的節點。', 3000)
            return
        if _WINDOW is None:
            viewport.flash_status('請先開啟輪廓外形面板。', 3000)
            return
        points = list(self.path)
        # Explicit click on the first vertex, or a final hovered snapped
        # endpoint, creates a closed path (rather than an open frame with a gap).
        closed = bool(self._closed_requested or
                      (self.hover is not None and self._near_start(self.hover)))
        if closed and len(points) < 3:
            viewport.flash_status('封閉路徑至少需要三個節點。', 3000)
            return
        # A failed commit should leave the path ready for correction.
        if _WINDOW._produce([(points, closed)], mouse_draw=True,
                            mouse_orientation=self._mouse_orientation_options()):
            self._clear()
            # One operation finishes the drawing tool; selecting a new tool
            # also clears its temporary previews. Use built-in Select where
            # available; otherwise leave the viewport in its neutral state.
            try:
                from tools.select import SelectTool
                viewport.set_active_tool(SelectTool())
            except (ImportError, TypeError, AttributeError):
                viewport.set_active_tool(None)
            viewport.flash_status('輪廓已完成：' + ('封閉線板' if closed else '線板') + '已建立為獨立群組。', 3500)
        viewport.update()


def experimental_hit_wall_normal(ctx):
    """Best-effort wall face normal from IngeTrazo's current snap/pick.

    Snap APIs vary by release: no unsupported calls are assumed mandatory.
    Never confuse an edge or an axis inference with a face normal.
    """
    def normal_of(obj):
        if obj is None: return None
        if isinstance(obj, dict):
            kind=str(obj.get('kind',obj.get('type',''))).lower()
            if kind and not any(word in kind for word in ('face','surface','plane','on_face')):
                return None
            value=obj.get('normal') or obj.get('face_normal')
        else:
            kind=str(getattr(obj,'kind',getattr(obj,'type',''))).lower()
            if kind and not any(word in kind for word in ('face','surface','plane','on_face')):
                return None
            value=getattr(obj,'normal',None)
            if value is None: value=getattr(obj,'face_normal',None)
        if callable(value):
            try: value=value()
            except (TypeError,ValueError,RuntimeError): return None
        if value is None: return None
        try:
            if hasattr(value,'x'): result=as_tuple(value)
            else: result=tuple(float(x) for x in value)
            if len(result)==3 and all(math.isfinite(x) for x in result) and v_norm(result)>.5:
                return v_unit(result)
        except (TypeError,ValueError,AttributeError,RuntimeError):
            pass
        return None
    snap=getattr(ctx,'snap',None)
    for source in (getattr(snap,'face',None),getattr(ctx,'face',None),
                   getattr(ctx,'hit_face',None),getattr(ctx,'picked_face',None)):
        result=normal_of(source)
        if result is not None: return result
    if snap is not None:
        result=normal_of(snap)
        if result is not None: return result
    # Some versions expose a dedicated viewport face picker; avoid calling
    # the general pick_group() which cannot identify a wall face reliably.
    viewport=getattr(ctx,'viewport',None)
    picker=getattr(viewport,'pick_face',None)
    screen=getattr(ctx,'screen',None)
    if callable(picker) and screen is not None:
        try:
            hit=picker(screen.x(),screen.y())
            if isinstance(hit,(tuple,list)):
                for item in hit:
                    result=normal_of(item)
                    if result is not None:return result
            else:
                return normal_of(hit)
        except (TypeError,AttributeError,RuntimeError,ValueError):
            pass
    return None


class ExperimentalProfileDrawTool(ProfileDrawTool):
    """Dedicated alternative mouse painter. Never changes production tool."""
    name='輪廓實驗中'
    description='實驗版：滑鼠繪製與原版相同；三維轉角使用平行移動座標架構。'
    wireframe_color=(0.95,0.65,0.1,1.)

    def __init__(self):
        super().__init__()
        self.wall_normal=None
        self._hover_wall_normal=None
        self.wall_flipped=False
        self._vertical_wall_axis=0

    def _wall_normal_for(self, points):
        normal=self.wall_normal or self._hover_wall_normal
        if normal is None: return None
        if points and len(points)>=2:
            directions=[v_sub(points[i+1],points[i]) for i in range(len(points)-1)]
            # A snapped face can contain projected path points with small
            # floating-point noise; never silently turn an off-wall stroke.
            if any(abs(v_dot(v_unit(d),normal))>1e-4 for d in directions if v_norm(d)>EPS):
                return None
        return v_mul(normal,-1.) if self.wall_flipped else normal

    def _sweep_normal(self,points):
        normal=self._wall_normal_for(points)
        if normal is not None:return normal
        if self.wall_normal is not None:
            # Pass the locked wall normal even when the current route drifts
            # off-plane, so sweep rejects instead of silently switching axes.
            return v_mul(self.wall_normal,-1.) if self.wall_flipped else self.wall_normal
        # No face was provided by host: use geometrically determined plane
        # and let F flip it. Avoid claiming to know which side is indoors.
        if len(points)>=3:
            for i in range(len(points)-2):
                a=v_sub(points[i+1],points[i]);b=v_sub(points[i+2],points[i+1])
                cross=v_cross(a,b)
                if v_norm(cross)>1e-6:
                    n=v_unit(cross)
                    canonical=((0.,0.,1.),(0.,-1.,0.),(1.,0.,0.))
                    best=max(canonical,key=lambda c:abs(v_dot(c,n)))
                    if v_dot(n,best)<0:n=v_mul(n,-1.)
                    return v_mul(n,-1.) if self.wall_flipped else n
        if len(points)>=2:
            d=v_sub(points[1],points[0])
            if v_norm(d)>EPS:
                d=v_unit(d)
                if abs(d[2])>.99:
                    # Vertical first segment has no unique wall plane without
                    # a face hit. Tab chooses between XZ and YZ walls.
                    n=((0.,-1.,0.),(1.,0.,0.))[self._vertical_wall_axis]
                elif abs(d[0])>.99:
                    n=(0.,-1.,0.)
                elif abs(d[1])>.99:
                    n=(1.,0.,0.)
                else:
                    n=(0.,0.,1.)
                return v_mul(n,-1.) if self.wall_flipped else n
        return None

    def on_click(self,ctx):
        if self._is_horizontal() or self._is_wall():
            return super().on_click(ctx)
        hit=experimental_hit_wall_normal(ctx)
        if self.wall_normal is None:
            if hit is not None:
                self.wall_normal=hit
            elif not self.path and self._hover_wall_normal is not None:
                # Edge/vertex snaps often lose the last face hit; retain the
                # wall identified immediately before the first click.
                self.wall_normal=self._hover_wall_normal
        super().on_click(ctx)

    def on_hover(self,ctx):
        if self.wall_normal is None:
            self._hover_wall_normal=experimental_hit_wall_normal(ctx)
        super().on_hover(ctx)

    def on_key(self,viewport,key,modifiers):
        if self._is_horizontal() or self._is_wall():
            return super().on_key(viewport,key,modifiers)
        if key==int(Qt.Key_Tab) and self.wall_normal is None:
            self._vertical_wall_axis=1-self._vertical_wall_axis
            self._refresh_preview()
            viewport.flash_status('實驗中：無牆面吸附，切換垂直線段牆面基準（Tab）',3000)
            viewport.update()
            return True
        if key==int(Qt.Key_F):
            self.wall_flipped=not self.wall_flipped
            self._refresh_preview()
            viewport.flash_status('實驗中：牆面正面已反轉（F 可切回）',2500)
            viewport.update()
            return True
        return super().on_key(viewport,key,modifiers)

    def on_activate(self, viewport):
        self.wall_normal=None
        self._hover_wall_normal=None
        self.wall_flipped=False
        self._vertical_wall_axis=0
        super().on_activate(viewport)
        viewport.flash_status('實驗中：吸附牆面、F 反轉正面、Tab 切換垂直牆軸、Space／Enter 完成', 6000)

    def _refresh_preview(self):
        if self._is_horizontal() or self._is_wall():
            return super()._refresh_preview()
        self._preview_mesh=None
        self._preview_segments=[]
        points=self._preview_points()
        if len(points)<2 or _WINDOW is None:
            return
        try:
            section=_WINDOW.profiles.get(_WINDOW.profile.currentText())
            if not section: return
            closed=len(self.path)>=3 and self.hover is not None and self._near_start(self.hover)
            polygons=experimental_sweep_faces(
                points,section,closed,preview_side=not closed,
                side_hint_path=self.path if len(self.path)>=3 else None,
                stable_mouse_plane=True,wall_normal=self._sweep_normal(points),
                **_WINDOW.opts())
            triangles=mesh_ready_faces(polygons)
            hidden_keys,smooth_keys=edge_display_rules(polygons,triangles,closed)
            mesh=Mesh()
            for triangle in triangles:
                mesh.add_face([qvec(p) for p in triangle])
            apply_edge_display(mesh,hidden_keys,smooth_keys)
            segments=[]
            for cap in (() if closed else (polygons[-2],polygons[-1])):
                for j in range(len(cap)):
                    segments.append((qvec(cap[j]),qvec(cap[(j+1)%len(cap)])))
            self._preview_mesh=mesh
            self._preview_segments=segments
        except (ValueError,ArithmeticError):
            # Never insert failed preview geometry into the scene.
            pass

    def finish(self,viewport):
        if self._is_horizontal() or self._is_wall():
            return super().finish(viewport)
        if len(self.path)<2:
            viewport.flash_status('實驗中：至少需要兩個已確認節點。',3000)
            return
        if _WINDOW is None:
            viewport.flash_status('請先開啟剖面面板。',3000)
            return
        points=list(self.path)
        closed=bool(self._closed_requested or
                    (self.hover is not None and self._near_start(self.hover)))
        if closed and len(points)<3:
            viewport.flash_status('封閉路徑至少需要三個節點。',3000)
            return
        if _WINDOW._produce([(points,closed)],mouse_draw=True,experimental=True,
                            wall_normal=self._sweep_normal(points)):
            self._clear()
            try:
                from tools.select import SelectTool
                viewport.set_active_tool(SelectTool())
            except (ImportError,TypeError,AttributeError):
                viewport.set_active_tool(None)
            viewport.flash_status('實驗中：線板已建立為獨立群組。',3500)
        viewport.update()

    def status_clause(self):
        return '實驗中｜F 反轉｜Tab 切換牆軸｜' + super().status_clause()


def setup(app):
    """Plugin menu plus a native Qt toolbar button; never creates a dock tab."""
    app.add_menu_action('輪廓外形面板', lambda: _show_window(app),
                        shortcut='Ctrl+Shift+K', tip='開啟獨立輪廓外形視窗')
    try:
        bar=QToolBar('快速輪廓工具', app.window)
        bar.setObjectName('ingetrazo_quick_profile_toolbar')
        bar.setIconSize(QSize(32,32))
        bar.setMovable(True)
        action=QAction(make_icon('profile',36),'輪廓外形面板',app.window)
        action.setToolTip('輪廓外形面板（Ctrl+Shift+K）')
        action.triggered.connect(lambda _checked=False: _show_window(app))
        bar.addAction(action)
        app.window.addToolBar(Qt.TopToolBarArea,bar)
        app.window.__dict__['_quick_profile_toolbar']=bar
    except Exception:
        # Menu is always available even if this build has no QMainWindow toolbar.
        pass

