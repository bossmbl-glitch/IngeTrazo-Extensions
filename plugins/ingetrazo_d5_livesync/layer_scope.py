# SPDX-License-Identifier: GPL-3.0-or-later
"""Compose a complete D5 input from current roots and last confirmed roots.

No mesh decoding, triangulation or source document mutation. A root group owns
its entire subtree so transforms/inherited paint remain a coherent snapshot.
"""
from __future__ import annotations
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

DEFAULT_LAYER = 'Layer 0'
MODES = {'normal', 'hold', 'exclude'}
POLICY_KEY = 'layer_sync_modes_v1'
META = '_bridge_layer_scope'
MATERIAL_BINDINGS = 'material_bindings_v1'


def clean_policy(raw):
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError('圖層同步設定格式無效')
    result = {}
    for name, mode in raw.items():
        if not isinstance(name, str) or mode not in MODES:
            raise ValueError('圖層同步設定含有未知狀態')
        if mode != 'normal':
            result[name or DEFAULT_LAYER] = mode
    return result


def host_policy(scene):
    data = (getattr(scene, 'plugin_data', {}) or {}).get('ingetrazo_d5_livesync', {}) or {}
    return clean_policy(data.get(POLICY_KEY, {}))


def layer_name(value):
    if isinstance(value, str):
        return value or DEFAULT_LAYER
    if isinstance(value, dict):
        return str(value.get('name') or DEFAULT_LAYER)
    return str(getattr(value, 'name', DEFAULT_LAYER) or DEFAULT_LAYER)


def host_inventory(scene):
    counts = {DEFAULT_LAYER: 0}
    for layer in getattr(scene, 'layers', []) or []:
        counts.setdefault(layer_name(layer), 0)
    for group in getattr(scene, 'groups', []) or []:
        name = layer_name(getattr(group, 'layer', DEFAULT_LAYER))
        counts[name] = counts.get(name, 0) + 1
    for name in host_policy(scene):
        counts.setdefault(name, 0)
    return counts


def light_layers(scene):
    # Legacy non-proxy lights belong to Layer 0. Nested proxies follow their
    # root owner's layer exactly like geometry, including parent movement.
    from .light_proxy import metadata, identity
    result = {}
    def walk(group, owner, ancestors):
        if id(group) in ancestors:
            raise ValueError('模型群組循環，無法同步')
        data = metadata(group)
        if data:
            result[identity(group, data)] = owner
        for child in getattr(group, 'children', []) or []:
            walk(child, owner, ancestors | {id(group)})
    for group in getattr(scene, 'groups', []) or []:
        walk(group, layer_name(getattr(group, 'layer', DEFAULT_LAYER)), set())
    return result


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


class Bundle:
    def __init__(self, path):
        self.path = Path(path)
        self.archive = None
        if zipfile.is_zipfile(self.path):
            self.archive = zipfile.ZipFile(self.path)
            try:
                self.data = json.loads(self.archive.read('document.json'))
            except Exception:
                self.archive.close()
                raise
        else:
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(self.data.get('scene'), dict):
            self.close()
            raise ValueError('快照沒有有效的 scene')
        self.scene = self.data['scene']

    def member(self, name):
        if not self.archive:
            raise ValueError('二進位幾何／貼圖缺少封裝：' + name)
        return self.archive.read(name)  # ZipFile verifies CRC here.

    def close(self):
        if self.archive:
            self.archive.close()
            self.archive = None


def _roots(bundle, scoped):
    # Pin the importer's original full-document keys before removing groups.
    # Otherwise excluding a duplicate UID elsewhere changes a held child's
    # key from its tree path to uid/X, dropping its established association.
    counts = {}
    def count(groups):
        for group in groups or []:
            uid = group.get('uid')
            if uid:counts[uid] = counts.get(uid,0)+1
            count(group.get('children'))
    def pin(groups, parent=None):
        for index, group in enumerate(groups or []):
            uid = group.get('uid')
            fallback = f'{parent}/{index}' if parent is not None else f'g/{index}'
            key = group.get('_bridge_scope_key') or (f'uid/{uid}' if uid and counts.get(uid)==1 else fallback)
            group['_bridge_scope_key'] = key
            pin(group.get('children'), key)
    count(bundle.scene.get('groups'));pin(bundle.scene.get('groups'))
    result = {}
    for index, group in enumerate(bundle.scene.get('groups', []) or []):
        uid = group.get('uid')
        if scoped and not uid:
            raise ValueError('圖層控制需要群組固定識別；有最外層群組缺少 uid，已停止傳送')
        key = 'uid/' + str(uid) if uid else 'index/' + str(index)
        if key in result:
            raise ValueError('最外層群組識別重複，已停止以避免套錯圖層')
        result[key] = group
    return result


