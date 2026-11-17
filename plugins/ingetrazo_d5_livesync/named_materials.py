# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable named identity; appearance fingerprints remain change evidence."""
import copy
KEY='named_materials_v1'

class MaterialConflict(ValueError):
    def __init__(self,name,reason):
        self.material_name=name;self.reason=reason
        super().__init__(f'材質「{name}」：{reason}。請先在 IngeTrazo 另建不同名稱；尚未傳送，不會自動合併舊材質。')

def visual(attrs):return {k:copy.deepcopy(attrs[k]) for k in ('color','opacity','texture') if k in attrs}

class NamedMaterials:
    def __init__(self,composer,previous):
        self.composer=composer;self.previous=previous;self.changed=set()
        meta=previous.data.get('_bridge_layer_scope',{}) if previous else {}
        stored=meta.get(KEY,{})
        if not isinstance(stored,dict):raise ValueError('名稱材質對應表損壞')
        self.entries=copy.deepcopy(stored);self.references={};self.old_registry={}
        if previous:
            try:from .layer_scope import material_attribute_maps
            except ImportError:from layer_scope import material_attribute_maps
            self.old_registry=composer.source_registry(previous)
            for attrs in material_attribute_maps(previous.scene):
                name=attrs.get('mat')
                if isinstance(name,str):self.references.setdefault(name,composer.convert(visual(attrs),previous,False))
        for name,entry in self.entries.items():
            if not isinstance(name,str) or not isinstance(entry,dict) or not isinstance(entry.get('targets'),dict):raise ValueError('名稱材質對應表無效')
            scopes=entry.get('scopes',{});fingerprints=entry.get('fingerprints',{})
            if not isinstance(scopes,dict) or not isinstance(fingerprints,dict):raise ValueError('名稱材質對應表無效')
            if any(not isinstance(v,list) or any(t not in entry['targets'] for t in v) for v in scopes.values()):raise ValueError('名稱材質群組對應無效')
            for target,record in entry['targets'].items():
                if not isinstance(target,str) or not isinstance(record,dict) or not isinstance(record.get('visual'),dict) or not isinstance(record.get('definition'),dict):raise ValueError('名稱材質對應表無效')
                if previous:
                    record['visual']=composer.convert(record['visual'],previous,False)
                    record['definition']=composer.convert(record.get('definition',{'name':target}),previous,False)

    def resolve(self,out,original,bundle):
        name=original.get('mat')
        if not isinstance(name,str) or not name:return False
        if name.startswith('_scope_'):raise MaterialConflict(name,'來源使用橋接保留前綴，請改用一般材質名稱')
        c=self.composer;fingerprint=c.material_info(original,bundle)[0]
        entry=self.entries.setdefault(name,{'targets':{},'fingerprints':{}})
        targets=entry['targets'];existing=c.bindings.get(c.material_scope,{}).get(fingerprint,set())
        existing={n for n in existing if isinstance(n,str)}
        scoped=entry.setdefault('scopes',{}).get(c.material_scope,[])
        old_aliases={n for values in c.bindings.get(c.material_scope,{}).values() for n in values if isinstance(n,str) and n.startswith('_scope_')}
        if not existing and not scoped and old_aliases and name in self.old_registry:
            raise MaterialConflict(name,'舊分支的外觀已改變，請先核對原材質後再建立新名稱')
        if len(existing)>1:raise MaterialConflict(name,'同一來源有多個舊識別')
        if len(existing)==1:target=next(iter(existing))
        elif len(scoped)==1:target=scoped[0]
        elif len(scoped)>1:raise MaterialConflict(name,'此群組保留了多個舊材質版本，不能自動選擇')
        elif len(targets)==1:target=next(iter(targets))
        elif len(targets)>1:raise MaterialConflict(name,'同名舊材質各有不同 D5 關聯，無法安全選擇')
        elif name in self.references:target=name
        elif self.previous and name in self.old_registry:
            raise MaterialConflict(name,'舊專案找不到可確認的名稱對應')
        else:target=name
        if target not in targets:
            reference=self.references.get(target,visual(out))
            definition=copy.deepcopy(self.old_registry.get(target,c.source_registry(bundle).get(name,{})))
            definition['name']=target
            targets[target]={'visual':reference,'definition':definition}
        entry['scopes'][c.material_scope]=sorted(set(scoped)|{target})
        record=targets[target]
        def actual(t,r):
            opacity=r['visual'].get('opacity')
            suffix=f' {int(float(opacity)*100)}%' if opacity is not None and float(opacity)<1 else ''
            return (t+suffix)[:63]
        for other,other_entry in self.entries.items():
            for other_target,other_record in other_entry['targets'].items():
                if other_target!=target and actual(other_target,other_record)==actual(target,record):
                    raise MaterialConflict(name,'名稱截短後與另一份材質相同，無法安全建立關聯')
                if other!=name and other_target==target:raise MaterialConflict(name,'此內部名稱已屬於另一份來源材質')
        previous_fingerprint=entry.setdefault('fingerprints',{}).get(c.material_scope)
        if previous_fingerprint is not None and previous_fingerprint!=fingerprint:self.changed.add(name)
        entry['fingerprints'][c.material_scope]=fingerprint
        # Protect the established appearance and SDK-owned datablock. Current
        # UV placement still belongs to geometry, so keep its projection values.
        incoming_texture=out.get('texture',{})
        for field in ('color','opacity','texture'):out.pop(field,None)
        out.update(copy.deepcopy(record['visual']))
        if isinstance(out.get('texture'),dict) and isinstance(incoming_texture,dict):
            for field in ('uvw','sw','sh'):
                if field in incoming_texture:out['texture'][field]=copy.deepcopy(incoming_texture[field])
        out['mat']=target;out['_bridge_named_target']=target
        c.materials[target]=copy.deepcopy(record['definition'])
        c.bindings.setdefault(c.material_scope,{}).setdefault(fingerprint,set()).add(target)
        if target.startswith('_scope_'):c.aliases.setdefault(fingerprint,set()).add(target)
        return True
