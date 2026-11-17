# SPDX-License-Identifier: GPL-3.0-or-later
"""Tiny certified layer visibility requests; never decode/rebuild geometry."""
import copy
import hashlib
import json
from pathlib import Path
import zipfile
try:
    from .motion_delta import DeltaRejected,PatchCache
    from .layer_scope import META
    from .visibility import VERSION_KEY,VISIBLE,LOOSE,stamp_groups,layer_visibility
except ImportError:
    from motion_delta import DeltaRejected,PatchCache
    from layer_scope import META
    from visibility import VERSION_KEY,VISIBLE,LOOSE,stamp_groups,layer_visibility


def certify(certificate,baseline):
    current=certificate.capture()
    if current['groups']!=baseline['groups'] or current['loose']!=baseline['loose'] or current['materials']!=baseline['materials']:
        raise ValueError('visibility_not_only_change')
    old=json.loads(baseline['environment']);new=json.loads(current['environment'])
    def split(env):
        env=copy.deepcopy(env);layers=env.pop('layers',[]);env.pop('active_layer',None)
        visible={};rest=[]
        for layer in layers:
            row=dict(layer);name=row.get('name')
            if not isinstance(name,str) or name in visible:raise ValueError('visibility_layer_identity')
            visible[name]=row.pop('visible',True);rest.append(row)
        return visible,rest,env
    before,old_layers,old_env=split(old);after,new_layers,new_env=split(new)
    if old_layers!=new_layers or old_env!=new_env or before==after:raise ValueError('visibility_global_change')
    if any(type(v) is not bool for v in [*before.values(),*after.values()]):raise ValueError('visibility_invalid_flag')
    for path,stamp in baseline['dependencies'].items():
        if stamp is None or certificate._stat(path)!=stamp:raise ValueError('texture_file_changed')
    current['dependencies']=baseline['dependencies']
    return {'before':before,'after':after},current


def prepare(baseline,payload,ack,output,cache=None):
    cache=cache or PatchCache();digest=hashlib.sha256(Path(baseline).read_bytes()).hexdigest()
    if payload.get('protocol')!=1 or payload.get('base_revision')!=ack or ack<1 or payload.get('base_model_sha256')!=digest:
        raise DeltaRejected('顯示更新基準不符')
    document,members=cache.load(baseline,digest);document=copy.deepcopy(document)
    scene=document['scene'];meta=document.get(META,{})
    if scene.get(VERSION_KEY)!=1 or meta.get('policy',{})!=payload.get('layer_policy'):raise DeltaRejected('顯示更新政策不符')
    before=payload.get('before');after=payload.get('after')
    if not isinstance(before,dict) or not isinstance(after,dict) or set(before)!=set(after) or any(type(v) is not bool for v in [*before.values(),*after.values()]):raise DeltaRejected('顯示圖層清單不符')
    if {'Layer 0':True,**before}!={'Layer 0':True,**meta.get('source_layer_visibility',layer_visibility(scene))}:raise DeltaRejected('顯示更新來源基準不符')
    source_layers=[{'name':name,'visible':visible} for name,visible in after.items()]
    policy=meta.get('policy',{});groups=[];changed=0
    for group in scene.get('groups',[]):
        if policy.get(group.get('layer') or 'Layer 0','normal')=='hold':groups.append(group);continue
        stamped=stamp_groups([group],{'layers':source_layers})[0]
        def count(a,b):return int(a.get(VISIBLE)!=b.get(VISIBLE))+sum(count(x,y) for x,y in zip(a.get('children',[]),b.get('children',[])))
        changed+=count(group,stamped);groups.append(stamped)
    scene['groups']=groups
    if policy.get('Layer 0','normal')!='hold':
        new=after.get('Layer 0',True);changed+=int(scene.get(LOOSE)!=new);scene[LOOSE]=new
    meta['source_layer_visibility']=after;document[META]=meta
    with zipfile.ZipFile(output,'w',zipfile.ZIP_STORED) as z:
        z.writestr('document.json',json.dumps(document,ensure_ascii=False,separators=(',',':')))
        for name,data in members:z.writestr(name,data)
    return scene,{'visibility_objects_changed':changed,'d5_lights':meta.get('lights',[]),'d5_light_ids':meta.get('known_light_ids',[])}
