import ast, math
from pathlib import Path
source=Path(__file__).with_name('__init__.py').read_text(encoding='utf8')
tree=ast.parse(source)
funcs={f.name:f for f in tree.body if isinstance(f,ast.FunctionDef)}
names=['v_add','v_sub','v_mul','v_dot','v_cross','v_norm','v_unit','clean_polygon','sweep_faces','experimental_sweep_faces','mesh_ready_faces']
ns={'math':math,'EPS':1e-8}
exec(compile(ast.Module(body=[funcs[n] for n in names], type_ignores=[]),'<exp>','exec'),ns)
sweep=ns['experimental_sweep_faces']; legacy=ns['sweep_faces']; tri=ns['mesh_ready_faces']
section=[(0,0),(17,0),(17,5),(12,5),(12,10),(7,10),(7,17),(0,17)]
paths=[[(0,0,0),(.6,0,0),(.6,.4,0),(.6,.4,.5)],
       [(0,0,0),(.6,0,0),(.6,0,.5),(.6,.4,.5)],
       [(0,0,0),(0,.6,0),(0,.6,.5),(.3,.6,.5)]]
for path in paths:
    for item in [path,path[::-1]]:
        polys=sweep(item,section,stable_mouse_plane=True,preview_side=True)
        assert polys==sweep(item,section,stable_mouse_plane=True,preview_side=True), 'unstable'
        triangles=tri(polys)
        assert triangles and all(math.isfinite(z) for f in triangles for p in f for z in p)
        for i in range(len(item)-2):
            for j in range(len(section)):
                assert math.dist(polys[i*len(section)+j][3],polys[(i+1)*len(section)+j][0])<1e-8
plane=[(0,0,0),(.6,0,0),(.6,0,.5)]
assert sweep(plane,section,stable_mouse_plane=True)==legacy(plane,section,stable_mouse_plane=True)
classes={v.name:v for v in tree.body if isinstance(v,ast.ClassDef)}
assert any(isinstance(c,ast.ClassDef) and c.name=='ExperimentalProfileDrawTool' for c in tree.body)
panel=classes['ProfilePanel']; method=lambda name:next(x for x in panel.body if isinstance(x,ast.FunctionDef) and x.name==name)
# v2.2.0 removes the experimental UI; retain geometry regressions above.
assert not any(isinstance(x,ast.FunctionDef) and x.name=='activate_experimental_draw' for x in panel.body)
assert 'experimental_button' not in source
# Production dispatch now handles the versioned wall layout; the legacy
# sweep itself is unchanged and covered by the v2.2.0 AST regression.
assert 'experimental_sweep_faces if experimental else profile_sweep_faces' in ast.get_source_segment(source,method('_produce'))
assert 'viewport.set_active_tool(ProfileDrawTool())' in ast.get_source_segment(source,method('activate_draw'))
print('PASS v2.1.3 experimental 3D bends, continuity, baseline isolation')
