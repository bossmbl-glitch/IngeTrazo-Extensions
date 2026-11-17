"""Integrated safety regressions on actual files and native host serialization."""
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import test_material_identity as material_tests
import test_bridge_backups as backup_tests
import test_0910 as host_tests
from ingetrazo_d5_livesync import bridge_backups as b
from ingetrazo_d5_livesync.backup_schedule import due,half_hour,save_settings,settings
from ingetrazo_d5_livesync.backup_service import BackupService,capture
from ingetrazo_d5_livesync.layer_scope import compose,META,MATERIAL_BINDINGS
from ingetrazo_d5_livesync.named_materials import KEY,MaterialConflict
from ingetrazo_d5_livesync.visibility import VISIBLE,LOOSE
from ingetrazo_d5_livesync.visibility_delta import prepare,certify
from ingetrazo_d5_livesync.motion_delta import PatchCache,DeltaRejected
from ingetrazo_d5_livesync.transport import SnapshotSession,write_json
from ingetrazo_d5_livesync.schedule import RecoveryGate
from ingetrazo_d5_livesync.renderer_watch import renderer_window
from formats.igz import save_scene

class StableNameTests(unittest.TestCase):
    setUp=material_tests.CompositionTests.setUp
    write=material_tests.CompositionTests.write
    read=material_tests.CompositionTests.read
    refs=material_tests.CompositionTests.refs
    def step(self,source,baseline=None,policy=None):
        self.serial+=1;output=self.root/f'stable-{self.serial}.igz'
        compose(source,baseline,policy or {},output,stable_names=True);return output

    def test_color_and_definition_change_preserve_named_identity_and_old_visual(self):
        source=self.write(self.source);base=self.step(source)
        altered=copy.deepcopy(self.source);altered['scene']['materials'][0]['finish']='glass'
        for g in altered['scene']['groups']:g['faces'][0]['color']=[1,0,0]
        result=self.step(self.write(altered),base)
        self.assertEqual(self.refs(result),self.refs(base))
        before=self.read(base);after=self.read(result)
        self.assertEqual(after['scene']['materials'],before['scene']['materials'])
        self.assertEqual(after['scene']['groups'][0]['faces'],before['scene']['groups'][0]['faces'])
        self.assertEqual(list(after[META][KEY]['Shared']['targets']),['Shared'])

    def test_texture_replacement_keeps_old_bytes_and_current_uv(self):
        import zipfile
        source=copy.deepcopy(self.source)
        for g in source['scene']['groups']:g['faces'][0]['texture']={'embed':'textures/wood.png','uvw':[1,0,0,0,0,1,0,0]}
        base=self.step(self.write(source,{'textures/wood.png':b'old wood'}))
        for g in source['scene']['groups']:g['faces'][0]['texture']['uvw'][3]=42
        result=self.step(self.write(source,{'textures/wood.png':b'new wood'}),base)
        data=self.read(result)
        with zipfile.ZipFile(result) as z:
            for g in data['scene']['groups']:
                tex=g['faces'][0]['texture'];self.assertEqual(z.read(tex['embed']),b'old wood');self.assertEqual(tex['uvw'][3],42)
        self.assertEqual(self.refs(result),self.refs(base))

    def test_old_raw_and_scoped_bindings_not_merged_by_new_name(self):
        src,base,unused=material_tests.CompositionTests.legacy(self)
        original=self.refs(base);result=self.step(src,base)
        self.assertEqual(self.refs(result),original)
        self.assertEqual(len(self.read(result)[META][KEY]['Shared']['targets']),2)
        # Existing scoped owners can stay distinct; an unbound new owner
        # cannot guess between two different historical D5 overrides.
        changed=copy.deepcopy(self.source)
        changed['scene']['groups'][0]['faces'][0]['color']=[1,0,0]
        preserved=self.step(self.write(changed),result)
        self.assertEqual(self.refs(preserved),original)
        changed['scene']['groups'].append({'uid':'new','layer':'B','faces':[material_tests.face()]})
        with self.assertRaises(MaterialConflict):self.step(self.write(changed),preserved)

    def test_legacy_upgrade_with_simultaneous_paint_cannot_merge_scoped_branch(self):
        src,base,unused=material_tests.CompositionTests.legacy(self)
        changed=copy.deepcopy(self.source);changed['scene']['groups'][1]['faces'][0]['color']=[1,0,0]
        with self.assertRaises(MaterialConflict):self.step(self.write(changed),base)

    def test_rename_explicitly_creates_new_identity_keeps_old_registry(self):
        src=self.write(self.source);base=self.step(src)
        changed=copy.deepcopy(self.source)
        changed['scene']['materials']=[{'name':'Shared-New'}]
        for g in changed['scene']['groups']:g['faces'][0]['mat']='Shared-New'
        result=self.step(self.write(changed),base)
        self.assertEqual(set(self.refs(result).values()),{'Shared-New'})
        self.assertIn('Shared',{m['name'] for m in self.read(result)['scene']['materials']})

    def test_hold_and_live_named_material_share_confirmed_appearance(self):
        src=self.write(self.source);base=self.step(src)
        changed=copy.deepcopy(self.source);changed['scene']['layers'][0]['visible']=False
        changed['scene']['groups'][1]['faces'][0]['color']=[0,0,1]
        result=self.step(self.write(changed),base,{'A':'hold'})
        self.assertEqual(self.refs(result),self.refs(base));self.assertTrue(self.read(result)['scene']['groups'][0][VISIBLE])

    def test_bad_name_ledger_refuses_upgrade(self):
        src=self.write(self.source);base=self.step(src);data=self.read(base);data[META][KEY]={'Shared':{'targets':[]}}
        with self.assertRaisesRegex(ValueError,'對應表'):self.step(src,self.write(data))

    def test_exclude_restore_keeps_dormant_name_targets(self):
        src=self.write(self.source);base=self.step(src)
        for policy in ({'A':'exclude','B':'exclude'},{},{'B':'hold'},{}):base=self.step(src,base,policy)
        self.assertEqual(set(self.refs(base).values()),{'Shared'})
        self.assertEqual(list(self.read(base)[META][KEY]['Shared']['targets']),['Shared'])

