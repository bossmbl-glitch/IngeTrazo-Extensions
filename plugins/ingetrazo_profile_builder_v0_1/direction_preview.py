# SPDX-License-Identifier: GPL-3.0-or-later
"""Wall-panel placement and a software-rendered example using production meshes."""
import copy
import math
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

ANCHORS = {'左下':(0.,0.),'下中':(.5,0.),'右下':(1.,0.),
           '左中':(0.,.5),'中心':(.5,.5),'右中':(1.,.5),
           '左上':(0.,1.),'上中':(.5,1.),'右上':(1.,1.)}


def wall_panel_sweep_faces(path, section, closed=False, **options):
    """Interpret anchors on the clockwise upright section, retaining legacy sweeps.

    Stored width/height still refer to the original library section in mm.
    Upright display x = original v (depth), y = width - original u (height).
    A horizontal stroke to the right with the front normal -Y uses -Z as its
    route-side axis, so legacy u = -display_y, legacy v = display_x.
    """
    from . import clean_polygon, sweep_faces
    settings=dict(options)
    points=clean_polygon(section)
    ax,ay=ANCHORS[settings.get('anchor','左下')]
    settings['anchor']=next(name for name,uv in ANCHORS.items() if uv==(1.-ay,ax))
    # Mirror is horizontal in the upright display, hence reverse original v.
    if settings.get('mirror',False):
        low=min(y for x,y in points);high=max(y for x,y in points)
        points=[(x,low+high-y) for x,y in points]
    settings['mirror']=False
    settings['off_u_mm']=-options.get('off_v_mm',0.)
    settings['off_v_mm']=options.get('off_u_mm',0.)
    return sweep_faces(path,points,closed,**settings)


def wall_panel_preview_points(section, options):
    """Project the exact production first ring into the displayed section plane."""
    from . import clean_polygon
    settings=dict(options)
    settings.update(wall_frame=True,plane_normal_override=(0.,-1.,0.))
    settings.setdefault('fixed_side',1.)
    settings.setdefault('wall_depth_sign',1.)
    raw=wall_panel_sweep_faces([(0.,0.,0.),(1.,0.,0.)],section,False,**settings)
    return [(-raw[i][0][1]*1000.,raw[i][0][2]*1000.) for i in range(len(clean_polygon(section)))]


def example_recipe(section, settings, mode, side=1., depth=1.):
    """A labelled canonical example, not the user's live path or scene geometry."""
    largest=max(settings['width_mm'],settings['height_mm'])/1000.
    length=max(.12,largest*5.)
    if mode==1:
        route=[(0.,0.,0.),(length,0.,0.),(length,0.,-length*.8)]
        orientation=dict(wall_frame=True,wall_section_layout=True,
                         plane_normal_override=(0.,-1.,0.),fixed_side=side,wall_depth_sign=depth)
    else:
        route=[(0.,0.,0.),(length,0.,0.),(length,length*.55,0.)]
        orientation=dict(horizontal=True,fixed_side=side)
    return dict(profile='方向示意',section=copy.deepcopy(section),settings=dict(settings),
                path=route,closed=False,mouse_draw=True,mouse_orientation=orientation)


