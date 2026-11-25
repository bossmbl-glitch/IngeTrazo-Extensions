"""Chamfer/fillet maths, real generated meshes and transactional Qt previews."""
import copy,json,math,os,tempfile
from pathlib import Path
from types import SimpleNamespace as O
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QMatrix4x4
from PySide6.QtWidgets import QApplication,QMainWindow
from PySide6.QtTest import QTest
from core.orient import is_closed
from test_v225_path_editor import fixture,Viewport,ctx
import ingetrazo_profile_builder_v0_1 as p
from ingetrazo_profile_builder_v0_1.path_corners import corner_geometry
from ingetrazo_profile_builder_v0_1.path_editor import activate_path_edit,_fingerprint
from ingetrazo_profile_builder_v0_1.path_editor_enhancements import EnhancedPathSession
from ingetrazo_profile_builder_v0_1.profile_operations import _map_mesh

# Exact chamfer setbacks, tangent circular samples and traversal.
route=[(0,0,0),(2,0,0),(2,1,0),(0,1,0)];original=copy.deepcopy(route)
points,changed=corner_geometry(route,1,True,'chamfer',.05)
assert len(points)==5 and tuple(changed)==(1,2)
assert np.allclose(points[1],(1.95,0,0)) and np.allclose(points[2],(2,.05,0)) and route==original
for steps in (2,8,64):
 points,changed=corner_geometry(route,1,True,'fillet',.05,steps)
 arc=np.array([points[i] for i in changed]);center=np.array((1.95,.05,0))
 assert len(arc)==steps+1 and np.allclose(np.linalg.norm(arc-center,axis=1),.05)
 assert np.allclose(arc[0],(1.95,0,0)) and np.allclose(arc[-1],(2,.05,0))
 assert np.allclose(arc[0]-center,(0,-.05,0)) and np.allclose(arc[-1]-center,(.05,0,0))
 assert np.all(np.diff(arc[:,0])>0) and np.all(np.diff(arc[:,1])>0)
# Acute/obtuse and arbitrary 3D planes use true radius, not a 90-degree shortcut.
for theta in (math.pi/3,2*math.pi/3):
 route2=[(-2,0,0),(0,0,0),(2*math.cos(theta),2*math.sin(theta),0)]
 r=.15;points,indices=corner_geometry(route2,1,False,'fillet',r,12)
 u=np.array((-1.,0.,0.));v=np.array(route2[-1])/2
 interior=math.acos(float(u@v));center=(u+v)/np.linalg.norm(u+v)*r/math.sin(interior/2)
 assert np.allclose(np.linalg.norm(np.array([points[i] for i in indices])-center,axis=1),r)
 assert math.isclose(math.dist(points[indices[0]],(0,0,0)),r/math.tan(interior/2))
# Closed seam and reversed traversal produce the same spatial replacement.
for index in range(4):
 forward,_=corner_geometry(route,index,True,'fillet',.1,8)
 backward,_=corner_geometry(route[::-1],3-index,True,'fillet',.1,8)
 assert {tuple(np.round(q,9)) for q in forward}=={tuple(np.round(q,9)) for q in backward}
# Input rejection leaves all input coordinates untouched.
for args in [(route,1,True,'fillet',0,8),(route,1,True,'fillet',float('nan'),8),
             (route,1,True,'chamfer',1.,8),(route,1,True,'fillet',1.,8),
             (route,1,True,'fillet',.1,1),(route,1,True,'fillet',.1,65),
             (route,0,False,'fillet',.1,8),([(0,0,0),(1,0,0),(2,0,0)],1,False,'chamfer',.1,8),
             ([(0,0,0),(1,0,0),(0,0,0)],1,False,'fillet',.1,8),
             (route,1,True,'unknown',.1,8), (route*512,1,True,'fillet',.1,8)]:
 try:corner_geometry(*args)
 except ValueError:pass
 else:raise AssertionError('invalid corner accepted')