def _finish(chosen, name, picture, opacity):
    # Reuse the bundled pure resolver without importing its bpy-based package.
    global _finish_module
    if '_finish_module' not in globals():
        spec = importlib.util.spec_from_file_location('bridge_scope_finish',
                Path(__file__).parent / 'vendor/ingetrazo_io/finish.py')
        _finish_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_finish_module)
    return _finish_module.resolve(chosen, name, picture, opacity)


def material_attribute_maps(scene,groups=None,include_loose=True):
    """Walk rendered material maps, never vertex/UV arrays or edit recipes."""
    protos=scene.get('protos',[]) or [];visited=set()
    def attributes(mesh):
        for attrs in mesh.get('faces',[]) or []:
            if isinstance(attrs,dict) and any(k in attrs for k in ('mat','color','opacity','texture')):
                yield attrs
                if isinstance(attrs.get('back'),dict):yield attrs['back']
        geom=mesh.get('geom')
        if isinstance(geom,dict):
            for attrs in geom.get('face_attrs',[]) or []:
                if isinstance(attrs,dict):
                    yield attrs
                    if isinstance(attrs.get('back'),dict):yield attrs['back']
    if include_loose:yield from attributes(scene)
    stack=list(groups if groups is not None else scene.get('groups',[]) or [])
    while stack:
        group=stack.pop()
        if any(k in group for k in ('mat','color','opacity','texture')):yield group
        if isinstance(group.get('material'),dict):yield group['material']
        if 'proto' in group:
            index=int(group['proto'])
            if not 0<=index<len(protos):raise ValueError('群組剖面索引無效')
            if index not in visited:
                visited.add(index);yield from attributes(protos[index])
        else:yield from attributes(group)
        stack.extend(group.get('children',[]) or [])


def referenced_scope_materials(scene):
    return {attrs['mat'] for attrs in material_attribute_maps(scene)
            if isinstance(attrs.get('mat'),str) and attrs['mat'].startswith('_scope_')}


