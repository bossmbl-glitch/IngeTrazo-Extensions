"""v1.1 sweep-direction regressions, pure Python, no IngeTrazo required."""
import ast, math
from pathlib import Path
src=Path(__file__).with_name('__init__.py').read_text(encoding='utf8')
mod=ast.parse(src)
functions={node.name:node for node in mod.body if isinstance(node,ast.FunctionDef)}
names=['v_add','v_sub','v_mul','v_dot','v_cross','v_norm','v_unit','clean_polygon','sweep_faces']
ns={'math':math,'EPS':1e-8}
exec(compile(ast.Module(body=[functions[n] for n in names],type_ignores=[]),'v11_test','exec'),ns)
sweep=ns['sweep_faces']
profile=[[0,0],[17,0],[17,5],[12,5],[12,10],[7,10],[7,17],[0,17]]
for path in [[(0,0,0),(1,0,0),(1,1,0),(0,1,0)],[(0,0,0),(1,0,0),(1,0,1),(0,0,1)],[(0,0,0),(0,1,0),(0,1,1),(0,0,1)]]:
    n=len(path)
    for side in ['內側','外側']:
        for reverse_depth in [True,False]:
            a=sweep(path,profile,closed=True,closed_side=side,reverse_depth=reverse_depth)
            b=sweep(list(reversed(path)),profile,closed=True,closed_side=side,reverse_depth=reverse_depth)
            points=lambda polys: {tuple(round(v,6) for v in vertex) for face in polys for vertex in face}
            assert points(a)==points(b),(path,side,reverse_depth)
            assert len(a)==n*len(profile)
    inside=points(sweep(path,profile,closed=True,closed_side='內側'))
    outside=points(sweep(path,profile,closed=True,closed_side='外側'))
    assert inside!=outside
# On XZ, switching depth must flip the Y offsets without changing in-plane coordinates.
path=[(0,0,0),(1,0,0),(1,0,1)]
a=sweep(path,profile,reverse_depth=True)
b=sweep(path,profile,reverse_depth=False)
for face_a,face_b in zip(a,b):
    for pa,pb in zip(face_a,face_b):
        assert math.isclose(pa[0],pb[0],abs_tol=1e-7)
        assert math.isclose(pa[2],pb[2],abs_tol=1e-7)
        assert math.isclose(pa[1],-pb[1],abs_tol=1e-7)
assert 'self.closed_side.currentText()' in src and 'self.reverse_depth.isChecked()' in src
print('PASS v1.1: winding-independent closed inside/outside on XY/XZ/YZ; depth flip and UI options')
