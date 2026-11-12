"""Regression: upright wall moulding retains section side and depth through turns."""
import runpy, math, ast
from pathlib import Path
from types import SimpleNamespace as Obj
root=Path(__file__).parent
ns=runpy.run_path(str(root/'test_v215_horizontal.py'))['ns']
sweep=ns['sweep_faces'];v_sub=ns['v_sub'];dot=ns['v_dot']
profile=[(0,0),(17,0),(17,3),(11,3),(11,10),(4,17),(0,17)]
# Include both axes, a rotated wall, both normal signs, clockwise/counterclockwise,
# every starting corner, and horizontal/vertical first spans.
checks=0
for angle in (0,math.pi/2,.37):
    axis=(math.cos(angle),math.sin(angle),0)
    normal=(axis[1],-axis[0],0)
    origin=(3.,-2.,.4)
    point=lambda u,v:tuple(origin[k]+axis[k]*u+(v if k==2 else 0) for k in range(3))
    rectangle=[point(0,0),point(1.2,0),point(1.2,2),point(0,2)]
    for reverse in (False,True):
        route=rectangle[::-1] if reverse else rectangle
        for start in range(4):
            path=route[start:]+route[:start]
            for polarity in (-1.,1.):
                n=tuple(x*polarity for x in normal)
                for side in (-1.,1.):
                    opts=dict(wall_frame=True,plane_normal_override=n,fixed_side=side,width_mm=17,height_mm=17)
                    rings=[]
                    for size in range(2,5):
                        pts=path[:size]
                        preview=sweep(pts,profile,preview_side=True,stable_mouse_plane=True,**opts)
                        final=sweep(pts,profile,preview_side=False,stable_mouse_plane=True,**opts)
                        assert preview==final
                        # All spans protrude by 17mm from the fixed plane,
                        # including YZ walls; legacy reverse_depth must not invert it.
                        depths=[dot(v_sub(p,origin),n) for f in preview for p in f]
                        assert math.isclose(min(depths),0,abs_tol=1e-9)
                        assert math.isclose(max(depths),.017,abs_tol=1e-9)
                        first=[preview[j][0] for j in range(len(profile))]
                        rings.append(first)
                    assert rings[0]==rings[1]==rings[2],'first section changed at turn'
                    shell=sweep(path,profile,closed=True,**opts)
                    assert shell==sweep(path,profile,closed=True,preview_side=True,**opts)
                    edges={}
                    for tri in ns['mesh_ready_faces'](shell):
                        for j in range(3):
                            edge=tuple(sorted(tuple(round(x,8) for x in p) for p in (tri[j],tri[(j+1)%3])))
                            edges[edge]=edges.get(edge,0)+1
                    assert set(edges.values())=={2},'closed wall frame has a gap'
                    back=sweep(path,profile,wall_depth_sign=-1,**opts)
                    depths=[dot(v_sub(p,origin),n) for f in back for p in f]
                    assert math.isclose(min(depths),-.017,abs_tol=1e-9)
                    assert math.isclose(max(depths),0,abs_tol=1e-9)
                    checks+=1
# Wall tool state must be shared by both entry points. No host UI imports.
keys=ns['Qt'];keys.Key_R=82;keys.Key_Tab=9
mode=Obj(currentIndex=lambda:1)
ns['_WINDOW']=Obj(mouse_orientation=mode,width=Obj(value=lambda:17))
ns['experimental_hit_wall_normal']=lambda ctx:getattr(ctx,'normal',None)
viewport=Obj(flash_status=lambda *a:None,update=lambda:None)
for cls in ('ProfileDrawTool','ExperimentalProfileDrawTool'):
    tool=ns[cls]();tool._refresh_preview=lambda:None
    tool.on_hover(Obj(world=(0,0,0),viewport=viewport,normal=(0,-1,0)))
    tool.on_click(Obj(world=(0,0,0),viewport=viewport))
    assert tool._wall_plane==(0,-1,0),'edge snap lost prior wall face'
    tool.hover=(1,0,0)
    a=tool._mouse_orientation_options()
    tool.on_click(Obj(world=(1,0,0),viewport=viewport))
    tool.on_click(Obj(world=(1,0,2),viewport=viewport,normal=(1,0,0)))
    assert a==tool._mouse_orientation_options(),'corner changed wall plane'
    assert tool.drag_plane(None)==((0,0,0),(0,-1,0))
    tool.on_key(viewport,keys.Key_F,None)
    assert tool._wall_side==-1 and tool._wall_depth_sign==1
    tool.on_key(viewport,keys.Key_R,None)
    assert tool._wall_side==-1 and tool._wall_depth_sign==-1
    captured=[]
    ns['_WINDOW']._produce=lambda paths,**kwargs:captured.append(kwargs) or False
    tool.finish(viewport)
    assert captured[0]['mouse_orientation']==tool._mouse_orientation_options()
    tool._clear()
    # No face available: vertical first span must not force XY.
    tool.on_click(Obj(world=(0,0,0),viewport=viewport))
    tool.hover=(0,0,2)
    tool.on_key(viewport,keys.Key_Tab,None)
    tool.on_click(Obj(world=(0,0,2),viewport=viewport))
    assert tool._wall_plane==(1,0,0)
    tool.on_click(Obj(world=(0,1,2),viewport=viewport))
    assert tool._wall_plane==(1,0,0)
    tool.on_key(viewport,keys.Key_Backspace,None)
    tool.on_key(viewport,keys.Key_Backspace,None)
    tool.on_key(viewport,keys.Key_Backspace,None)
    assert tool._drawing_mode is None and tool._wall_plane is None
# Wrong-wall strokes are rejected, not silently reoriented.
try:sweep([(0,0,0),(1,0,0),(1,1,0)],profile,wall_frame=True,plane_normal_override=(0,-1,0))
except ValueError:pass
else:raise AssertionError('off-wall stroke accepted')
print(f'PASS v2.1.6: {checks} wall frame configurations; fixed depth/side, first span, closure, mouse states, F/R/Tab, both entries')
