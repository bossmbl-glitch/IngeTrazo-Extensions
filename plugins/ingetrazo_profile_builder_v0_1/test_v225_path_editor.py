"""Real Mesh/Group/History + Qt overlay/dialog tests; spatial input is simulated."""
import copy
import json
import math
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace as O
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
from PySide6.QtCore import Qt,QPointF
from PySide6.QtGui import QVector3D,QMatrix4x4,QPainter,QPen,QColor
from PySide6.QtWidgets import QApplication,QMainWindow,QWidget
from core.scene import Scene
from core.group import Group
from core.history import History
from core.orient import is_closed
import ingetrazo_profile_builder_v0_1 as p
from ingetrazo_profile_builder_v0_1.profile_operations import build_mesh,_map_mesh
from ingetrazo_profile_builder_v0_1.path_editor import PathEditSession,ProfilePathEditTool,_fingerprint,activate_path_edit as activate_current_path_edit

def activate_path_edit(panel):
    activate_current_path_edit(panel)
    tool=panel.app.viewport.active_tool
    if isinstance(tool,ProfilePathEditTool):tool.exit_decision=lambda:'discard'

app=QApplication.instance() or QApplication([])
settings=dict(width_mm=16.01,height_mm=130,rotate_deg=0,mirror=False,off_u_mm=0,off_v_mm=0,miter=True,anchor='左上',closed_side='內側',reverse_depth=True)

def fixture(mode='horizontal',closed=False):
    scene=Scene();history=History(scene)
    if mode=='horizontal':
        route=[(0,0,0),(2,0,0),(2,1,0),(0,1,0)]
        orientation=dict(horizontal=True,fixed_side=1)
    elif mode in ('legacy_wall','wall'):
        route=[(0,0,2),(2,0,2),(2,0,0),(0,0,0)]
        orientation=dict(wall_frame=True,plane_normal_override=(0,-1,0),fixed_side=1,wall_depth_sign=1)
        if mode=='wall':orientation['wall_section_layout']=True
    else:
        route=[(0,0,0),(2,0,0),(2,1,1),(0,1,2)]
        orientation={}
    if not closed:route=route[:3]
    recipe=dict(profile='058',section=p.PRESETS['058｜弧形線板'],settings=copy.deepcopy(settings),path=route,
                closed=closed,mouse_draw=True,mouse_orientation=orientation,material='木材',tag='線板')
    group=Group(build_mesh(recipe,{'color':(.4,.3,.2),'layer':'線板'}),'原線板')
    group.ext={p.PLUGIN_KEY:recipe,'other_plugin':{'keep':True}};group.material={'color':(.2,.4,.6)};group.layer='線板'
    scene.groups.append(group);scene.selection={group}
    vp=O(scene=scene,history=history,notify_scene_changed=lambda:None)
    return scene,history,group,vp

count=0
for mode in ('horizontal','wall','legacy_wall','space'):
    for closed in (False,True):
        scene,history,group,vp=fixture(mode,closed)
        # Unrelated loose geometry and a nested sibling must survive untouched.
        loose=scene.mesh.add_face([QVector3D(0,0,0),QVector3D(2,0,0),QVector3D(0,1,0)])
        sibling=Group(name='其他物件');sibling.children=[Group(name='子物件')];scene.groups.append(sibling)
        old_fingerprint=_fingerprint(group);old_ext=copy.deepcopy(group.ext);old_positions=[v.position.toTuple() for v in group.mesh.vertices]
        session=PathEditSession(scene,group)
        assert _fingerprint(group)==old_fingerprint and not history.undo_stack
        original=list(session.points)
        idx=session.insert(0,(np.array(original[0])+original[1])*.5)
        assert len(session.points)==len(original)+1
        session.undo();assert session.points==original
        session.redo();assert len(session.points)==len(original)+1
        session.delete(idx);assert session.points==original
        changed=list(original[1]);changed[0]+=.3;session.move(1,changed)
        assert _fingerprint(group)==old_fingerprint and not history.undo_stack
        replacement=session.commit(vp)
        assert replacement is not group and len(history.undo_stack)==1
        assert replacement.uid==group.uid and replacement.name==group.name
        assert replacement.material==group.material and replacement.layer==group.layer
        assert replacement.ext['other_plugin']==old_ext['other_plugin']
        assert replacement.ext[p.PLUGIN_KEY]['settings']==old_ext[p.PLUGIN_KEY]['settings']
        assert replacement.ext[p.PLUGIN_KEY]['mouse_orientation']==old_ext[p.PLUGIN_KEY]['mouse_orientation']
        assert replacement.mesh.faces[0].attrs==group.mesh.faces[0].attrs
        assert is_closed(replacement.mesh)
        assert loose in scene.mesh.faces and scene.groups[-1] is sibling and sibling.children
        assert group.ext==old_ext and [v.position.toTuple() for v in group.mesh.vertices]==old_positions
        assert history.undo() and scene.groups[0] is group
        assert history.redo() and scene.groups[0] is replacement
        assert scene.groups[-1] is sibling
        replacement.ext=json.loads(json.dumps(replacement.ext))
        again=PathEditSession(scene,replacement)
        assert math.dist(again.points[1],changed)<1e-5
        assert again.commit(vp) is replacement and len(history.undo_stack)==1
        count+=1
