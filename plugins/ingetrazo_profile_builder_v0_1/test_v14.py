"""Offline edge-display tests; no PySide6 or IngeTrazo installation required."""
import ast
import math
from collections import defaultdict
from pathlib import Path
source=Path(__file__).with_name('__init__.py').read_text(encoding='utf-8')
node=ast.parse(source)
func_names=['v_add','v_sub','v_mul','v_dot','v_cross','v_norm','v_unit',
            'clean_polygon','sweep_faces','mesh_ready_faces',
            '_mesh_vertex_key','_mesh_edge_key','edge_display_rules','apply_edge_display']
funcs={x.name:x for x in node.body if isinstance(x,ast.FunctionDef)}
ns={'math':math,'EPS':1e-8}
exec(compile(ast.Module(body=[funcs[n] for n in func_names],type_ignores=[]),'<isolated>','exec'),ns)
ns['as_tuple']=lambda x:tuple(x)
# Avoid dependence on PySide6's QVector3D when exercising position quantization.
sweep=ns['sweep_faces']; triangles=ns['mesh_ready_faces']; rules=ns['edge_display_rules']
class F:
    def __init__(self, pts):
        e1=ns['v_sub'](pts[1],pts[0]);e2=ns['v_sub'](pts[2],pts[0])
        self.n=ns['v_cross'](e1,e2)
    def normal(self):return self.n
class V:
    def __init__(self,p):self.position=p
class E:
    def __init__(self,a,b):
        self.v0=V(a);self.v1=V(b);self.faces=[];self.hidden=False;self.soft=False
class M:
    def __init__(self, polygons):
        lookup={}
        for triangle in polygons:
            face=F(triangle)
            for i in range(3):
                a,b=triangle[i],triangle[(i+1)%3]
                key=ns['_mesh_edge_key'](a,b)
                if key not in lookup:lookup[key]=E(a,b)
                lookup[key].faces.append(face)
        self.edges=list(lookup.values())

section058=[[0,0],[17,0],[17,3],[13,3]]+[[13-10*math.sin(i*math.pi/24),13-10*math.cos(i*math.pi/24)] for i in range(1,13)]+[[3,17],[0,17]]
section012=[[0,0],[17,0],[17,5],[12,5],[12,10],[7,10],[7,17],[0,17]]
shapes=[
    ([(0,0,0),(.35,0,0),(.35,0,.3)],False),
    ([(0,0,0),(.35,0,0),(.35,0,.3),(0,0,.3)],True),
    ([(0,0,0),(.35,.0,0),(.35,.3,0),(0,.3,0)],True),
]
count=0
for path,closed in shapes:
    for section in (section058,section012):
        for mirror in (True,False):
            raw=sweep(path,section,closed=closed,anchor='左上',mirror=mirror,rotate_deg=180)
            tris=triangles(raw)
            hidden,along=rules(raw,tris,closed)
            m=M(tris)
            stats=ns['apply_edge_display'](m,hidden,along)
            assert stats['hidden']>0, stats
            assert all(e.hidden for e in m.edges if len(e.faces)==2 and ns['_mesh_edge_key'](e.v0.position,e.v1.position) in hidden)
            assert all(not e.hidden for e in m.edges if len(e.faces)!=2)
            assert all(not e.hidden for e in m.edges if ns['_mesh_edge_key'](e.v0.position,e.v1.position) not in hidden)
            # A miter seam sits on the cross-section ring; it must be retained.
            ring_edges={ns['_mesh_edge_key'](quad[0],quad[1]) for quad in raw[:len(raw)-(0 if closed else 2)]}
            assert all(not e.hidden and not e.soft for e in m.edges if ns['_mesh_edge_key'](e.v0.position,e.v1.position) in ring_edges)
            if section is section058:
                assert stats['soft']>0,stats
            count+=1
print('PASS v1.4',count,'meshes: triangulation hidden; curve soft; miter and boundary edges retained')
