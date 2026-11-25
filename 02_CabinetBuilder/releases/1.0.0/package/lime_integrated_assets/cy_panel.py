"""CY country panel from CC.igz, fitted to cabinet finished-front rectangles.

Inputs are millimetres; host geometry is metres. Frame widths and 4 mm
bevels survive overall resizing. The source rear surface follows the
finished rectangle so the cabinet's existing 2 mm perimeter gap stays clear.
"""
from copy import deepcopy
from functools import lru_cache
import json
import math
from pathlib import Path

import numpy as np
from PySide6.QtGui import QVector3D
from core.group import Group
from core.mesh import Mesh
from core.texture import affine_uv, fit_uv_affine

ROOT = Path(__file__).resolve().parent
TEMPLATE = json.loads((ROOT/'cy_stock.json').read_text(encoding='utf-8'))


def resolve_assets(value):
    if isinstance(value, dict):
        if 'asset' in value:
            value.pop('asset')
            value['path'] = str(ROOT/'cy_elm.jpg')
        for child in value.values():resolve_assets(child)
    elif isinstance(value, list):
        for child in value:resolve_assets(child)


resolve_assets(TEMPLATE)


def frame(params=None):
    return tuple(float((params or {}).get('cy_'+key, 60.)) for key in ('left','right','top','bottom'))


def validate(width, height, widths):
    left, right, top, bottom = widths
    if not all(math.isfinite(v) and 9. <= v <= 20000 for v in widths):
        raise ValueError('CY門板四邊框料須介於 0.9～2000 cm，以保留 0.4 cm 導角。')
    if not all(math.isfinite(v) for v in (width,height)) or min(width-left-right, height-top-bottom) < 9.:
        raise ValueError('CY門板放不下框料與導角，請增加面板尺寸或減少框料寬度。')


def remap_paint(attrs, before, after):
    attrs = deepcopy(attrs)
    for paint in (attrs,attrs.get('back')):
        if not isinstance(paint,dict):continue
        texture = paint.get('texture')
        if texture and texture.get('uvw') and len(before)==len(after):
            fitted = fit_uv_affine(after,affine_uv(texture['uvw'],before))
            if fitted is not None:texture['uvw']=fitted
    return attrs


def panel(rect, name='CY門板（鄉村面板）', widths=None):
    x0,z0,x2,z2 = (float(value)/1000 for value in rect)
    width,height = x2-x0,z2-z0
    widths = frame() if widths is None else tuple(map(float,widths))
    validate(width*1000,height*1000,widths)
    left,right,top,bottom = (v/1000 for v in widths)
    children = []
    def stretch(v, old, new):
        bevel=.004
        if v<=bevel+1e-7:return v
        if v>=old-bevel-1e-7:return new-(old-v)
        return bevel+(v-bevel)*(new-2*bevel)/(old-2*bevel)
    titles={'top':'上橫料','bottom':'下橫料','left':'左框料','right':'右框料','panel':'中央板','backing':'背面'}
    for source in TEMPLATE['parts']:
        role=source['role'];vertices=[]
        old_length=max(v[0] for v in source['vertices'])
        old_height=max(v[2] for v in source['vertices'])
        for x,y,z in source['vertices']:
            if role in ('left','right','top','bottom'):
                horizontal=role in ('top','bottom')
                x=stretch(x,old_length,width-left-right if horizontal else height)
                z=stretch(z,.06,{'left':left,'right':right,'top':top,'bottom':bottom}[role])
                if role=='bottom':point=[x0+left+x,y,z0+z]
                elif role=='top':point=[x0+left+x,y,z2-z]
                elif role=='left':point=[x0+left-z,y,z0+x]
                else:point=[x2-z,y,z0+x]
            elif role=='panel':
                point=[x0+left+x*(width-left-right)/.18,y+.01,z0+bottom+z*(height-top-bottom)/.123]
            else:
                point=[x0+x*width/old_length,.02,z0+z*height/old_height]
            vertices.append(QVector3D(*point))
        mesh=Mesh()
        # Reflection of a rail changes handedness. Keep all closed parts outward.
        volume=0.
        for loops in source['faces']:
            pts=[np.asarray(vertices[i].toTuple(),float) for i in loops[0]]
            for i in range(1,len(pts)-1):volume+=float(np.dot(pts[0],np.cross(pts[i],pts[i+1])))/6
        flipped=role!='backing' and volume<0
        for loops,attrs in zip(source['faces'],source['face_attrs']):
            if flipped:loops=[list(reversed(loop)) for loop in loops]
            points=[[vertices[i] for i in loop] for loop in loops]
            face=mesh.add_face(points[0],points[1:] or None)
            if face is None:raise ValueError('CY門板面生成失敗，請檢查面板與框料尺寸。')
            face.attrs={}
        for a,b,soft,hidden in source['edges']:
            edge=mesh.add_edge(vertices[a],vertices[b]);edge.soft=bool(soft);edge.hidden=bool(hidden)
        # Resolve source T junctions without splitting faces or losing paint.
        while True:
            split=False
            for edge in list(mesh.edges):
                mid=mesh.interior_vertex_on(edge) if len(edge.faces)==1 else None
                if mid is not None:
                    result=mesh.split_edge_at(edge,mid)
                    for key in ('e0','e1'):
                        if result[key+'_new']:result[key].hidden=edge.hidden
                    split=True;break
            if not split:break
        child=Group(mesh,'CY '+titles[role])
        child.ext={'lime_cy_source':source['name'],'lime_cy_default_paint':1}
        children.append(child)
    result=Group(name=name);result.adopt(children)
    result.ext={'lime_cy_panel':TEMPLATE['source_sha256']}
    return result


@lru_cache(maxsize=64)
def triangles(width,height,widths=None):
    result=[]
    for child in panel([0,0,width,height],widths=widths).children:
        for face in child.mesh.faces:
            result.extend([[np.asarray(point.toTuple())*1000 for point in tri] for tri in face.triangulate()])
    data=np.asarray(result);data.setflags(write=False)
    return data


def carried_paint(old,new,legacy=False):
    """Remove only the known CC source finish; retain user recipes and UVs."""
    attrs=deepcopy(old.attrs)
    def source_recipe(paint):
        if not isinstance(paint,dict):return False
        color=paint.get('color',[])
        cream=paint.get('mat')=='浴櫃' and len(color)==3 and np.allclose(color,[1.,.9921568627450981,.9764705882352941],atol=1e-8)
        texture=paint.get('texture') or {}
        wood=paint.get('mat')=='*89' or Path(str(texture.get('path',''))).name in ('cy_elm.jpg','elm.jpg')
        return cream or wood
    if legacy:
        for paint in (attrs,attrs.get('back')):
            if source_recipe(paint):
                for key in ('color','texture','opacity','mat'):paint.pop(key,None)
    # Correcting source winding exchanges the two physical paint sides.
    reversed_winding=QVector3D.dotProduct(old.normal(),new.normal())<0
    if reversed_winding:
        front={key:attrs.pop(key) for key in ('color','texture','opacity','mat') if key in attrs}
        back=attrs.pop('back',{})
        attrs.update(back);attrs['back']=front
        a=attrs.pop('_lime_auto_front',None);b=attrs.pop('_lime_auto_back',None)
        if b is not None:attrs['_lime_auto_front']=b
        if a is not None:attrs['_lime_auto_back']=a
    return remap_paint(attrs,list(reversed(old.vertices)) if reversed_winding else old.vertices,new.vertices)