# Extend an open endpoint at either end, then rebuild and undo.
for first in (True,False):
    scene,history,group,vp=fixture('horizontal',False);session=PathEditSession(scene,group)
    index=session.extend_endpoint(0 if first else len(session.points)-1)
    target=(-.5,0,0) if first else (2,1.5,0)
    session.move(index,target);new=session.commit(vp)
    assert len(new.ext[p.PLUGIN_KEY]['path'])==4 and is_closed(new.mesh)
    assert history.undo() and scene.groups==[group]

# Affine positions: pre-recorded transform, later baked move/rotate/scale,
# matrix placement, mirrored axes, and repeated edits must not reset position.
for mirrored in (False,True):
    scene,history,group,vp=fixture('horizontal',True)
    prior=np.array([[1,0,0],[0,1,0],[0,0,1],[2,3,1.]])
    _map_mesh(group.mesh,prior);group.ext[p.PLUGIN_KEY]['geometry_transform']=prior.tolist()
    angle=.4
    delta=np.array([[math.cos(angle),math.sin(angle),0],[-math.sin(angle),math.cos(angle),0],[0,0,1.2],[4,-2,2.]])
    if mirrored:delta[0]*=-1
    _map_mesh(group.mesh,delta)
    transform=QMatrix4x4();transform.translate(1,2,3);transform.rotate(25,1,0,0);transform.scale(1.2,.8,1.)
    group.xform=transform;group.component=False
    session=PathEditSession(scene,group)
    original_local=np.asarray(group.ext[p.PLUGIN_KEY]['path'])
    changed=original_local.copy();changed[1,0]+=.25
    wanted=session.to_world(changed)
    session.move(1,wanted[1]);replacement=session.commit(vp)
    again=PathEditSession(scene,replacement)
    assert max(math.dist(a,b) for a,b in zip(again.points,wanted))<5e-5
    assert replacement.xform is group.xform
    wanted[2]=again.to_world([tuple(changed[2]+(.2,0,0))])[0]
    again.move(2,wanted[2]);twice=again.commit(vp)
    third=PathEditSession(scene,twice)
    assert max(math.dist(a,b) for a,b in zip(third.points,wanted))<1e-4
# Rejections leave the original model and history intact.
for fault in ('duplicate','off_plane','crossing'):
    scene,history,group,vp=fixture('horizontal',True);before=_fingerprint(group)
    session=PathEditSession(scene,group)
    if fault=='duplicate':session.move(1,session.points[0])
    elif fault=='off_plane':session.move(1,(2,0,.1))
    else:session.points=[(0,0,0),(2,1,0),(2,0,0),(0,1,0)]
    try:session.commit(vp)
    except ValueError:pass
    else:raise AssertionError(fault)
    assert _fingerprint(group)==before and not history.undo_stack and scene.groups==[group]
for fault in ('locked','nested','stamp','mixed_paint','topology','local_deformation'):
    scene,history,group,vp=fixture()
    if fault=='locked':group.locked=True
    elif fault=='nested':group.children=[Group(name='child')]
    elif fault=='stamp':group.ext[p.PLUGIN_KEY]['kind']='stamp'
    elif fault=='mixed_paint':group.mesh.faces[0].attrs['color']=(1,0,0)
    elif fault=='topology':group.mesh.faces.pop()
    else:group.mesh.move_vertex(group.mesh.vertices[0],QVector3D(.2,.1,.3))
    before=_fingerprint(group)
    try:PathEditSession(scene,group)
    except ValueError:pass
    else:raise AssertionError(fault)
    assert _fingerprint(group)==before and not history.undo_stack
scene,history,group,vp=fixture();session=PathEditSession(scene,group)
session.move(1,(2.3,0,0));group.name='外部改名'
try:session.commit(vp)
except ValueError:pass
else:raise AssertionError('stale source overwritten')
assert not history.undo_stack and group.name=='外部改名'
# Real Qt dialog and overlay, with input and projection standing in for the
# Windows viewport. The application hooks used were checked in 0.6.0 source.
class Viewport(QWidget):
    def __init__(self,scene,history):
        super().__init__();self.scene=scene;self.history=history;self.active_tool=None;self.messages=[]
        self.setMinimumSize(620,430)
    def _world_to_pixel(self,v):return (80+v.x()*180+v.y()*50,350-v.z()*110-v.y()*130)
    def flash_status(self,text,*a):self.messages.append(text)
    def notify_scene_changed(self):self.update()
    def set_hover(self,entity):pass
    def set_active_tool(self,tool):
        if self.active_tool is not None:self.active_tool.on_deactivate(self)
        self.active_tool=tool
        if tool is not None:tool.on_activate(self)
    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor('#f6f7f8'))
        painter.setPen(QColor('#4a5660'));painter.drawText(16,25,'路徑編輯工具測試畫布（非 Windows OpenGL 畫面）')
        painter.setPen(QPen(QColor('#c3c6c8'),1))
        for group in self.scene.groups:
            for e in group.mesh.edges:
                if e.hidden:continue
                painter.drawLine(QPointF(*self._world_to_pixel(e.v0.position)),QPointF(*self._world_to_pixel(e.v1.position)))
        if isinstance(self.active_tool,ProfilePathEditTool):self.active_tool.draw_overlay(self,painter)
        painter.end()