class VisibilityDeltaTests(unittest.TestCase):
    setUp=host_tests.LiveSyncTests.setUp
    def session(self):
        session=SnapshotSession(self.directory);session.motion_certificate=self.c
        first=session.publish(self.s,save_scene);base=self.directory/'confirmed.igz'
        compose(session.directory/first['file'],None,{},base,stable_names=True)
        sha=hashlib.sha256(base.read_bytes()).hexdigest()
        state={'phase':'sent','official_return_code':0,'acknowledged_revision':1,'fast_transform_protocol':1,'partial_update_protocol':1,'visibility_protocol':1,'model_sha256':sha}
        write_json(session.directory/'status.json',state);session.acknowledge(state)
        return session,base

    def test_real_host_layer_toggle_bypasses_full_export(self):
        session,base=self.session();self.s.layers[0].visible=False;self.s.version+=1
        exporter=Mock(side_effect=AssertionError('full model export'))
        result=session.publish(self.s,exporter);self.assertEqual(result['kind'],'visibility');exporter.assert_not_called()
        payload=json.loads((session.directory/result['file']).read_text());output=self.directory/'visibility.igz'
        scene,stats=prepare(base,payload,1,output)
        self.assertFalse(scene['groups'][0][VISIBLE]);self.assertFalse(scene[LOOSE]);self.assertGreater(stats['visibility_objects_changed'],0)
        self.assertEqual(scene['materials'],material_tests.CompositionTests.read(self,base)['scene']['materials'])

    def test_material_or_geometry_change_rejects_visibility_certificate(self):
        session,base=self.session();before=session.motion_baseline
        self.s.layers[0].visible=False
        self.g.mesh.add_face([host_tests.QVector3D(),host_tests.QVector3D(5,0,0),host_tests.QVector3D(0,5,0)])
        with self.assertRaisesRegex(ValueError,'only_change'):certify(self.c,before)

    def test_wrong_base_visibility_refused(self):
        session,base=self.session();self.s.layers[0].visible=False
        flags,current=certify(self.c,session.motion_baseline)
        payload={'protocol':1,'base_revision':1,'base_model_sha256':hashlib.sha256(base.read_bytes()).hexdigest(),'layer_policy':{},**flags}
        payload['before']['Layer 0']=False
        with self.assertRaises(DeltaRejected):prepare(base,payload,1,self.directory/'bad.igz')

    def test_recovery_requires_stable_ready_and_times_out_without_mutating_revision(self):
        gate=RecoveryGate();self.assertFalse(gate.check(False,0));self.assertFalse(gate.check(True,1))
        self.assertFalse(gate.check(False,1.5));self.assertFalse(gate.check(True,2));self.assertTrue(gate.check(True,3))
        self.assertTrue(gate.expired(31))

    def test_window_fallback_excludes_browser_title_and_other_unreal_apps(self):
        self.assertTrue(renderer_window('UnrealWindow','Project - D5 Render'))
        self.assertFalse(renderer_window('Chrome_WidgetWin_1','D5 Render'))
        self.assertFalse(renderer_window('UnrealWindow','Other renderer'))

