"""v1.0 offline regression checks (no IngeTrazo/Qt needed)."""
import ast
import collections
import math
from pathlib import Path

text=Path(__file__).with_name('__init__.py').read_text(encoding='utf-8')
tree=ast.parse(text)
functions={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
needed=['v_add','v_sub','v_mul','v_dot','v_cross','v_norm','v_unit','clean_polygon','sweep_faces']
ns={'math':math,'EPS':1e-8}
exec(compile(ast.Module(body=[functions[n] for n in needed],type_ignores=[]),'<test_v10>','exec'),ns)
for profile in ([[0,0],[17,0],[17,17],[0,17]],
                [[0,0],[17,0],[17,5],[12,5],[12,10],[7,10],[7,17],[0,17]]):
    for path in ([ (0,0,0),(1,0,0),(1,1,0),(0,1,0) ],
                 [ (0,0,0),(1,0,0),(1,0,1),(0,0,1) ]):
        faces=ns['sweep_faces'](path,profile,closed=True)
        assert len(faces)==len(path)*len(profile)
        counts=collections.Counter()
        for face in faces:
            assert len(face)==4
            for point in face:
                assert all(math.isfinite(x) for x in point)
            for i,a in enumerate(face):
                b=face[(i+1)%len(face)]
                a=tuple(round(x,7) for x in a)
                b=tuple(round(x,7) for x in b)
                counts[tuple(sorted((a,b)))]+=1
        assert all(v==2 for v in counts.values()),'unclosed edge in closed profile'
classes={n.name:n for n in tree.body if isinstance(n,ast.ClassDef)}
methods={n.name:n for n in classes['ProfileDrawTool'].body if isinstance(n,ast.FunctionDef)}
finish=ast.get_source_segment(text,methods['finish'])
calls=[n for n in ast.walk(methods['finish']) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='_produce']
assert len(calls)==1
kwargs={k.arg:k.value for k in calls[0].keywords}
assert isinstance(kwargs['mouse_draw'],ast.Constant) and kwargs['mouse_draw'].value is True
assert isinstance(kwargs['mouse_orientation'],ast.Call)
assert 'self._closed_requested' in finish
assert '_near_start' in ast.get_source_segment(text,methods['on_hover'])
assert 'self.finish(ctx.viewport)' in ast.get_source_segment(text,methods['on_click'])
keys=ast.get_source_segment(text,methods['on_key'])
assert all(k in keys for k in ('Qt.Key_Space','Qt.Key_Return','Qt.Key_Enter'))
panel_methods={n.name:n for n in classes['ProfilePanel'].body if isinstance(n,ast.FunctionDef)}
init=ast.get_source_segment(text,panel_methods['__init__'])
assert 'super().__init__(host, Qt.Tool' in init
assert 'self.setAttribute(Qt.WA_QuitOnClose, False)' in init
assert 'super().__init__(None, Qt.Window)' not in init
print('PASS v1.0: closed rectangular/stair profiles on XY/XZ, endpoint finish, Qt parent/Tool flags')
