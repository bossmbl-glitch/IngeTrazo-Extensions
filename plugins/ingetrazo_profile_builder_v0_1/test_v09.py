"""Offline v0.9 regressions: geometry and input flow."""
import ast
import math
from pathlib import Path
src=Path(__file__).with_name('__init__.py').read_text(encoding='utf-8')
tree=ast.parse(src)
functions={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
names={'v_add','v_sub','v_mul','v_dot','v_cross','v_norm','v_unit','clean_polygon','sweep_faces'}
ns={'math':math,'EPS':1e-8}
exec(compile(ast.Module(body=[functions[n] for n in names],type_ignores=[]),'<test>','exec'),ns)
profile=[[0,0],[17,0],[17,17],[0,17]]
paths=[[(0,0,0),(1,0,0),(1,0,1)],[(0,0,0),(1,0,0),(1,1,0)],[(0,0,0),(0,0,1)],[(0,0,0),(0,0,1),(1,0,1)],[(0,0,0),(1,0,0),(1,1,0),(1,1,1)]]
for path in paths:
    faces=ns['sweep_faces'](path,profile)
    assert faces and all(math.isfinite(c) for f in faces for p in f for c in p)
    assert all(len({tuple(round(x,9) for x in pt) for pt in f})>=3 for f in faces),path
classes={n.name:n for n in tree.body if isinstance(n,ast.ClassDef)}
methods={n.name:n for n in classes['ProfileDrawTool'].body if isinstance(n,ast.FunctionDef)}
key=ast.get_source_segment(src,methods['on_key'])
assert all(k in key for k in ('Qt.Key_Space','Qt.Key_Return','Qt.Key_Enter'))
finish=ast.get_source_segment(src,methods['finish'])
assert '_WINDOW._produce' in finish and 'viewport.set_active_tool' in finish
preview=ast.get_source_segment(src,methods['_refresh_preview'])
assert 'scene.groups.append' not in preview and 'history.execute' not in preview
assert 'def finish_drawing(self):' in src and ("('finish'," in src or 'act_finish=menu.addAction' in src)
assert 'Group(mesh, name)' in src
print('PASS v0.9: 3D/vertical paths, key flow, finish button, independent groups, temporary preview')
