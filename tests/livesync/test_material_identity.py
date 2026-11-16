"""Policy/identity regressions; pure composition, real archive data, no D5 SDK."""
import copy
import hashlib
import json
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'plugins'))
from ingetrazo_d5_livesync.layer_scope import compose,Bundle,Composer,META,MATERIAL_BINDINGS,fingerprint
from ingetrazo_d5_livesync.local_update import prepare_local,TAG
from ingetrazo_d5_livesync.motion_delta import PatchCache
from ingetrazo_d5_livesync.material_identity import retain_material_identities
from ingetrazo_d5_livesync.optimization_backup import backup_bridge_identity


def face(color=(.8,.8,.8)):
    return {'vertices':[[0,0,0],[1,0,0],[0,1,0]],'mat':'Shared','color':list(color)}

class CompositionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.source={'scene':{'groups':[{'uid':n,'name':n,'layer':n,'faces':[face()],'edges':[]} for n in ('A','B')],
            'faces':[],'edges':[],'layers':[{'name':n} for n in ('A','B')], 'materials':[{'name':'Shared'}]}}
        self.serial=0

    def write(self,data,assets=None):
        self.serial+=1;p=self.root/f'doc-{self.serial}.igz'
        if assets is None:p.write_text(json.dumps(data))
        else:
            with zipfile.ZipFile(p,'w') as z:
                z.writestr('document.json',json.dumps(data))
                for name,content in assets.items():z.writestr(name,content)
        return p

    def read(self,path):
        with closing(Bundle(path)) as b:return copy.deepcopy(b.data)

    def step(self,source,baseline,policy):
        self.serial+=1;p=self.root/f'out-{self.serial}.igz'
        compose(source,baseline,policy,p);return p

    def refs(self,path):
        d=self.read(path);s=d['scene']
        return {g['uid']:(s['protos'][g['proto']] if 'proto' in g else g)['faces'][0].get('mat') for g in s['groups']}

    def legacy(self,prototype=False):
        source=copy.deepcopy(self.source)
        if prototype:
            source['scene']['protos']=[{'faces':[face()],'edges':[]}]
            for g in source['scene']['groups']:g.pop('faces');g.pop('edges');g['proto']=0
        src=self.write(source)
        with closing(Bundle(src)) as b:key=Composer(False).material_info(face(),b)[0]
        old=copy.deepcopy(source);old[META]={'format':1,'policy':{'A':'hold'},'material_aliases_v2':{key:['_scope_'+'a'*24]}}
        for g in old['scene']['groups']:g['_bridge_scope_key']='uid/'+g['uid']
        if prototype:
            old['scene']['protos'].append(copy.deepcopy(old['scene']['protos'][0]))
            old['scene']['protos'][1]['faces'][0]['mat']='_scope_'+'a'*24
            old['scene']['groups'][1]['proto']=1
        else:old['scene']['groups'][1]['faces'][0]['mat']='_scope_'+'a'*24
        old['scene']['materials'].append({'name':'_scope_'+'a'*24,'finish':'matte'})
        return src,self.write(old),key

    def test_normal_hold_exclude_restore_never_renames_other_layer(self):
        src=self.write(self.source);baseline=self.step(src,None,{})
        for policy in ({'A':'hold'},{'A':'exclude'},{},{'B':'hold'},{'B':'exclude'},{}):
            baseline=self.step(src,baseline,policy)
            self.assertTrue(all(name=='Shared' for name in self.refs(baseline).values()))
        self.assertEqual(self.refs(baseline),{'A':'Shared','B':'Shared'})

    def test_legacy_raw_and_scoped_same_appearance_keep_distinct_ids(self):
        src,baseline,key=self.legacy()
        original=self.refs(baseline)
        for policy in ({'A':'exclude'},{},{'B':'exclude'},{}):
            baseline=self.step(src,baseline,policy)
            for uid,name in self.refs(baseline).items():self.assertEqual(name,original[uid])
        self.assertEqual(self.refs(baseline),original)

    def test_excluding_all_keeps_dormant_bindings_and_registry(self):
        src,baseline,key=self.legacy();original=self.refs(baseline)
        excluded=self.step(src,baseline,{'A':'exclude','B':'exclude'})
        d=self.read(excluded);self.assertEqual(d['scene']['groups'],[])
        self.assertEqual(d[META][MATERIAL_BINDINGS]['uid/B'][key],['_scope_'+'a'*24])
        self.assertIn('_scope_'+'a'*24,{m['name'] for m in d['scene']['materials']})
        restored=self.step(src,excluded,{})
        self.assertEqual(self.refs(restored),original)

    def test_shared_prototype_can_keep_different_existing_material_ids(self):
        src,baseline,key=self.legacy(prototype=True)
        restored=self.step(src,baseline,{})
        self.assertEqual(self.refs(restored),{'A':'Shared','B':'_scope_'+'a'*24})
        s=self.read(restored)['scene'];self.assertNotEqual(s['groups'][0]['proto'],s['groups'][1]['proto'])

    def test_real_paint_change_does_not_repaint_held_definition(self):
        src=self.write(self.source);base=self.step(src,None,{})
        changed=copy.deepcopy(self.source)
        changed['scene']['groups'][1]['faces'][0]['color']=[1,0,0]
        changed['scene']['materials'][0]['finish']='glass'
        held=self.step(self.write(changed),base,{'A':'hold'})
        refs=self.refs(held);self.assertEqual(refs['A'],'Shared');self.assertNotEqual(refs['A'],refs['B'])
        registry={m['name']:m for m in self.read(held)['scene']['materials']}
        self.assertNotEqual(registry['Shared'].get('finish'),registry[refs['B']].get('finish'))
        self.assertEqual(self.refs(self.step(self.write(changed),held,{'A':'exclude'}))['B'],refs['B'])

    def test_uv_and_embedded_texture_survive_policy_changes(self):
        source=copy.deepcopy(self.source)
        for g in source['scene']['groups']:
            g['faces'][0]['texture']={'embed':'textures/shared.png','uvw':[1,0,0,0,0,1,0,0],'sw':1,'sh':1}
        src=self.write(source,{'textures/shared.png':b'unchanged image fixture'})
        base=self.step(src,None,{})
        for policy in ({'A':'hold'},{'A':'exclude'},{}):base=self.step(src,base,policy)
        d=self.read(base)
        for g in d['scene']['groups']:
            self.assertEqual(g['faces'][0]['texture'],source['scene']['groups'][0]['faces'][0]['texture'])
        with zipfile.ZipFile(base) as z:self.assertEqual(z.read('textures/shared.png'),b'unchanged image fixture')

    def test_partial_after_restore_uses_same_binding_and_keeps_other_root(self):
        src,baseline,key=self.legacy();baseline=self.step(src,baseline,{})
        source=copy.deepcopy(self.source);source['scene']['groups']=source['scene']['groups'][1:]
        source['scene']['groups'][0]['faces'][0]['vertices'][0]=[2,0,0]
        source[TAG]={'protocol':1,'base_revision':1,'base_model_sha256':hashlib.sha256(baseline.read_bytes()).hexdigest(),
                     'layer_policy':{},'root_keys':['uid/B'],'node_keys':[['uid/B']]}
        incoming=self.write(source,{});output=self.root/'partial-result.igz'
        prepare_local(baseline,incoming,1,output,PatchCache())
        self.assertEqual(self.refs(output),self.refs(baseline))
        before={g['uid']:g for g in self.read(baseline)['scene']['groups']}
        after={g['uid']:g for g in self.read(output)['scene']['groups']}
        self.assertEqual(before['A'],after['A']);self.assertNotEqual(before['B'],after['B'])
        final=self.step(src,output,{});self.assertEqual(self.refs(final),self.refs(baseline))

    def test_ambiguous_or_damaged_binding_stops_without_output(self):
        src,baseline,key=self.legacy();d=self.read(baseline)
        d[META][MATERIAL_BINDINGS]={'uid/B':{key:['_scope_one','_scope_two']}}
        # Remove the live B node so the dormant ambiguity cannot be resolved
        # using a known current node; restoring it must not guess a new ID.
        d['scene']['groups']=d['scene']['groups'][:1]
        with self.assertRaisesRegex(ValueError,'多個既有對應'):self.step(src,self.write(d),{})
        d[META][MATERIAL_BINDINGS]={'uid/B':{key:[]}}
        with self.assertRaisesRegex(ValueError,'格式無效'):self.step(src,self.write(d),{})

    def test_multiple_legacy_uv_aliases_are_preserved_without_merging(self):
        source=copy.deepcopy(self.source)
        faces=[]
        for offset in (0,4):
            attrs=face();attrs['texture']={'embed':'textures/shared.png','uvw':[1,0,0,offset,0,1,0,0]}
            faces.append(attrs)
        source['scene']['groups'][1]['faces']=faces
        src=self.write(source,{'textures/shared.png':b'image'})
        with closing(Bundle(src)) as b:key=Composer(False).material_info(faces[0],b)[0]
        names=['_scope_'+fingerprint(({k:v for k,v in attrs.items() if k in ('mat','color','texture','opacity')},{'name':'Shared'}))[:24] for attrs in faces]
        old=copy.deepcopy(source)
        old[META]={'policy':{'A':'hold'},'material_aliases_v2':{key:names}}
        for g in old['scene']['groups']:g['_bridge_scope_key']='uid/'+g['uid']
        for attrs,name in zip(old['scene']['groups'][1]['faces'],names):attrs['mat']=name
        old['scene']['materials'].extend({'name':n,'finish':'matte'} for n in names)
        baseline=self.write(old,{'textures/shared.png':b'image'})
        restored=self.step(src,baseline,{})
        b=next(g for g in self.read(restored)['scene']['groups'] if g['uid']=='B')
        self.assertEqual([f['mat'] for f in b['faces']],names)
        source['scene']['groups'][1]['faces'][0]['texture']['uvw'][3]=42
        changed=self.write(source,{'textures/shared.png':b'image'})
        before=restored.read_bytes()
        with self.assertRaisesRegex(ValueError,'多個既有對應'):self.step(changed,restored,{})
        self.assertEqual(restored.read_bytes(),before)

    def test_unknown_legacy_alias_is_rejected_instead_of_renamed(self):
        src,baseline,key=self.legacy();d=self.read(baseline);d[META]['material_aliases_v2']={}
        with self.assertRaisesRegex(ValueError,'缺少可確認的對應'):self.step(src,self.write(d),{})

    def test_back_material_reference_survives_exclude_restore(self):
        source=copy.deepcopy(self.source)
        source['scene']['groups'][1]['faces'][0]['back']={'mat':'Back','color':[.1,.2,.3]}
        source['scene']['materials'].append({'name':'Back','finish':'matte'})
        src=self.write(source);base=self.step(src,None,{})
        for policy in ({'A':'hold'},{'B':'exclude'},{}):base=self.step(src,base,policy)
        b=next(g for g in self.read(base)['scene']['groups'] if g['uid']=='B')
        self.assertEqual(b['faces'][0]['back'],source['scene']['groups'][1]['faces'][0]['back'])

    def test_migration_and_policy_metrics(self):
        src,baseline,key=self.legacy();output=self.root/'metrics.igz'
        metrics=compose(src,baseline,{},output)
        self.assertTrue(metrics['material_identity_migrated']);self.assertTrue(metrics['material_policy_changed'])
        metrics=compose(src,output,{},self.root/'metrics-2.igz')
        self.assertFalse(metrics['material_identity_migrated']);self.assertFalse(metrics['material_policy_changed'])

