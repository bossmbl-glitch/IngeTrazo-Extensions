import os,copy
os.environ['QT_QPA_PLATFORM']='offscreen';os.environ.setdefault('APPDATA','/tmp/profile220-modal')
from PySide6.QtWidgets import QApplication,QMainWindow,QDialog,QCheckBox
from PySide6.QtCore import QTimer
from types import SimpleNamespace as O
from core.scene import Scene
from core.group import Group
from core.history import History
import ingetrazo_profile_builder_v0_1 as p
from ingetrazo_profile_builder_v0_1.profile_operations import build_mesh,edit_selected_profiles
app=QApplication([]);sc=Scene();h=History(sc);errors=[]
vp=O(scene=sc,history=h,active_tool=None,flash_status=lambda *a:None,update=lambda:None,notify_scene_changed=lambda:None)
panel=p.ProfilePanel(O(window=QMainWindow(),viewport=vp,scene=sc));panel.report=errors.append
recipe=dict(profile='orig',section=p.PRESETS['058｜弧形線板'],path=[(0,0,0),(1,0,0),(1,0,1)],closed=False,settings=panel.opts(),mouse_orientation={'wall_frame':True,'plane_normal_override':(0,-1,0),'fixed_side':1,'wall_depth_sign':1},material='預設材質',tag='預設標籤')
g=Group(build_mesh(recipe),'original');g.ext={p.PLUGIN_KEY:recipe};sc.groups.append(g);sc.selection={g}
panel.profile.setCurrentText('012｜階梯線板')
def accept():
 d=app.activeModalWidget();assert isinstance(d,QDialog)
 # Two columns; labels legible and all ten options present.
 assert len(d.findChildren(QCheckBox))==10
 d.grab().save('/tmp/profile_edit220.png');d.accept()
QTimer.singleShot(50,accept);edit_selected_profiles(panel)
assert not errors,errors
assert len(sc.groups)==1 and sc.groups[0].ext[p.PLUGIN_KEY]['profile']=='012｜階梯線板'
assert len(h.undo_stack)==1
assert h.undo() and sc.groups[0] is g
# Cancelling dialog must not append a command or mutate objects.
sc.selection={g};commands=len(h.undo_stack)
QTimer.singleShot(30,lambda:app.activeModalWidget().reject());edit_selected_profiles(panel)
assert sc.groups[0] is g and len(h.undo_stack)==commands
print('PASS actual edit-dialog accept/cancel and history integration')
