"""All 15 path editing aids; real core and Qt, simulated spatial input."""
import copy,json,math,os,tempfile
from pathlib import Path
from types import SimpleNamespace as O
import numpy as np
from PySide6.QtCore import Qt,QPointF
from PySide6.QtGui import QVector3D
from PySide6.QtWidgets import QApplication,QMainWindow
from core.camera import OrbitCamera
from core.snap import SnapResult
# Reuse the integration fixture; importing also exercises the old operation suite.
from test_v225_path_editor import fixture,Viewport,ctx
import ingetrazo_profile_builder_v0_1 as p
from ingetrazo_profile_builder_v0_1.path_editor import activate_path_edit,_fingerprint
from ingetrazo_profile_builder_v0_1.path_editor_enhancements import EnhancedPathSession,EnhancedPathEditTool,PathValidationError

app=QApplication.instance() or QApplication([])
# 4. Atomic relative motion and one local undo for multiple nodes.
scene,h,g,_=fixture('horizontal',True);s=EnhancedPathSession(scene,g);before=copy.deepcopy(s.points)
s.move_many({1,2},(.1,0,0));assert math.isclose(s.points[1][0],2.1) and math.isclose(s.points[2][0],2.1)
assert len(s.undo_stack)==1;s.undo();assert s.points==before;s.redo();assert math.isclose(s.points[1][0],2.1)
# 8/9. Exact physical lengths with start, end and center fixed.
for fixed in ('start','end','center'):
 scene,h,g,_=fixture('horizontal',True);s=EnhancedPathSession(scene,g);a,b=map(np.array,s.points[:2]);center=(a+b)*.5
 s.set_segment_length(0,3.,fixed)
 assert math.isclose(s.segment_length(0),3.)
 if fixed=='start':assert np.allclose(s.points[0],a)
 elif fixed=='end':assert np.allclose(s.points[1],b)
 else:assert np.allclose((np.array(s.points[0])+s.points[1])*.5,center)
# 10. Interior angle, not turn angle; endpoints have no angle.
scene,h,g,_=fixture('horizontal',True);s=EnhancedPathSession(scene,g)
assert math.isclose(s.angle_at(2),90.)
scene,h,g,_=fixture('horizontal',False);s=EnhancedPathSession(scene,g);assert s.angle_at(0) is None
# 11. Horizontal / vertical alignment in each actual constrained plane.
for mode in ('horizontal','wall'):
 scene,h,g,_=fixture(mode,True);s=EnhancedPathSession(scene,g);u,v=s.plane_axes()
 s.move(1,np.asarray(s.points[1])+v*.2);s.align({0,1},0,True)
 assert abs(np.dot(np.asarray(s.points[1])-s.points[0],v))<1e-8
 s.move(2,np.asarray(s.points[2])+u*.2);s.align({1,2},1,False)
 assert abs(np.dot(np.asarray(s.points[2])-s.points[1],u))<1e-8
 s.validate()
# 12. Insert distance from either end, or a ratio; one undo each.
for kind,value,expected in [('distance',.2,.2),('end',.2,1.8),('ratio',.5,1.)]:
 scene,h,g,_=fixture('horizontal',False);s=EnhancedPathSession(scene,g)
 idx=s.insert_at(0,value,kind);assert idx==1 and math.isclose(s.points[idx][0],expected)
 assert len(s.undo_stack)==1;s.undo();assert len(s.points)==3
for bad in (-1,0,1,2):
 try:s.insert_at(0,bad,'ratio')
 except ValueError:pass
 else:raise AssertionError('invalid insert ratio')
# 13. Closed-route reversal keeps the start; open reversal exchanges ends.
for closed in (False,True):
 scene,h,g,_=fixture('horizontal',closed);s=EnhancedPathSession(scene,g);original=list(s.points)
 s.reverse_route();assert s.points==([original[0]]+original[:0:-1] if closed else original[::-1])
 assert np.allclose(g.ext[p.PLUGIN_KEY]['path'],original);s.undo();assert s.points==original