def ctx(vp,point):return O(world=QVector3D(*point),screen=QPointF(*vp._world_to_pixel(QVector3D(*point))),viewport=vp,modifiers=Qt.NoModifier)
with tempfile.TemporaryDirectory() as data:
    os.environ['APPDATA']=data
    scene,history,group,_=fixture('horizontal',True)
    vp=Viewport(scene,history);host=QMainWindow();host.setCentralWidget(vp)
    errors=[];panel=p.ProfilePanel(O(window=host,viewport=vp,scene=scene));panel.report=errors.append
    before=_fingerprint(group)
    activate_path_edit(panel);tool=vp.active_tool
    assert isinstance(tool,ProfilePathEditTool) and tool.toolbar.isVisible()
    # A plain click selects; drag-release moves without a mode change.
    tool.on_click(ctx(vp,tool.session.points[1]));tool.on_release(vp)
    assert not tool.moving
    tool.on_click(ctx(vp,tool.session.points[1]))
    tool.on_hover(ctx(vp,(2.3,0,0)));tool.on_click(ctx(vp,(2.3,0,0)))
    assert not tool.moving and math.dist(tool.session.points[1],(2.3,0,0))<1e-6
    # Press-drag-release uses the same API as the native Eraser/Move callbacks.
    tool.on_click(ctx(vp,tool.session.points[2]));tool.on_hover(ctx(vp,(2.3,1,.4)));tool.on_release(vp)
    assert not tool.moving and math.dist(tool.session.points[2],(2.3,1,0))<1e-6
    tool.mode_combo.setCurrentIndex(1);tool.on_click(ctx(vp,(1,0,0)))
    assert len(tool.session.points)==5 and tool.moving
    tool.on_click(ctx(vp,(1,-.3,0)));tool.mode_combo.setCurrentIndex(2)
    tool.on_click(ctx(vp,tool.session.points[1]));assert len(tool.session.points)==4
    tool.undo();assert len(tool.session.points)==5
    tool.redo();assert len(tool.session.points)==4
    # Coordinate application, closed/open conversion, and local undo.
    tool.mode_combo.setCurrentIndex(0);tool.on_click(ctx(vp,tool.session.points[1]))
    for control,value in zip(tool.coordinates,(240,0,0)):control.setValue(value)
    tool._apply_coordinates();assert math.isclose(tool.session.points[1][0],2.4)
    tool.closed_box.setChecked(False);assert tool.session.closed is False
    tool.undo();assert tool.session.closed is True
    assert _fingerprint(group)==before and not history.undo_stack
    folder=Path(os.environ.get('PROFILE225_PATH_SCREENSHOTS','/tmp/profile225-path-screenshots'));folder.mkdir(parents=True,exist_ok=True)
    host.show();panel.show();app.processEvents()
    vp.grab().save(str(folder/'path_overlay.png'));tool.dialog.grab().save(str(folder/'path_dialog.png'));panel.grab().save(str(folder/'panel.png'))
    tool.finish();assert not errors,errors
    assert len(history.undo_stack)==1 and not isinstance(vp.active_tool,ProfilePathEditTool)
    replacement=scene.groups[0];assert replacement is not group
    assert history.undo() and scene.groups[0] is group
    assert history.redo() and scene.groups[0] is replacement
    # Switching tools / Esc / closing the dialog all discard pending edits.
    for exit_mode in ('switch','escape','close'):
        scene.selection={replacement};activate_path_edit(panel);tool=vp.active_tool;commands=len(history.undo_stack)
        assert isinstance(tool,ProfilePathEditTool),errors
        tool.session.move(1,(2.7,0,0));fingerprint=_fingerprint(replacement)
        if exit_mode=='switch':
            from tools.select import SelectTool
            vp.set_active_tool(SelectTool())
        elif exit_mode=='escape':tool.on_key(vp,int(Qt.Key_Escape),Qt.NoModifier)
        else:tool.toolbar.reject()
        app.processEvents()
        assert _fingerprint(replacement)==fingerprint and len(history.undo_stack)==commands
        assert scene.groups[0] is replacement
    scene.selection={replacement};activate_path_edit(panel);tool=vp.active_tool
    commands=len(history.undo_stack);fingerprint=_fingerprint(replacement)
    tool.session.move(1,tool.session.points[0]);tool.finish()
    assert not tool.done and tool.toolbar.isVisible() and tool.error
    assert len(history.undo_stack)==commands and _fingerprint(replacement)==fingerprint
    tool.cancel()
    scene.selection=set();activate_path_edit(panel);assert errors and '一個' in errors[-1];errors.clear()
    panel.close();host.close()
print(f'PASS path editing: {count} route/mode variants; transformed placements; attributes; no scene writes before commit; atomic undo/redo; invalid/stale input rejection; real Qt dialog/overlay with simulated spatial input')