class Composer:
    def __init__(self, isolate_current, previous=None, compact_legacy=False):
        self.members = {}
        self.asset_map = {}
        self.protos = []
        self.proto_map = {}
        self.proto_hash = {}
        self.materials = {}
        # Existing identities outlive policy changes. Only genuinely new
        # appearances may acquire a new isolated name after a baseline exists.
        self.isolate_current = bool(isolate_current or previous)
        self.compact_legacy = compact_legacy
        self.registry_maps = {}
        self.definition_keys = {}
        self.material_info_cache = {}
        self.finish_cache = {}
        self.canonical_keys = {}
        self.simple_legacy_names = {}
        self.candidates = {}
        self.aliases = {}
        self.retained_legacy = set()
        self.previous_names = referenced_scope_materials(previous.scene) if previous else set()
        previous_meta = previous.data.get(META,{}) if previous else {}
        self.alias_history = previous_meta.get('material_aliases_v2',{})
        if not isinstance(self.alias_history,dict):raise ValueError('舊材質對應表格式無效，已停止以保護關聯')
        self.material_scope='@loose'
        self.bindings={}
        self.unmapped_scopes={}
        stored=previous_meta.get(MATERIAL_BINDINGS,{})
        if not isinstance(stored,dict):raise ValueError('材質識別對應表格式無效，已停止以保護關聯')
        for scope,keys in stored.items():
            if not isinstance(scope,str) or not isinstance(keys,dict):raise ValueError('材質識別對應表格式無效')
            self.bindings[scope]={}
            for key,names in keys.items():
                if not isinstance(key,str) or not isinstance(names,list) or not names or any(n is not None and not isinstance(n,str) for n in names):
                    raise ValueError('材質識別對應表格式無效')
                self.bindings[scope][key]=set(names)
        reverse={}
        for key,names in self.alias_history.items():
            if isinstance(names,list):
                for name in names:
                    if isinstance(name,str) and name.startswith('_scope_'):
                        self.previous_names.add(name);reverse.setdefault(name,set()).add(key)
        if previous:
            scopes=[('@loose',None)] + [(self.scope_key(g),[g]) for g in previous.scene.get('groups',[])]
            for scope,groups in scopes:
                for attrs in material_attribute_maps(previous.scene,groups or [],include_loose=groups is None):
                    if not any(k in attrs for k in ('mat','color','opacity','texture')):continue
                    name=attrs.get('mat')
                    if isinstance(name,str) and name.startswith('_scope_'):
                        keys=reverse.get(name,set())
                        if not keys:self.unmapped_scopes.setdefault(scope,set()).add(name)
                    else:keys={self.material_info(attrs,previous)[0]}
                    for key in keys:self.bindings.setdefault(scope,{}).setdefault(key,set()).add(name)
        for keys in self.bindings.values():
            for names in keys.values():self.previous_names.update(n for n in names if isinstance(n,str) and n.startswith('_scope_'))

    @staticmethod
    def scope_key(group):
        return group.get('_bridge_scope_key') or ('uid/'+str(group['uid']) if group.get('uid') else 'g/unknown')

    def convert_root(self,group,bundle,current=False):
        prior=self.material_scope;self.material_scope=self.scope_key(group)
        try:return self.convert(group,bundle,current)
        finally:self.material_scope=prior

    def binding_ledger(self):
        return {scope:{key:sorted(names,key=lambda n:(n is not None,n or '')) for key,names in keys.items()}
                for scope,keys in self.bindings.items()}

    def asset(self, bundle, name):
        cache_key = (id(bundle), name)
        if cache_key in self.asset_map:
            return self.asset_map[cache_key]
        data = bundle.member(name)
        target = name
        if target in self.members and self.members[target] != data:
            # Old frozen members keep their original names. Only conflicting
            # incoming bytes are namespaced; all references are rewritten.
            p = Path(name)
            target = str(p.parent / (p.stem + '-scope-' + hashlib.sha256(data).hexdigest()[:20] + p.suffix)).replace('\\', '/')
        if target in self.members and self.members[target] != data:
            raise ValueError('封裝資產識別衝突')
        self.members[target] = data
        self.asset_map[cache_key] = target
        return target

    def convert(self, value, bundle, current=False):
        if isinstance(value, list):
            return [self.convert(x, bundle, current) for x in value]
        if not isinstance(value, dict):
            return value
        out = {}
        for key, item in value.items():
            if key == 'ext' and isinstance(item, dict):
                # Same nonvisual recipe filter as 0.8's source snapshot. Also
                # handles a raw migration fixture without copying huge edit
                # histories into every retained complete scope snapshot.
                item = {k:v for k,v in item.items() if k not in
                        ('lime_face_auto','lime_suite_parametric')}
            if key == 'proto':
                source_index = int(item)
                cache_key = (id(bundle), source_index, current,self.material_scope)
                if cache_key not in self.proto_map:
                    protos = bundle.scene.get('protos', []) or []
                    if not 0 <= source_index < len(protos):
                        raise ValueError('群組剖面索引無效')
                    converted = self.convert(protos[source_index], bundle, current)
                    sig = fingerprint(converted)
                    if sig not in self.proto_hash:
                        self.proto_hash[sig] = len(self.protos)
                        self.protos.append(converted)
                    self.proto_map[cache_key] = self.proto_hash[sig]
                out[key] = self.proto_map[cache_key]
            elif key in ('vertices','holes','xform','axes','uvw') and isinstance(item,(list,tuple)):
                # Numeric geometry/projection arrays are immutable here. Reuse
                # parsed arrays; only material/resource mappings need walking.
                out[key] = item
            elif key in ('embed', 'member') and isinstance(item, str):
                out[key] = self.asset(bundle, item)
            else:
                out[key] = self.convert(item, bundle, current)
        # A source texture is normally embedded by the host exporter. Reject
        # a missing referenced member instead of silently making a white mat.
        if current and any(k in value for k in ('mat', 'texture', 'color', 'opacity')):
            # Only visual attribute mappings, never a layer's display color or
            # arbitrary plugin data. Callers convert registry separately.
            if 'vertices' not in value and 'a' in value and 'b' in value:
                return out
            self.isolate(out, value, bundle)
        return out

    def source_registry(self,bundle):
        key=id(bundle)
        if key not in self.registry_maps:
            self.registry_maps[key] = {m.get('name'):m for m in bundle.scene.get('materials',[])
                                       if isinstance(m,dict)}
        return self.registry_maps[key]

    def material_info(self,attrs,bundle):
        cache_key=(id(bundle),id(attrs))
        if cache_key in self.material_info_cache:return self.material_info_cache[cache_key]
        reg=attrs.get('mat');definition=self.source_registry(bundle).get(reg,{})
        visual={k:attrs[k] for k in ('mat','color','opacity') if k in attrs}
        tex=attrs.get('texture')
        rewritten=None
        if isinstance(tex,dict):
            rewritten=dict(tex)
            if isinstance(tex.get('embed'),str):rewritten['embed']=self.asset(bundle,tex['embed'])
            visual['texture']={k:rewritten[k] for k in ('embed','path') if k in rewritten}
        dk=(id(bundle),reg)
        if dk not in self.definition_keys:
            normalized={k:v for k,v in definition.items() if k!='texture'}
            if isinstance(definition.get('texture'),dict):
                normalized['texture']={k:definition['texture'][k] for k in ('embed','path') if k in definition['texture']}
            self.definition_keys[dk]=normalized
        token=(id(bundle),tuple(sorted((k,repr(v)) for k,v in visual.items())))
        if token not in self.canonical_keys:
            self.canonical_keys[token]=fingerprint((visual,self.definition_keys[dk]))
        key=self.canonical_keys[token]
        legacy=None
        if self.previous_names:
            old_visual={k:attrs[k] for k in ('mat','texture','color','opacity') if k in attrs}
            if rewritten is not None:old_visual['texture']=rewritten
            if rewritten is None:
                old_token=(id(bundle),tuple(sorted((k,repr(v)) for k,v in old_visual.items())))
                if old_token not in self.simple_legacy_names:
                    self.simple_legacy_names[old_token]='_scope_'+fingerprint((old_visual,definition))[:24]
                legacy=self.simple_legacy_names[old_token]
            else:
                legacy='_scope_'+fingerprint((old_visual,definition))[:24]
        picture=Path((tex or {}).get('embed') or (tex or {}).get('path') or '').name
        if key not in self.finish_cache:
            self.finish_cache[key]=_finish(definition.get('finish'),reg,picture,attrs.get('opacity'))
        finish=self.finish_cache[key]
        info=(key,legacy,finish)
        self.material_info_cache[cache_key]=info
        return info

    def prepare_materials(self,groups,bundle,loose=None):
        # Only opt-in legacy compaction needs a prior pass to select an
        # existing representative. Default mode resolves exact IDs lazily.
        if not (self.isolate_current and self.compact_legacy and self.previous_names):return
        for attrs in material_attribute_maps(bundle.scene,groups,include_loose=loose is not None):
            key,legacy,_=self.material_info(attrs,bundle)
            if legacy in self.previous_names:self.candidates.setdefault(key,set()).add(legacy)

    def isolate(self, out, original, bundle):
        key,legacy,finish=self.material_info(original,bundle)
        retained=self.bindings.get(self.material_scope,{}).get(key,set())
        unmapped=self.unmapped_scopes.get(self.material_scope,set())
        if not retained and unmapped and legacy not in unmapped:
            raise ValueError('舊隔離材質缺少可確認的對應，已停止傳送以保護 D5 材質')
        history=self.alias_history.get(key,[])
        history=[name for name in history if isinstance(name,str) and name in self.previous_names] if isinstance(history,list) else []
        candidates=set(self.candidates.get(key,())) | set(history)
        if legacy in retained:
            name=legacy
        elif len(retained)==1:
            name=next(iter(retained))
        elif retained and not self.compact_legacy:
            raise ValueError('材質識別有多個既有對應，已停止傳送以避免覆寫 D5 材質')
        elif not self.isolate_current:
            name=original.get('mat')
        elif not self.compact_legacy and legacy in self.previous_names:
            # Default upgrade path: do not collapse IDs which may have
            # different overrides inside the D5 project we cannot inspect.
            name=legacy;self.retained_legacy.add(name)
        elif len(history)==1:
            name=history[0]
        elif self.compact_legacy and candidates:
            # Explicit legacy compaction keeps one deterministic existing ID.
            # Other IDs' independent D5 edits cannot all survive a merge.
            name=sorted(candidates)[0]
        elif len(candidates)==1:
            name=next(iter(candidates))
        else:
            name='_scope_'+key[:24]
        if name is None:out.pop('mat',None)
        else:out['mat']=name
        self.bindings.setdefault(self.material_scope,{}).setdefault(key,set()).add(name)
        if isinstance(name,str) and name.startswith('_scope_'):
            self.aliases.setdefault(key,set()).add(name)
            self.materials[name]={'name':name,'finish':finish}

    def registry(self, bundle):
        for material in bundle.scene.get('materials', []) or []:
            if isinstance(material, dict) and material.get('name') not in self.materials:
                self.materials[material.get('name')] = self.convert(material, bundle, False)