class AsyncBackupTests(unittest.TestCase):
    setUp=backup_tests.BackupTests.setUp
    save=backup_tests.BackupTests.save
    point=backup_tests.BackupTests.point

    def test_minute_settings_and_half_hour_override(self):
        self.assertEqual(settings(self.project)['minutes'],10)
        save_settings(self.project,90);self.assertEqual(settings(self.project)['minutes'],90)
        self.assertEqual(due(self.day+dt.timedelta(minutes=30),self.day,half_hour(self.day),90)[0],True)
        self.assertEqual(due(self.day+dt.timedelta(minutes=5),self.day,half_hour(self.day),10)[0],False)
        with self.assertRaises(ValueError):save_settings(self.project,0)

    def test_every_half_hour_survives_rotation_and_shares_storage(self):
        points=[]
        for minutes in (0,30,60,90):
            when=self.day+dt.timedelta(minutes=minutes)
            points.append(b.create_point(self.project,['half_hour'],now=when,protected_slots=[half_hour(when)],force_new=True))
        b.prune_points(self.project,rolling=1,daily_days=1)
        self.assertTrue(all(p.exists() for p in points))
        # Native hardlinks on this Linux filesystem share unchanged blend bytes.
        self.assertEqual(len({(p/'bridge.blend').stat().st_ino for p in points}),1)
        self.assertEqual(len(b.list_points(self.project)),4)

    def test_half_hour_protection_is_pinned_for_old_version_retention(self):
        point=b.create_point(self.project,protected_slots=[half_hour(self.day)])
        self.assertTrue(b.verify_point(self.project,point)['pinned'])
        with self.assertRaisesRegex(ValueError,'不能取消'):b.set_pinned(self.project,point,False)

    def test_deleting_one_shared_point_cannot_damage_other_or_live_pair(self):
        one=self.point();two=b.create_point(self.project,force_new=True)
        original=(self.project/'bridge.blend').read_bytes()
        self.assertEqual(b.delete_points(self.project,[one]),1)
        self.assertTrue(b.verify_point(self.project,two));self.assertEqual((self.project/'bridge.blend').read_bytes(),original)

    def test_protected_delete_requires_explicit_opt_in_and_worker_lock(self):
        point=b.create_point(self.project,protected_slots=[half_hour(self.day)])
        with self.assertRaisesRegex(ValueError,'保護'):b.delete_points(self.project,[point])
        from ingetrazo_d5_livesync.project_store import ProjectLease
        lease=ProjectLease(self.project).acquire()
        try:
            with self.assertRaises(RuntimeError):b.delete_points(self.project,[point],True)
        finally:lease.close()
        self.assertEqual(b.delete_points(self.project,[point],True),1)
        self.assertEqual(b.current_state(self.project)['revision'],1)

    def test_immutable_capture_survives_next_checkpoint_replace(self):
        folder=self.base/'capture';capture(self.project,folder)
        # SDK checkpoint always replaces the inode, never overwrites in-place.
        fresh=self.project/'new.blend';fresh.write_bytes(b'new SDK IDs');os.replace(fresh,self.project/'bridge.blend')
        self.assertEqual((folder/'bridge.blend').read_bytes(),b'blend SDK data 1')

    def test_background_verification_does_not_block_submission_or_use_new_pair(self):
        service=BackupService(self.project,auto_schedule=False);entered=threading.Event();release=threading.Event();real=b.create_point
        def blocked(*args,**kwargs):entered.set();release.wait(5);return real(*args,**kwargs)
        try:
            with patch.object(b,'create_point',side_effect=blocked):
                started=time.monotonic();service.submit(['interval'],now=self.day,slot=half_hour(self.day))
                self.assertLess(time.monotonic()-started,1);self.assertTrue(entered.wait(2))
                # Simulate a new checkpoint by atomic replace, not in-place test writes.
                fresh=self.project/'next.blend';fresh.write_bytes(b'next pair');os.replace(fresh,self.project/'bridge.blend')
                release.set();service.queue.join()
            rows=b.list_points(self.project,verify=True)
            self.assertEqual(len(rows),1);self.assertTrue(rows[0]['valid']);self.assertEqual(rows[0]['state']['revision'],1)
            self.assertEqual((Path(rows[0]['path'])/'bridge.blend').read_bytes(),b'blend SDK data 1')
        finally:release.set();service.close()

    def test_half_hour_capture_is_queued_even_while_previous_copy_is_busy(self):
        service=BackupService(self.project,auto_schedule=False);entered=threading.Event();release=threading.Event();real=b.create_point
        def slow(*args,**kwargs):entered.set();release.wait(5);return real(*args,**kwargs)
        try:
            with patch.object(b,'create_point',side_effect=slow):
                service.tick(self.day);self.assertTrue(entered.wait(2))
                service.tick(self.day+dt.timedelta(minutes=30));service.tick(self.day+dt.timedelta(minutes=31))
                self.assertEqual(service.queue.qsize(),1)
                release.set();service.queue.join()
            points=b.list_points(self.project,verify=True)
            self.assertEqual(len(points),2)
            self.assertEqual({r['protected_slots'][0] for r in points},{half_hour(self.day),half_hour(self.day+dt.timedelta(minutes=30))})
        finally:release.set();service.close()

    def test_failed_job_preserved_and_replayed_after_restart(self):
        service=BackupService(self.project,auto_schedule=False)
        with patch.object(b,'create_point',side_effect=OSError('disk full')):
            service.submit(['interval'],now=self.day);service.queue.join()
        self.assertTrue(service.error);self.assertTrue(list(service.root.glob('job-*')));service.close()
        resumed=BackupService(self.project,auto_schedule=False)
        try:
            resumed.queue.join();self.assertEqual(len(b.list_points(self.project,verify=True)),1)
            self.assertFalse(list(resumed.root.glob('job-*')))
        finally:resumed.close()

    def test_unpublished_capture_is_not_replayed_or_retried(self):
        folder=self.project/'backup-jobs'/'job-interrupted.pending'
        capture(self.project,folder)
        b.atomic_json(folder/'request.json',{'created_at':self.day.isoformat(),'slot':half_hour(self.day),
            'request_id':'job-interrupted','reasons':['half_hour'],'pinned':False})
        service=BackupService(self.project,auto_schedule=False)
        try:
            service.retry_jobs();service.queue.join()
            self.assertTrue(folder.exists());self.assertFalse(b.list_points(self.project))
        finally:service.close()

    def test_restart_does_not_capture_same_slot_twice_while_replaying(self):
        folder=self.project/'backup-jobs'/'job-published'
        capture(self.project,folder)
        b.atomic_json(folder/'request.json',{'created_at':self.day.isoformat(),'slot':half_hour(self.day),
            'request_id':'job-published','reasons':['half_hour'],'pinned':False})
        entered=threading.Event();release=threading.Event();real=b.create_point
        def slow(*args,**kwargs):entered.set();release.wait(5);return real(*args,**kwargs)
        with patch.object(b,'create_point',side_effect=slow):
            service=BackupService(self.project,auto_schedule=False)
            try:
                self.assertTrue(entered.wait(2));service.tick(self.day+dt.timedelta(minutes=1))
                self.assertEqual(service.queue.qsize(),0)
                release.set();service.queue.join()
                self.assertEqual(len(b.list_points(self.project,verify=True)),1)
            finally:release.set();service.close()

    def test_export_finishes_pending_protected_jobs_before_moving(self):
        from ingetrazo_d5_livesync import project_transfer
        service=BackupService(self.project,auto_schedule=False)
        with patch.object(b,'create_point',side_effect=OSError('disk full')):
            service.submit(['half_hour'],now=self.day,slot=half_hour(self.day));service.queue.join()
        service.close()
        archive=self.base/'move.zip';manifest=project_transfer.export_project(self.project,archive)
        self.assertFalse(list((self.project/'backup-jobs').glob('job-*')))
        target=self.base/'new-user'/self.project.name
        project_transfer.import_project(target,archive)
        self.assertTrue(any(half_hour(self.day) in row.get('protected_slots',[]) for row in b.list_points(target,verify=True)))

    def test_prepared_checkpoint_avoids_rehashing_same_inode(self):
        pending=self.project/'bridge.checkpoint.blend';pending.write_bytes(b'new pair')
        state=b.current_state(self.project)
        saved=b.prepare_checkpoint(self.project,self.project/state['scope_file'],{'revision':2},pending)
        stamp=b.file_stamp(pending);os.replace(pending,self.project/'bridge.blend')
        with patch.object(b,'digest',side_effect=AssertionError('redundant full file hash')):
            self.assertTrue(b.finish_checkpoint(self.project,saved,stamp))
        self.assertEqual(b.current_state(self.project)['revision'],2)

    def test_delete_cannot_target_live_bridge_or_traversal(self):
        self.point()
        for point in (self.project,self.project/'bridge.blend',self.project/'../escape'):
            with self.assertRaises((ValueError,OSError)):b.delete_points(self.project,[point],True)
        self.assertEqual(b.current_state(self.project)['revision'],1)

    def test_delete_interruption_resumes_only_confirmed_tombstone(self):
        point=self.point();real=b.shutil.rmtree
        with patch.object(b.shutil,'rmtree',side_effect=OSError('file locked')):
            with self.assertRaises(OSError):b.delete_points(self.project,[point])
        self.assertTrue((self.project/'backup-delete.pending.json').exists())
        self.assertTrue(b.recover_deletions(self.project));self.assertEqual(b.current_state(self.project)['revision'],1)

