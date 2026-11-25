"""Direct gestures and actual clickable Qt length controls; simulated 3D picking."""
import os,tempfile,math,copy
from pathlib import Path
from types import SimpleNamespace as O
from PySide6.QtCore import Qt,QPointF
from PySide6.QtWidgets import QApplication,QMainWindow,QPushButton,QDoubleSpinBox
from PySide6.QtTest import QTest
from test_v225_path_editor import fixture,Viewport,ctx
import ingetrazo_profile_builder_v0_1 as p
from ingetrazo_profile_builder_v0_1.path_editor import activate_path_edit,_fingerprint
from ingetrazo_profile_builder_v0_1.path_editor_direct import DirectPathEditTool
app=QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as data:
 os.environ['APPDATA']=data
 scene,history,group,_=fixture('horizontal',True);vp=Viewport(scene,history)
 host=QMainWindow();host.setCentralWidget(vp);host.resize(1000,700);host.show()
 panel=p.ProfilePanel(O(window=host,viewport=vp,scene=scene));errors=[];panel.report=errors.append
 original=_fingerprint(group);activate_path_edit(panel);tool=vp.active_tool;tool.exit_decision=lambda:"discard"
 assert isinstance(tool,DirectPathEditTool) and not errors
 app.processEvents()
 assert tool.toolbar.isVisible() and not tool.dialog.isVisible()
 assert tool.toolbar.height()<230 and not tool.toolbar.findChildren(QDoubleSpinBox)
 assert [b.text() for b in tool.toolbar.findChildren(QPushButton)]==['更多工具 ▾','↶ 復原','↷ 重做','完成','取消']
 # Click selects, release leaves no carried point and no local/model undo.
 tool.on_click(ctx(vp,tool.session.points[1]));tool.on_release(vp)
 assert tool.selected==1 and not tool.moving and not tool.session.undo_stack
 tool.on_hover(ctx(vp,(2.5,0,0)));assert math.isclose(tool.session.points[1][0],2.)
 # Drag a corner, then drag a segment with no mode changes.
 tool.on_click(ctx(vp,tool.session.points[1]));tool.on_hover(ctx(vp,(2.3,0,0)));tool.on_release(vp)
 assert math.isclose(tool.session.points[1][0],2.3,abs_tol=1e-6) and not tool.moving
 tool.undo();a=tool.session.points[0];b=tool.session.points[1]
 tool.on_click(ctx(vp,(1,0,0)));tool.on_hover(ctx(vp,(1,-.2,0)));tool.on_release(vp)
 assert tool.mode==0 and math.isclose(tool.session.points[0][1],-.2,abs_tol=1e-6)
 assert math.isclose(math.dist(tool.session.points[0],tool.session.points[1]),math.dist(a,b))
 assert tool.selection=={0,1};tool.undo()
 # Blank-area box selection, with no dedicated mode.
 start=ctx(vp,(0,0,0));start.screen=QPointF(425,200)
 end=ctx(vp,(0,0,0));end.screen=QPointF(500,370)
 tool.on_click(start);tool.on_hover(end);tool.on_release(vp)
 assert tool.selection=={1,2} and tool.mode==0
 shift=ctx(vp,tool.session.points[0]);shift.modifiers=Qt.ShiftModifier
 tool.on_click(shift);tool.on_release(vp);assert tool.selection=={0,1,2}
 # First press/release of a double click only selects; double inserts once.
 tool.selection.clear();tool.selected=None
 tool.on_click(ctx(vp,(1,0,0)));tool.on_release(vp);before=len(tool.session.undo_stack)
 tool.on_double_click(ctx(vp,(1,0,0)));tool.on_release(vp)
 assert len(tool.session.points)==5 and len(tool.session.undo_stack)==before+1
 assert math.dist(tool.session.points[1],(1,0,0))<1e-6
 tool.on_key(vp,int(Qt.Key_Delete),Qt.NoModifier)
 assert len(tool.session.points)==4 and not tool.selection and tool.selected is None
 tool.undo();tool.undo();assert len(tool.session.points)==4
 # The labels are real buttons; an actual Qt click opens a compact editor.
 vp.update();app.processEvents();tool._update_dimensions()
 labels=[b for b in tool.dimension_buttons if b.isVisible()]
 assert len(labels)==4 and all('cm' in b.text() for b in labels)
 button=next(b for b in labels if b.property('segment')==0)
 QTest.mouseClick(button,Qt.LeftButton);app.processEvents()
 assert tool.length_dialog.isVisible() and math.isclose(tool.length_input.value(),200.)
 tool.length_input.setValue(250.);QTest.mouseClick(tool.length_ok,Qt.LeftButton)
 assert math.isclose(tool.session.segment_length(0),2.5) and not history.undo_stack
 tool.undo()
 # More tools are optional; closing them retains working edits and returns direct mode.
 tool.show_more();app.processEvents();assert tool.dialog.isVisible() and not tool.direct_mode
 tool.selected=1;tool.selection={1};tool._sync()
 assert all(spin.suffix()==' cm' and spin.decimals()>=5 for spin in tool.coordinates)
 for spin,value in zip(tool.coordinates,(225.,0.,0.)):spin.setValue(value)
 tool._apply_coordinates();assert math.isclose(tool.session.points[1][0],2.25)
 tool.dialog.reject();assert not tool.done and tool.direct_mode and not tool.dialog.isVisible()
 assert math.isclose(tool.session.points[1][0],2.25);tool.undo()
 # Prepare a representative screenshot with all controls collapsed.
 tool.session.move_many({1,2},(.3,0,0));tool.selected=1;tool.selection={1,2};tool.segment=0;tool._sync();tool._refresh_analysis()
 vp.update();app.processEvents();tool._update_dimensions()
 folder=Path(os.environ.get('PROFILE225_DIRECT_SCREENSHOTS','/tmp/profile225-direct'));folder.mkdir(parents=True,exist_ok=True)
 vp.grab().save(str(folder/'direct_overlay.png'));tool.toolbar.grab().save(str(folder/'direct_toolbar.png'))
 tool._open_length(0);app.processEvents();tool.length_dialog.grab().save(str(folder/'length_popup.png'))
 assert _fingerprint(group)==original and not history.undo_stack
 # Undo dismisses a pending length editor; cancel removes every viewport control.
 buttons=list(tool.dimension_buttons);tool.undo();assert tool.length_dialog is None
 tool.cancel();app.processEvents();assert tool.done and not any(b.isVisible() for b in buttons)
 assert _fingerprint(group)==original and not history.undo_stack
 # Finish creates exactly one model command and preserves attributes.
 scene.selection={group};activate_path_edit(panel);tool=vp.active_tool;tool.exit_decision=lambda:"discard"
 tool.session.move_many({1,2},(.1,0,0));tool._sync()
 next(b for b in tool.toolbar.findChildren(QPushButton) if b.text()=='完成').click()
 assert tool.done and len(history.undo_stack)==1
 replacement=scene.groups[0];assert replacement.uid==group.uid and replacement.layer==group.layer
 assert history.undo() and scene.groups[0] is group
 assert history.redo() and scene.groups[0] is replacement
 panel.close();host.close();app.processEvents()
print('PASS direct editor: compact toolbar, default node/segment drag, plain-click selection, automatic box/Shift selection, native double-click insertion, clickable cm lengths, optional tools, converted absolute cm, cleanup, commit/cancel/undo')
