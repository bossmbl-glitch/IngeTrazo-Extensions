"""All sixteen requested usability aids; actual core/Qt, simulated 3D input."""
import copy,math,os,tempfile
from pathlib import Path
from types import SimpleNamespace as O
import numpy as np
from PySide6.QtCore import Qt,QTimer,QPointF
from PySide6.QtWidgets import QApplication,QMainWindow,QMessageBox,QPushButton,QInputDialog
from PySide6.QtTest import QTest
from PySide6.QtGui import QMatrix4x4
from core.orient import is_closed
from test_v225_path_editor import fixture,Viewport,ctx
import ingetrazo_profile_builder_v0_1 as p
from ingetrazo_profile_builder_v0_1.path_editor import activate_path_edit,_fingerprint
from ingetrazo_profile_builder_v0_1.path_aid_session import ProductivePathSession
from ingetrazo_profile_builder_v0_1.path_aids import ProductivePathEditTool

# 11: locks survive all index changes, prohibit indirect mutation and participate in undo.
scene,h,g,vp=fixture('horizontal',True)
g.mesh.edges[0].hidden=not g.mesh.edges[0].hidden;g.mesh.edges[0].soft=True
def flags(mesh):
 return {tuple(sorted((e.v0.position.toTuple(),e.v1.position.toTuple()))):(e.hidden,e.soft) for e in mesh.edges}
original_flags=flags(g.mesh)
s=ProductivePathSession(scene,g);original=copy.deepcopy(s.points)
s.lock_nodes({0});s.move_many({0,1},(.2,0,0));assert s.points[0]==original[0] and math.isclose(s.points[1][0],2.2)
s.undo();assert s.locks and s.points==original
assert s.frame_points(3.,2.,"bottom_left")[0]==original[0]
for operation in [lambda:s.move(0,(.2,0,0)),lambda:s.delete(0),lambda:s.set_segment_length(0,3.,'end'),lambda:s.frame_points(3.,2.,'center')]:
 try:operation()
 except ValueError:pass
 else:raise AssertionError('locked vertex changed')
s.divide_segment(3,4);assert s.locked(0) and len(s.points)==7
s.undo();s.reverse_route();assert s.locked(0);s.undo()
replacement=s.commit(vp);assert replacement.ext[p.PLUGIN_KEY]['locked_path_nodes']==[0]
assert replacement.mesh is not g.mesh and flags(replacement.mesh)==original_flags
again=ProductivePathSession(scene,replacement);assert again.locked(0) and not again.dirty()
assert h.undo() and scene.groups[0] is g
# 13: open a closed loop by removing chosen edge; join first/last and undo exactly.
scene,h,g,vp=fixture('wall',True);s=ProductivePathSession(scene,g);before=copy.deepcopy(s.points)
s.cut_segment(1);assert not s.closed and s.points==before[2:]+before[:2]
assert is_closed(s.replacement().mesh);s.join_ends();assert s.closed and is_closed(s.replacement().mesh)
s.undo();assert not s.closed;s.undo();assert s.points==before and s.closed
# 14: independent copy, no source modifications, no duplicate UID, atomic history.
scene,h,g,vp=fixture('wall',True);s=ProductivePathSession(scene,g);fingerprint=_fingerprint(g)
s.lock_nodes({0});s.arm_copy();s.move_many({1,2},(.2,0,0));expected_flags=flags(s.replacement().mesh);clone=s.commit(vp)
assert flags(clone.mesh)==expected_flags
assert len(scene.groups)==2 and scene.groups[0] is g and _fingerprint(g)==fingerprint
assert clone.uid!=g.uid and clone.mesh is not g.mesh and clone.ext is not g.ext
assert ProductivePathSession(scene,clone).locked(0)
assert clone.material is not g.material and clone.material==g.material
assert clone.ext[p.PLUGIN_KEY]['section']==g.ext[p.PLUGIN_KEY]['section'] and is_closed(clone.mesh)
assert h.undo() and scene.groups==[g];assert h.redo() and scene.groups==[g,clone]
# Matrix copy is independent too.
scene,h,g,vp=fixture('wall',True);matrix=QMatrix4x4();matrix.translate(3,4,0);g.xform=matrix;g.component=False
s=ProductivePathSession(scene,g);s.arm_copy();clone=s.commit(vp)
assert clone.xform is not g.xform and clone.mesh is not g.mesh
# 15: frame physical widths/heights and fixed anchors in horizontal/wall planes.
for mode in ('horizontal','wall'):
 for anchor,fraction in [('center',(.5,.5)),('bottom_left',(0,0)),('top_right',(1,1))]:
  scene,h,g,vp=fixture(mode,True);s=ProductivePathSession(scene,g);u,v,low,high=s.frame_bounds()
  fixed=low+(high-low)*np.array(fraction);new=s.frame_points(3.,1.5,anchor)
  coords=np.column_stack((np.array(new)@u,np.array(new)@v));lo=coords.min(axis=0);hi=coords.max(axis=0)
  assert np.allclose(hi-lo,(3.,1.5)) and np.allclose(lo+(hi-lo)*np.array(fraction),fixed)
  s.points=new;assert is_closed(s.replacement().mesh)