class BackupUiNewTests(unittest.TestCase):
    setUpClass=classmethod(backup_tests.BackupUiTests.setUpClass.__func__)
    setUp=backup_tests.BackupTests.setUp
    save=backup_tests.BackupTests.save
    point=backup_tests.BackupTests.point
    dialog=backup_tests.BackupUiTests.dialog
    def test_minute_controls_and_date_range_delete_require_stopped(self):
        self.point();panel,dialog=self.dialog();dialog.table.selectRow(0)
        self.assertEqual(dialog.minutes.minimum(),1);self.assertEqual(dialog.minutes.maximum(),1440)
        panel.running=True;dialog.update_controls();self.assertFalse(dialog.delete.isEnabled())
        from PySide6.QtCore import QDate
        panel.running=False;dialog.date_from.setDate(QDate(2026,11,16));dialog.date_to.setDate(QDate(2026,11,16))
        dialog.date_filter.setChecked(True);dialog.update_controls()
        self.assertTrue(dialog.delete_range.isEnabled())

    def test_manual_backup_runs_without_blocking_qt_events(self):
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QTimer
        self.point();panel,dialog=self.dialog();release=threading.Event();ticks=[];real=b.manual_point
        def slow(project):release.wait(5);return real(project)
        with patch.object(b,'manual_point',side_effect=slow):
            dialog.make_backup();self.assertIsNotNone(dialog.task)
            QTimer.singleShot(30,lambda:(ticks.append(1),release.set()))
            deadline=time.monotonic()+10
            while dialog.task is not None and time.monotonic()<deadline:QApplication.processEvents();time.sleep(.005)
        self.assertEqual(ticks,[1]);self.assertIsNone(dialog.task);self.assertFalse(panel.running)

    def test_minute_setting_is_persisted_without_starting_sync(self):
        self.point();panel,dialog=self.dialog();dialog.minutes.setValue(7);dialog.apply_minutes()
        self.assertEqual(settings(self.project)['minutes'],7);self.assertFalse(panel.running)

