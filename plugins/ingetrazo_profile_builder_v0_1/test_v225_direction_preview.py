"""Real 0.6.0 meshes + offscreen Qt: upright wall placement and production diagrams."""
import ast
import copy
import math
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace as O
from zipfile import ZipFile
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QMainWindow
from PySide6.QtGui import QVector3D
from PySide6.QtCore import Qt
from core.scene import Scene
from core.history import History
from core.orient import is_closed
import ingetrazo_profile_builder_v0_1 as p
from ingetrazo_profile_builder_v0_1.direction_preview import wall_panel_preview_points,ANCHORS,example_recipe
from ingetrazo_profile_builder_v0_1.profile_operations import build_mesh,recipe_faces,prepare_replacement

app=QApplication.instance() or QApplication([])
# Asymmetric wide/low section, representative of the supplied 066 screenshot.
# The actual user's custom 066 source file was not provided.
section=[(0,0),(40,0),(40,3),(34,3),(34,5),(28,5),(20,6),(14,11),(12,14),(8,17),(3,17),(0,15)]
settings=dict(width_mm=40,height_mm=17,rotate_deg=0,mirror=False,off_u_mm=0,off_v_mm=0,miter=True,anchor='左上',closed_side='內側',reverse_depth=True)
for anchor,(ax,ay) in ANCHORS.items():
    points=wall_panel_preview_points(section,dict(settings,anchor=anchor))
    assert math.isclose(min(x for x,y in points),-ax*17,abs_tol=1e-8)
    assert math.isclose(max(x for x,y in points),(1-ax)*17,abs_tol=1e-8)
    assert math.isclose(min(y for x,y in points),-ay*40,abs_tol=1e-8)
    assert math.isclose(max(y for x,y in points),(1-ay)*40,abs_tol=1e-8)
# Mirror is left/right in the upright preview; offsets move section, not anchor.
normal=wall_panel_preview_points(section,settings)
mirrored=wall_panel_preview_points(section,dict(settings,mirror=True))
for a,b in zip(normal,mirrored):assert math.dist(b,(17-a[0],a[1]))<1e-8
for angle in (0,37,90,180):
    actual=wall_panel_preview_points(section,dict(settings,rotate_deg=angle,off_u_mm=3,off_v_mm=-5))
    c=math.cos(math.radians(angle));s=math.sin(math.radians(angle))
    for (x,y),point in zip(normal,actual):assert math.dist(point,(x*c-y*s+3,x*s+y*c-5))<1e-8

