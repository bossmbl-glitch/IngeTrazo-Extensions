"""Offline mesh tessellation checks: no IngeTrazo install required."""
import ast, math
from collections import Counter
from pathlib import Path
src=Path(__file__).with_name('__init__.py').read_text(encoding='utf-8')
mod=ast.parse(src)
funcs={x.name:x for x in mod.body if isinstance(x,ast.FunctionDef)}
needed=['v_add','v_sub','v_mul','v_dot','v_cross','v_norm','v_unit','clean_polygon','sweep_faces','mesh_ready_faces']
ns={'math':math, 'EPS':1e-8}
exec(compile(ast.Module(body=[funcs[n] for n in needed],type_ignores=[]),'test','exec'),ns)
sweep=ns['sweep_faces']; to_mesh=ns['mesh_ready_faces']
section058=[[0,0],[17,0],[17,3],[13,3]]+[[13-10*math.sin(i*math.pi/24),13-10*math.cos(i*math.pi/24)] for i in range(1,13)]+[[3,17],[0,17]]
section012=[[0,0],[17,0],[17,5],[12,5],[12,10],[7,10],[7,17],[0,17]]

def verify_manifold(faces):
    edges=Counter()
    for triangle in faces:
        assert len(triangle)==3
        a,b,c=triangle
        cross=ns['v_cross'](ns['v_sub'](b,a),ns['v_sub'](c,a))
        assert ns['v_norm'](cross)>1e-13, ('degenerate',triangle)
        vertices=[tuple(round(v,8) for v in p) for p in triangle]
        for j in range(3):
            edge=tuple(sorted((vertices[j],vertices[(j+1)%3])))
            edges[edge]+=1
    invalid={edge:count for edge,count in edges.items() if count!=2}
    assert not invalid, ('open/nonmanifold edges', len(invalid), list(invalid.items())[:4])

shapes=[
    [(0,0,0),(.35,0,0),(.35,0,.3)],
    [(0,0,0),(.35,0,0),(.35,0,.3),(0,0,.3)],
    [(0,0,0),(.35,0,0),(.35,.3,0),(0,.3,0)],
    [(0,0,0),(0,.35,0),(0,.35,.3),(0,0,.3)],
]
count=0
for shape in shapes:
    for sect in [section058,section012]:
        for reverse in [False,True]:
            for mirror in [False,True]:
                for side in ['內側','外側']:
                    points=list(reversed(shape)) if reverse else shape
                    faces=to_mesh(sweep(points,sect,closed=len(shape)==4,anchor='左上',rotate_deg=180,mirror=mirror,closed_side=side))
                    verify_manifold(faces)
                    count+=1
print('PASS v1.3',count,'meshes (open L / XY XZ YZ closed; 058/012; opposite winding; mirror; inside/outside), manifold triangle edges')
