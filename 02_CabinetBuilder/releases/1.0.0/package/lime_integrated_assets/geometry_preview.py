"""Front orthographic raster from actual faces, without subsampling long louver edges."""
import numpy as np
from PySide6.QtCore import Qt,QPointF
from PySide6.QtGui import QImage,QPainter,QPainterPath,QColor,QPen,QMatrix4x4


def polygons(api,root,rigid=None):
    inverse,ok=(rigid if rigid is not None else QMatrix4x4()).inverted()
    if not ok:raise ValueError('預覽座標不可逆。')
    result=[]
    def visit(node,parent,inherited=None):
        matrix=parent*(node.xform if node.xform is not None else QMatrix4x4());paint=api.paint_recipe(node.material) or inherited or {}
        for face in node.mesh.faces:
            rings=[np.asarray([api.xyz(matrix.map(v.position)) for v in loop]) for loop in (face.loop,*face.hole_loops)]
            xyz=rings[0];area=abs(np.sum(xyz[:,0]*np.roll(xyz[:,2],-1)-np.roll(xyz[:,0],-1)*xyz[:,2]))/2
            if area<1e-12:continue
            attrs=api.paint_recipe(face.attrs) or paint
            color=attrs.get('color',[.82,.88,.9]);opacity=attrs.get('opacity',1.)
            result.append({'rings':rings,'depth':float(xyz[:,1].mean()),'color':color,'opacity':opacity,'name':node.name})
        for child in node.children or ():visit(child,matrix,paint)
    visit(root,inverse)
    return sorted(result,key=lambda f:f['depth'],reverse=True)


def render(api,root,width,height,rigid=None,caption=True):
    width=max(1,int(width));height=max(1,int(height));image=QImage(width,height,QImage.Format_ARGB32);image.fill(QColor('#f5f8fb'))
    faces=polygons(api,root,rigid)
    if not faces:return image
    points=np.concatenate([ring[:,[0,2]] for face in faces for ring in face['rings']]);lo=points.min(0);hi=points.max(0)
    scale=min(max(1,width-24)/max(hi[0]-lo[0],1e-6),max(1,height-50)/max(hi[1]-lo[1],1e-6));center=(lo+hi)/2
    painter=QPainter(image);painter.setRenderHint(QPainter.Antialiasing)
    for face in faces:
        path=QPainterPath();path.setFillRule(Qt.OddEvenFill)
        for ring in face['rings']:
            p=(ring[:,[0,2]]-center)*scale;p[:,0]+=width/2;p[:,1]=height/2-p[:,1]
            path.moveTo(QPointF(*map(float,p[0])))
            for v in p[1:]:path.lineTo(QPointF(*map(float,v)))
            path.closeSubpath()
        color=QColor.fromRgbF(*map(float,face['color'][:3]),float(face['opacity']))
        painter.setBrush(color);painter.setPen(QPen(QColor('#608793'),.65));painter.drawPath(path)
    if caption:painter.setPen(QColor('#356278'));painter.drawText(8,height-10,'實體正面預覽｜依目前模型幾何')
    painter.end()
    return image
