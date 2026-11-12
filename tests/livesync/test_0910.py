"""Linux tests with the real upstream v0.6.0 core; no Windows/D5 claims.
Run with upstream core on PYTHONPATH and PySide6 Essentials installed.
"""
import copy
from contextlib import closing
import importlib.util
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'plugins'))
from PySide6.QtGui import QVector3D, QMatrix4x4
from core.scene import Scene
from core.mesh import Mesh
from core.group import Group
from core.history import History, MoveGroupCommand, RotateGroupCommand, Command
from formats.igz import save_scene
from ingetrazo_d5_livesync.host_motion import MotionCertificate
from ingetrazo_d5_livesync.transport import SnapshotSession,write_json
from ingetrazo_d5_livesync.layer_scope import compose,Bundle,META
from ingetrazo_d5_livesync.local_update import prepare_local
from ingetrazo_d5_livesync.motion_delta import PatchCache

spec=importlib.util.spec_from_file_location('ingetrazo_plugin_it_material_studio',ROOT/'plugins/it_material_studio/__init__.py',submodule_search_locations=[str(ROOT/'plugins/it_material_studio')])
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
from ingetrazo_plugin_it_material_studio.engine import PaintCommand,default_recipe


def group(name='Group 176'):
    m=Mesh();f=m.add_face([QVector3D(0,0,0),QVector3D(10,0,0),QVector3D(0,10,0)])
    f.attrs={'color':[.2,.3,.4]}
    return Group(m,name)

class LiveSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.directory=Path(self.tmp.name)
        self.s=Scene();self.g=group();self.other=group('unchanged');self.s.groups=[self.g,self.other]
        self.h=History(self.s)
        self.c=MotionCertificate(SimpleNamespace(scene=self.s,viewport=SimpleNamespace(history=self.h)))
        self.addCleanup(self.c.close)
        self.assertTrue(self.c.supported)

    def baseline(self):return self.c.capture(with_dependencies=True)
    def paint(self,entities=None):
        recipe=default_recipe();recipe.update(name='test red',color=[1.,0.,0.])
        return PaintCommand(self.s,recipe,entities=entities or [self.g])
    def execute(self,cmd):
        self.h.execute(cmd);self.assertIsNone(self.h.last_error)

    def test_baked_move_uv_and_undo_redo(self):
        self.g.mesh.faces[0].attrs['texture']={'uvw':[1,0,0,0,0,1,0,0],'sw':1,'sh':1}
        before=copy.deepcopy(self.g.mesh.faces[0].attrs)
        b=self.baseline();self.execute(MoveGroupCommand(self.g,QVector3D(10,0,0)))
        roots,current=self.c.partial_plan(b);self.assertEqual(roots,[current['groups'][0]['key']])
        self.assertIsNone(self.g.xform)
        self.assertEqual(self.g.mesh.faces[0].attrs['texture']['uvw'][3],-10)
        decision=self.c.describe(b,'test');self.assertIn('axes',decision['changed_groups'][0]['metadata_fields'])
        for action in (self.h.undo,self.h.redo):
            b=self.baseline();action();self.assertEqual(len(self.c.partial_plan(b)[0]),1)
        self.h.undo();self.assertEqual(self.g.mesh.faces[0].attrs,before)

    def test_baked_rotation_nested_scope(self):
        self.g.children=[group('child')]
        b=self.baseline();self.execute(RotateGroupCommand(self.g,QVector3D(),QVector3D(0,0,1),90))
        self.assertEqual(len(self.c.partial_plan(b)[0]),1)

    def test_instance_fast_path(self):
        self.g.xform=QMatrix4x4();b=self.baseline()
        self.execute(MoveGroupCommand(self.g,QVector3D(0,20,0)))
        self.assertEqual(len(self.c.delta(b)[0]),1)

    def test_nested_paint_clone_undo_redo(self):
        self.g.children=[group('child')];b=self.baseline()
        self.execute(self.paint());self.assertEqual(len(self.c.partial_plan(b)[0]),1)
        self.assertNotEqual(b['loose'],self.baseline()['loose'])
        for action in (self.h.undo,self.h.redo):
            b=self.baseline();action();self.assertEqual(len(self.c.partial_plan(b)[0]),1)

    def test_loose_paint_falls_back(self):
        f=self.s.mesh.add_face([QVector3D(),QVector3D(1,0,0),QVector3D(0,1,0)])
        b=self.baseline();self.execute(self.paint([f]))
        with self.assertRaisesRegex(ValueError,'loose_geometry_changed'):self.c.partial_plan(b)

    def test_shared_back_material_falls_back(self):
        f=self.s.mesh.add_face([QVector3D(),QVector3D(1,0,0),QVector3D(0,1,0)])
        f.attrs={'back':{'mat':'test red'}}
        b=self.baseline();self.execute(self.paint())
        with self.assertRaisesRegex(ValueError,'loose_material_changed'):self.c.partial_plan(b)

    def test_component_metadata_falls_back(self):
        self.g.component='prototype';b=self.baseline();self.execute(self.paint())
        with self.assertRaisesRegex(ValueError,'metadata_changed'):self.c.partial_plan(b)

    def test_unknown_command_falls_back(self):
        class Unknown(Command):
            def do(cmd,scene):scene.version+=1
            def undo(cmd,scene):scene.version+=1
        b=self.baseline();self.execute(Unknown())
        with self.assertRaisesRegex(ValueError,'unsupported_command'):self.c.partial_plan(b)

    def test_external_edit_falls_back(self):
        b=self.baseline();self.s.version+=1
        with self.assertRaisesRegex(ValueError,'unsupported_command'):self.c.partial_plan(b)

    def session(self):
        session=SnapshotSession(self.directory);session.motion_certificate=self.c
        result=session.publish(self.s,save_scene)
        state={'phase':'idle','acknowledged_revision':result['revision'],'official_return_code':0,
               'fast_transform_protocol':1,'partial_update_protocol':1,'model_sha256':'a'*64}
        write_json(session.directory/'status.json',state);session.acknowledge(state)
        return session,state

    def test_noop_no_export_no_revision(self):
        session,state=self.session();manifest=(session.directory/'latest.json').read_bytes()
        def forbidden(*args):self.fail('No-op called exporter/progress')
        for _ in range(3):
            self.assertEqual(session.publish(self.s,forbidden,on_export=forbidden)['kind'],'skipped')
        self.assertEqual(session.revision,1);self.assertFalse(session.pending)
        self.assertEqual((session.directory/'latest.json').read_bytes(),manifest)

    def test_manual_full_bypasses_skip(self):
        session,state=self.session()
        self.assertEqual(session.publish(self.s,save_scene,force_full=True)['kind'],'full')
        self.assertEqual(session.revision,2)

    def test_receiver_error_never_skips(self):
        session,state=self.session();state['phase']='error';write_json(session.directory/'status.json',state)
        self.assertEqual(session.publish(self.s,save_scene)['kind'],'full')

    def test_texture_change_never_skips(self):
        p=self.directory/'texture.bin';p.write_bytes(b'a')
        b=self.baseline();b['dependencies']={str(p):self.c._stat(p)}
        self.assertTrue(self.c.unchanged(b));p.write_bytes(b'changed')
        self.assertFalse(self.c.unchanged(b))

    def test_paint_source_mismatch_falls_back(self):
        import ingetrazo_plugin_it_material_studio.engine as engine
        old=engine.__file__;engine.__file__=str(self.directory/'missing.py')
        try:
            b=self.baseline();self.execute(self.paint())
            with self.assertRaises(ValueError):self.c.partial_plan(b)
        finally:engine.__file__=old

    def test_native_partial_export_and_hold_composition(self):
        self.other.layer='hold'
        self.s.plugin_data={'ingetrazo_d5_livesync':{'layer_sync_modes_v1':{'hold':'hold'}}}
        session,state=self.session();session.partial_enabled=True
        base=self.directory/'baseline.igz'
        initial=self.directory/'initial.igz'
        compose(session.directory/'snapshot-00000001.igz',None,{},initial)
        compose(session.directory/'snapshot-00000001.igz',initial,{'hold':'hold'},base)
        self.execute(MoveGroupCommand(self.g,QVector3D(10,0,0)))
        import hashlib
        session.motion_base_hash=hashlib.sha256(base.read_bytes()).hexdigest()
        result=session.publish(self.s,save_scene)
        self.assertEqual(result['kind'],'partial')
        out=self.directory/'out.igz'
        prepare_local(base,session.directory/result['file'],1,out,PatchCache())
        with closing(Bundle(base)) as a,closing(Bundle(out)) as b:
            old={g['uid']:g for g in a.scene['groups']};new={g['uid']:g for g in b.scene['groups']}
            self.assertEqual(old[self.other.uid],new[self.other.uid])
            self.assertEqual(set(old),set(new))
            self.assertNotEqual(old[self.g.uid],new[self.g.uid])

    def test_pending_never_skips(self):
        session,state=self.session();self.execute(MoveGroupCommand(self.g,QVector3D(2,0,0)))
        session.publish(self.s,save_scene)
        with self.assertRaisesRegex(RuntimeError,'上一版'):session.publish(self.s,save_scene)

    def test_noop_metadata_and_history_changes_never_skip(self):
        b=self.baseline();self.g.name='renamed'
        self.assertFalse(self.c.unchanged(b));self.g.name='Group 176'
        self.execute(MoveGroupCommand(self.g,QVector3D(1,0,0)));self.h.undo()
        self.assertFalse(self.c.unchanged(b))

    def test_missing_texture_never_skips(self):
        b=self.baseline();b['dependencies']={str(self.directory/'missing'):None}
        self.assertFalse(self.c.unchanged(b))

    def test_history_overflow_falls_back(self):
        b=self.baseline()
        for _ in range(257):self.execute(MoveGroupCommand(self.g,QVector3D(1,0,0)))
        with self.assertRaisesRegex(ValueError,'history_overflow'):self.c.partial_plan(b)

    def test_paint_partial_keeps_uid_and_hold(self):
        self.g.children=[group('child')];self.other.layer='hold'
        session,state=self.session();session.partial_enabled=True
        base=self.directory/'baseline.igz';initial=self.directory/'initial.igz'
        compose(session.directory/'snapshot-00000001.igz',None,{},initial)
        self.s.plugin_data={'ingetrazo_d5_livesync':{'layer_sync_modes_v1':{'hold':'hold'}}}
        # Establish a full confirmed policy baseline before testing painting.
        session.motion_baseline=self.baseline()
        compose(session.directory/'snapshot-00000001.igz',initial,{'hold':'hold'},base)
        import hashlib
        session.motion_base_hash=hashlib.sha256(base.read_bytes()).hexdigest()
        self.execute(self.paint());result=session.publish(self.s,save_scene)
        self.assertEqual(result['kind'],'partial')
        out=self.directory/'out.igz'
        prepare_local(base,session.directory/result['file'],1,out,PatchCache())
        with closing(Bundle(base)) as a,closing(Bundle(out)) as b:
            old={g['uid']:g for g in a.scene['groups']};new={g['uid']:g for g in b.scene['groups']}
            self.assertEqual(old[self.other.uid],new[self.other.uid])
            before=old[self.g.uid];after=new[self.g.uid]
            self.assertEqual(before['uid'],after['uid'])
            self.assertEqual(before['children'][0]['uid'],after['children'][0]['uid'])
            self.assertNotEqual(before,after)

    def test_noop_with_both_update_options_disabled(self):
        session,state=self.session();session.motion_enabled=False;session.partial_enabled=False
        self.assertEqual(session.publish(self.s,save_scene)['kind'],'skipped')

    def test_swallowed_native_failure_invalidates_local_certificate(self):
        b=self.baseline();cmd=self.paint()
        with patch.object(PaintCommand,'do',side_effect=ValueError('test failure')),patch.object(History,'_log_failure'):
            self.h.execute(cmd)
        self.assertTrue(self.c.trace[-1]['command_failed'])
        self.assertIsNotNone(self.h.last_error)
        with self.assertRaises(ValueError):self.c.partial_plan(b)

if __name__=='__main__':unittest.main()
