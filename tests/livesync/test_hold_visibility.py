"""Freeze visible D5 content while source tags hide; preserve material identity."""
import copy
import hashlib
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'plugins'))
from ingetrazo_d5_livesync.visibility import VISIBLE,LOOSE,VERSION_KEY,apply_visibility
from ingetrazo_d5_livesync.layer_scope import META,MATERIAL_BINDINGS
from ingetrazo_d5_livesync.local_update import prepare_local,TAG
from ingetrazo_d5_livesync.motion_delta import PatchCache
import test_material_identity as material_tests
face=material_tests.face

class HoldCompositionTests(unittest.TestCase):
    setUp=material_tests.CompositionTests.setUp
    write=material_tests.CompositionTests.write
    read=material_tests.CompositionTests.read
    step=material_tests.CompositionTests.step
    refs=material_tests.CompositionTests.refs

    def test_hide_held_layer_keeps_display_geometry_and_material_names(self):
        src=self.write(self.source);base=self.step(src,None,{})
        old=self.read(base);changed=copy.deepcopy(self.source)
        changed['scene']['layers'][0]['visible']=False
        changed['scene']['groups'][0]['hidden']=True
        changed['scene']['groups'][0]['faces'][0]['color']=[1,0,0]
        changed['scene']['groups'][1]['faces'][0]['vertices'][0]=[2,0,0]
        result=self.step(self.write(changed),base,{'A':'hold'})
        now=self.read(result);a=next(g for g in now['scene']['groups'] if g['uid']=='A')
        self.assertTrue(a[VISIBLE]);self.assertEqual(a,old['scene']['groups'][0])
        self.assertEqual(self.refs(result),self.refs(base))
        self.assertEqual(now['scene']['materials'],old['scene']['materials'])
        self.assertEqual(now[META][MATERIAL_BINDINGS],old[META][MATERIAL_BINDINGS])

    def test_native_default_layer_omission_preserves_groups_and_loose(self):
        from core.scene import Scene
        from core.group import Group
        from core.mesh import Mesh
        from PySide6.QtGui import QVector3D
        from formats.igz import save_scene
        scene=Scene();mesh=Mesh();mesh.add_face([QVector3D(),QVector3D(10,0,0),QVector3D(0,10,0)])
        scene.groups=[Group(mesh,'default')]
        scene.mesh.add_face([QVector3D(),QVector3D(20,0,0),QVector3D(0,20,0)])
        src=self.root/'native.igz';save_scene(scene,src)
        self.assertNotIn('layers',self.read(src)['scene'])
        base=self.step(src,None,{})
        scene.layers[0].visible=False;hidden=self.root/'hidden.igz';save_scene(scene,hidden)
        held=self.step(hidden,base,{'Layer 0':'hold'});data=self.read(held)['scene']
        self.assertTrue(data['groups'][0][VISIBLE]);self.assertTrue(data[LOOSE])
        normal=self.read(self.step(hidden,held,{}))['scene']
        self.assertFalse(normal['groups'][0][VISIBLE]);self.assertFalse(normal[LOOSE])

    def test_mixed_shared_child_tag_freezes_only_held_tree_without_new_aliases(self):
        source=copy.deepcopy(self.source)
        source['scene']['layers'].append({'name':'SharedTag'})
        for g in source['scene']['groups']:
            g['children']=[{'uid':g['uid']+'child','layer':'SharedTag','faces':[face()]}]
        base=self.step(self.write(source),None,{})
        original=self.refs(base)
        source['scene']['layers'][2]['visible']=False
        source['scene']['layers'][0]['visible']=False
        for unused in range(3):
            base=self.step(self.write(source),base,{'A':'hold'})
            groups={g['uid']:g for g in self.read(base)['scene']['groups']}
            self.assertTrue(groups['A'][VISIBLE]);self.assertTrue(groups['A']['children'][0][VISIBLE])
            self.assertFalse(groups['B']['children'][0][VISIBLE])
            for g in groups.values():self.assertEqual(g['children'][0]['layer'],'SharedTag')
            self.assertEqual(self.refs(base),original)
        restored=self.read(self.step(self.write(source),base,{}))['scene']
        self.assertFalse(next(g for g in restored['groups'] if g['uid']=='A')[VISIBLE])

    def test_previously_hidden_held_content_stays_hidden_when_source_shown(self):
        source=copy.deepcopy(self.source);source['scene']['layers'][0]['visible']=False
        base=self.step(self.write(source),None,{})
        result=self.read(self.step(self.write(self.source),base,{'A':'hold'}))
        self.assertFalse(next(g for g in result['scene']['groups'] if g['uid']=='A')[VISIBLE])

    def test_held_deleted_root_and_child_visibility_are_preserved(self):
        source=copy.deepcopy(self.source)
        source['scene']['groups'][0]['children']=[{'uid':'child','layer':'A','hidden':True,'faces':[face()]}]
        base=self.step(self.write(source),None,{})
        source['scene']['groups']=source['scene']['groups'][1:]
        result=self.read(self.step(self.write(source),base,{'A':'hold'}))
        a=next(g for g in result['scene']['groups'] if g['uid']=='A')
        self.assertTrue(a[VISIBLE]);self.assertFalse(a['children'][0][VISIBLE])

    def test_legacy_baseline_uses_last_layer_display_without_renaming(self):
        source=copy.deepcopy(self.source);old=copy.deepcopy(source)
        for g in old['scene']['groups']:g['_bridge_scope_key']='uid/'+g['uid']
        source['scene']['layers'][0]['visible']=False
        base=self.write(old);result=self.step(self.write(source),base,{'A':'hold'})
        self.assertTrue(self.read(result)['scene']['groups'][0][VISIBLE])
        self.assertEqual(self.refs(result),self.refs(base))

    def test_partial_live_edit_preserves_frozen_display_and_material_ledger(self):
        src=self.write(self.source);base=self.step(src,None,{})
        changed=copy.deepcopy(self.source);changed['scene']['layers'][0]['visible']=False
        base=self.step(self.write(changed),base,{'A':'hold'})
        partial=copy.deepcopy(changed);partial['scene']['groups']=partial['scene']['groups'][1:]
        partial['scene']['groups'][0]['faces'][0]['vertices'][0]=[5,0,0]
        partial[TAG]={'protocol':1,'base_revision':1,'base_model_sha256':hashlib.sha256(base.read_bytes()).hexdigest(),
                      'layer_policy':{'A':'hold'},'root_keys':['uid/B'],'node_keys':[['uid/B']]}
        output=self.root/'partial.igz';prepare_local(base,self.write(partial,{}),1,output,PatchCache())
        before=self.read(base);after=self.read(output)
        self.assertEqual(after['scene']['groups'][0],before['scene']['groups'][0])
        self.assertTrue(after['scene']['groups'][1][VISIBLE])
        self.assertEqual(self.refs(output),self.refs(base))
        self.assertEqual(after[META][MATERIAL_BINDINGS],before[META][MATERIAL_BINDINGS])

    def test_hold_hide_release_keeps_legacy_scoped_material_bindings(self):
        src,base,unused=material_tests.CompositionTests.legacy(self)
        original=self.refs(base);source=copy.deepcopy(self.source)
        source['scene']['layers'][0]['visible']=False
        hidden=self.write(source)
        for policy in ({'A':'hold'},{'A':'hold'},{},{'A':'hold'},{}):
            base=self.step(hidden,base,policy)
            self.assertEqual(self.refs(base),original)

    def test_hold_cannot_run_opt_in_material_compaction(self):
        from ingetrazo_d5_livesync.layer_scope import compose
        src=self.write(self.source);base=self.step(src,None,{})
        target=self.root/'forbidden.igz'
        with self.assertRaisesRegex(ValueError,'材質整理'):
            compose(src,base,{'A':'hold'},target,compact_legacy=True)
        self.assertFalse(target.exists())

    def test_damaged_frozen_display_stops_before_output(self):
        src=self.write(self.source);base=self.step(src,None,{})
        damaged=self.read(base);damaged['scene']['groups'][0].pop(VISIBLE)
        with self.assertRaisesRegex(ValueError,'確認可見性遺失'):self.step(src,self.write(damaged),{'A':'hold'})

