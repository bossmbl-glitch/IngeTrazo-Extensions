"""Real 0.6.0 mesh and Push/Pull regressions for stamp topology."""
import os,math,copy
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QVector3D
from core.scene import Scene
from core.group import Group
from core.orient import is_closed,signed_volume
from tests.test_pushpull_ux import _push_group
import ingetrazo_profile_builder_v0_1 as p
from ingetrazo_profile_builder_v0_1.profile_operations import build_mesh,prepare_replacement
app=QApplication.instance() or QApplication([])
settings=dict(width_mm=170,height_mm=170,rotate_deg=0,mirror=False,off_u_mm=0,off_v_mm=0,miter=True,anchor='左下',closed_side='內側',reverse_depth=True)
count=0
for name,shape in p.PRESETS.items():
 for normal in ((0,0,1),(0,-1,0),(1,0,0)):
  for mirror in (False,True):
   recipe=dict(kind='stamp',stamp_topology='single_face_v1',profile=name,section=shape,settings=dict(settings,mirror=mirror),origin=(0,0,0),normal=normal,material='預設材質',tag='預設標籤')
   mesh=build_mesh(recipe)
   assert len(mesh.faces)==1,'stamp split into separate faces'
   assert len(mesh.edges)==len(p.clean_polygon(shape))
   assert all(len(e.faces)==1 and not e.hidden for e in mesh.edges),'internal editable edges remain'
   assert mesh.faces[0].triangulate(),'concave rendering triangulation failed'
   scene=Scene();group=Group(mesh,'stamp');group.ext={p.PLUGIN_KEY:recipe};scene.groups.append(group)
   vp=_push_group(scene,group,mesh.faces[0],.3)
   assert vp.last_status is None,vp.last_status
   assert is_closed(group.mesh),'push/pull did not make a closed solid'
   assert all(len(e.faces)==2 for e in group.mesh.edges)
   assert abs(signed_volume(group.mesh))>1e-6
   count+=1
# Upgrade a real v2.2.0 triangulated stamp, preserving moved position.
recipe=dict(kind='stamp',profile='old',section=p.PRESETS['058｜弧形線板'],settings=settings,origin=(1,2,0),normal=(0,0,1),material='預設材質',tag='預設標籤')
oldmesh=build_mesh(recipe,legacy_stamp=True);assert len(oldmesh.faces)>1
for v in list(oldmesh.vertices):oldmesh.move_vertex(v,QVector3D(3,4,0))
group=Group(oldmesh,'old');group.ext={p.PLUGIN_KEY:recipe}
current={k:copy.deepcopy(recipe[k]) for k in ('profile','section','settings','material','tag')}
new=prepare_replacement(group,current,{'profile'},Scene())
assert len(new.mesh.faces)==1
assert new.ext[p.PLUGIN_KEY]['stamp_topology']=='single_face_v1'
assert all(v.position.x()>=4-1e-5 and v.position.y()>=6-1e-5 for v in new.mesh.vertices)
assert len(group.mesh.faces)>1,'upgrade mutated original before commit'
print(f'PASS v2.2.1: {count} single-face stamp / real group Push-Pull solid cases; upgrade moved v2.2.0 stamp')
