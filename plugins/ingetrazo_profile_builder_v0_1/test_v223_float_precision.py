"""Regression: real-world placement coordinates must survive float32 storage."""
from PySide6.QtGui import QVector3D
import ingetrazo_profile_builder_v0_1 as p
from ingetrazo_profile_builder_v0_1.profile_operations import recipe_faces,build_mesh
for offset in (0.,5.884974479675293,6.714974880218506,20.,100.):
 for x in range(125):
  pt=(offset+x*.0000163815,5.140038967132568+x*.000030125,0.13+x*.000017)
  assert p._mesh_vertex_key(pt)==p._mesh_vertex_key(QVector3D(*pt))
# Dense curved section at the user's approximate drawing location.
import math
section=[(0,0),(17,0),(17,3)]+[(13-10*math.sin(i*math.pi/240),13-10*math.cos(i*math.pi/240)) for i in range(121)]+[(3,17),(0,17)]
r=dict(profile='dense',section=section,path=[(6.714974880218506,5.140038967132568,0),(6.714974880218506,6.650038719177246,0),(5.884974479675293,6.650038719177246,0)],closed=False,settings=dict(width_mm=16.01,height_mm=130,rotate_deg=0,mirror=False,off_u_mm=0,off_v_mm=0,miter=True,anchor='左下',closed_side='內側',reverse_depth=True),mouse_orientation=dict(horizontal=True,fixed_side=1),mouse_draw=True)
raw=recipe_faces(r);tri=p.mesh_ready_faces(raw);hidden,along=p.edge_display_rules(raw,tri,False)
mesh=build_mesh(r)
actual={p._mesh_edge_key(e.v0.position,e.v1.position):e for e in mesh.edges}
assert hidden<=actual.keys(),'raw double precision diagonal failed to match float32 edge'
assert along<=actual.keys(),'raw double precision curve seam failed to match float32 edge'
assert all(actual[k].hidden and actual[k].soft for k in hidden)
print('PASS v2.2.3: float64/float32 keys at origin and 5–100m; dense profile diagonal and curve edge classification complete')
