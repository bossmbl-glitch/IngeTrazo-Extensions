import ast, math
from pathlib import Path
src=Path(__file__).with_name('__init__.py').read_text(encoding='utf8')
tree=ast.parse(src)
fns={x.name:x for x in tree.body if isinstance(x,ast.FunctionDef)}
names=['v_add','v_sub','v_mul','v_dot','v_cross','v_norm','v_unit','clean_polygon','sweep_faces','experimental_sweep_faces','mesh_ready_faces']
ns={'math':math,'EPS':1e-8}
exec(compile(ast.Module(body=[fns[n] for n in names],type_ignores=[]),'<wall>','exec'),ns)
sweep=ns['experimental_sweep_faces']; baseline=ns['sweep_faces']
profile=[(0,0),(17,0),(17,7),(10,7),(10,17),(0,17)]
for path,normal in [([(0,0,0),(.5,0,0),(.5,0,.5)],(0,-1,0)),
                    ([(0,0,0),(0,.5,0),(0,.5,.5)],(1,0,0))]:
    forward=sweep(path,profile,stable_mouse_plane=True,wall_normal=normal)
    reverse=sweep(path,profile,stable_mouse_plane=True,wall_normal=tuple(-x for x in normal))
    assert forward != reverse,'reversing wall front must affect resulting sweep'
    assert ns['mesh_ready_faces'](forward),'mesh should triangulate'
    assert ns['mesh_ready_faces'](reverse),'reversed mesh should triangulate'
    # Preview and final call use same geometry, including selected face normal.
    assert forward == sweep(path,profile,stable_mouse_plane=True,wall_normal=normal,preview_side=False)
    try:
        sweep(path,profile,wall_normal=(0,0,1))
    except ValueError as e:
        assert '牆面' in str(e)
    else:raise AssertionError('off-wall normal should be rejected')
# Production untouched unless explicit override is passed.
path=[(0,0,0),(.5,0,0),(.5,0,.5)]
assert baseline(path,profile,stable_mouse_plane=True)==sweep(path,profile,stable_mouse_plane=True)
classes={c.name:c for c in tree.body if isinstance(c,ast.ClassDef)}
exp=classes['ExperimentalProfileDrawTool']
methods={f.name:ast.get_source_segment(src,f) for f in exp.body if isinstance(f,ast.FunctionDef)}
assert 'Qt.Key_F' in methods['on_key']
assert 'wall_normal=self._sweep_normal(points)' in methods['_refresh_preview']
assert 'wall_normal=self._sweep_normal(points)' in methods['finish']
print('PASS v2.1.4 wall-normal alignment, both wall axes, front flip, non-invasive baseline')
# A face snap gives us a unit outward normal; an edge snap must not invent one.
class Obj: pass
helper_ns={'math':math,'v_norm':ns['v_norm'],'v_unit':ns['v_unit']}
exec(compile(ast.Module(body=[fns['experimental_hit_wall_normal']],type_ignores=[]),'<hit>','exec'),helper_ns)
ctx=Obj();ctx.snap=Obj();ctx.snap.kind='face';ctx.snap.normal=lambda:(0,5,0)
assert helper_ns['experimental_hit_wall_normal'](ctx)==(0,1,0)
ctx.snap.kind='edge'
assert helper_ns['experimental_hit_wall_normal'](ctx) is None