class MaterialBlock(dict):
    def __init__(self,name,document='',signature=None):
        super().__init__(ingetrazo_doc=document)
        self.name=name;self.users=0;self.use_fake_user=False
        if signature:self['bridge_material_signature']=signature

class DatablockAndBackupTests(unittest.TestCase):
    def test_orphaned_owned_material_keeps_sdk_properties_and_fake_user(self):
        scoped=MaterialBlock('_scope_existing','model.igz');scoped['d5_uuid']='keep-me'
        raw=MaterialBlock('Shared','model.igz','signature');external=MaterialBlock('_scope_foreign','other.igz')
        user=MaterialBlock('User material','model.igz')
        self.assertEqual(retain_material_identities([scoped,raw,external,user],'model.igz'),2)
        self.assertTrue(scoped.use_fake_user);self.assertTrue(raw.use_fake_user)
        self.assertEqual(scoped['d5_uuid'],'keep-me')
        self.assertFalse(external.use_fake_user);self.assertFalse(user.use_fake_user)

    def test_backup_copies_exact_bridge_and_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);(p/'bridge.blend').write_bytes(b'bridge with SDK properties')
            base=p/'scope-cache'/'scope-example.igz';base.parent.mkdir();base.write_bytes(b'confirmed scope')
            result=Path(backup_bridge_identity(p,base,version='0.9.10-test.3',purpose='material identity'))
            metadata=json.loads((result/'backup.json').read_text())
            self.assertFalse(metadata['d5_project_included']);self.assertEqual(metadata['version'],'0.9.10-test.3')
            for record in metadata['files']:
                self.assertEqual(hashlib.sha256((result/record['path']).read_bytes()).hexdigest(),record['sha256'])
            self.assertEqual((result/'bridge.blend').read_bytes(),(p/'bridge.blend').read_bytes())
