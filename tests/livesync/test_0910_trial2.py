"""Trial 2 regression checks with upstream v0.6.0; no native D5 SDK required."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import test_0910 as base
from test_0910 import group
from core.history import ReshareInstanceCommand,MoveGroupCommand
from core.saved_views import SavedView
from PySide6.QtGui import QMatrix4x4,QVector3D
from formats.igz import save_scene
from ingetrazo_d5_livesync.renderer_watch import RendererWatch
from ingetrazo_d5_livesync.host_activity import preview_active
from ingetrazo_d5_livesync.status_model import StatusModel

class Trial2Tests(unittest.TestCase):
    setUp=base.LiveSyncTests.setUp
    def baseline(self):return self.c.capture(with_dependencies=True,allow_edit=True)
    execute=base.LiveSyncTests.execute
    paint=base.LiveSyncTests.paint
    session=base.LiveSyncTests.session

    def test_camera_navigation_no_export(self):
        session,state=self.session()
        for yaw in (0,1,2):
            self.s.camera_home={'yaw':yaw,'target':[0,0,0]}
            forbidden=Mock(side_effect=AssertionError('navigation exported'))
            result=session.publish(self.s,forbidden,on_export=forbidden)
            self.assertEqual(result['kind'],'skipped');forbidden.assert_not_called()
        self.assertEqual(session.revision,1)

    def test_saved_view_remains_a_real_global_change(self):
        session,state=self.session();self.s.saved_views=[SavedView('saved',yaw=2)]
        result=session.publish(self.s,save_scene)
        self.assertEqual(result['kind'],'full')
        self.assertIn('saved_views',result['sync_decision']['skip_check']['environment_fields'])

    def test_empty_partial_no_export_and_rebase_survives_ack(self):
        session,state=self.session();session.partial_enabled=True
        self.s.begin_group_edit(self.g)
        forbidden=Mock(side_effect=AssertionError('empty exported'))
        result=session.publish(self.s,forbidden,on_export=forbidden)
        self.assertEqual(result['sync_decision']['reason'],'certified_tracking_only')
        self.assertEqual(session.revision,1);self.assertFalse(session.pending)
        session.acknowledge(state)
        self.assertEqual(session.publish(self.s,forbidden)['kind'],'skipped')
        forbidden.assert_not_called()
        self.execute(self.paint([self.s.mesh.faces[0]]))
        self.assertEqual(session.publish(self.s,save_scene)['kind'],'partial')

    def test_manual_full_never_uses_empty_skip(self):
        session,state=self.session();session.partial_enabled=True;self.s.begin_group_edit(self.g)
        self.assertEqual(session.publish(self.s,save_scene,force_full=True)['kind'],'full')

    def test_edit_face_paint_then_exit_undo_redo(self):
        self.s.begin_group_edit(self.g);b=self.baseline()
        self.execute(self.paint([self.s.mesh.faces[0]]))
        self.assertTrue(self.c.trace[-1]['paint_certified'])
        self.assertEqual(len(self.c.partial_plan(b)[0]),1)
        self.s.end_group_edit()
        for action in (self.h.undo,self.h.redo):
            b=self.baseline();action()
            self.assertEqual(len(self.c.partial_plan(b)[0]),1)

    def test_instance_edit_paint_share_undo_redo(self):
        self.g.xform=QMatrix4x4();self.g.xform.translate(2,0,0)
        self.other.mesh=self.g.mesh;self.other.xform=QMatrix4x4()
        self.s.begin_group_edit(self.g);b=self.baseline()
        self.execute(self.paint([self.s.mesh.faces[0]]))
        self.assertEqual(len(self.c.partial_plan(b)[0]),1)
        shared=self.s.take_edit_share();edited=self.g.mesh
        self.s.end_one_group_edit()
        command=ReshareInstanceCommand(shared[0],shared[1],shared[2],edited)
        self.execute(command)
        self.assertEqual(len(self.c.partial_plan(b)[0]),2)
        for action in (self.h.undo,self.h.redo):
            before=self.baseline();action()
            self.assertEqual(len(self.c.partial_plan(before)[0]),2)

    def test_dependency_failure_is_explained(self):
        b=self.baseline();b['dependencies']={str(self.directory/'lost.png'):None}
        report=self.c.unchanged_report(b)
        self.assertFalse(report['unchanged']);self.assertEqual(report['dependencies'][0]['reason'],'unavailable')

    def test_real_geometry_still_not_skipped(self):
        session,state=self.session();session.partial_enabled=True
        self.execute(MoveGroupCommand(self.g,QVector3D(1,0,0)))
        result=session.publish(self.s,save_scene)
        self.assertEqual(result['kind'],'partial')
        self.assertIn('groups',result['sync_decision']['skip_check']['changed_checks'])

    def test_edit_paint_serialized_patch_preserves_hold_and_uids(self):
        import hashlib
        from contextlib import closing
        from ingetrazo_d5_livesync.layer_scope import compose,Bundle
        from ingetrazo_d5_livesync.local_update import prepare_local
        from ingetrazo_d5_livesync.motion_delta import PatchCache
        self.g.xform=QMatrix4x4();self.g.xform.translate(2,0,0)
        self.other.mesh=self.g.mesh;self.other.xform=QMatrix4x4();self.other.layer='hold'
        self.s.plugin_data={'ingetrazo_d5_livesync':{'layer_sync_modes_v1':{'hold':'hold'}}}
        session,state=self.session();session.partial_enabled=True
        initial=self.directory/'initial.igz';baseline=self.directory/'baseline.igz'
        compose(session.directory/'snapshot-00000001.igz',None,{},initial)
        compose(session.directory/'snapshot-00000001.igz',initial,{'hold':'hold'},baseline)
        session.motion_base_hash=hashlib.sha256(baseline.read_bytes()).hexdigest()
        self.s.begin_group_edit(self.g);self.execute(self.paint([self.s.mesh.faces[0]]))
        shared=self.s.take_edit_share();edited=self.g.mesh;self.s.end_one_group_edit()
        self.execute(ReshareInstanceCommand(shared[0],shared[1],shared[2],edited))
        result=session.publish(self.s,save_scene);self.assertEqual(result['kind'],'partial')
        output=self.directory/'result.igz'
        prepare_local(baseline,session.directory/result['file'],1,output,PatchCache())
        with closing(Bundle(baseline)) as a,closing(Bundle(output)) as b:
            before={g['uid']:g for g in a.scene['groups']};after={g['uid']:g for g in b.scene['groups']}
            self.assertEqual(set(before),set(after))
            self.assertEqual(before[self.other.uid],after[self.other.uid])
            self.assertNotEqual(before[self.g.uid],after[self.g.uid])

class GuardTests(unittest.TestCase):
    def test_watch_waits_for_observed_renderer_then_closes(self):
        probe=Mock(side_effect=[set(),{42},set(),set(),set()]);watch=RendererWatch(probe)
        self.assertFalse(watch.closed('d5',0));self.assertFalse(watch.closed('d5',1))
        self.assertFalse(watch.closed('d5',2));self.assertFalse(watch.closed('d5',3))
        self.assertTrue(watch.closed('d5',4))

    def test_probe_unknown_and_busy_renderer_never_stop(self):
        watch=RendererWatch(Mock(side_effect=[{42},None,set(),{42},None]))
        for now in range(5):self.assertFalse(watch.closed('d5',now))

    def test_preview_between_clicks_blocks_even_without_mouse_button(self):
        for tool in (SimpleNamespace(start_point=QVector3D()),SimpleNamespace(_grip=object()),SimpleNamespace(dragging=True)):
            self.assertTrue(preview_active(SimpleNamespace(active_tool=tool)))
        self.assertFalse(preview_active(SimpleNamespace(active_tool=SimpleNamespace(_grip=None,dragging=False))))

    def test_stopped_ui_wins_over_stale_sending_receipt(self):
        model=StatusModel();model.begin(2,0);model.failure('old error');model.stop(5,'D5 已關閉')
        card=model.view(6,state={'phase':'sending','acknowledged_revision':1},session_revision=2,running=False,alive=True)
        self.assertEqual(card['kind'],'stopped');self.assertIn('D5 已關閉',card['detail'])

class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt=QApplication.instance() or QApplication([])

    setUp=base.LiveSyncTests.setUp
    baseline=base.LiveSyncTests.baseline
    session=base.LiveSyncTests.session

    def panel(self):
        from ingetrazo_d5_livesync.panel import SyncPanel
        from ingetrazo_d5_livesync.perf_diagnostics import PerfRecorder
        session,state=self.session()
        app=SimpleNamespace(scene=self.s,viewport=SimpleNamespace(active_tool=SimpleNamespace(_grip=None)),
                            on_document_changed=lambda callback:None,on_document_closed=lambda callback:None)
        panel=SyncPanel(app);panel.session=session;panel.running=True
        panel.process=Mock();panel.process.poll.return_value=None
        panel.bound_scene=self.s;panel.bound_mesh=self.s.loose_mesh
        panel.motion_certificate=self.c;panel.source_recorder=PerfRecorder(session.directory,journal='source-events.jsonl')
        def cleanup():
            panel.process=None;panel.timer.stop();panel.progress.timer.stop();panel.close();panel.deleteLater()
        self.addCleanup(cleanup)
        return panel

    def test_d5_close_stops_panel_without_acknowledging_pending(self):
        panel=self.panel();panel.session.publish(self.s,save_scene,force_full=True)
        panel.renderer_watch=SimpleNamespace(closed=lambda *args:True)
        panel.tick()
        self.assertFalse(panel.running);self.assertTrue(panel.cancelled)
        self.assertEqual(panel.session.acknowledged,1);self.assertEqual(panel.session.revision,2)
        self.assertTrue(panel.session.pending);self.assertTrue(panel.schedule.dirty)
        self.assertEqual(panel.progress.last_card['kind'],'stopped')
        self.assertIn('D5 已關閉',panel.status.text())
        panel.tick();self.assertIn('D5 已關閉',panel.status.text())

    def test_panel_defers_manual_export_until_preview_commits(self):
        panel=self.panel();panel.app.viewport.active_tool._grip=object()
        panel.schedule.request();panel.publish=Mock()
        panel.tick();panel.publish.assert_not_called()
        self.assertTrue(panel.schedule.manual)
        panel.app.viewport.active_tool._grip=None
        panel.tick();panel.publish.assert_called_once()
