"""Resize straight extrusions; translate curved/angled profile intervals rigidly."""
from bisect import bisect_right
import math
from PySide6.QtGui import QVector3D


def merge(intervals,epsilon=1e-6):
    result=[]
    for a,b in sorted(intervals):
        if b-a<=epsilon:continue
        if result and a<=result[-1][1]+epsilon:result[-1]=(result[-1][0],max(b,result[-1][1]))
        else:result.append((a,b))
    return result


def profile_intervals(targets,to_local):
    """An axial stretch preserves a face only if that face runs along the axis.

    Curved tubes comprise many inclined facets. Protect the union of their
    axial extents, including internal feet, rather than just the outer rim.
    Transverse caps have zero axial extent and can translate unchanged.
    """
    blocked=[[],[],[]]
    for target in targets:
        for face in target.faces:
            points=[to_local(p) for p in face.vertices]
            if len(points)<3:continue
            origin=points[0];normal=QVector3D()
            for a,b in zip(points[1:-1],points[2:]):
                normal+=QVector3D.crossProduct(a-origin,b-origin)
            # QVector3D.normalized treats tiny metre-scale facet normals as zero.
            raw=normal.toTuple();length=math.sqrt(sum(v*v for v in raw))
            if length<1e-12:continue
            normal=tuple(v/length for v in raw)
            coords=[p.toTuple() for p in points]
            for axis in range(3):
                lo=min(p[axis] for p in coords);hi=max(p[axis] for p in coords)
                if hi-lo>1e-6 and abs(normal[axis])>1e-5:blocked[axis].append((lo,hi))
    return tuple(merge(ranges) for ranges in blocked)


def resize_mapper(points,params,frame,bands,protected):
    inverse,ok=frame.inverted()
    if not ok:raise ValueError('模型座標無法反轉。')
    local=[inverse.map(p).toTuple() for p in points]
    lo=[min(p[i] for p in local) for i in range(3)];hi=[max(p[i] for p in local) for i in range(3)]
    knots={};planes=[]
    for axis in range(3):
        if not params.fixed_axes[axis]:continue
        span=hi[axis]-lo[axis]
        length=params.target_cm[axis]/100 if params.target_cm is not None else span*params.factors[axis]
        delta=length-span
        if abs(delta)<1e-7:continue
        left,right=[v/100 for v in bands[axis]];middle=params.center_band_cm[axis]/100
        intervals=list(protected[axis])+[(lo[axis],lo[axis]+left),(hi[axis]-right,hi[axis])]
        if middle>0:intervals.append(((lo[axis]+hi[axis]-middle)/2,(lo[axis]+hi[axis]+middle)/2))
        intervals=merge([(max(lo[axis],a),min(hi[axis],b)) for a,b in intervals])
        # One central stretch zone; EVERYTHING before and after it translates
        # rigidly, including straight overhangs between end caps and mounting feet.
        # Preserving the feet alone still changed the proportions of both ends.
        midpoint=(lo[axis]+hi[axis])/2
        cursor=lo[axis];gaps=[]
        for a,b in intervals:
            if a-cursor>1e-6:gaps.append((cursor,a))
            cursor=max(cursor,b)
        if hi[axis]-cursor>1e-6:gaps.append((cursor,hi[axis]))
        central=next(((a,b) for a,b in gaps if a+1e-6<midpoint<b-1e-6),None)
        if central is None and middle<=0:
            raise ValueError(f'{"XYZ"[axis]} 中心沒有可伸縮的直段；兩端與中心造型保持不變，未套用。')
        if central is not None:
            a,b=central
            # On an unadorned extrusion there are no feature boundaries. Keep
            # both outer quarters rigid and use only the middle half.
            if a<=lo[axis]+1e-6:a=lo[axis]+span*.25
            if b>=hi[axis]-1e-6:b=hi[axis]-span*.25
            intervals=merge([*intervals,(lo[axis],a),(b,hi[axis])])
        rigid=sum(b-a for a,b in intervals);free=span-rigid
        if free<max(1e-5,span*1e-5):
            raise ValueError(f'{"XYZ"[axis]} 方向沒有可延長的直段；已保留圓形、彎腳與截面，未拉扁模型。')
        if length-rigid<=max(1e-5,span*1e-5):
            raise ValueError(f'尺寸太小：會壓到彎腳／造型保留區，已取消。此方向須大於 {rigid*100:.2f} cm。')
        start=lo[axis]-delta/2 if params.center else lo[axis]
        if params.anchor is not None:
            anchor=inverse.map(params.anchor).toTuple()[axis]
            start=anchor+(lo[axis]-anchor)*length/span
        boundaries=sorted(set([lo[axis],hi[axis],*[v for pair in intervals for v in pair]]))
        ratio=(length-rigid)/free;new=start;pairs=[(boundaries[0],new)]
        for a,b in zip(boundaries,boundaries[1:]):
            midpoint=(a+b)/2
            fixed=any(x<=midpoint<=y for x,y in intervals)
            new+=(b-a)*(1. if fixed else ratio);pairs.append((b,new))
        knots[axis]=pairs;planes.extend((axis,a) for a,_ in pairs[1:-1])
    keys={axis:[a for a,b in pairs] for axis,pairs in knots.items()}
    def mapped(point):
        p=list(inverse.map(point).toTuple())
        for axis,pairs in knots.items():
            index=max(0,min(len(pairs)-2,bisect_right(keys[axis],p[axis])-1))
            (a,b),(c,d)=pairs[index:index+2]
            p[axis]=b+(p[axis]-a)*(d-b)/(c-a)
        return frame.map(QVector3D(*p))
    mapped.cut_planes=planes;mapped.to_local=inverse.map;mapped.to_world=frame.map
    mapped.changed=bool(knots)
    return mapped