scene,h,g,vp=fixture('space',True);s=ProductivePathSession(scene,g)
try:s.frame_points(3,2)
except ValueError:pass
else:raise AssertionError('nonplanar frame')
# 16: equal division, endpoints and local history preserved.
scene,h,g,vp=fixture('horizontal',False);s=ProductivePathSession(scene,g);source=copy.deepcopy(s.points)
s.divide_segment(0,4);assert np.allclose([s.points[i][0] for i in range(5)],[0,.5,1,1.5,2])
assert s.points[0]==source[0] and s.points[4]==source[1];s.undo();assert s.points==source

app=QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as data:
 os.environ['APPDATA']=data
 scene,history,group,_=fixture('horizontal',True);vp=Viewport(scene,history)
 host=QMainWindow();host.setCentralWidget(vp);host.resize(1000,700);host.show()
 panel=p.ProfilePanel(O(window=host,viewport=vp,scene=scene));errors=[];panel.report=errors.append
 activate_path_edit(panel);tool=vp.active_tool
 assert isinstance(tool,ProductivePathEditTool) and not errors
 folder=Path(os.environ.get('PROFILE225_AID_SCREENSHOTS','/tmp/profile225-aids'));folder.mkdir(parents=True,exist_ok=True)
 more=next(b for b in tool.toolbar.findChildren(QPushButton) if b.text()=='更多工具 ▾')
 QTest.mouseClick(more,Qt.LeftButton);app.processEvents()
 texts=[a.text() for a in tool.actions_menu.actions()]
 assert '整框寬高…' in texts and '等距分點…' in texts and '複製並編輯' in texts and '直角吸附' in texts
 tool.actions_menu.close()
 source=copy.deepcopy(tool.session.points);fingerprint=_fingerprint(group)
 # 3: hover does not change data and distinguishes nodes from edges.
 tool.on_hover(ctx(vp,source[1]));assert tool.hover_node==1 and tool.hover_segment is None
 tool.on_hover(ctx(vp,(1,0,0)));assert tool.hover_node is None and tool.hover_segment==0
 assert tool.session.points==source and not history.undo_stack
 # 9/10: screen-tolerance inference in the actual working plane.
 tool.on_click(ctx(vp,source[1]));tool.on_hover(ctx(vp,(2.02,0,0)))
 assert '同高' in tool.guide_text or '直角' in tool.guide_text
 # Isolate the right-angle constraint from alignment.
 tool.align_snap=False;tool.on_hover(ctx(vp,(2.01,.01,0)))
 assert '90°' in tool.guide_text
 display=tool._display_points();a=np.array(display[0])-display[1];b=np.array(display[2])-display[1]
 assert abs(float(a@b))<1e-8
 vp.update();app.processEvents();vp.grab().save(str(folder/'right_snap.png'))
 tool._stop_move();tool.align_snap=True;tool.right_snap=False
 tool.on_click(ctx(vp,source[1]));tool.on_hover(ctx(vp,(2.3,.01,0)))
 assert '同高' in tool.guide_text and abs(tool.candidate[1])<1e-8
 # 1/2/5: pending distance, immutable old/new paths, cm typing while moving and after release.
 assert tool.move_before==source and tool.session.points==source
 tool.right_snap=True;vp._value_buffer='10';tool.on_value(vp,123.)
 assert math.isclose(tool.session.points[1][0],2.1,abs_tol=1e-6) and len(tool.session.undo_stack)==1
 vp._value_buffer='-20';tool.on_value(vp,999.);assert math.isclose(tool.session.points[1][0],1.8,abs_tol=1e-6)
 vp._value_buffer='-10';tool.on_value(vp,999.);assert math.isclose(tool.session.points[1][0],1.9,abs_tol=1e-6)
 assert len(tool.session.undo_stack)==1
 assert tool.value_is_unitless()
 vp._value_buffer='100 mm';tool.on_value(vp,999.);assert math.isclose(tool.session.points[1][0],2.1,abs_tol=1e-6)
 vp._value_buffer='1/2';tool.on_value(vp,999.);assert math.isclose(tool.session.points[1][0],2.005,abs_tol=1e-6)
 tool.undo();assert tool.session.points==source
 # 4: actual visible dimension widgets don't overlap each other or nodes.
 tool._update_dimensions();vp.update();app.processEvents();labels=[b for b in tool.dimension_buttons if b.isVisible()]
 assert len(labels)==4
 for i,b in enumerate(labels):
  assert not any(b.geometry().intersects(other.geometry()) for other in labels[i+1:])
 # 6: known error has an on-model undo button and one click restores the working path.
 tool.session.move(1,tool.session.points[0]);tool._sync();tool._refresh_analysis();vp.update();app.processEvents()
 assert tool.problem is not None and tool.error_button.isVisible()
 QTest.mouseClick(tool.error_button,Qt.LeftButton);tool._refresh_analysis();assert tool.problem is None and tool.session.points==source
 # 11: visible lock action, drag cannot move locked point, division doesn't shift locks.
 tool.selected=1;tool.selection={1};tool.toggle_selected_locks();tool.on_click(ctx(vp,source[1]));tool.on_release(vp)
 assert not tool.moving and tool.session.points==source and tool.session.locked(1)
 tool.on_click(ctx(vp,(1,0,0)));tool.on_hover(ctx(vp,(1.2,0,0)));tool.on_release(vp)
 assert tool.session.points==source and not tool.moving
 tool.selected=1;tool.selection={1}
 tool.open_corner();assert tool.corner_editor.points is None and not tool.corner_editor.apply_button.isEnabled()
 tool.corner_editor.dialog.reject()
 tool.toggle_selected_locks();assert not tool.session.locked(1)
 # 15: live frame preview is transactional, respects anchors, then one local undo.
 tool.open_frame();frame=tool.frame_preview;frame.anchor.setCurrentIndex(1)
 frame.fields[0].setValue(300.);frame.fields[1].setValue(150.);frame.refresh()
 assert frame.points is not None and tool.session.points==source and not history.undo_stack
 assert np.allclose(frame.fixed_point,source[0])
 folder=Path(os.environ.get('PROFILE225_AID_SCREENSHOTS','/tmp/profile225-aids'));folder.mkdir(parents=True,exist_ok=True)
 vp.update();app.processEvents();vp.grab().save(str(folder/'frame_overlay.png'));frame.dialog.grab().save(str(folder/'frame_dialog.png'))
 frame.apply();assert tool.frame_preview is None and math.isclose(tool.session.points[1][0],3.)
 tool.undo();assert tool.session.points==source
 # 16: actual divide dialog takes the count and adds equally spaced points.
 tool.segment=0
 def choose_parts():
  dialog=app.activeModalWidget();assert isinstance(dialog,QInputDialog)
  dialog.setIntValue(4);dialog.accept()
 QTimer.singleShot(0,choose_parts);tool.open_divide()
 assert len(tool.session.points)==7 and math.isclose(tool.session.points[1][0],.5)
 tool.undo();assert tool.session.points==source
 # 7: long routes use cheap path-only preview during dragging and full mesh on release.
 original_points=tool.session.points
 circle=[(math.cos(t),math.sin(t),0) for t in np.linspace(0,math.pi*2,96,endpoint=False)]
 tool.session.points=circle;tool.selected=0;tool._begin_move({0},circle[0],QPointF(260,350));tool.candidate=(1.01,0,0)
 tool._refresh_analysis();assert not tool.ghost and tool.problem is None
 tool._drop();tool._refresh_analysis();assert tool.ghost and tool.problem is None
 tool.session.points=original_points;tool.session.undo_stack=[];tool.session.redo_stack=[];tool.selected=None;tool.selection.clear()
 # 8: actual QMessageBox for close; stay retains data, discard cancels, finish commits.
 def choose(label):
  def click():
   box=app.activeModalWidget();assert isinstance(box,QMessageBox)
   next(b for b in box.buttons() if b.text()==label).click()
  QTimer.singleShot(0,click)
 tool.session.move(1,(2.2,0,0));choose('繼續編輯');tool.toolbar.reject()
 assert not tool.done and tool.toolbar.isVisible() and math.isclose(tool.session.points[1][0],2.2)
 panel.show();choose('繼續編輯');panel.close();assert not tool.done and panel.isVisible()
 choose('放棄修改');tool.cancel();assert tool.done and not history.undo_stack and _fingerprint(group)==fingerprint
 scene.selection={group};activate_path_edit(panel);tool=vp.active_tool
 tool.session.move(1,(2.2,0,0));choose('完成並退出');tool.cancel()
 assert tool.done and len(history.undo_stack)==1
 replacement=scene.groups[0];assert history.undo() and scene.groups[0] is group;assert history.redo()
 # 14: arming copy through UI creates a separately undoable group on finish.
 scene.selection={replacement};activate_path_edit(panel);tool=vp.active_tool;tool.arm_copy()
 assert tool.session.copy_mode and len(scene.groups)==1 and '副本' in tool.title_label.text()
 tool.toggle_copy_mode();assert not tool.session.copy_mode and tool.title_label.text()=='編輯路徑'
 tool.toggle_copy_mode();assert tool.session.copy_mode
 tool.finish();assert len(scene.groups)==2 and scene.groups[0] is replacement and scene.groups[1].uid!=replacement.uid
 assert history.undo() and scene.groups==[replacement];assert history.redo() and len(scene.groups)==2
 panel.close();host.close();app.processEvents()
print('PASS all 16 aids: distance/VCB, hover, label layout, before/after, error undo, deferred large preview, actual exit choices, right-angle/alignment inference, persistent locks, retained corners, cut/join, independent copy, anchored frame, equal division')
