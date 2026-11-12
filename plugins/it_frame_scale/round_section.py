"""Explicitly repair elliptical straight cylinders; never infer this during resize."""
import numpy as np
from PySide6.QtGui import QVector3D
from .deform import remap_attrs


def cylinder_map(target):
    """Accept only a closed, constant elliptical extrusion with a polygonal cap.

    Preserve the smaller diameter, length and centreline. A deliberate oval is
    only changed when the user invokes the separate repair action.
    """
    if not target.faces or set(target.faces)!=set(target.mesh.faces):return None
    if any(len(e.faces)!=2 for e in target.mesh.edges):return None
    all_points=np.array([v.position.toTuple() for v in target.vertices],dtype=float)
    for cap in target.faces:
        if len(cap.vertices)<12 or cap.holes:continue
        ring=np.array([p.toTuple() for p in cap.vertices],dtype=float)
        center=ring.mean(axis=0);values,axes=np.linalg.eigh((ring-center).T@(ring-center)/len(ring))
        if values[1]<1e-12 or values[0]>values[1]*1e-5:continue
        minor,major=np.sqrt(2*values[1:])
        if major/minor>5:continue
        radial=axes[:,1:]
        normalized=(all_points-center)@radial/np.array([minor,major])
        # Every vertex must belong to the same extruded ellipse. Reject caps
        # with arbitrary outlines, stepped shafts, cones, bends and spheres.
        if np.max(np.abs(np.sum(normalized**2,axis=1)-1))>.002:continue
        axis=axes[:,0];axial=(all_points-center)@axis
        if np.ptp(axial)<1e-5:continue
        if abs(major-minor)<max(1e-7,minor*1e-4):return None
        direction=axes[:,2];factor=minor/major
        def mapped(point):
            p=np.array(point.toTuple());p=p+direction*np.dot(p-center,direction)*(factor-1)
            return QVector3D(*p)
        mapped.before_mm=(float(major*2000),float(minor*2000))
        mapped.diameter_mm=float(minor*2000)
        return mapped
    return None


def repair(targets):
    plans=[]
    for target in targets:
        mapper=cylinder_map(target)
        if mapper is None:continue
        attrs=[(f,remap_attrs(f,f.vertices,[mapper(p) for p in f.vertices])) for f in target.faces]
        vertices=[(v,mapper(v.position)) for v in target.vertices]
        plans.append((target,mapper,attrs,vertices))
    if not plans:raise ValueError('未找到可修復的橢圓柱；請選取完整把手或圓柱群組。正圓、彎管與其他造型不會強制修改。')
    for target,mapper,attrs,vertices in plans:
        for vertex,point in vertices:target.mesh.place_vertex(vertex,point)
        for face,attr in attrs:face.attrs=attr
        if any(f.area()<1e-12 for f in target.faces):raise ValueError('修復產生過小面，已取消。')
    return [{'part':target.group.name if target.group else '選取幾何',
             'before_mm':mapper.before_mm,'diameter_mm':mapper.diameter_mm}
            for target,mapper,_,_ in plans]