class Block(dict):
    def __init__(self,**values):
        super().__init__(values);self.hide_viewport=True;self.hide_render=True

class VisibilityApplicationTests(unittest.TestCase):
    def fixture(self):
        doc='model.igz';a=Block(ingetrazo_doc=doc,ingetrazo_key='uid/A',sdk_uuid='A',material='custom')
        b=Block(ingetrazo_doc=doc,ingetrazo_key='uid/B',sdk_uuid='B',material='custom')
        tag=Block(ingetrazo_doc=doc,ingetrazo_tag='SharedTag',sdk_uuid='tag')
        foreign=Block(ingetrazo_doc='other.igz',ingetrazo_key='uid/A')
        scene={VERSION_KEY:1,LOOSE:True,'groups':[{'_bridge_scope_key':'uid/A',VISIBLE:True},{'_bridge_scope_key':'uid/B',VISIBLE:False}]}
        data=SimpleNamespace(objects=[a,b,foreign],collections=[tag])
        return data,scene,doc

    def test_shared_collection_no_longer_hides_held_object_or_changes_identity(self):
        data,scene,doc=self.fixture();before=[dict(o) for o in data.objects]
        identities=[id(o) for o in data.objects];tags=dict(data.collections[0])
        self.assertEqual(apply_visibility(data,scene,doc),2)
        self.assertFalse(data.objects[0].hide_render);self.assertTrue(data.objects[1].hide_render)
        self.assertTrue(data.objects[2].hide_render);self.assertFalse(data.collections[0].hide_render)
        self.assertEqual([dict(o) for o in data.objects],before)
        self.assertEqual([id(o) for o in data.objects],identities);self.assertEqual(dict(data.collections[0]),tags)

    def test_missing_or_duplicate_object_refuses_before_any_display_mutation(self):
        for duplicate in (False,True):
            data,scene,doc=self.fixture()
            if duplicate:data.objects.append(Block(**dict(data.objects[0])))
            else:data.objects.pop(1)
            with self.assertRaisesRegex(ValueError,'唯一'):apply_visibility(data,scene,doc)
            self.assertTrue(data.collections[0].hide_render);self.assertTrue(data.objects[0].hide_render)

    def test_frozen_loose_object_stays_visible_without_material_changes(self):
        data,scene,doc=self.fixture();loose=Block(ingetrazo_doc=doc,ingetrazo_key='loose',sdk_uuid='loose',material='D5')
        data.objects.append(loose);apply_visibility(data,scene,doc)
        self.assertFalse(loose.hide_render);self.assertEqual(loose['sdk_uuid'],'loose');self.assertEqual(loose['material'],'D5')

    def test_normal_hidden_parent_also_hides_descendant_on_different_tag(self):
        from ingetrazo_d5_livesync.visibility import stamp_groups
        groups=[{'layer':'A','children':[{'layer':'B'}]}]
        result=stamp_groups(groups,{'layers':[{'name':'A','visible':False},{'name':'B','visible':True}]})
        self.assertFalse(result[0][VISIBLE]);self.assertFalse(result[0]['children'][0][VISIBLE])
        self.assertNotIn(VISIBLE,groups[0])
