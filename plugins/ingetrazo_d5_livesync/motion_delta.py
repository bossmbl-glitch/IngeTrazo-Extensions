# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate/apply an absolute placement patch to a confirmed full baseline."""
import hashlib,json,math,zipfile
from pathlib import Path

META='_bridge_layer_scope'
class DeltaRejected(ValueError):pass


class PatchCache:
    """One verified baseline, bounded by its uncompressed archive size.

    Reuse decoded JSON and immutable member bytes, never geometry or SDK IDs.
    The on-disk SHA256 must match on every use, including after a failed send.
    """
    limit=128*1024*1024

    def __init__(self):self.sha256=None;self.document=None;self.members=None

    def load(self,path,digest):
        if self.sha256==digest and self.document is not None:return self.document,self.members
        self.sha256=None;self.document=None;self.members=None
        with zipfile.ZipFile(path) as source:
            document=json.loads(source.read('document.json'))
            members=[(i.filename,source.read(i.filename)) for i in source.infolist() if i.filename!='document.json']
            if sum(i.file_size for i in source.infolist())<=self.limit:
                self.sha256=digest;self.document=document;self.members=members
        return document,members

    def prime(self,path,digest):self.load(path,digest)


def same(a,b):return isinstance(a,(list,tuple)) and isinstance(b,(list,tuple)) and len(a)==len(b)==16 and all(abs(float(x)-float(y))<2e-6 for x,y in zip(a,b))


def valid_matrix(values):
    return isinstance(values,(list,tuple)) and len(values)==16 and all(isinstance(x,(int,float)) and math.isfinite(x) for x in values) and all(abs(values[c*4+3]-(1 if c==3 else 0))<1e-5 for c in range(4))


def prepare_patch(baseline,payload,ack,output,cache=None):
    baseline=Path(baseline);output=Path(output)
    if not isinstance(payload,dict):raise DeltaRejected('快速資料格式不正確')
    if payload.get('protocol')!=1 or payload.get('base_revision')!=ack or ack<1:raise DeltaRejected('快速更新基準版本不一致')
    digest=hashlib.sha256(baseline.read_bytes()).hexdigest()
    if digest!=payload.get('base_model_sha256'):raise DeltaRejected('快速更新基準雜湊不一致')
    cache=cache if cache is not None else PatchCache()
    document,members=cache.load(baseline,digest)
    try:
        meta=document.get(META,{})
        if meta.get('policy',{})!=payload.get('layer_policy',{}):raise DeltaRejected('圖層模式已變更，需完整同步')
        nodes={};layers={}
        def walk(groups,layer=None):
            for g in groups:
                k=g.get('_bridge_scope_key')
                root_layer=layer if layer is not None else g.get('layer','Layer 0')
                if k in nodes:raise DeltaRejected('橋接群組識別重複')
                nodes[k]=g;layers[k]=root_layer
                walk(g.get('children',[]),root_layer)
        walk(document['scene'].get('groups',[]))
        changes=payload.get('changes');updates=[];seen=set();held=0
        if not isinstance(changes,list) or not changes:raise DeltaRejected('快速更新沒有有效變換')
        for row in changes:
            if not isinstance(row,dict):raise DeltaRejected('快速群組資料格式不正確')
            k=row.get('key')
            if not isinstance(k,str) or k in seen:raise DeltaRejected('快速群組識別格式錯誤')
            seen.add(k)
            if not valid_matrix(row.get('before')) or not valid_matrix(row.get('after')):raise DeltaRejected('快速變換矩陣格式錯誤')
            mode=meta.get('policy',{}).get(row.get('root_layer'),'normal')
            if mode in ('hold','exclude'):held+=1;continue
            g=nodes.get(k)
            if g is None or layers[k]!=row.get('root_layer') or not same(g.get('xform'),row['before']):raise DeltaRejected('群組基準與快速資料不符')
            if g.get('billboard'):raise DeltaRejected('面向相機物件需完整同步')
            updates.append((k,row['before'],row['after']))
        # Do not modify the cached baseline until every row has been checked.
        for k,before,after in updates:nodes[k]['xform']=list(after)
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED) as target:
            target.writestr('document.json',json.dumps(document,ensure_ascii=False,separators=(',',':')))
            for name,data in members:target.writestr(name,data)
        # A later failed send still uses the prior disk baseline: its different
        # digest causes a reload, rather than trusting this unconfirmed patch.
        if cache.document is document:cache.sha256=hashlib.sha256(output.read_bytes()).hexdigest()
    except Exception:
        cache.sha256=None;cache.document=None;cache.members=None
        raise
    return updates,{'changed_groups':len(updates),'held_or_excluded_groups':held,
                    'd5_lights':meta.get('lights',[]),'d5_light_ids':meta.get('known_light_ids',[])}


def apply_placements(bpy,updates,model_file):
    from mathutils import Matrix
    from ingetrazo_io.igz import matrix_from_column_major
    objects={};duplicate=set()
    for obj in bpy.context.view_layer.objects:
        if obj.get('ingetrazo_doc')!=str(model_file):continue
        k=obj.get('ingetrazo_key')
        if k in objects:duplicate.add(k)
        objects[k]=obj
    assignments=[]
    for k,before,after in updates:
        obj=objects.get(k)
        if obj is None or k in duplicate:raise DeltaRejected('背景物件識別不完整')
        old=Matrix(matrix_from_column_major(before));new=Matrix(matrix_from_column_major(after))
        try:
            delta=new@old.inverted()
            if old.determinant()<=0 or new.determinant()<=0 or any(abs(sum(delta[k][i]*delta[k][j] for k in range(3))-(1. if i==j else 0.))>2e-4 for i in range(3) for j in range(3)):raise DeltaRejected('快速更新僅支援純移動／旋轉')
        except ValueError:raise DeltaRejected('背景變換不可逆') from None
        def close(a,b):return all(abs(a[r][c]-b[r][c])<2e-5 for r in range(4) for c in range(4))
        if not (close(obj.matrix_basis,old) or close(obj.matrix_basis,new)):raise DeltaRejected('背景物件已偏離確認的位置')
        if obj.constraints or obj.modifiers or (obj.type=='MESH' and obj.data.shape_keys):raise DeltaRejected('背景物件需完整重建')
        assignments.append((obj,new))
    for obj,value in assignments:obj.matrix_basis=value
    bpy.context.view_layer.update()


def refresh_report(report,bpy,revision):
    objects={o.get('ingetrazo_key'):o for o in bpy.context.view_layer.objects if o.get('ingetrazo_key')}
    for group in report.get('groups',[])+report.get('assembly_groups',[]):
        for version in group.get('variants',[]):
            for member in version.get('members',[]):
                obj=objects.get(member.get('object_id'))
                if obj is not None:member['matrix_world']=[float(x) for row in obj.matrix_world for x in row]
    report['revision']=revision;report['geometry_scan_reused']=True
    return report
