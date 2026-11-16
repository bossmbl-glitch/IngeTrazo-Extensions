# SPDX-License-Identifier: GPL-3.0-or-later
"""Root-scoped source snapshots and immutable-baseline patch composition."""
import copy,hashlib,json,zipfile,shutil
from pathlib import Path
try:
    from .motion_delta import DeltaRejected
    from .layer_scope import Bundle,Composer,META,MATERIAL_BINDINGS
except ImportError:
    from motion_delta import DeltaRejected
    from layer_scope import Bundle,Composer,META,MATERIAL_BINDINGS

TAG='_bridge_partial'

def export_roots(scene,keys,state,exporter,target,base_revision,base_hash,policy):
    # Use the actual host serializer, but give it only selected live roots.
    from core.scene import Scene
    from .light_proxy import filtered_scene
    snapshot=Scene();rows=[r for r in state['groups'] if r['parent'] is None]
    snapshot.groups=[g for g,r in zip(scene.groups,rows) if r['key'] in keys]
    snapshot.layers=scene.layers
    used=set()
    def material_references(value):
        if isinstance(value,dict):
            if isinstance(value.get('mat'),str):used.add(value['mat'])
            for item in value.values():material_references(item)
        elif isinstance(value,(list,tuple)):
            for item in value:material_references(item)
    def references(g):
        material_references(getattr(g,'material',None))
        for f in g.mesh.faces:material_references(f.attrs)
        for c in g.children:references(c)
    for g in snapshot.groups:references(g)
    snapshot.materials={k:v for k,v in scene.materials.items() if k in used}
    snapshot=filtered_scene(snapshot)
    result=exporter(snapshot,target)
    if result and result.get('missing',0):raise ValueError('局部更新來源貼圖無法讀取；請修復貼圖路徑後重試')
    with _opened(target) as bundle:
        data=bundle.data;members={i.filename:bundle.member(i.filename) for i in bundle.archive.infolist() if i.filename!='document.json'} if bundle.archive else {}
    node_keys=[[r['key'] for r in state['groups'] if r['root_key']==k] for k in keys]
    data[TAG]={'protocol':1,'base_revision':base_revision,'base_model_sha256':base_hash,
               'layer_policy':policy,'root_keys':keys,'node_keys':node_keys,'no_visual_change':not keys}
    temp=Path(str(target)+'.pack')
    try:
        with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_STORED) as z:
            z.writestr('document.json',json.dumps(data,ensure_ascii=False,separators=(',',':')))
            for name,value in members.items():z.writestr(name,value)
        temp.replace(target)
    finally:temp.unlink(missing_ok=True)
    return data[TAG]

class _opened:
    def __init__(self,path):self.bundle=Bundle(path)
    def __enter__(self):return self.bundle
    def __exit__(self,*args):self.bundle.close()

def tree(group):
    yield group
    for child in group.get('children',[]) or []:yield from tree(child)

class _CachedBundle:
    def __init__(self,document,members):self.data=document;self.scene=document['scene'];self.members=dict(members)
    def member(self,name):return self.members[name]

