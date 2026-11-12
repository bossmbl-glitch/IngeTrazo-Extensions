"""Offline geometry regression tests; no PySide6/IngeTrazo installation required."""
import ast
import math
from pathlib import Path
p=Path(__file__).with_name('__init__.py')
tree=ast.parse(p.read_text(encoding='utf-8'))
names={'v_add','v_sub','v_mul','v_dot','v_cross','v_norm','v_unit','clean_polygon','sweep_faces'}
selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
ns={'math':math,'EPS':1e-8}
exec(compile(ast.Module(body=selected,type_ignores=[]),str(p),'exec'),ns)
rectangle=[[0,0],[17,0],[17,17],[0,17]]
for points,closed,expected in [([(0,0,0),(1,0,0)],False,6),
                               ([(0,0,0),(1,0,0),(1,1,0)],False,10),
                               ([(0,0,0),(1,0,0),(1,1,0),(0,1,0)],True,16)]:
    for anchor in ('左下','中心','右上'):
        for mirror in (False,True):
            faces=ns['sweep_faces'](points,rectangle,closed,anchor=anchor,mirror=mirror)
            assert len(faces)==expected,(anchor,len(faces),expected)
            assert all(len(face)>=3 for face in faces)
            assert all(math.isfinite(v) for face in faces for point in face for v in point)
# Centre anchor: start cap should straddle baseline at +/- 8.5 mm.
faces=ns['sweep_faces']([(0,0,0),(1,0,0)],rectangle,anchor='中心')
start=faces[-2]
assert abs(sum(p[1] for p in start)/4)<1e-8
assert abs(sum(p[2] for p in start)/4)<1e-8
print('PASS: 18 sweep combinations, anchored centre, clean caps')