class DirectionPreview(QWidget):
    """QPainter-only 3D illustration; no OpenGL context or scene/history writes."""
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setMinimumSize(240,190)
        self.recipe=None;self.mesh=None;self.mode=0;self.error='';self._key=None
        self.setToolTip('滑鼠繪製的示意路徑使用目前剖面與參數生成；紅線為路徑、紅點為起點。實際路徑請看模型中的即時預覽。')

    def set_example(self,section,settings,mode,side=1.,depth=1.):
        from .profile_operations import build_mesh
        key=repr((section,settings,mode,side,depth))
        if key==self._key:return
        self._key=key;self.mode=mode;self.mesh=None;self.recipe=None;self.error=''
        if mode==2:
            self.update();return
        try:
            self.recipe=example_recipe(section,settings,mode,side,depth)
            self.mesh=build_mesh(self.recipe)
        except (ValueError,ArithmeticError,TypeError,KeyError) as exc:
            self.error=str(exc)
        self.update()

    def paintEvent(self,event):
        from . import as_tuple,v_dot,v_unit
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(),QColor('#fafafa'))
        painter.setPen(QColor('#35424e'))
        font=painter.font();font.setBold(True);painter.setFont(font)
        painter.drawText(QRectF(10,4,self.width()-20,22),Qt.AlignLeft|Qt.AlignVCenter,'生成示意')
        font.setBold(False);painter.setFont(font)
        if self.mesh is None:
            text=self.error or ('空間路徑請查看模型即時預覽' if self.mode==2 else '請先選擇剖面')
            painter.drawText(QRectF(12,30,self.width()-24,self.height()-42),Qt.AlignCenter|Qt.TextWordWrap,text)
            painter.end();return
        vertices=[as_tuple(v.position) for v in self.mesh.vertices]
        route=self.recipe['path'];length=route[1][0]
        # Put a wall behind the section and floor under skirting, preserving
        # the nominal baseline instead of moving the wall to fit offsets.
        margin=length*.15
        bottom=-length if self.mode==1 else -margin
        wall=[(-margin,.0005,bottom),(length+margin,.0005,bottom),
              (length+margin,.0005,margin if self.mode==1 else length*.65),
              (-margin,.0005,margin if self.mode==1 else length*.65)]
        floor=[(-margin,-length*.45,0),(length+margin,-length*.45,0),
               (length+margin,length*.7,0),(-margin,length*.7,0)]
        right=(.88,.47,0.);up=(-.18,.34,.92);eye=(.43,-.81,.39)
        project=lambda v:(v_dot(v,right),-v_dot(v,up))
        bounds=[project(v) for v in vertices+wall+route+(floor if self.mode==0 else [])]
        xs=[p[0] for p in bounds];ys=[p[1] for p in bounds]
        scale=min((self.width()-24)/max(max(xs)-min(xs),1e-6),
                  (self.height()-64)/max(max(ys)-min(ys),1e-6))
        cx=self.width()/2-(min(xs)+max(xs))*scale/2
        cy=30+(self.height()-64)/2-(min(ys)+max(ys))*scale/2
        def screen(v):
            x,y=project(v);return QPointF(cx+x*scale,cy+y*scale)
        def draw_poly(poly,color,outline=None):
            painter.setBrush(QColor(color));painter.setPen(QPen(QColor(outline),.7) if outline else QPen(Qt.NoPen))
            painter.drawPolygon(QPolygonF([screen(v) for v in poly]))
        if self.mode==0:draw_poly(floor,'#e6dfd2','#c1b8a7')
        draw_poly(wall,'#d7d8d5','#adb0ad')
        faces=sorted(self.mesh.faces,key=lambda f:sum(v_dot(as_tuple(v.position),eye) for v in f.loop)/len(f.loop))
        light=v_unit((-.3,-.7,1.))
        for face in faces:
            points=[as_tuple(v.position) for v in face.loop]
            normal=as_tuple(face.normal())
            shade=int(155+65*abs(v_dot(normal,light)))
            draw_poly(points,QColor(shade,shade,shade))
        painter.setPen(QPen(QColor('#5f676b'),.75));painter.setBrush(Qt.NoBrush)
        for edge in self.mesh.edges:
            if edge.hidden:continue
            if edge.soft and len(edge.faces)==2:
                facing=[v_dot(as_tuple(f.normal()),eye) for f in edge.faces]
                if facing[0]*facing[1]>=0:continue
            painter.drawLine(screen(as_tuple(edge.v0.position)),screen(as_tuple(edge.v1.position)))
        # Path stays visible as an overlay even when the profile covers it.
        painter.setPen(QPen(QColor('#da3434'),2,Qt.DashLine))
        for a,b in zip(route,route[1:]):painter.drawLine(screen(a),screen(b))
        a,b=screen(route[0]),screen(route[1]);dx=b.x()-a.x();dy=b.y()-a.y();norm=max(math.hypot(dx,dy),1.)
        tip=QPointF(a.x()+dx*.67,a.y()+dy*.67)
        painter.setPen(QPen(QColor('#da3434'),2,Qt.SolidLine))
        for sign in (-1,1):
            painter.drawLine(tip,QPointF(tip.x()-dx/norm*8+sign*dy/norm*4,tip.y()-dy/norm*8-sign*dx/norm*4))
        painter.setBrush(QColor('#e52d2d'));painter.setPen(QPen(QColor('white'),1))
        painter.drawEllipse(a,4,4)
        painter.setPen(QColor('#4b545b'))
        caption='壁板示意：左 → 右 → 下' if self.mode==1 else '踢腳示意：沿牆腳行進'
        painter.drawText(QRectF(6,self.height()-28,self.width()-12,24),Qt.AlignCenter,caption)
        painter.end()