def prepare_local(baseline,source,ack,output,cache):
    digest=hashlib.sha256(Path(baseline).read_bytes()).hexdigest()
    with _opened(source) as incoming:
        request=incoming.data.get(TAG,{})
        if request.get('protocol')!=1 or request.get('base_revision')!=ack or ack<1:raise DeltaRejected('局部更新基準版本不符')
        if request.get('base_model_sha256')!=digest:raise DeltaRejected('局部更新基準雜湊不符')
        document,members=cache.load(baseline,digest);previous=_CachedBundle(document,members)
        meta=document.get(META,{})
        if request.get('layer_policy')!=meta.get('policy',{}):raise DeltaRejected('局部更新圖層設定已變更')
        keys=request.get('root_keys');node_keys=request.get('node_keys');groups=incoming.scene.get('groups',[])
        if not isinstance(keys,list) or len(set(keys))!=len(keys) or len(keys)!=len(groups) or not isinstance(node_keys,list) or len(node_keys)!=len(keys):raise DeltaRejected('局部群組清單無效')
        if not keys:
            if request.get('no_visual_change') is not True:raise DeltaRejected('空局部清單缺少無可見變動確認')
            shutil.copyfile(baseline,output)
            return [],{'partial_roots':0,'partial_depth':meta.get('partial_depth',0),'no_visual_change':True,
                       'd5_lights':meta.get('lights',[]),'d5_light_ids':meta.get('known_light_ids',[])}
        if meta.get('partial_depth',0)>=8:raise DeltaRejected('局部快照累積8輪，執行完整整理')
        old={g.get('_bridge_scope_key'):g for g in previous.scene.get('groups',[])}
        selected=[];ignored=0
        for key,new,expected in zip(keys,groups,node_keys):
            before=old.get(key)
            if before is None:raise DeltaRejected('局部更新找不到確認群組')
            old_nodes=list(tree(before));new_nodes=list(tree(new))
            if expected!=[g.get('_bridge_scope_key') for g in old_nodes] or len(old_nodes)!=len(new_nodes):raise DeltaRejected('局部更新階層識別不符')
            for a,b,k in zip(old_nodes,new_nodes,expected):
                if a.get('uid')!=b.get('uid') or a.get('layer')!=b.get('layer'):raise DeltaRejected('局部更新群組或圖層識別變更')
                b['_bridge_scope_key']=k
                if b.get('billboard'):raise DeltaRejected('面向相機群組需完整同步')
            if meta.get('policy',{}).get(before.get('layer') or 'Layer 0','normal') in ('hold','exclude'):
                ignored+=1;continue
            selected.append((key,new))
        isolate=any(meta.get('policy',{}).get(g.get('layer') or 'Layer 0')=='hold' for g in old.values()) or meta.get('policy',{}).get('Layer 0')=='hold'
        composer=Composer(isolate,previous)
        composer.members=dict(members);composer.protos=list(previous.scene.get('protos',[]))
        if previous.scene.get('_bridge_visibility_version')==1:
            try:from .visibility import stamp_groups
            except ImportError:from visibility import stamp_groups
            selected=[(key,stamp_groups([g],incoming.scene)[0]) for key,g in selected]
        replacements={key:composer.convert_root(g,incoming,True) for key,g in selected}
        composer.registry(incoming)
        # Retain every unaffected document node and asset exactly; only newly
        # selected groups go through material/resource conversion.
        out=copy.copy(document);scene=copy.copy(previous.scene);out['scene']=scene
        scene['groups']=[replacements.get(g.get('_bridge_scope_key'),g) for g in previous.scene.get('groups',[])]
        scene['protos']=composer.protos
        registry={m['name']:m for m in previous.scene.get('materials',[]) if isinstance(m,dict)}
        registry.update(composer.materials);scene['materials']=list(registry.values())
        updated_meta=copy.deepcopy(meta);out[META]=updated_meta;updated_meta['partial_depth']=meta.get('partial_depth',0)+1
        updated_meta[MATERIAL_BINDINGS]=composer.binding_ledger()
        with zipfile.ZipFile(baseline) as z:origin_bytes=meta.get('partial_origin_bytes',sum(i.file_size for i in z.infolist()))
        updated_meta['partial_origin_bytes']=origin_bytes
        ledger=updated_meta.setdefault('material_aliases_v2',{})
        for k,names in composer.aliases.items():ledger[k]=sorted(set(ledger.get(k,[]))|names)
        if sum(len(v) for v in composer.members.values())-sum(len(v) for _,v in members)>64*1024*1024:raise DeltaRejected('局部資產變動過大，需完整同步')
        encoded=json.dumps(out,ensure_ascii=False,separators=(',',':'))
        total_bytes=len(encoded.encode('utf-8'))+sum(len(v) for v in composer.members.values())
        if total_bytes>max(64*1024*1024,2*origin_bytes):raise DeltaRejected('局部快照容量需完整整理')
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED) as z:
            z.writestr('document.json',encoded)
            for name,value in composer.members.items():z.writestr(name,value)
        if total_bytes<=cache.limit:
            cache.document=out;cache.members=list(composer.members.items());cache.sha256=hashlib.sha256(Path(output).read_bytes()).hexdigest()
        else:cache.document=None;cache.members=None;cache.sha256=None
        affected=[key for key,_ in selected]
        return affected,{'partial_roots':len(affected),'held_or_excluded_roots':ignored,
                         'partial_depth':updated_meta['partial_depth'],'d5_lights':meta.get('lights',[]),
                         'd5_light_ids':meta.get('known_light_ids',[])}
