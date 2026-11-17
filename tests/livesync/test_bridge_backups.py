"""Recovery protocol tests on real files; .blend bytes and SDK are test data."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'plugins'))
from ingetrazo_d5_livesync import bridge_backups as b
from ingetrazo_d5_livesync.project_store import ProjectLease,checkpoint

class BackupTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.base=Path(temp.name)
        self.project=self.base/'IngeTrazoD5Bridge'/'projects'/('a'*32);self.project.mkdir(parents=True)
        self.day=dt.datetime(2026,11,16,10,tzinfo=dt.timezone(dt.timedelta(hours=8)))
        self.save(1)

    def save(self,revision):
        payload=json.dumps({'scene':{'groups':[]},'revision':revision}).encode()
        sha=hashlib.sha256(payload).hexdigest();scope=self.project/'scope-cache'/('scope-'+sha+'.igz')
        scope.parent.mkdir(exist_ok=True);scope.write_bytes(payload)
        (self.project/'bridge.blend').write_bytes(('blend SDK data '+str(revision)).encode())
        state=b.build_state(self.project,scope,{'revision':revision,'plugin_version':b.VERSION,'layer_policy':{'A':'hold'}})
        b.atomic_json(self.project/b.STATE,state);return state

    def point(self,**kwargs):return b.create_point(self.project,now=self.day,**kwargs)

    def test_complete_point_contains_verified_pair_and_no_d5(self):
        (self.project/'D5-project.drs').write_bytes(b'D5 must not be touched')
        point=self.point();m=b.verify_point(self.project,point)
        self.assertEqual(len(m['files']),3);self.assertFalse(m['d5_project_included'])
        self.assertTrue(m['daily']);self.assertEqual(m['local_day'],'2026-11-16')
        self.assertEqual((point/'bridge.blend').read_bytes(),(self.project/'bridge.blend').read_bytes())
        self.assertFalse((point/'D5-project.drs').exists())

    def test_retries_reuse_point_and_add_risk_pin(self):
        one=self.point(reasons=['before_sync']);two=self.point(reasons=['layer_policy_change'],pinned=True)
        self.assertEqual(one,two);m=b.verify_point(self.project,one)
        self.assertTrue(m['pinned']);self.assertEqual(m['reasons'],['before_sync','layer_policy_change'])
        self.assertEqual(len(b.list_points(self.project)),1)

    def test_broken_current_pair_never_gets_blessed(self):
        (self.project/'bridge.blend').write_bytes(b'broken')
        with self.assertRaisesRegex(ValueError,'不符'):self.point()
        self.assertEqual(b.list_points(self.project),[])

    def test_corrupt_same_state_point_does_not_block_new_verified_backup(self):
        old=self.point();data=(old/'bridge.blend').read_bytes();(old/'bridge.blend').write_bytes(b'x'*len(data))
        new=self.point()
        self.assertNotEqual(old,new);self.assertTrue(old.exists())
        self.assertTrue(b.verify_point(self.project,new)['daily'])

    def test_source_changes_during_copy_never_replace_old_backup(self):
        point=self.point();self.save(2)
        with patch.object(b,'copy_verified',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.point()
        self.assertTrue(point.exists());self.assertEqual(b.verify_point(self.project,point)['state']['revision'],1)
        self.assertEqual(b.current_state(self.project)['revision'],2)

    def test_copy_hash_failure_is_reported_as_backup_io_error(self):
        point=self.point();source=self.project/'bridge.blend'
        expected=b.digest(source)
        with patch.object(b,'digest',side_effect=[expected,expected,'0'*64]):
            with self.assertRaisesRegex(OSError,'複製驗證失敗'):
                b.copy_verified(source,self.project/'copy.pending')
        self.assertEqual(b.verify_point(self.project,point)['state']['revision'],1)

    def test_restore_pair_after_live_scope_was_pruned(self):
        old=self.point();oldstate=b.current_state(self.project);self.save(2)
        (self.project/oldstate['scope_file']).unlink()
        d5=self.project/'D5.drs';d5.write_bytes(b'unchanged D5')
        result=b.restore_point(self.project,old)
        self.assertEqual(b.current_state(self.project)['revision'],1)
        self.assertTrue(Path(result['rescue']).is_dir());self.assertEqual(d5.read_bytes(),b'unchanged D5')
        self.assertTrue(any(r.get('pinned') and 'before_restore' in r.get('reasons',[]) for r in b.list_points(self.project)))

    def test_corrupt_archive_rejected_before_live_changes(self):
        point=self.point();self.save(2);before=(self.project/'bridge.blend').read_bytes()
        data=(point/'bridge.blend').read_bytes();(point/'bridge.blend').write_bytes(b'x'*len(data))
        with self.assertRaisesRegex(ValueError,'驗證失敗'):b.restore_point(self.project,point)
        self.assertEqual((self.project/'bridge.blend').read_bytes(),before)

    def test_other_project_cannot_restore_this_point(self):
        point=self.point();other=self.project.parent/('b'*32);other.mkdir();(other/b.ROOT).mkdir()
        copied=other/b.ROOT/point.name;shutil.copytree(point,copied)
        with self.assertRaisesRegex(ValueError,'不屬於'):b.restore_point(other,copied)
        self.assertFalse((other/'bridge.blend').exists())

    def test_running_worker_lease_blocks_restore_manual_and_pin(self):
        point=self.point();lease=ProjectLease(self.project).acquire()
        try:
            for action in (lambda:b.restore_point(self.project,point),lambda:b.manual_point(self.project),lambda:b.set_pinned(self.project,point,True)):
                with self.assertRaisesRegex(RuntimeError,'另一個同步'):action()
        finally:lease.close()

    def test_restore_failure_rolls_back_every_replaced_file(self):
        point=self.point();self.save(2);before=(self.project/'bridge.blend').read_bytes();marker=(self.project/b.STATE).read_bytes()
        real=b._replace_file;calls=[]
        def fail_once(source,target):
            calls.append(target)
            if len(calls)==2:raise OSError('simulated replace failure')
            return real(source,target)
        with patch.object(b,'_replace_file',side_effect=fail_once):
            with self.assertRaises(OSError):b.restore_point(self.project,point)
        self.assertEqual((self.project/'bridge.blend').read_bytes(),before)
        self.assertEqual((self.project/b.STATE).read_bytes(),marker)
        self.assertFalse((self.project/b.RESTORE).exists());self.assertEqual(b.current_state(self.project)['revision'],2)

    def test_state_install_failure_rolls_back_already_replaced_blend(self):
        point=self.point();self.save(2);before=(self.project/'bridge.blend').read_bytes()
        marker=(self.project/b.STATE).read_bytes();real=b._replace_file;calls=[]
        def fail_third(source,target):
            calls.append(target)
            if len(calls)==3:raise OSError('state marker replace failed')
            return real(source,target)
        with patch.object(b,'_replace_file',side_effect=fail_third):
            with self.assertRaises(OSError):b.restore_point(self.project,point)
        self.assertEqual((self.project/'bridge.blend').read_bytes(),before)
        self.assertEqual((self.project/b.STATE).read_bytes(),marker)
        self.assertEqual(b.current_state(self.project)['revision'],2)

    def test_interrupted_restore_is_rolled_back_before_next_start(self):
        point=self.point();self.save(2);before=(self.project/'bridge.blend').read_bytes()
        real_replace=b._replace_file;real_recover=b.recover_restore;calls=[]
        def fail_second(source,target):
            calls.append(target)
            if len(calls)==2:raise OSError('power interruption')
            return real_replace(source,target)
        # First call is the initial no-op recovery. Interrupt the exception
        # rollback to leave exactly the journal a process crash would leave.
        recover_calls=[]
        def interrupted(project):
            recover_calls.append(1)
            if len(recover_calls)>1:raise KeyboardInterrupt()
            return real_recover(project)
        with patch.object(b,'_replace_file',side_effect=fail_second),patch.object(b,'recover_restore',side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):b.restore_point(self.project,point)
        self.assertTrue((self.project/b.RESTORE).exists())
        self.assertTrue(b.recover_restore(self.project))
        self.assertEqual((self.project/'bridge.blend').read_bytes(),before)
        self.assertEqual(b.current_state(self.project)['revision'],2)

    def test_restore_can_replace_broken_current_bridge_but_keeps_evidence(self):
        point=self.point();(self.project/'bridge.blend').write_bytes(b'corrupt evidence')
        result=b.restore_point(self.project,point)
        self.assertEqual(b.current_state(self.project)['revision'],1)
        self.assertEqual((Path(result['rescue'])/'bridge.blend').read_bytes(),b'corrupt evidence')

    def test_retention_keeps_ten_recent_seven_daily_and_pins(self):
        points=[]
        for day in range(12):
            for hour in (0,1):
                self.save(day*2+hour+1)
                now=self.day+dt.timedelta(days=day,hours=hour)
                points.append(b.create_point(self.project,['before_sync'],pinned=(day==0 and hour==0),now=now))
        b.prune_points(self.project)
        remaining={Path(r['path']) for r in b.list_points(self.project,verify=True)}
        self.assertTrue(set(points[-10:])<=remaining);self.assertIn(points[0],remaining)
        rows=b.list_points(self.project)
        days={r['local_day'] for r in rows if r['daily']}
        expected={(self.day+dt.timedelta(days=d)).date().isoformat() for d in range(5,12)}
        self.assertTrue(expected<=days)
        self.assertLess(len(remaining),len(points))

    def test_retention_does_not_remove_corrupt_or_unknown_backups(self):
        point=self.point();payload=(point/'bridge.blend').read_bytes();(point/'bridge.blend').write_bytes(b'x'*len(payload))
        unknown=self.project/b.ROOT/'old-manual-folder';unknown.mkdir();(unknown/'keep').write_bytes(b'evidence')
        for revision in (2,3):self.save(revision);b.create_point(self.project,now=self.day+dt.timedelta(days=revision))
        b.prune_points(self.project,rolling=1,daily_days=1)
        self.assertTrue(point.exists());self.assertTrue((unknown/'keep').exists())

    def test_unpin_makes_old_point_eligible_for_rotation(self):
        old=self.point(pinned=True);self.save(2);b.create_point(self.project,now=self.day+dt.timedelta(days=1))
        b.set_pinned(self.project,old,False);b.prune_points(self.project,rolling=1,daily_days=1)
        self.assertFalse(old.exists())

    def test_manifest_path_traversal_is_rejected(self):
        point=self.point();m=json.loads((point/'backup.json').read_text());m['files'][0]['path']='../../outside'
        b.atomic_json(point/'backup.json',m)
        self.assertFalse(b.list_points(self.project)[0]['valid'])
        with self.assertRaises(ValueError):b.restore_point(self.project,point)

    def test_checkpoint_recovery_finishes_verified_pending_pair(self):
        old=self.point();payload=b'new confirmed scope';sha=hashlib.sha256(payload).hexdigest()
        scope=self.project/'scope-cache'/('scope-'+sha+'.igz');scope.write_bytes(payload)
        pending=self.project/'bridge.checkpoint.blend';pending.write_bytes(b'new SDK identities')
        b.prepare_checkpoint(self.project,scope,{'revision':2},pending)
        self.assertTrue(b.recover_checkpoint(self.project))
        self.assertEqual(b.current_state(self.project)['revision'],2)
        self.assertEqual((self.project/'bridge.blend').read_bytes(),b'new SDK identities')
        self.assertTrue(old.exists());self.assertFalse((self.project/b.CHECKPOINT).exists())

    def test_bad_pending_checkpoint_cannot_overwrite_old_pair(self):
        state=b.current_state(self.project);pending=self.project/'bridge.checkpoint.blend';pending.write_bytes(b'pending')
        b.prepare_checkpoint(self.project,self.project/state['scope_file'],{'revision':2},pending)
        pending.write_bytes(b'damaged')
        before=(self.project/'bridge.blend').read_bytes()
        with self.assertRaises(ValueError):b.recover_checkpoint(self.project)
        self.assertEqual((self.project/'bridge.blend').read_bytes(),before)
        self.assertTrue((self.project/b.CHECKPOINT).exists())

    def test_non_object_manifest_is_listed_invalid_without_deletion(self):
        point=self.point();b.atomic_json(point/'backup.json',[])
        self.assertFalse(b.list_points(self.project)[0]['valid'])
        b.prune_points(self.project);self.assertTrue(point.exists())

    def test_bootstrap_never_overwrites_mismatched_existing_marker(self):
        state=b.current_state(self.project);marker=(self.project/b.STATE).read_bytes()
        (self.project/'bridge.blend').write_bytes(b'wrong checkpoint')
        with self.assertRaises(ValueError):b.bootstrap_state(self.project,self.project/state['scope_file'],{})
        self.assertEqual((self.project/b.STATE).read_bytes(),marker)

    def test_checkpoint_recovers_marker_after_bridge_already_replaced(self):
        state=b.current_state(self.project);pending=self.project/'bridge.checkpoint.blend';pending.write_bytes(b'new checkpoint')
        b.prepare_checkpoint(self.project,self.project/state['scope_file'],{'revision':2},pending)
        os.replace(pending,self.project/'bridge.blend')
        self.assertTrue(b.recover_checkpoint(self.project))
        self.assertEqual(b.current_state(self.project)['revision'],2)

    def test_status_keeps_backup_failure_visible_after_successful_receipt(self):
        from ingetrazo_d5_livesync.status_model import StatusModel
        model=StatusModel();model.begin(2,0)
        card=model.view(5,state={'phase':'idle','acknowledged_revision':2,'official_return_code':0,
                                'bridge_backup_warning':'disk full'},session_revision=2,running=True,alive=True)
        self.assertEqual(card['kind'],'waiting');self.assertIn('備份未完成',card['title'])
        self.assertIn('disk full',card['note'])

    def test_prepare_callback_fails_before_checkpoint_replaces_original(self):
        from types import SimpleNamespace
        before=(self.project/'bridge.blend').read_bytes()
        def save_as_mainfile(**kwargs):Path(kwargs['filepath']).write_bytes(b'new blend');return {'FINISHED'}
        bpy=SimpleNamespace(ops=SimpleNamespace(wm=SimpleNamespace(save_as_mainfile=save_as_mainfile)))
        with self.assertRaises(OSError):checkpoint(bpy,self.project/'bridge.blend',before_install=lambda path:(_ for _ in ()).throw(OSError('journal disk full')))
        self.assertEqual((self.project/'bridge.blend').read_bytes(),before)

class BackupUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt=QApplication.instance() or QApplication([])

    setUp=BackupTests.setUp
    save=BackupTests.save
    point=BackupTests.point

    def dialog(self):
        from PySide6.QtCore import Signal
        from PySide6.QtWidgets import QWidget,QLabel
        from types import SimpleNamespace
        from unittest.mock import Mock
        from ingetrazo_d5_livesync.backup_ui import BridgeBackupsDialog
        from ingetrazo_d5_livesync.light_data import KEY
        class Panel(QWidget):state_changed=Signal()
        panel=Panel();panel.running=False;panel.checking=False;panel.exporting=False;panel.process=None
        panel.app=SimpleNamespace(scene=SimpleNamespace(plugin_data={KEY:{'bridge_project_id':self.project.name}}))
        panel.status=QLabel();panel.progress=Mock()
        env=patch.dict(os.environ,{'LOCALAPPDATA':str(self.base)});env.start();self.addCleanup(env.stop)
        dialog=BridgeBackupsDialog(panel);self.addCleanup(dialog.deleteLater);self.addCleanup(panel.deleteLater)
        return panel,dialog

    def test_ui_blocks_restore_and_manual_while_sync_running(self):
        self.point();panel,dialog=self.dialog();dialog.table.selectRow(0)
        panel.running=True;dialog.update_controls()
        self.assertFalse(dialog.restore.isEnabled());self.assertFalse(dialog.manual.isEnabled())
        panel.running=False;dialog.update_controls();self.assertTrue(dialog.restore.isEnabled())

    def test_ui_restore_updates_stop_status_without_starting_sync(self):
        from PySide6.QtWidgets import QMessageBox
        point=self.point();self.save(2);panel,dialog=self.dialog()
        index=next(i for i,r in enumerate(dialog.rows) if r['path']==str(point));dialog.table.selectRow(index)
        with patch('ingetrazo_d5_livesync.backup_ui.QMessageBox.question',return_value=QMessageBox.StandardButton.Yes):
            dialog.restore_selected()
        from PySide6.QtWidgets import QApplication
        import time
        deadline=time.monotonic()+10
        while dialog.task is not None and time.monotonic()<deadline:
            QApplication.processEvents();time.sleep(.005)
        self.assertIsNone(dialog.task)
        self.assertEqual(b.current_state(self.project)['revision'],1)
        self.assertIn('橋接資料已還原',panel.status.text());panel.progress.stop.assert_called_once()
        self.assertFalse(panel.running)