def compose(current_path, baseline_path, policy, output_path, lights=None,
            light_layer_map=None, known_light_ids=None, compact_legacy=False):
    """Return metrics + complete light batch. Caller commits baseline only
    after D5 send AND atomic Blender-ID checkpoint both succeed.
    """
    policy = clean_policy(policy)
    current = Bundle(current_path)
    previous = None
    try:
        previous = Bundle(baseline_path) if baseline_path else None
        if any(mode == 'hold' for mode in policy.values()) and previous is None:
            raise ValueError('尚無0.9版成功同步基準；請先將圖層設為正常同步並成功更新一次，再暫時不更新')
        if compact_legacy and any(mode=='hold' for mode in policy.values()):
            raise ValueError('暫時不更新時不可執行舊材質整理；請關閉材質整理以保護既有 D5 關聯')
        incoming = _roots(current, bool(policy))
        old = _roots(previous, bool(policy)) if previous else {}
        selected_old, selected_new = [], []
        omitted = 0
        for key in dict.fromkeys([*old, *incoming]):
            before, after = old.get(key), incoming.get(key)
            old_layer = (before or {}).get('layer') or DEFAULT_LAYER
            new_layer = (after or {}).get('layer') or DEFAULT_LAYER
            # Explicit exclusion wins over hold, including moves between tags.
            modes = [policy.get(new_layer, 'normal')] if after else []
            if before:
                modes.append(policy.get(old_layer, 'normal'))
            excluded = ((after is not None and policy.get(new_layer,'normal') == 'exclude') or
                        (after is None and policy.get(old_layer,'normal') == 'exclude') or
                        (after is not None and policy.get(new_layer,'normal') == 'hold' and
                         before is not None and policy.get(old_layer,'normal') == 'exclude'))
            if excluded:
                omitted += 1
            elif 'hold' in modes:
                if before:
                    selected_old.append(before)
                else:
                    omitted += 1
            elif after:
                selected_new.append(after)
        loose_mode = policy.get(DEFAULT_LAYER, 'normal')
        frozen_loose = loose_mode == 'hold' and previous is not None
        composer = Composer(bool(selected_old) or frozen_loose, previous, compact_legacy)
        try:from .visibility import stamp_groups,loose_visibility,VERSION_KEY,LOOSE
        except ImportError:from visibility import stamp_groups,loose_visibility,VERSION_KEY,LOOSE
        # Snapshot each root's display, independent from shared tag collections.
        # Keeping original tag/material names avoids identity changes on hide.
        selected_old=stamp_groups(selected_old,previous.scene,preserve=True) if previous else []
        selected_new=stamp_groups(selected_new,current.scene)
        old_groups = [composer.convert_root(g, previous) for g in selected_old]
        if frozen_loose:
            faces = composer.convert(previous.scene.get('faces', []), previous)
            edges = composer.convert(previous.scene.get('edges', []), previous)
        else:
            faces, edges = [], []
        if previous:
            composer.registry(previous)
        composer.prepare_materials(selected_new,current,
                                   {'faces':current.scene.get('faces',[]),'edges':current.scene.get('edges',[])} if loose_mode=='normal' else None)
        new_groups = [composer.convert_root(g, current, True) for g in selected_new]
        if loose_mode == 'normal':
            faces = composer.convert(current.scene.get('faces', []), current, True)
            edges = composer.convert(current.scene.get('edges', []), current, True)
        # Preserve all current document-level metadata, resolve assets, then
        # overwrite the scope-owned payload. These are bridge copies only.
        scene = {k: copy.deepcopy(v) for k,v in current.scene.items()
                 if k not in ('groups','protos','faces','edges','materials','layers')}
        # Document metadata may contain camera image refs. Resolve them too.
        scene = composer.convert(scene, current, False)
        composer.registry(current)
        layers = {x.get('name'): copy.deepcopy(x) for x in current.scene.get('layers', []) if isinstance(x,dict)}
        if previous:
            for layer in previous.scene.get('layers', []) or []:
                if policy.get(layer.get('name'), 'normal') == 'hold':
                    layers[layer.get('name')] = copy.deepcopy(layer)
        seen_keys = set()
        def validate_keys(groups):
            for group in groups:
                key = group.get('_bridge_scope_key')
                if key in seen_keys:
                    raise ValueError('合併後群組識別衝突，請修正重複識別後再同步')
                seen_keys.add(key)
                validate_keys(group.get('children',[]) or [])
        validate_keys(old_groups+new_groups)
        scene.update(groups=old_groups+new_groups,protos=composer.protos,
                     faces=faces,edges=edges,materials=list(composer.materials.values()),layers=list(layers.values()))
        scene[VERSION_KEY]=1
        scene[LOOSE]=loose_visibility(previous.scene,preserve=True) if frozen_loose else loose_visibility(current.scene)
        previous_meta = previous.data.get(META, {}) if previous else {}
        previous_lights = {x['id']: x for x in previous_meta.get('lights', [])}
        current_lights = {x['id']: x for x in lights or []}
        old_map = previous_meta.get('light_layers', {})
        new_map = light_layer_map or {}
        effective_lights, effective_map = [], {}
        for key in dict.fromkeys([*previous_lights,*current_lights]):
            before, after = previous_lights.get(key), current_lights.get(key)
            modes = []
            if before:modes.append(policy.get(old_map.get(key,DEFAULT_LAYER),'normal'))
            if after:modes.append(policy.get(new_map.get(key,DEFAULT_LAYER),'normal'))
            if 'exclude' in modes:continue
            if 'hold' in modes:
                if before:
                    effective_lights.append(before);effective_map[key]=old_map.get(key,DEFAULT_LAYER)
            elif after:
                effective_lights.append(after);effective_map[key]=new_map.get(key,DEFAULT_LAYER)
        known = sorted(set(known_light_ids or []) | set(previous_meta.get('known_light_ids',[])) |
                       set(previous_lights) | set(current_lights))
        alias_ledger={key:sorted(names) for key,names in composer.aliases.items()}
        old_ledger=previous_meta.get('material_aliases_v2',{})
        if isinstance(old_ledger,dict):
            for key,names in old_ledger.items():
                if isinstance(names,list):
                    kept={name for name in names if isinstance(name,str) and name.startswith('_scope_')}
                    alias_ledger[key]=sorted(kept|set(alias_ledger.get(key,[])))
        meta = {'format':1,'policy':policy,'lights':effective_lights,
                'light_layers':effective_map,'known_light_ids':known,
                'material_aliases_v2':alias_ledger,MATERIAL_BINDINGS:composer.binding_ledger()}
        output = {k:copy.deepcopy(v) for k,v in current.data.items() if k not in ('scene',META)}
        output.update(scene=scene)
        output[META] = meta
        output_path=Path(output_path)
        with zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            archive.writestr('document.json',json.dumps(output,ensure_ascii=False,separators=(',',':')))
            for name,data in composer.members.items():archive.writestr(name,data)
        return {'held_root_groups':len(selected_old),'normal_root_groups':len(selected_new),
                'omitted_root_groups':omitted,'loose_mode':loose_mode,
                'layer_policy':policy,'d5_lights':effective_lights,'d5_light_ids':known,
                'isolated_materials':len({name for names in composer.aliases.values() for name in names}),
                'logical_materials':len(composer.aliases),'legacy_names_preserved':len(composer.retained_legacy),
                'visibility_protocol':1,
                'visibility_migrated':bool(previous and previous.scene.get(VERSION_KEY)!=1),
                'material_identity_version':1,
                'material_identity_migrated':bool(previous and MATERIAL_BINDINGS not in previous_meta),
                'material_policy_changed':bool(previous and previous_meta.get('policy',{})!=policy),
                'material_binding_scopes':len(composer.bindings),
                'compact_legacy_materials':bool(compact_legacy),
                'legacy_aliases_collapsed':sum(max(0,len(names)-1) for names in composer.candidates.values()) if compact_legacy else 0}
    finally:
        current.close()
        if previous:previous.close()
