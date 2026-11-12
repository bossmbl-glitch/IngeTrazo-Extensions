"""Real Qt/0.6.0 capture, persistence isolation, generated history and reuse."""
import copy
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace as O
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtGui import QVector3D
from core.scene import Scene
from core.history import History
import ingetrazo_profile_builder_v0_1 as p
from ingetrazo_profile_builder_v0_1.profile_operations import ProfileStampTool, prepare_replacement
from ingetrazo_profile_builder_v0_1.profile_browser import ProfileBrowser

app=QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,APPDATA=directory):
    scene=Scene();history=History(scene);errors=[]
    vp=O(scene=scene,history=history,active_tool=None,flash_status=lambda *a:None,
         update=lambda:None,notify_scene_changed=lambda:None)
    host=QMainWindow()
    panel=p.ProfilePanel(O(window=host,viewport=vp,scene=scene));panel.report=errors.append
    face=scene.mesh.add_face([QVector3D(*v) for v in [(0,0,0),(.01601,0,0),(.01601,0,.13),(0,0,.13)]])
    scene.selection={face}
    source=[v.position.toTuple() for v in face.loop]
    # Any dialog or library write during + is a regression.
    with patch.object(p.QInputDialog,'getText',side_effect=AssertionError('capture asked for a name')):
        panel.save_face()
        first=panel.profile.currentText()
        assert first in panel._temporary_profiles
        assert abs(panel.width.value()-16.01)<.001 and abs(panel.height.value()-130)<.001
        panel.save_face()
        second=panel.profile.currentText()
    assert first!=second and not errors,errors
    assert [v.position.toTuple() for v in face.loop]==source
    assert not scene.groups and not history.undo_stack
    assert not panel.file.exists()
    assert panel.preview.points==panel.profiles[second]
    assert panel.profile_name.text()==second
    # Unrelated save/export/sync paths must never include unsaved captures.
    panel._persist_profiles()
    assert first not in json.loads(panel.file.read_text()) and second not in panel.library_profiles()
    output=Path(directory)/'export.json'
    with patch.object(p.QFileDialog,'getSaveFileName',return_value=(str(output),'')), patch.object(p.QMessageBox,'information'):
        panel.export_library()
        panel.sync_legacy_profiles()
    assert second not in json.loads(output.read_text())
    browser=ProfileBrowser(panel)
    assert second not in [item['name'] for item in browser._profile_items()]
    panel.save_last_used_state()
    assert len(errors)==1 and '另存剖面' in errors.pop()
    assert not panel._preferences_path().exists()
    # Invalid selection is rejected without replacing the active capture.
    scene.selection=set();panel.save_face()
    assert errors and panel.profile.currentText()==second;errors.clear()
    scene.selection={face}
    # Stamp from an unnamed capture: still one full face and independent undo.
    tool=ProfileStampTool(panel)
    for origin in ((1,2,0),(2,3,0)):
        tool.on_click(O(world=QVector3D(*origin),viewport=vp))
    assert not errors and len(scene.groups)==2 and len(history.undo_stack)==2,errors
    assert all(len(g.mesh.faces)==1 and g.ext[p.PLUGIN_KEY]['temporary_profile'] for g in scene.groups)
    assert history.undo() and len(scene.groups)==1
    assert history.redo() and len(scene.groups)==2
    stamp=scene.groups[0]
    # A normal swept group also carries its full section and remains undoable.
    assert panel._produce([([(0,0,0),(1,0,0),(1,1,0)],False)],mouse_draw=True,
                          mouse_orientation={'horizontal':True,'fixed_side':1})
    swept=scene.groups[-1];recipe=copy.deepcopy(swept.ext[p.PLUGIN_KEY])
    assert recipe['temporary_profile'] and recipe['section']==[list(pt) for pt in panel.profiles[second]]
    assert history.undo() and swept not in scene.groups
    assert history.redo() and swept in scene.groups
    # A fresh panel cannot restore an unsaved capture from disk; sampling a
    # generated object restores it in memory without promoting it to a library.
    reopened=p.ProfilePanel(O(window=host,viewport=vp,scene=scene));reopened.report=errors.append
    assert second not in reopened.profiles
    ok,message=reopened.apply_picked_group(swept);assert ok,message
    assert reopened.profile.currentText() in reopened._temporary_profiles
    reopened._persist_profiles()
    assert second not in json.loads(reopened.file.read_text())
    current={'profile':second,'section':panel.profiles[second],'settings':panel.opts(),
             'material':'預設材質','tag':'預設標籤','temporary_profile':True}
    edited=prepare_replacement(stamp,current,{'profile'},scene)
    assert len(edited.mesh.faces)==1 and edited.ext[p.PLUGIN_KEY]['temporary_profile']
    # Cancel leaves all files and session state intact; explicit save persists
    # only the chosen named section, even when several captures exist.
    before=panel.file.read_bytes()
    with patch.object(p.QInputDialog,'getText',return_value=('',False)):
        panel.save_current_profile()
    assert panel.file.read_bytes()==before and panel.profile.currentText()==second
    with patch.object(p.QInputDialog,'getText',return_value=('我的剖面',True)), patch.object(p.QMessageBox,'information'):
        panel.save_current_profile()
    assert not errors,errors
    assert panel.profile.currentText()=='我的剖面'
    assert '我的剖面' not in panel._temporary_profiles
    saved=json.loads(panel.file.read_text())
    assert set(saved)=={'我的剖面'} and saved['我的剖面']==[list(pt) for pt in panel.profiles[second]]
    assert (Path(directory)/'ingetrazo/quick_profile/library/我的剖面.profile.json').is_file()
    # Sampling a temporary group with identical geometry must not turn an
    # explicitly saved same-name entry back into a temporary library entry.
    named=copy.copy(swept);named.ext=copy.deepcopy(swept.ext)
    named.ext[p.PLUGIN_KEY]['profile']='我的剖面'
    assert panel.apply_picked_group(named)[0]
    assert '我的剖面' in panel.library_profiles()
    browser.close();panel.close();reopened.close();host.close()
print('PASS v2.2.4 real Qt/core: immediate capture, dimensions, no prompts/writes, library isolation, explicit save/cancel, stamp/sweep undo/redo, restart sampling and edit')
