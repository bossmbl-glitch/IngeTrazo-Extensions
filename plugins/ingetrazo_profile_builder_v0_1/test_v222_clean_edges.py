"""Real meshes: hide splits, soften curvature, retain steps/miters and copy flags."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtGui import QMatrix4x4
from core.group import transformed_mesh
import ingetrazo_profile_builder_v0_1 as p
from ingetrazo_profile_builder_v0_1.profile_operations import build_mesh,recipe_faces
for profile in ('058｜弧形線板','012｜階梯線板'):
 for closed in (False,True):
  r=dict(profile=profile,path=[(0,0,0),(2,0,0),(2,1,0),(0,1,0)],closed=closed,section=p.PRESETS[profile],settings=dict(width_mm=16.01,height_mm=130,rotate_deg=0,mirror=False,off_u_mm=0,off_v_mm=0,miter=True,anchor='左下',closed_side='內側',reverse_depth=True),mouse_orientation=dict(horizontal=True,fixed_side=1),mouse_draw=True)
  raw=recipe_faces(r);tri=p.mesh_ready_faces(raw)
  hidden,along=p.edge_display_rules(raw,tri,closed);mesh=build_mesh(r)
  ring={p._mesh_edge_key(q[0],q[1]) for q in raw[:len(raw)-(0 if closed else 2)]}
  assert any(e.hidden for e in mesh.edges)
  for e in mesh.edges:
   key=p._mesh_edge_key(e.v0.position,e.v1.position)
   if key in hidden and len(e.faces)==2:assert e.hidden and e.soft
   if key in ring:assert not e.hidden and not e.soft,'miter or section edge removed'
   if len(e.faces)!=2:assert not e.hidden and not e.soft,'open end hidden'
  copy=transformed_mesh(mesh,QMatrix4x4())
  bykey={p._mesh_edge_key(e.v0.position,e.v1.position):e for e in copy.edges}
  assert all(bykey[p._mesh_edge_key(e.v0.position,e.v1.position)].soft for e in mesh.edges if e.hidden),'group copy exposes hard diagonals'
  if profile.startswith('058'):assert any(e.soft and not e.hidden for e in mesh.edges)
# Curvature interval that previously remained black now becomes soft.
class Face:
 def __init__(self,n):self.n=n
 def normal(self):return p.qvec(self.n)
class Vertex:
 def __init__(self,v):self.position=p.qvec(v)
class Edge:
 def __init__(self):
  self.v0=Vertex((0,0,0));self.v1=Vertex((1,0,0));self.faces=[Face((0,0,1)),Face((0,.422618,.906308))];self.soft=False;self.hidden=False
class Mesh:pass
m=Mesh();e=Edge();m.edges=[e]
p.apply_edge_display(m,set(),{p._mesh_edge_key(e.v0.position,e.v1.position)})
assert e.soft and not e.hidden
print('PASS v2.2.2: curve seams soft, diagonals hidden+soft through group copy, stepped features/miter/end rims retained')
