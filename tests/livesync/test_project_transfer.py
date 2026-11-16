"""Real archives/files and ownership tags; no real Blender or D5 SDK."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'plugins'))
from ingetrazo_d5_livesync import project_transfer as t,bridge_backups as b
from ingetrazo_d5_livesync.project_store import ProjectLease

class TransferTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.base=Path(temp.name)
        self.source=self.base/'old-user'/'projects'/('a'*32);self.source.mkdir(parents=True)
        self.target=self.base/'new-user'/'projects'/('a'*32)
        self.archive=self.base/'transfer.zip';self.save(self.source,1)

    def save(self,project,revision):
        project.mkdir(parents=True,exist_ok=True)
        payload=('scope '+str(revision)).encode();sha=hashlib.sha256(payload).hexdigest()
        scope=project/'scope-cache'/('scope-'+sha+'.igz');scope.parent.mkdir(exist_ok=True);scope.write_bytes(payload)
        (project/'bridge.blend').write_bytes(('SDK UUIDs '+str(revision)).encode())
        b.atomic_json(project/b.STATE,b.build_state(project,scope,{'revision':revision,'plugin_version':b.VERSION}))

    def export(self):return t.export_project(self.source,self.archive)

    def rewrite(self,transform):
        with zipfile.ZipFile(self.archive) as z:files={n:z.read(n) for n in z.namelist()}
        transform(files)
        with zipfile.ZipFile(self.archive,'w',zipfile.ZIP_DEFLATED) as z:
            for n,data in files.items():z.writestr(n,data)

    def test_roundtrip_new_user_keeps_blend_bytes_and_rebases_all_backups(self):
        old=b.create_point(self.source,['manual'],pinned=True);self.save(self.source,2)
        original=(self.source/'bridge.blend').read_bytes()
        (self.source/'do-not-export.drs').write_bytes(b'D5 server asset')
        (self.source/'private.igz').write_bytes(b'native model')
        m=self.export();result=t.import_project(self.target,self.archive)
        self.assertEqual((self.target/'bridge.blend').read_bytes(),original)
        self.assertEqual(b.current_state(self.target)['revision'],2)
        self.assertEqual(b.current_state(self.target)['project_directory'],str(self.target))
        self.assertEqual(b.current_state(self.target)['transfer_model_owners'],[str(self.source/'model.igz')])
        self.assertEqual(result['points_imported'],2)
        rows=b.list_points(self.target,verify=True);self.assertTrue(all(r['valid'] for r in rows))
        self.assertTrue(any(r['state']['revision']==1 and r['pinned'] for r in rows))
        self.assertFalse(m['d5_included']);self.assertFalse(m['native_igz_included'])
        with zipfile.ZipFile(self.archive) as z:
            self.assertFalse(any(n.endswith('.drs') or n.endswith('private.igz') for n in z.namelist()))
        self.assertEqual(b.verify_point(self.source,old)['state']['revision'],1)

    def test_second_export_and_import_preserve_original_owner_and_restore_history(self):
        self.export();t.import_project(self.target,self.archive)
        second=self.base/'second.zip';t.export_project(self.target,second)
        third=self.base/'third-user'/'projects'/self.source.name
        t.import_project(third,second)
        state=b.current_state(third)
        self.assertEqual(state['transfer_model_owners'],[str(self.source/'model.igz')])
        rows=b.list_points(third,verify=True);self.assertTrue(all(r['valid'] for r in rows))
        point=next(r for r in rows if 'transfer_import' in r['reasons'])
        b.restore_point(third,point['path'])
        self.assertEqual(b.current_state(third)['transfer_model_owners'],state['transfer_model_owners'])

    def test_existing_target_pair_and_history_preserved_before_import(self):
        self.export();self.save(self.target,99);old=b.create_point(self.target,['manual'],pinned=True)
        result=t.import_project(self.target,self.archive)
        self.assertEqual(b.current_state(self.target)['revision'],1)
        self.assertEqual(b.verify_point(self.target,old)['state']['revision'],99)
        self.assertEqual((Path(result['rescue'])/'bridge.blend').read_bytes(),b'SDK UUIDs 99')

    def test_import_failure_rolls_back_live_pair(self):
        self.export();self.save(self.target,99);before=(self.target/b.STATE).read_bytes()
        real=b._replace_file;calls=[]
        def fail_third(source,target):
            calls.append(target)
            if len(calls)==3:raise OSError('simulated disk failure')
            return real(source,target)
        with patch.object(b,'_replace_file',side_effect=fail_third):
            with self.assertRaises(OSError):t.import_project(self.target,self.archive)
        self.assertEqual((self.target/b.STATE).read_bytes(),before)
        self.assertEqual((self.target/'bridge.blend').read_bytes(),b'SDK UUIDs 99')

    def test_mismatched_igz_identity_refused_before_creating_target(self):
        self.export();target=self.target.parent/('b'*32)
        with self.assertRaisesRegex(ValueError,'IGZ'):t.import_project(target,self.archive)
        self.assertFalse(target.exists())

    def test_tampered_archive_refused_before_live_changes(self):
        self.export();self.save(self.target,99)
        def change(files):
            name=next(n for n in files if n.endswith('bridge.blend'));files[name]=b'corrupt bytes'
        self.rewrite(change)
        with self.assertRaises(ValueError):t.import_project(self.target,self.archive)
        self.assertEqual(b.current_state(self.target)['revision'],99)

    def test_outer_hash_change_does_not_hide_inner_corruption(self):
        self.export()
        def change(files):
            name=next(n for n in files if n.endswith('bridge.blend'));files[name]=b'bad SDK data'
            m=json.loads(files[t.MANIFEST]);m['files'][name]={'bytes':len(files[name]),'sha256':hashlib.sha256(files[name]).hexdigest()}
            files[t.MANIFEST]=json.dumps(m).encode()
        self.rewrite(change)
        with self.assertRaisesRegex(ValueError,'內層'):t.import_project(self.target,self.archive)
        self.assertFalse(self.target.exists())

    def test_path_traversal_extra_files_and_duplicate_entries_rejected(self):
        for name in ('../escape','C:/escape','bridge-backups/extra.exe'):
            self.export();self.rewrite(lambda f:f.update({name:b'bad'}))
            with self.assertRaises(ValueError):t.import_project(self.target,self.archive)
            self.assertFalse(self.target.exists())
        self.export()
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with zipfile.ZipFile(self.archive,'a') as z:z.writestr(t.MANIFEST,b'{}')
        with self.assertRaisesRegex(ValueError,'重複'):t.import_project(self.target,self.archive)

    def test_running_source_and_target_leases_block_transfer(self):
        lease=ProjectLease(self.source).acquire()
        try:
            with self.assertRaises(RuntimeError):self.export()
        finally:lease.close()
        self.export();self.save(self.target,99);lease=ProjectLease(self.target).acquire()
        try:
            with self.assertRaises(RuntimeError):t.import_project(self.target,self.archive)
        finally:lease.close()
        self.assertEqual(b.current_state(self.target)['revision'],99)

    def test_corrupt_backup_and_current_pair_cannot_be_exported(self):
        p=b.create_point(self.source);(p/'bridge.blend').write_bytes(b'damaged')
        with self.assertRaises(ValueError):self.export()
        self.assertFalse(self.archive.exists())
        (self.source/'bridge.blend').write_bytes(b'damaged current')
        with self.assertRaises(ValueError):self.export()

    def test_failed_export_preserves_existing_archive(self):
        self.export();before=self.archive.read_bytes()
        with patch.object(t,'_unpack',side_effect=ValueError('validation failed')):
            with self.assertRaises(ValueError):self.export()
        self.assertEqual(self.archive.read_bytes(),before)

    def test_export_cannot_write_into_project(self):
        with self.assertRaises(ValueError):t.export_project(self.source,self.source/'transfer.zip')

    def test_windows_origin_and_second_migration_keep_actual_blend_owner(self):
        state=b.current_state(self.source);origin=t.ntpath.join(r'C:\Users\old\projects',self.source.name)
        state['project_directory']=origin
        rebased=t._rebase(state,origin,self.target)
        self.assertEqual(rebased['transfer_model_owners'],[origin+r'\model.igz'])
        second=t._rebase(rebased,str(self.target),self.source)
        self.assertEqual(second['transfer_model_owners'],rebased['transfer_model_owners'])

    def test_ownership_rebind_changes_only_known_tags_and_keeps_sdk_ids(self):
        from types import SimpleNamespace
        original={'ingetrazo_doc':str(self.source/'model.igz'),'ingetrazo_key':'g-9','sdk_uuid':'original','name':'Mesh'}
        block=copy.deepcopy(original);data=SimpleNamespace(objects=[block])
        state={'transfer_model_owners':[str(self.source/'model.igz')]}
        count=t.rebind_model_owners(data,state,self.target/'model.igz')
        self.assertEqual(count,1);self.assertEqual(block['ingetrazo_doc'],str(self.target/'model.igz'))
        for key in ('ingetrazo_key','sdk_uuid','name'):self.assertEqual(block[key],original[key])
        self.assertEqual(t.rebind_model_owners(data,state,self.target/'model.igz'),0)

    def test_unknown_owner_aborts_before_any_tag_changes(self):
        from types import SimpleNamespace
        known={'ingetrazo_doc':'old'};unknown={'ingetrazo_doc':'unknown'}
        with self.assertRaises(ValueError):t.rebind_model_owners(SimpleNamespace(objects=[known,unknown]),{'transfer_model_owners':['old']},self.target/'model.igz')
        self.assertEqual(known['ingetrazo_doc'],'old')

class TransferUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def dialog(self):
        from PySide6.QtCore import Signal
        from PySide6.QtWidgets import QWidget,QLabel
        from types import SimpleNamespace
        from ingetrazo_d5_livesync.backup_ui import BridgeBackupsDialog
        from ingetrazo_d5_livesync.light_data import KEY
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        env=patch.dict(os.environ,{'LOCALAPPDATA':temp.name});env.start();self.addCleanup(env.stop)
        class Panel(QWidget):state_changed=Signal()
        p=Panel();p.running=p.checking=p.exporting=False;p.process=None;p.status=QLabel();p.progress=Mock()
        p.app=SimpleNamespace(scene=SimpleNamespace(plugin_data={KEY:{'bridge_project_id':'a'*32}}))
        d=BridgeBackupsDialog(p);self.addCleanup(d.deleteLater);self.addCleanup(p.deleteLater)
        return p,d

    def test_import_available_without_local_bridge_and_disabled_when_running(self):
        p,d=self.dialog();self.assertTrue(d.import_transfer.isEnabled());self.assertFalse(d.export_transfer.isEnabled())
        p.running=True;d.update_controls();self.assertFalse(d.import_transfer.isEnabled())

    def test_successful_import_stays_stopped_and_does_not_change_scene_identity(self):
        from PySide6.QtWidgets import QMessageBox
        p,d=self.dialog();before=copy.deepcopy(p.app.scene.plugin_data)
        with patch('ingetrazo_d5_livesync.backup_ui.QFileDialog.getOpenFileName',return_value=('demo.zip','')),patch('ingetrazo_d5_livesync.backup_ui.QMessageBox.question',return_value=QMessageBox.StandardButton.Yes),patch.object(t,'import_project',return_value={'points_imported':3}) as run:
            d.import_project()
        run.assert_called_once();self.assertFalse(p.running);p.progress.stop.assert_called_once()
        self.assertEqual(p.app.scene.plugin_data,before);self.assertIn('3 個還原點',p.status.text())
