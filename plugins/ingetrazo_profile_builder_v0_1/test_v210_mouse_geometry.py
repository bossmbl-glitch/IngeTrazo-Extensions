"""Mouse-draw regression: open preview = commit; stable planar 90-degree joints."""
import ast
import math
from pathlib import Path
src=Path(__file__).with_name('__init__.py').read_text(encoding='utf-8')
module=ast.parse(src)
functions={n.name:n for n in module.body if isinstance(n,ast.FunctionDef)}
names=['v_add','v_sub','v_mul','v_dot','v_cross','v_norm','v_unit','clean_polygon','sweep_faces','mesh_ready_faces']
ns={'math':math,'EPS':1e-8}
exec(compile(ast.Module(body=[functions[n] for n in names], type_ignores=[]),'<mouse_test>','exec'),ns)
sweep=ns['sweep_faces']; tri=ns['mesh_ready_faces']
profile=[(0,0),(17,0),(17,3),(13,3)]+[(13-10*math.sin(i*math.pi/24),13-10*math.cos(i*math.pi/24)) for i in range(1,13)]+[(3,17),(0,17)]
sections=[profile,[(0,0),(17,0),(17,5),(12,5),(12,10),(7,10),(7,17),(0,17)]]
paths=[[(0,0,0),(.6,0,0),(.6,0,.4)],
       [(0,0,0),(.6,0,0),(.6,.4,0)],
       [(0,0,0),(0,.6,0),(0,.6,.4)]]
checks=0
for section in sections:
    for path in paths:
        for reversed_path in (path, list(reversed(path))):
            for mirror in (False,True):
                options=dict(width_mm=17,height_mm=17,rotate_deg=180,mirror=mirror,anchor='左上',reverse_depth=True,stable_mouse_plane=True)
                prev=sweep(reversed_path,section,closed=False,preview_side=True,side_hint_path=reversed_path,**options)
                final=sweep(reversed_path,section,closed=False,preview_side=True,**options)
                assert prev == final, 'mouse-draw preview and final mismatch'
                triangles=tri(final)
                assert triangles
                assert all(math.isfinite(coord) for face in triangles for point in face for coord in point)
                # Both adjacent stretches share *the same* intermediate ring.
                n=len(section)
                for k in range(n):
                    assert math.dist(final[k][3],final[n+k][0])<1e-10
                checks+=1
# A straight first span should not turn over when the next point follows
# the same horizontal wall direction then rises on Z.
two=[(0,0,0),(.6,0,0)]
three=[*two,(.6,0,.4)]
for section in sections:
    first=sweep(two,section,preview_side=True,stable_mouse_plane=True)
    next_segment=sweep(three,section,preview_side=False,stable_mouse_plane=True)
    for j in range(len(section)):
        assert math.dist(first[j][0],next_segment[j][0])<1e-8, 'first ring rotated when Z turn was added'
classes={n.name:n for n in module.body if isinstance(n,ast.ClassDef)}
panel=next(n for n in classes['ProfilePanel'].body if isinstance(n,ast.FunctionDef) and n.name=='_produce')
draw=classes['ProfileDrawTool']
finish=next(n for n in draw.body if isinstance(n,ast.FunctionDef) and n.name=='finish')
refresh=next(n for n in draw.body if isinstance(n,ast.FunctionDef) and n.name=='_refresh_preview')
assert 'preview_side=(mouse_draw and not closed)' in ast.get_source_segment(src,panel)
assert 'stable_mouse_plane=mouse_draw' in ast.get_source_segment(src,panel)
assert 'mouse_draw=True' in ast.get_source_segment(src,finish)
assert 'side_hint_path=self.path' in ast.get_source_segment(src,refresh)
assert 'mesh_ready_faces' in ast.get_source_segment(src,refresh)
print(f'PASS v2.1 mouse geometry: {checks} open L shapes, preview equals final, 90-degree joints, wall-plane continuity')
# Moving the cursor after the last confirmed point must not flip previously
# placed joints. The new hover segment may bevel the last ring, but not the
# earlier confirmed corners or the side of the entire sweep.
clicked=[(0,0,0),(.6,0,0),(.6,0,.4)]
for hover in [(0.4,0,.8),(.9,0,.8),(.6,0,.9)]:
    fixed=sweep(clicked,profile,closed=False,preview_side=True,side_hint_path=clicked,stable_mouse_plane=True)
    moving=sweep(clicked+[hover],profile,closed=False,preview_side=True,side_hint_path=clicked,stable_mouse_plane=True)
    for k in range(len(profile)):
        assert math.dist(fixed[k][0],moving[k][0])<1e-10
        assert math.dist(fixed[k][3],moving[k][3])<1e-10
# Closed frame preview and commit must be bitwise identical, including the
# seam at the first/last ring.
closed=[(0,0,0),(.6,0,0),(.6,0,.4),(0,0,.4)]
for section in sections:
    for rev in (False,True):
        pts=closed[::-1] if rev else closed
        assert sweep(pts,section,closed=True,preview_side=False) == sweep(pts,section,closed=True,preview_side=True)
print('PASS confirmed-corner hover stability and closed-frame preview parity')