class WorkerIntegrationTests(unittest.TestCase):
    def run_worker(self,fail_send=False,fail_checkpoint=False):
        import importlib.util
        import types
        root=Path(__file__).resolve().parents[2]/'plugins/ingetrazo_d5_livesync'
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);base=Path(temp.name)
        project=base/'projects'/('c'*32);project.mkdir(parents=True);session=base/'session';session.mkdir()
        source={'scene':{'groups':[{'uid':'g1','layer':'Layer 0','faces':[material_tests.face()]}],
                         'faces':[],'edges':[],'materials':[{'name':'Shared'}]}}
        (session/'snapshot-00000001.igz').write_text(json.dumps(source))
        write_json(session/'owner.json',{'plugin':'ingetrazo_d5_livesync','parent_pid':123})
        write_json(session/'config.json',{'d5_path':'fake','project_id':project.name,'project_directory':str(project),'duplicate_scan':False})
        write_json(session/'latest.json',{'revision':1,'file':'snapshot-00000001.igz','kind':'full','published_at':time.time(),'bytes':100})
        write_json(session/'control.json',{'allowed_revision':1})
        scene={};objects=[];fake_bpy=types.ModuleType('bpy');fake_bpy.data=SimpleNamespace(objects=objects,materials=[],collections=[],filepath='')
        fake_bpy.context=SimpleNamespace(scene=scene,view_layer=SimpleNamespace(update=lambda:None))
        fake_bpy.app=SimpleNamespace(version_string='fake Blender')
        def save(**kwargs):
            Path(kwargs['filepath']).write_bytes(json.dumps(scene,sort_keys=True).encode());return {'FINISHED'}
        fake_bpy.ops=SimpleNamespace(wm=SimpleNamespace(save_as_mainfile=save))
        importer=Mock();importer.load.return_value={'objects':1,'faces':1,'meshes_built':0}
        fake_import=types.ModuleType('incremental');fake_import.IncrementalImporter=lambda:importer
        adapter=SimpleNamespace(version=(1,0,0,28),busy=lambda:False,state=lambda:'in-project',
            sdk=SimpleNamespace(D5RenderInProject='in-project'),close=Mock(),recover_connection=Mock())
        adapter.send=Mock(side_effect=[RuntimeError('code 4'),{'official_return_code':0,'sync_mode':'full'}] if fail_send else [{'official_return_code':0,'sync_mode':'full'}])
        fake_adapter=types.ModuleType('d5_adapter');fake_adapter.D5Adapter=lambda *args,**kwargs:adapter
        paths=list(sys.path)
        with patch.dict(sys.modules,{'bpy':fake_bpy,'incremental':fake_import,'d5_adapter':fake_adapter}):
            spec=importlib.util.spec_from_file_location('test7_worker_runtime',root/'worker.py');worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
            clock=[0.]
            def mono():clock[0]+=.3;return clock[0]
            worker.time=SimpleNamespace(monotonic=mono,perf_counter=time.perf_counter,time=time.time,sleep=lambda seconds:None)
            loops=[0]
            def alive(pid):
                loops[0]+=1
                return loops[0]<200 and not (session/'status.json').exists() or (loops[0]<200 and json.loads((session/'status.json').read_text()).get('acknowledged_revision',0)<1)
            worker.parent_alive=alive
            import bridge_backups as top_b
            real=top_b.finish_checkpoint;calls=[]
            def finish(*args):
                calls.append(1)
                if fail_checkpoint and len(calls)==1:raise OSError('marker write interrupted')
                return real(*args)
            with patch.object(top_b,'finish_checkpoint',side_effect=finish):worker.main(session)
        sys.path[:]=paths
        events=[json.loads(line) for line in (session/'sync-events.jsonl').read_text().splitlines()]
        state=json.loads((session/'status.json').read_text())
        self.assertEqual(state['acknowledged_revision'],1)
        self.assertEqual(importer.load.call_count,1)
        return adapter,events,project

    def test_sdk_retry_reuses_prepared_model_and_resets_only_after_ready(self):
        adapter,events,project=self.run_worker(fail_send=True)
        self.assertEqual(adapter.send.call_count,2);adapter.recover_connection.assert_called_once()
        self.assertTrue(any(e['event']=='prepared_revision_reused' for e in events))
        self.assertEqual(b.current_state(project)['revision'],1)

    def test_checkpoint_retry_never_resends_successful_sdk_payload(self):
        adapter,events,project=self.run_worker(fail_checkpoint=True)
        self.assertEqual(adapter.send.call_count,1);adapter.recover_connection.assert_not_called()
        self.assertTrue(any(e['event']=='sdk_receipt_reused' for e in events))
        self.assertEqual(b.current_state(project)['revision'],1)