assert route==original
# Real geometry for each editor mode, open/closed and each corner treatment.
variants=0
for mode in ('horizontal','wall','legacy_wall','space'):
 for closed in (False,True):
  for kind in ('chamfer','fillet'):
   scene,h,g,vp=fixture(mode,closed);s=EnhancedPathSession(scene,g);before=_fingerprint(g)
   settings=copy.deepcopy(g.ext[p.PLUGIN_KEY]['settings']);profile=copy.deepcopy(g.ext[p.PLUGIN_KEY]['section'])
   new,_=corner_geometry(s.points,1,closed,kind,.05,8)
   s._record();s.points=new;assert _fingerprint(g)==before and not h.undo_stack
   replacement=s.commit(vp);assert is_closed(replacement.mesh)
   assert replacement.ext[p.PLUGIN_KEY]['section']==profile and replacement.ext[p.PLUGIN_KEY]['settings']==settings
   assert replacement.material==g.material and replacement.layer==g.layer and replacement.uid==g.uid
   assert h.undo() and scene.groups[0] is g
   assert h.redo() and scene.groups[0] is replacement
   replacement.ext=json.loads(json.dumps(replacement.ext));again=EnhancedPathSession(scene,replacement)
   assert np.allclose(again.points,new,atol=1e-5)
   variants+=1
# Production closed seams and concave corners, not only isolated point maths.
for mode in ('horizontal','wall'):
 for index in (0,3):
  scene,h,g,vp=fixture(mode,True);s=EnhancedPathSession(scene,g)
  s.points,_=corner_geometry(s.points,index,True,'fillet',.1,8)
  replacement=s.commit(vp);assert is_closed(replacement.mesh)
scene,h,g,vp=fixture('horizontal',True);s=EnhancedPathSession(scene,g)
s.points=[(0,0,0),(3,0,0),(3,3,0),(1.5,2,0),(0,3,0)]
s.points,_=corner_geometry(s.points,3,True,'fillet',.1,12)
assert is_closed(s.commit(vp).mesh)

# World radius survives baked and group matrix transformations, including far origin.
for mirrored in (False,True):
 scene,h,g,vp=fixture('wall',True)
 transform=np.array([[1.2,0,0],[0,.8,0],[0,0,1.1],[2,3,1.]])
 if mirrored:transform[0]*=-1
 _map_mesh(g.mesh,transform)
 matrix=QMatrix4x4();matrix.translate(1000,500,10);matrix.rotate(25,1,0,0);matrix.scale(1.2,.8,1.)
 g.xform=matrix;g.component=False
 s=EnhancedPathSession(scene,g);new,indices=corner_geometry(s.points,1,True,'fillet',.1,8)
 arc=np.array([new[i] for i in indices]);x=arc[0];y=arc[4];z=arc[-1]
 # Circumradius from three samples, directly in world space.
 radius=np.linalg.norm(x-y)*np.linalg.norm(y-z)*np.linalg.norm(z-x)/(2*np.linalg.norm(np.cross(y-x,z-x)))
 assert math.isclose(radius,.1,rel_tol=1e-7)
 s.points=new;replacement=s.commit(vp);again=EnhancedPathSession(scene,replacement)
 assert np.allclose(again.points,new,atol=.0003)

