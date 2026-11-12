"""Headless geometry and mouse-state regressions; no IngeTrazo UI claimed."""
import ast, math
from pathlib import Path
from types import SimpleNamespace as Obj
src=Path(__file__).with_name('__init__.py').read_text(encoding='utf8')
tree=ast.parse(src)
ns={'math':math,'EPS':1e-8}
# Load pure geometry without importing the IngeTrazo application.
names=['v_add','v_sub','v_mul','v_dot','v_cross','v_norm','v_unit','clean_polygon','sweep_faces','experimental_sweep_faces','mesh_ready_faces']
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),'<geometry>','exec'),ns)
sweep=ns['sweep_faces'];exp=ns['experimental_sweep_faces']
profile=[(0,0),(16.01,0),(16.01,30),(8,30),(8,130),(0,130)]
opts=dict(horizontal=True,width_mm=16.01,height_mm=130,anchor='左下',rotate_deg=0)
rectangle=[(0,0,.2),(2,0,.2),(2,1,.2),(0,1,.2)]
variants=[]
for reverse in (False,True):
    path=list(reversed(rectangle)) if reverse else rectangle[:]
    for shift in range(4):variants.append(path[shift:]+path[:shift])
for path in variants:
    for sign in (-1.,1.):
        for size in range(2,5):
            pts=path[:size]
            a=sweep(pts,profile,preview_side=True,side_hint_path=pts,stable_mouse_plane=True,fixed_side=sign,**opts)
            b=sweep(pts,profile,preview_side=False,stable_mouse_plane=True,fixed_side=sign,**opts)
            assert a==b,'preview/final mismatch'
            assert a==exp(pts,profile,wall_normal=(0,1,0),fixed_side=sign,**opts)
            coords=[v for f in a for v in f]
            assert abs(min(v[2] for v in coords)-.2)<1e-10
            assert abs(max(v[2] for v in coords)-.33)<1e-10,'height direction changed'
            # Each outgoing span retains the exact section width/height.
            tangent=ns['v_unit'](ns['v_sub'](pts[1],pts[0]))
            side=ns['v_mul'](ns['v_cross'](tangent,(0,0,1)),sign)
            assert math.isclose(ns['v_dot'](ns['v_sub'](a[0][1],a[0][0]),side),.01601,abs_tol=1e-10)
        a=sweep(path,profile,closed=True,fixed_side=sign,**opts)
        assert a==sweep(path,profile,closed=True,preview_side=True,fixed_side=sign,**opts)
        tris=ns['mesh_ready_faces'](a)
        edges={}
        for tri in tris:
            for i in range(len(tri)):
                edge=tuple(sorted((tuple(round(x,9) for x in tri[i]),tuple(round(x,9) for x in tri[(i+1)%len(tri)]))))
                edges[edge]=edges.get(edge,0)+1
        assert edges and set(edges.values())=={2},'closed shell has gaps or overlaps'
# Hover beyond the next corner cannot change already established segment geometry.
a=sweep(rectangle[:3],profile,fixed_side=1,**opts)
b=sweep(rectangle,profile,fixed_side=1,**opts)
assert a[:len(profile)]==b[:len(profile)]
# Height deviation is explicit, never silently changes orientation.
try:sweep([(0,0,0),(1,0,0),(1,1,.01)],profile,**opts)
except ValueError as e:assert '高度' in str(e)
else:raise AssertionError('off-height route accepted')
# UI mouse state: mode locked at first click, start normal retained, F persists,
# Backspace reselects first segment correctly; completion reuses direction.
class Keys:
    Key_F=70;Key_Space=32;Key_Return=13;Key_Enter=14;Key_Backspace=8
    Key_Right=1;Key_Left=2;Key_Up=3;Key_Down=4
ns.update(Tool=object,Qt=Keys,_WINDOW=Obj(mouse_orientation=Obj(currentIndex=lambda:0),width=Obj(value=lambda:16.01)),
          as_tuple=lambda p:tuple(p),experimental_hit_wall_normal=lambda ctx:(0,1,0),qvec=lambda p:p)
classes=[n for n in tree.body if isinstance(n,ast.ClassDef) and n.name in ('ProfileDrawTool','ExperimentalProfileDrawTool')]
exec(compile(ast.Module(body=classes,type_ignores=[]),'<tools>','exec'),ns)
viewport=Obj(flash_status=lambda *a:None,update=lambda:None)
for name in ('ProfileDrawTool','ExperimentalProfileDrawTool'):
    tool=ns[name]();tool._refresh_preview=lambda:None
    tool.on_click(Obj(world=(0,0,0),viewport=viewport))
    assert tool._horizontal_mode is True
    # First-span preview already respects the same face as the confirmed span.
    tool.hover=(1,0,0)
    assert tool._mouse_orientation_options()['fixed_side']==-1
    tool.on_click(Obj(world=(1,0,0),viewport=viewport))
    assert tool._horizontal_side==-1
    tool.on_click(Obj(world=(1,1,0),viewport=viewport))
    assert tool._mouse_orientation_options()['fixed_side']==-1
    tool.on_key(viewport,Keys.Key_F,None)
    assert tool._mouse_orientation_options()['fixed_side']==1
    tool.on_key(viewport,Keys.Key_Backspace,None)
    tool.on_key(viewport,Keys.Key_Backspace,None)
    tool.on_click(Obj(world=(1,0,0),viewport=viewport))
    assert tool._horizontal_side==1,'manual F lost or face sign applied twice'
    assert tool.drag_plane(None)==((0,0,0),(0,0,1))
    captured=[]
    ns['_WINDOW']._produce=lambda paths,**kwargs:captured.append(kwargs) or False
    tool.finish(viewport)
    assert captured[0]['mouse_orientation']==dict(horizontal=True,fixed_side=1)
print('PASS v2.1.5: 8 route orders, 2 sides, span prefixes, upright dimensions, preview/commit, closed manifold, mouse state and both tool entries')