# 5. Structured diagnostics precisely identify duplicates and crossing segments.
scene,h,g,_=fixture('horizontal',True);s=EnhancedPathSession(scene,g)
s.move(1,s.points[0]);problem=s.diagnostics();assert problem.nodes=={0,1}
s.undo();s.points=[(0,0,0),(2,1,0),(2,0,0),(0,1,0)]
problem=s.diagnostics();assert problem.segments=={0,2}
try:s.validate()
except PathValidationError as exc:assert exc.segments=={0,2}
else:raise AssertionError('crossing allowed')
s.points=[(0,0,0),(2,0,0),(.2,.01,0),(0,1,0)]
assert s.diagnostics() is not None

with tempfile.TemporaryDirectory() as data:
 os.environ['APPDATA']=data
 scene,history,group,_=fixture('horizontal',True);vp=Viewport(scene,history);vp.camera=OrbitCamera()
 host=QMainWindow();host.setCentralWidget(vp);panel=p.ProfilePanel(O(window=host,viewport=vp,scene=scene));errors=[];panel.report=errors.append
 baseline=_fingerprint(group);activate_path_edit(panel);tool=vp.active_tool;tool.exit_decision=lambda:"discard"
 assert isinstance(tool,EnhancedPathEditTool) and tool.scroll_area.widget() is tool.scroll_body
 # 1/6. Real production mesh preview and an immutable original path.
 original=copy.deepcopy(tool.session.original);tool._refresh_analysis()
 assert tool.problem is None and len(tool.ghost)==len(tool.session.replacement().mesh.faces)
 assert _fingerprint(group)==baseline and not history.undo_stack
 # 3. Box-select only the right-hand endpoints, then Shift add/remove.
 tool.mode_combo.setCurrentIndex(4)
 def event(point,screen=None,modifiers=Qt.NoModifier,snap=None):
  c=ctx(vp,point);c.modifiers=modifiers;c.snap=snap
  if screen is not None:c.screen=QPointF(*screen)
  return c
 tool.on_click(event((0,0,0),(430,210)));tool.on_hover(event((0,0,0),(510,360)));tool.on_release(vp)
 assert tool.selection=={1,2},tool.selection
 tool.mode_combo.setCurrentIndex(0);tool.on_click(event(tool.session.points[0],modifiers=Qt.ShiftModifier))
 assert tool.selection=={0,1,2}
 tool.on_click(event(tool.session.points[0],modifiers=Qt.ShiftModifier));assert tool.selection=={1,2}
 tool.on_click(event(tool.session.points[1]));tool.on_hover(event((2.3,0,0)));tool.on_release(vp)
 assert math.isclose(tool.session.points[1][0],2.3,abs_tol=1e-6) and math.isclose(tool.session.points[2][0],2.3,abs_tol=1e-6)
 # 2. Translate a whole segment without changing its length.
 tool.mode_combo.setCurrentIndex(3);mid=(np.array(tool.session.points[0])+tool.session.points[1])*.5
 old_length=tool.session.segment_length(0);tool.on_click(event(mid));tool.on_hover(event(mid+(0,-.2,0)));tool.on_release(vp)
 assert math.isclose(tool.session.segment_length(0),old_length,abs_tol=1e-6)
 assert math.isclose(tool.session.points[0][1],-.2,abs_tol=1e-6) and math.isclose(tool.session.points[1][1],-.2,abs_tol=1e-6)
 assert tool.session.original==original
 # 4. UI cm/mm conversions preserve physical meaning; absolute XYZ now converts to cm.
 tool.deltas[0].setValue(10);tool.units.setCurrentText('mm');assert tool.deltas[0].value()==100
 assert all(control.suffix()==' cm' for control in tool.coordinates)
 tool.units.setCurrentText('cm');before=tool.session.points[0][0];tool._relative_move()
 assert math.isclose(tool.session.points[0][0],before+.1,abs_tol=1e-6)
 # 7. Snap/axis information reflects the actual ToolContext.
 target=QVector3D(*tool.session.points[0]);tool.on_hover(event(target.toTuple(),snap=SnapResult(target,'midpoint')))
 assert '中點' in tool.snap_label.text()
 tool.on_key(vp,int(Qt.Key_Right),Qt.NoModifier);tool.on_hover(event(target.toTuple(),snap=SnapResult(target,'endpoint')))
 assert '端點' in tool.snap_label.text() and 'X' in tool.snap_label.text()
 tool.on_key(vp,int(Qt.Key_Down),Qt.NoModifier)
 # 8/9/10. UI measurement, fixed-end length action, angle annotation.
 tool.segment_spin.setValue(2);assert tool.segment==1 and '線段2' in tool.measure_label.text()
 tool.target_length.setValue(120);tool.fixed_end.setCurrentIndex(0);tool._set_length()
 assert math.isclose(tool.session.segment_length(1),1.2,abs_tol=1e-6)
 tool.undo();tool.selected=1;tool.selection={1};tool._sync();assert '夾角' in tool.measure_label.text()
 # 12. UI ratio insertion supports a specified segment.
 tool.segment=0;tool.insert_kind.setCurrentIndex(2);tool.insert_value.setValue(50);tool._insert_at()
 assert len(tool.session.points)==5;tool.undo();assert len(tool.session.points)==4
 # 14. Use the native camera target without changing zoom or model state.
 tool.selected=1;tool.selection={1};distance=vp.camera.distance;tool.focus_node()
 assert (vp.camera.target-QVector3D(*tool.session.points[1])).length()<1e-6 and vp.camera.distance==distance
 # 5. Error overlays and original path remain visible before committing.
 good=copy.deepcopy(tool.session.points);tool.session.move(1,tool.session.points[0]);tool._refresh_analysis()
 assert tool.problem.nodes=={0,1} and not tool.ghost
 tool.undo();assert tool.session.points==good;tool._refresh_analysis();assert tool.ghost
 # 13. Reverse updates the ghost but does not commit until Apply/Finish.
 tool._reverse();tool._refresh_analysis();assert tool.ghost and _fingerprint(group)==baseline
 assert not history.undo_stack;tool.undo();tool._refresh_analysis()
 # Actual Qt controls/overlay screenshots of the test canvas.
 folder=Path(os.environ.get('PROFILE225_ADV_SCREENSHOTS','/tmp/profile225-advanced'));folder.mkdir(parents=True,exist_ok=True)
 host.show();panel.show();app.processEvents();tool._refresh_analysis();app.processEvents()
 vp.grab().save(str(folder/'advanced_overlay.png'));tool.dialog.grab().save(str(folder/'advanced_dialog.png'))
 tool.cancel();assert _fingerprint(group)==baseline and not history.undo_stack
 # 15. Each application is a distinct model command; cancel keeps applied work.
 scene.selection={group};activate_path_edit(panel);tool=vp.active_tool;tool.exit_decision=lambda:"discard"
 tool.session.move(1,(2.2,0,0));tool.apply_continue();first=scene.groups[0]
 assert not tool.done and tool.toolbar.isVisible() and len(history.undo_stack)==1
 assert tool.session.group is first and tool.session.original[0]==tool.session.points
 tool.apply_continue();assert len(history.undo_stack)==1,'no-op Apply added history'
 tool.session.move(1,(2.4,0,0));tool.apply_continue();second=scene.groups[0]
 assert len(history.undo_stack)==2 and first is not second
 tool.session.move(1,(2.6,0,0));tool.cancel();assert scene.groups[0] is second
 assert history.undo() and scene.groups[0] is first
 assert history.undo() and scene.groups[0] is group
 assert history.redo() and history.redo() and scene.groups[0] is second
 assert not errors,errors
 panel.close();host.close()
print('PASS all 15 enhancements: ghost preview, segment/multinode/box moves, relative units, precise diagnostics, original comparison, snap hints, lengths/angles, fixed-end resize, planar align, precise inserts, reverse preview, native camera focus, apply-and-continue history')
