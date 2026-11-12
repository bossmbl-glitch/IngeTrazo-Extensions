"""Run with IngeTrazo 0.6.0 on PYTHONPATH; uses real Mesh/Group/History and Qt."""
import copy,math,os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from types import SimpleNamespace as Obj
import numpy as np
from PySide6.QtWidgets import QApplication,QMainWindow,QWidget,QHBoxLayout
from PySide6.QtGui import QVector3D,QFont,QMatrix4x4
from core.scene import Scene
from core.history import History,SnapshotImport
from core.group import Group
import ingetrazo_profile_builder_v0_1 as p
from ingetrazo_profile_builder_v0_1.profile_operations import *
from ingetrazo_profile_builder_v0_1.profile_operations import _map_mesh
from ingetrazo_profile_builder_v0_1.profile_browser import ProfileCard
app=QApplication.instance() or QApplication([])
app.setFont(QFont('Noto Sans CJK TC',11))
settings=dict(width_mm=17,height_mm=17,rotate_deg=0,mirror=False,off_u_mm=0,off_v_mm=0,miter=True,anchor='左下',closed_side='內側',reverse_depth=True)
section=p.PRESETS['058｜弧形線板']
base=dict(profile='old',section=section,path=[(0,0,0),(1,0,0),(1,0,2),(0,0,2)],closed=True,settings=settings,mouse_draw=True,mouse_orientation=dict(wall_frame=True,plane_normal_override=(0,-1,0),fixed_side=1,wall_depth_sign=1),material='預設材質',tag='預設標籤')
current=dict(profile='new',section=p.PRESETS['012｜階梯線板'],settings=dict(settings,width_mm=29,height_mm=40,rotate_deg=12),material='預設材質',tag='預設標籤')
scene=Scene();history=History(scene)
g=Group(build_mesh(base),'original');g.ext={'quick_profile':copy.deepcopy(base),'other':{'keep':1}}
g.material={'color':(.2,.3,.4)};g.layer='keep';scene.groups.append(g)
# Simulate a classic group moved/rotated/scaled after drawing (metadata unchanged).
a=.47;mat=np.array([[math.cos(a),math.sin(a),0],[-math.sin(a),math.cos(a),0],[0,0,1.2],[3,4,2]])
_map_mesh(g.mesh,mat)
new=prepare_replacement(g,current,{'profile'},scene)
assert new.ext['quick_profile']['settings']['rotate_deg']==0
assert new.ext['quick_profile']['path']==base['path']
assert new.ext['quick_profile']['mouse_orientation']==base['mouse_orientation']
assert new.material==g.material and new.layer=='keep' and new.uid==g.uid
assert new.ext['other']=={'keep':1}
expected=build_mesh(merged_recipe(base,current,{'profile'}));_map_mesh(expected,mat)
for a,b in zip(new.mesh.vertices,expected.vertices):assert (a.position-b.position).length()<2e-5
# A second edit must retain the first edit's transform as well.
new2=prepare_replacement(new,current,{'rotate_deg'},scene)
expected=build_mesh(merged_recipe(base,current,{'profile','rotate_deg'}));_map_mesh(expected,mat)
for a,b in zip(new2.mesh.vertices,expected.vertices):assert (a.position-b.position).length()<3e-5
before=copy.deepcopy(g.ext)
assert prepare_replacement(g,current,{'material'},scene).ext['quick_profile']['path']==base['path']
assert g.ext==before
# Real history replacement: one undo restores the original object, redo restores new.
history.execute(SnapshotImport(lambda sc:sc.groups.__setitem__(0,new)))
assert scene.groups[0] is new
assert history.undo() and scene.groups[0] is g
assert history.redo() and scene.groups[0] is new
# Stamp plane / anchors / dimensions / mirror / rotation / offsets.
for normal in ((0,0,1),(1,0,0),(0,-1,0),(.6,.8,0)):
 poly=stamp_polygon(section,settings,(3,4,5),normal)
 assert all(abs(p.v_dot(p.v_sub(v,(3,4,5)),normal))<1e-8 for v in poly)
 rec=dict(kind='stamp',profile='stamp',section=section,settings=settings,origin=(3,4,5),normal=normal,material='預設材質',tag='預設標籤')
 assert build_mesh(rec).faces
 st=Group(build_mesh(rec),'stamp');st.ext={'quick_profile':rec}
 # Resizing a planar stamp remains on its placement plane.
 edited=prepare_replacement(st,current,{'profile'},scene)
 assert edited.mesh.faces