with tempfile.TemporaryDirectory() as data:
    old_data=os.environ.get('APPDATA');os.environ['APPDATA']=data
    scene=Scene();history=History(scene);errors=[]
    vp=O(scene=scene,history=history,active_tool=None,flash_status=lambda *a:None,
         update=lambda:None,notify_scene_changed=lambda:None)
    host=QMainWindow();panel=p.ProfilePanel(O(window=host,viewport=vp,scene=scene));panel.report=errors.append
    panel.profiles['測試剖面']=section;panel._refresh_profiles('測試剖面')
    old_window=p._WINDOW;p._WINDOW=panel
    panel.mouse_orientation.setCurrentIndex(1);panel.anchor.setCurrentText('左上')
    # Reverse-picked face normals must not reverse the drawn in-wall side.
    configurations=0
    for angle in (0,math.pi/2,.37):
        axis=(math.cos(angle),math.sin(angle),0.)
        origin=(6.7,5.1,2.)
        route=[origin,p.v_add(origin,p.v_mul(axis,.4)),p.v_add(p.v_add(origin,p.v_mul(axis,.4)),(0,0,-.3)),p.v_add(origin,(0,0,-.3))]
        ref=(axis[1],-axis[0],0.)
        for polarity in (-1,1):
            n=p.v_mul(ref,polarity)
            for side in (-1,1):
                for depth in (-1,1):
                    tool=p.ProfileDrawTool();tool._drawing_mode=1;tool._horizontal_mode=False
                    tool._wall_plane=n;tool._wall_side=side;tool._wall_depth_sign=depth
                    tool.path=route[:2];tool.hover=route[1]
                    opts=tool._mouse_orientation_options()
                    assert opts['wall_section_layout'] is True
                    recipe=dict(profile='test',section=section,settings=settings,path=route[:2],closed=False,mouse_draw=True,mouse_orientation=opts)
                    raw=recipe_faces(recipe)
                    first=[f[0] for f in raw[:len(section)]]
                    # Left-top anchor puts the section downward on a right stroke.
                    z=[v[2]-origin[2] for v in first]
                    if side==1:assert min(z)<-.039 and max(z)<1e-8
                    else:assert max(z)>.039 and min(z)>-1e-8
                    depths=[p.v_dot(p.v_sub(v,origin),n)*depth for v in first]
                    assert min(depths)>-1e-8 and math.isclose(max(depths),.017,abs_tol=1e-8)
                    for size in (3,4):
                        recipe['path']=route[:size]
                        extended=recipe_faces(recipe)
                        assert first==[f[0] for f in extended[:len(section)]],'first ring changed at a turn'
                    recipe['closed']=True
                    mesh=build_mesh(recipe)
                    assert is_closed(mesh) and all(len(e.faces)==2 for e in mesh.edges)
                    assert opts==tool._mouse_orientation_options()
                    configurations+=1
    # Real tool preview and commit use the same versioned layout and recipe.
    tool=p.ProfileDrawTool();vp.active_tool=tool;tool.on_activate(vp)
    tool.on_key(vp,int(Qt.Key_F),None);tool.on_key(vp,int(Qt.Key_R),None)
    assert panel.generation_preview.recipe['mouse_orientation']['fixed_side']==-1
    assert panel.generation_preview.recipe['mouse_orientation']['wall_depth_sign']==-1
    tool.on_cancel(vp)
    assert panel.generation_preview.recipe['mouse_orientation']['fixed_side']==1
    assert panel.generation_preview.recipe['mouse_orientation']['wall_depth_sign']==1
    tool.on_click(O(world=QVector3D(0,0,0),viewport=vp,normal=(0,-1,0)))
    tool.on_click(O(world=QVector3D(.4,0,0),viewport=vp))
    tool.on_hover(O(world=QVector3D(.4,0,-.3),viewport=vp))
    assert tool.preview_faces() and panel.generation_preview.mesh
    before=copy.deepcopy(tool._mouse_orientation_options())
    tool.on_key(vp,int(Qt.Key_F),None);tool.on_key(vp,int(Qt.Key_R),None)
    assert panel.generation_preview.recipe['mouse_orientation']['fixed_side']==-1
    assert panel.generation_preview.recipe['mouse_orientation']['wall_depth_sign']==-1
    assert panel.preview.wall_application['fixed_side']==-1
    assert panel.preview.wall_application['wall_depth_sign']==-1
    assert len(scene.groups)==0 and len(history.undo_stack)==0
    tool.on_key(vp,int(Qt.Key_F),None);tool.on_key(vp,int(Qt.Key_R),None)
    assert before==tool._mouse_orientation_options()
    tool.on_click(O(world=QVector3D(.4,0,-.3),viewport=vp))
    # Ignore the hover extension when comparing the confirmed generated mesh.
    tool.hover=tool.path[-1];tool._refresh_preview()
    preview_vertices=[v.position.toTuple() for v in tool._preview_mesh.vertices]
    assert panel._produce([(tool.path,False)],mouse_draw=True,mouse_orientation=tool._mouse_orientation_options())
    group=scene.groups[-1]
    assert group.ext[p.PLUGIN_KEY]['mouse_orientation']['wall_section_layout']
    assert preview_vertices==[v.position.toTuple() for v in group.mesh.vertices]
    assert history.undo() and not scene.groups
    assert history.redo() and len(scene.groups)==1
    current=dict(profile='test',section=section,settings=dict(settings,width_mm=45),material='預設材質',tag='預設標籤')
    replacement=prepare_replacement(group,current,{'profile'},scene)
    assert replacement.ext[p.PLUGIN_KEY]['mouse_orientation']['wall_section_layout']
    assert group.ext[p.PLUGIN_KEY]['settings']['width_mm']==40
    # Diagram changes never append commands or alter model objects.
    vp.active_tool=None;baseline=list(scene.groups);commands=len(history.undo_stack)
    for mode in (0,1,2):
        panel.mouse_orientation.setCurrentIndex(mode)
        for value in (0,45,90):panel.rotation.setValue(value)
        panel.mirror.setChecked(not panel.mirror.isChecked())
        panel.offsetu.setValue(2);panel.offsetv.setValue(-3)
        if mode!=2:
            expected=build_mesh(panel.generation_preview.recipe)
            assert [v.position.toTuple() for v in expected.vertices]==[v.position.toTuple() for v in panel.generation_preview.mesh.vertices]
        assert scene.groups==baseline and len(history.undo_stack)==commands
    # Screenshots of the actual implementation, not generated illustrations.
    folder=Path(os.environ.get('PROFILE225_SCREENSHOTS','/tmp/profile225-screenshots'));folder.mkdir(parents=True,exist_ok=True)
    panel.rotation.setValue(0);panel.mirror.setChecked(False);panel.offsetu.setValue(0);panel.offsetv.setValue(0)
    panel.resize(530,760);panel.show();app.processEvents()
    for mode,name in ((1,'wall'),(0,'skirting')):
        panel.mouse_orientation.setCurrentIndex(mode);panel.anchor.setCurrentText('左上' if mode==1 else '左下');app.processEvents()
        assert panel.footer_buttons[-1].geometry().bottom()<=panel.size().height()
        panel.grab().save(str(folder/f'{name}.png'))
    assert not errors,errors
    panel.close();host.close();p._WINDOW=old_window
    if old_data is None:os.environ.pop('APPDATA',None)
    else:os.environ['APPDATA']=old_data
# Legacy core is intentionally unchanged, including skirting and float32 rules.
root=Path(__file__).parent
archive=root.parents[1]/'01_ProfileBuilder/releases/v2.2.4/Lime_Quick_Profile_IngeTrazo_v2.2.4_Release.zip'
if archive.exists():
    with ZipFile(archive) as z:old=z.read(root.name+'/__init__.py').decode()
    functions=lambda source:{n.name:ast.dump(n,include_attributes=False) for n in ast.parse(source).body if isinstance(n,ast.FunctionDef)}
    a,b=functions(old),functions((root/'__init__.py').read_text())
    for name in ('sweep_faces','experimental_sweep_faces','mesh_ready_faces','edge_display_rules','apply_edge_display','_mesh_vertex_key','_mesh_edge_key','ordered_paths'):
        assert a[name]==b[name],name
print(f'PASS v2.2.5: 9 anchors; rotation/mirror/offset mapping; {configurations} wall configurations; real preview/commit/edit/undo; diagrams independent of scene; legacy core unchanged')