class AdapterVisibilityTests(unittest.TestCase):
    def adapter(self):
        import importlib.util,types
        root=Path(__file__).resolve().parents[2]/'plugins/ingetrazo_d5_livesync'
        updates=SimpleNamespace(clear=Mock(),add=Mock())
        class Scene(dict):pass
        scene=Scene();scene.d5=SimpleNamespace(sync=SimpleNamespace(updates=updates))
        obj=SimpleNamespace(as_pointer=lambda:7,type='MESH',d5=SimpleNamespace(set_dirty=Mock()))
        bpy=types.ModuleType('bpy');bpy.context=SimpleNamespace(scene=scene,view_layer=SimpleNamespace(objects=[obj]),window_manager=SimpleNamespace(d5=SimpleNamespace(sync=SimpleNamespace())))
        bpy.data=SimpleNamespace(filepath='bridge.blend')
        modules={'bpy':bpy,'addon_utils':types.ModuleType('addon_utils')}
        project=types.ModuleType('project_store');project.material_ids=lambda data:set();modules['project_store']=project
        plan=types.ModuleType('sync_plan');plan.capture=lambda objects:{};plan.plan=lambda previous,current:('visibility',[7]);modules['sync_plan']=plan
        handler=types.ModuleType('d5_export.handlers');handler.D5Handler=SimpleNamespace(ON_UPDATE_POST='post',unregister=Mock());modules['d5_export.handlers']=handler
        update=types.ModuleType('d5_export.handlers.update');update._frame_geometry_changed=set();modules['d5_export.handlers.update']=update
        sync=types.ModuleType('d5_export.data.sync');sync.live_sync_data={};modules['d5_export.data.sync']=sync
        official=types.ModuleType('official_log')
        class Capture:
            def __init__(self,*args,**kwargs):pass
            def finish(self):pass
            def summary(self):return {}
        official.OfficialLogCapture=Capture;modules['official_log']=official
        env=patch.dict(sys.modules,modules);env.start();self.addCleanup(env.stop)
        spec=importlib.util.spec_from_file_location('test7_adapter',root/'d5_adapter.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        adapter=module.D5Adapter.__new__(module.D5Adapter)
        adapter.sdk=SimpleNamespace(D5RenderInProject=1,DisConnect=Mock());adapter.state=lambda:1
        adapter.connected=True;adapter.force_initial=False;adapter.incremental_enabled=False;adapter.sent_state={}
        adapter.send_calls=0;adapter.last_return_code=None;adapter.last_error='';adapter._original_send=lambda:0
        adapter.converter=SimpleNamespace(session=object(),clear_mesh_cache=Mock(),destroy_session=Mock())
        adapter.light_sender=SimpleNamespace(send=Mock(return_value={}))
        def operator(*args,**kwargs):adapter._send_with_receipt();return {'FINISHED'}
        bpy.ops=SimpleNamespace(d5=SimpleNamespace(sync=Mock(side_effect=operator)))
        return adapter,bpy,updates

    def test_visibility_same_session_sends_no_material_or_geometry_rebuild(self):
        adapter,bpy,updates=self.adapter();receipt=adapter._send_impl()
        self.assertEqual(receipt['sync_mode'],'visibility');self.assertFalse(receipt['source_materials_sent'])
        self.assertFalse(bpy.ops.d5.sync.call_args.kwargs['export_materials'])
        self.assertTrue(bpy.ops.d5.sync.call_args.kwargs['use_sync_updates'])
        adapter.converter.clear_mesh_cache.assert_not_called();self.assertEqual(updates.add.call_count,1)

    def test_fresh_converter_never_suppresses_material_definitions(self):
        adapter,bpy,updates=self.adapter();adapter.force_initial=True
        receipt=adapter._send_impl()
        self.assertEqual(receipt['sync_mode'],'full');self.assertTrue(receipt['source_materials_sent'])
        self.assertTrue(bpy.ops.d5.sync.call_args.kwargs['export_materials'])
        self.assertFalse(bpy.ops.d5.sync.call_args.kwargs['use_sync_updates'])

    def test_failed_native_send_disconnects_before_retry(self):
        adapter,bpy,updates=self.adapter();adapter._original_send=lambda:4
        self.assertEqual(adapter._send_with_receipt(),4)
        adapter.sdk.DisConnect.assert_called_once_with(adapter.converter.session)
        self.assertEqual(adapter.last_return_code,4)

class NamedDatablockTests(unittest.TestCase):
    def test_existing_named_blender_material_is_not_repainted_or_recreated(self):
        import importlib.util,types
        root=Path(__file__).resolve().parents[2]/'plugins/ingetrazo_d5_livesync'
        fake_bpy=types.ModuleType('bpy');fake_math=types.ModuleType('mathutils');fake_math.Matrix=Mock()
        package=types.ModuleType('ingetrazo_io');package.__path__=[];package.igz=types.ModuleType('ingetrazo_io.igz')
        build=types.ModuleType('ingetrazo_io.build');build.ORIGIN_PROP='origin'
        class Builder:
            material=Mock(side_effect=AssertionError('repaint existing D5 material'))
        build.Builder=Builder
        with patch.dict(sys.modules,{'bpy':fake_bpy,'mathutils':fake_math,'ingetrazo_io':package,'ingetrazo_io.build':build}):
            spec=importlib.util.spec_from_file_location('test7_real_incremental',root/'incremental.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            class Mat(dict):name='胡桃木'
            mat=Mat(bridge_material_signature='old',bridge_material_id='keep',d5_uuid='SDK-stays',ingetrazo_doc='model.igz')
            builder=module.CachedBuilder.__new__(module.CachedBuilder);builder.cache=SimpleNamespace(named_materials={'胡桃木':mat})
            builder.recording={};builder.materials={};builder.doc_path='model.igz'
            got=builder.material({'mat':'胡桃木','color':[1,0,0],'_bridge_named_target':'胡桃木'})
            self.assertIs(got,mat);self.assertEqual(mat,{'bridge_material_signature':'old','bridge_material_id':'keep','d5_uuid':'SDK-stays','ingetrazo_doc':'model.igz'})
            Builder.material.assert_not_called()