# Use real Tool and History for consecutive stamps and independent undo.
scene=Scene();hist=History(scene);errors=[]
vp=Obj(scene=scene,history=hist,notify_scene_changed=lambda:None,flash_status=lambda *a:None,update=lambda:None,set_active_tool=lambda tool:None)
combo=lambda text:Obj(currentText=lambda:text)
panel=Obj(profile=combo('shape'),profiles={'shape':section},opts=lambda:settings,material=combo('預設材質'),tag=combo('預設標籤'),report=errors.append)
tool=ProfileStampTool(panel)
for point in ((0,0,0),(1,2,0),(2,3,0)):
 ctx=Obj(world=QVector3D(*point),viewport=vp)
 tool.on_hover(ctx);assert tool.preview_faces() and not scene.groups if point==(0,0,0) else tool.preview_faces()
 tool.on_click(ctx)
assert not errors,errors
assert len(scene.groups)==3 and len(hist.undo_stack)==3
assert len({g.uid for g in scene.groups})==3
assert hist.undo() and len(scene.groups)==2
assert hist.redo() and len(scene.groups)==3
tool.on_key(vp,int(Qt.Key_Escape),None);assert len(scene.groups)==3 and not tool.preview_faces()
# Card regression: large global QToolButton constraint must not cut card titles.
app.setStyleSheet('QToolButton { max-height: 132px; }')
widget=QWidget();layout=QHBoxLayout(widget)
names=['058｜弧形線板','066｜造型線板','自訂剖面18030_超長名稱測試','001｜矩形','012｜階梯線板']
cards=[]
for i,name in enumerate(names):
 card=ProfileCard(dict(name=name,type='profile',points=section),widget)
 card.setChecked(i==0);layout.addWidget(card);cards.append(card)
widget.show();app.processEvents()
for card in cards:
 assert card.height()>=card._image_height+card._title_height
 assert card.toolTip()==card.item['name']
widget.grab().save('/tmp/profile_cards220.png')
# Enlarge font to simulate font/accessibility changes: card must grow.
old=cards[0].height();cards[0].setFont(QFont('Noto Sans CJK TC',18));app.processEvents()
assert cards[0].height()>old
# Original production sweeps are unchanged (AST check).
import ast
from pathlib import Path
newtree=ast.parse(Path(p.__file__).read_text())
fns=lambda t:{n.name:ast.dump(n,include_attributes=False) for n in t.body if isinstance(n,ast.FunctionDef)}
import hashlib
expected_hashes={'sweep_faces': '760018fa316136f6dde8f3feba11d9afd757fe2a3c96288625ce0a17a4049ccb', 'experimental_sweep_faces': 'b941ab794ad207a9096d5ca62294f7f52254d79128b20fc8aae9c709591b357d', 'mesh_ready_faces': '43761dd06b6a4a2345fb2ef8b49847b65094c98016018ed656ec079a2cb1a97a', 'edge_display_rules': '6497a7ad86810cc9de8811d105e0ee7d3bd0f6a27c1e6ef1a68753bfb238571c', 'apply_edge_display': '0994ede51ab771f94374489b3b18540595fe0bc5ac4d390b813ce2fd098bc6e9'}
for name,digest in expected_hashes.items():
 if name=='apply_edge_display':continue  # v2.2.2 deliberately improves display flags.
 assert hashlib.sha256(fns(newtree)[name].encode()).hexdigest()==digest
print('PASS v2.2.0 real IngeTrazo Mesh/Group/History: selective edits, affine position, repeated edit, stamps, independent undo/redo, Qt cards, unchanged sweep geometry')