app=QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as data:
 os.environ['APPDATA']=data
 scene,history,group,_=fixture('horizontal',True);vp=Viewport(scene,history)
 host=QMainWindow();host.setCentralWidget(vp);host.resize(1000,700);host.show()
 panel=p.ProfilePanel(O(window=host,viewport=vp,scene=scene));errors=[];panel.report=errors.append
 activate_path_edit(panel);tool=vp.active_tool;tool.exit_decision=lambda:"discard";assert not errors
 source=copy.deepcopy(tool.session.points);fingerprint=_fingerprint(group)
 tool.on_click(ctx(vp,source[1]));tool.on_release(vp);vp.update();app.processEvents()
 assert tool.corner_button.isVisible()
 # Actual button click opens modal preview. Preview must not consume undo/redo or source data.
 QTest.mouseClick(tool.corner_button,Qt.LeftButton);editor=tool.corner_editor
 assert editor is not None and editor.dialog.windowModality()==Qt.ApplicationModal
 assert len(editor.points)==5 and len(tool._display_points())==5
 assert tool.session.points==source and not tool.session.undo_stack and not history.undo_stack
 assert _fingerprint(group)==fingerprint
 editor.kind.setCurrentIndex(1);editor.steps.setValue(12);editor.amount.setValue(30.);editor.refresh()
 assert len(editor.points)==16 and editor.apply_button.isEnabled()
 # Preview on/off never changes geometry or local history.
 editor.preview.setChecked(False);assert tool._display_points()==source
 editor.preview.setChecked(True);assert len(tool._display_points())==16
 # While the preview is modal, spatial callbacks cannot move the underlying session.
 tool.on_click(ctx(vp,source[2]));tool.on_hover(ctx(vp,(3,1,0)));tool.on_release(vp)
 assert tool.session.points==source
 folder=Path(os.environ.get('PROFILE225_CORNER_SCREENSHOTS','/tmp/profile225-corners'));folder.mkdir(parents=True,exist_ok=True)
 vp.update();app.processEvents();vp.grab().save(str(folder/'fillet_overlay.png'));editor.dialog.grab().save(str(folder/'fillet_dialog.png'))
 # Invalid values hide the candidate and disable Apply; previous preview isn't silently applied.
 editor.amount.setValue(200.);editor.refresh()
 assert editor.points is None and not editor.apply_button.isEnabled() and tool._display_points()==source
 editor.apply();assert tool.session.points==source and not tool.session.undo_stack
 editor.dialog.reject();assert tool.corner_editor is None and tool.session.points==source
 # Cancel via Escape leaves an existing local redo untouched.
 tool.session.move(1,(2.2,0,0));tool.undo();redo=copy.deepcopy(tool.session.redo_stack)
 tool.selected=1;tool.selection={1};tool.open_corner();tool.on_key(vp,int(Qt.Key_Escape),Qt.NoModifier)
 assert tool.corner_editor is None and tool.session.redo_stack==redo
 # Apply creates exactly one local operation; still no scene writes.
 tool.open_corner();editor=tool.corner_editor
 editor.kind.setCurrentIndex(1);editor.amount.setValue(20.);editor.steps.setValue(8);editor.refresh()
 candidate=copy.deepcopy(editor.points);QTest.mouseClick(editor.apply_button,Qt.LeftButton)
 assert tool.corner_editor is None and tool.session.points==candidate and len(tool.session.undo_stack)==1
 assert not tool.session.redo_stack and not history.undo_stack and _fingerprint(group)==fingerprint
 tool.undo();assert tool.session.points==source
 tool.redo();assert tool.session.points==candidate
 # The completed shape can be edited again and committed as one model operation.
 tool.finish();assert tool.done and len(history.undo_stack)==1
 replacement=scene.groups[0];assert is_closed(replacement.mesh)
 assert history.undo() and scene.groups[0] is group
 assert history.redo() and scene.groups[0] is replacement
 scene.selection={replacement};activate_path_edit(panel);tool=vp.active_tool;tool.exit_decision=lambda:"discard"
 assert np.allclose(tool.session.points,candidate);tool.cancel()
 # Self-intersection after cutting a tight concave path is rejected by production validation.
 scene.selection={replacement};activate_path_edit(panel);tool=vp.active_tool;tool.exit_decision=lambda:"discard"
 tight=[(0,0,0),(2,0,0),(2,2,0),(1.8,2,0),(1.8,.1,0),(0,.1,0)]
 tool.session.points=tight;tool.selected=1;tool.selection={1};tool.open_corner();editor=tool.corner_editor
 editor.amount.setValue(50.);editor.refresh();assert editor.points is None and not editor.apply_button.isEnabled()
 editor.dialog.reject();tool.cancel()
 # Source changes during preview must never get overwritten, even through programmatic calls.
 scene.selection={replacement};activate_path_edit(panel);tool=vp.active_tool;tool.exit_decision=lambda:"discard"
 tool.selected=1;tool.selection={1};tool.open_corner();editor=tool.corner_editor
 tool.session.move(1,np.array(tool.session.points[1])+(.01,0,0));changed=copy.deepcopy(tool.session.points)
 editor.apply();assert editor.points is None and tool.session.points==changed
 tool.cancel();assert tool.corner_editor is None and scene.groups[0] is replacement
 # Switching tools while preview is open discards it and removes modal controls.
 scene.selection={replacement};activate_path_edit(panel);tool=vp.active_tool;tool.exit_decision=lambda:"discard"
 tool.selected=1;tool.selection={1};tool.open_corner();editor=tool.corner_editor
 from tools.select import SelectTool
 vp.set_active_tool(SelectTool());assert editor.closed and tool.done and not editor.dialog.isVisible()
 assert scene.groups[0] is replacement and len(history.undo_stack)==1
 panel.close();host.close();app.processEvents()
print(f'PASS corners: equal setbacks, tangent/world-radius sampling, {variants} production mode/open/closed meshes, closed seams, transformed far-origin placement, modal no-write preview, invalid/self-cross/stale rejection, cancel/apply/local/model undo and cleanup')
