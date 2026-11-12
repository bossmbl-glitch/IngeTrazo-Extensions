# SPDX-License-Identifier: GPL-3.0-or-later
"""Host-only certificate for native placement commands; unknown edits fall back."""
import json,math,os,time
from collections import Counter
from pathlib import Path
from PySide6.QtGui import QMatrix4x4


def json_value(value):
    if hasattr(value,'to_dict'):return value.to_dict()
    if isinstance(value,(str,int,float,bool,type(None))):return value
    if isinstance(value,dict):return {str(k):json_value(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [json_value(v) for v in value]
    raise ValueError('Unsupported host metadata')


def frozen(value):return json.dumps(json_value(value),sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def matrix(value):return tuple(float(x) for x in value.data()) if value is not None else None


def rigid_change(before,after):
    if before is None or after is None:return False
    a=QMatrix4x4(list(before));b=QMatrix4x4(list(after))
    # The list constructor is row-major; source values are column-major.
    a=a.transposed();b=b.transposed();inverse,ok=a.inverted()
    if not ok or a.determinant()<=0 or b.determinant()<=0:return False
    delta=b*inverse
    if any(abs(delta(3,c)-(1. if c==3 else 0.))>1e-5 for c in range(4)):return False
    columns=[[delta(r,c) for r in range(3)] for c in range(3)]
    return all(abs(sum(columns[i][k]*columns[j][k] for k in range(3))-(1. if i==j else 0.))<2e-4 for i in range(3) for j in range(3))


class MotionCertificate:
    def __init__(self,app):
        self.app=app;self.scene=app.scene;self.history=getattr(getattr(app,'viewport',None),'history',None)
        self.epoch=0;self.last_cv=getattr(self.scene,'content_version',None);self.originals={};self.wrappers={};self.supported=False
        self.trace=[];self.sequence=0;self.local_epoch=0
        self.scene_originals={};self.scene_wrappers={}
        try:
            from core.history import History,MoveGroupCommand,RotateGroupCommand,CompoundCommand
            if type(self.history) is not History or self.history.scene is not self.scene:return
            self.safe=(MoveGroupCommand,RotateGroupCommand);self.compound=CompoundCommand
            from core import history as native
            self.local_types=tuple(getattr(native,n) for n in ('SetGroupMaterialCommand','SetFaceColorCommand',
                'SetFaceOpacityCommand','SetFaceMaterialTagCommand','SetFaceTextureCommand',
                'SetMaterialFinishCommand','RestampMaterialCommand','SnapshotMutation',
                'MoveVerticesCommand','RotateVerticesCommand','ScaleVerticesCommand','FlipFacesCommand','ReshareInstanceCommand') if hasattr(native,n))
            for name in ('execute','undo','redo'):
                original=getattr(self.history,name);self.originals[name]=original
                def wrapper(*args,_name=name,_original=original,**kwargs):
                    before=getattr(self.scene,'content_version',None)
                    if before!=self.last_cv:self._external(before)
                    stack=self.history.undo_stack if _name=='undo' else self.history.redo_stack
                    command=(args[0] if args else kwargs.get('cmd')) if _name=='execute' else (stack[-1] if stack else None)
                    try:
                        safe=self._safe(command);local=self._local(command)
                        record=self._record(command,_name,before)
                    except Exception as exc:
                        # Diagnostic inspection must never block a host command.
                        safe=False;local=False
                        record={'time':time.time(),'action':_name,'command':type(command).__name__,
                                'content_before':before,'faces':[],'inspection_error':str(exc)[:500]}
                    try:return _original(*args,**kwargs)
                    finally:
                        try:
                            finished=self._record(command,_name,before)
                            for key in ('faces','face_count','target_overflow'):record[key]=finished[key]
                        except Exception as exc:
                            local=False;record['inspection_error']=str(exc)[:500]
                        if not safe:self.epoch+=1
                        if not local or record.get('target_overflow'):self.local_epoch+=1
                        self.last_cv=getattr(self.scene,'content_version',None)
                        record.update(content_after=self.last_cv)
                        self._append(record)
                self.wrappers[name]=wrapper;setattr(self.history,name,wrapper)
            from core.scene import Scene
            if type(self.scene) is Scene:
                for name in ('begin_group_edit','end_group_edit','end_one_group_edit'):
                    original=getattr(self.scene,name);self.scene_originals[name]=original
                    def edit_wrapper(*args,_name=name,_original=original,**kwargs):
                        before=getattr(self.scene,'content_version',None)
                        if before!=self.last_cv:self._external(before)
                        target=(args[0] if args else kwargs.get('group')) if _name=='begin_group_edit' else getattr(self.scene,'edit_group',None)
                        try:return _original(*args,**kwargs)
                        finally:
                            self.epoch+=1;self.last_cv=getattr(self.scene,'content_version',None)
                            self._append({'time':time.time(),'action':'edit_context','command':_name,
                                          'targets':[{'name':str(getattr(target,'name','')),'uid':str(getattr(target,'uid',''))}] if target else [],
                                          'content_before':before,'content_after':self.last_cv,'faces':[]})
                    self.scene_wrappers[name]=edit_wrapper;setattr(self.scene,name,edit_wrapper)
            self.supported=self.last_cv is not None
        except (ImportError,AttributeError):self.close()

    def _safe(self,command):
        if type(command) in self.safe:return getattr(command.group,'xform',None) is not None
        return type(command) is self.compound and bool(command.commands) and all(self._safe(c) for c in command.commands)

    def _local(self,command):
        if type(command).__name__=='SnapshotMutation' and type(command) in self.local_types:
            return command.mutate is None
        if type(command) in self.safe+self.local_types:return True
        return type(command) is self.compound and bool(command.commands) and all(self._local(c) for c in command.commands)

    def _append(self,record):
        self.sequence+=1;record['sequence']=self.sequence
        self.trace.append(record);self.trace=self.trace[-256:]
        while sum(len(r.get('faces',[])) for r in self.trace)>50000:self.trace.pop(0)

    def _external(self,cv):
        self.epoch+=1;self.local_epoch+=1
        self._append({'time':time.time(),'action':'outside_history','command':'未經原生命令的內容版本變動',
                      'content_before':self.last_cv,'content_after':cv,'faces':[]})

    def _record(self,command,action,before):
        children=[]
        def walk(c):
            if type(c) is self.compound:
                for item in c.commands:walk(item)
            elif c is not None:children.append(c)
        walk(command)
        targeted=[]
        for c in children:
            targeted.extend(getattr(c,'_faces',[]) or [])
            if type(c).__name__=='RestampMaterialCommand' and type(c) in self.local_types:
                targeted.extend(row[0] for row in (getattr(c,'_old_faces',None) or []))
        face_count=len(targeted)
        return {'time':time.time(),'action':action,'command':type(command).__name__,
                'module':type(command).__module__,
                'face_count':face_count,'target_overflow':face_count>20000,
                'commands':[type(c).__name__ for c in children][:100],
                'targets':[{'name':str(getattr(c.group,'name','')),'uid':str(getattr(c.group,'uid',''))} for c in children if hasattr(c,'group')][:30],
                'faces':[id(f) for f in targeted] if face_count<=20000 else [],
                'content_before':before}

    def history_since(self,baseline=None):
        seq=(baseline or {}).get('trace_sequence',0)
        return [{k:v for k,v in r.items() if k!='faces'} for r in self.trace if r['sequence']>seq]

    def notice(self):
        cv=getattr(self.scene,'content_version',None)
        if cv!=self.last_cv:self._external(cv);self.last_cv=cv

    def close(self):
        for name,original in self.originals.items():
            if getattr(self.history,name,None) is self.wrappers.get(name):setattr(self.history,name,original)
        self.originals.clear();self.wrappers.clear();self.supported=False
        for name,original in self.scene_originals.items():
            if getattr(self.scene,name,None) is self.scene_wrappers.get(name):setattr(self.scene,name,original)
        self.scene_originals.clear();self.scene_wrappers.clear()

    def capture(self,with_dependencies=False,allow_edit=False):
        if not self.supported or self.scene is not self.app.scene:raise ValueError('unsupported_history')
        if getattr(self.scene,'edit_group',None) is not None and not allow_edit:raise ValueError('group_edit_context')
        self.notice()
        from .light_data import KEY
        from .light_proxy import metadata
        from .layer_scope import layer_name
        raw=(getattr(self.scene,'plugin_data',{}) or {}).get(KEY,{}) or {}
        if raw.get('d5_lights') or raw.get('d5_proxy_ids'):raise ValueError('host_lights_require_full_update')
        groups=[];seen=set();dependencies=set()
        def walk(items,parent,root_layer,path):
            for index,g in enumerate(items):
                if id(g) in seen:raise ValueError('group_cycle_or_alias')
                seen.add(id(g))
                if metadata(g):raise ValueError('host_light_proxy_requires_full_update')
                m=g.mesh;serial=getattr(m,'_mut_serial',None)
                if not isinstance(serial,int):raise ValueError('mesh_serial_unavailable')
                here=path+(index,);layer=root_layer if root_layer is not None else layer_name(g.layer)
                ext=getattr(g,'ext',None)
                if isinstance(ext,dict):ext={k:v for k,v in ext.items() if k not in {'lime_face_auto','lime_suite_parametric'}}
                attrs={k:getattr(g,k,None) for k in ('name','layer','uid','material','ifc','billboard','text3d','component','hidden','locked','exploded','explode_offset')}
                attrs['axes']=matrix(getattr(g,'axes',None));attrs['ext']=ext
                groups.append({'identity':id(g),'parent':parent,'path':here,'uid':str(g.uid or ''),'root_layer':layer,
                    'invariant':(id(m),serial,frozen(attrs)),'xform':matrix(g.xform),'billboard':bool(g.billboard),
                    'metadata':frozen({k:v for k,v in attrs.items() if k!='material'}),'name':str(g.name),
                    'metadata_without_axes':frozen({k:v for k,v in attrs.items() if k not in ('material','axes')}),
                    'paint':frozen(attrs['material'])})
                if with_dependencies:
                    self._paths(getattr(g,'material',None),dependencies)
                    # Full-export preparation only; never walk faces per move.
                    if id(m) not in meshes_seen:
                        meshes_seen.add(id(m))
                        for face in m.faces:self._paths(face.attrs,dependencies)
                walk(g.children,id(g),layer,here)
        meshes_seen=set();walk(self.scene.groups,None,None,())
        loose=self.scene.loose_mesh
        environment={'layers':getattr(self.scene,'layers',[]),
            'plugin_data':getattr(self.scene,'plugin_data',{}),'ambience':getattr(self.scene,'ambience',None),
            'kept':[(id(g),id(g.mesh)) for g in getattr(self.scene,'kept_components',[])]}
        for key in ('camera_home','saved_views','image_planes','shadows','units','active_layer',
                    'layer_folders','back_face_color','display_style'):
            environment[key]=getattr(self.scene,key,None)
        global_state=frozen({'materials':getattr(self.scene,'materials',{}),**environment})
        if with_dependencies:
            self._paths([json_value(m) for m in getattr(self.scene,'materials',{}).values()],dependencies)
            if id(loose) not in meshes_seen:
                for face in loose.faces:self._paths(face.attrs,dependencies)
        counts=Counter(row['uid'] for row in groups if row['uid']);by_path={}
        for row in groups:
            parent=by_path.get(row['path'][:-1]);index=row['path'][-1]
            fallback=(parent['key']+'/'+str(index)) if parent else 'g/'+str(index)
            row['key']='uid/'+row['uid'] if row['uid'] and counts[row['uid']]==1 else fallback
            row['root_key']=parent['root_key'] if parent else row['key']
            by_path[row['path']]=row
        return {'epoch':self.epoch,'local_epoch':self.local_epoch,'trace_sequence':self.sequence,
                'environment':frozen(environment),'materials':{k:frozen(v) for k,v in getattr(self.scene,'materials',{}).items()},
                'cv':self.last_cv,'global':global_state,
                'loose':(id(loose),getattr(loose,'_mut_serial',None)),
                'groups':groups,'dependencies':{p:self._stat(p) for p in sorted(dependencies)}}

    def describe(self,baseline,reason):
        result={'reason':reason,'commands':self.history_since(baseline)}
        if not baseline:return result
        try:
            now=self.capture(allow_edit=True);old={r['key']:r for r in baseline['groups']};new={r['key']:r for r in now['groups']}
            result['changed_groups']=[{'key':k,'name':new[k]['name'],'layer':new[k]['root_layer'],
                'geometry':old[k]['invariant'][:2]!=new[k]['invariant'][:2],
                'material':old[k]['paint']!=new[k]['paint'],'placement':old[k]['xform']!=new[k]['xform'],
                'metadata':old[k]['metadata']!=new[k]['metadata']} for k in old.keys()&new.keys() if old[k]['invariant']!=new[k]['invariant'] or old[k]['xform']!=new[k]['xform']][:60]
            result['added_keys']=sorted(new.keys()-old.keys())[:60];result['removed_keys']=sorted(old.keys()-new.keys())[:60]
            result['changed_materials']=[k for k in set(now['materials'])|set(baseline['materials']) if now['materials'].get(k)!=baseline['materials'].get(k)][:60]
        except (ValueError,AttributeError,TypeError) as exc:result['inspection_unavailable']=str(exc)
        return result

    def partial_plan(self,baseline):
        current=self.capture(allow_edit=True)
        if current['cv']<=baseline['cv']:raise ValueError('no_content_change')
        if self.trace and baseline.get('trace_sequence',0)<self.trace[0]['sequence']-1:raise ValueError('command_history_overflow')
        if current['environment']!=baseline['environment']:raise ValueError('global_settings_changed')
        if current['loose']!=baseline['loose']:raise ValueError('loose_geometry_changed')
        if len(current['groups'])!=len(baseline['groups']):raise ValueError('group_structure_changed')
        if current['local_epoch']!=baseline['local_epoch']:raise ValueError('unsupported_command_or_external_change')
        selected=set()
        metadata='metadata_without_axes' if any(r['sequence']>baseline.get('trace_sequence',0) and r.get('action')=='edit_context' for r in self.trace) else 'metadata'
        for a,b in zip(baseline['groups'],current['groups']):
            if any(a[k]!=b[k] for k in ('identity','parent','path','key','root_layer',metadata)):raise ValueError('group_structure_or_metadata_changed')
            if a['invariant']!=b['invariant'] or a['xform']!=b['xform']:selected.add(b['root_key'])
        faces={f for r in self.trace if r['sequence']>baseline.get('trace_sequence',0) for f in r.get('faces',[])}
        changed_materials={k for k in set(current['materials'])|set(baseline['materials']) if current['materials'].get(k)!=baseline['materials'].get(k)}
        if faces or changed_materials:
            def affected(g):
                paint=getattr(g,'material',None) or {}
                return (paint.get('mat') in changed_materials or any(id(f) in faces or f.attrs.get('mat') in changed_materials for f in g.mesh.faces)
                        or any(affected(c) for c in g.children))
            if any(id(f) in faces or f.attrs.get('mat') in changed_materials for f in self.scene.loose_mesh.faces):raise ValueError('loose_material_changed')
            roots=[r for r in current['groups'] if r['parent'] is None]
            for row,g in zip(roots,self.scene.groups):
                if affected(g):selected.add(row['key'])
        roots=[r for r in current['groups'] if r['parent'] is None]
        from .layer_scope import host_policy
        policy=host_policy(self.scene)
        active=[r for r in roots if policy.get(r['root_layer'],'normal')=='normal']
        active_selected=[r for r in active if r['key'] in selected]
        if len(active)>2 and len(active_selected)>max(2,len(active)//2):raise ValueError('partial_scope_too_broad')
        for p,stamp in baseline['dependencies'].items():
            if self._stat(p)!=stamp:raise ValueError('texture_file_changed')
        return [r['key'] for r in roots if r['key'] in selected],current

    @staticmethod
    def _paths(value,paths):
        if isinstance(value,dict):
            for k,v in value.items():
                if k=='path' and isinstance(v,str):paths.add(v)
                elif isinstance(v,(dict,list,tuple)):MotionCertificate._paths(v,paths)
        elif isinstance(value,(list,tuple)):
            for item in value:MotionCertificate._paths(item,paths)

    @staticmethod
    def _stat(path):
        try:
            s=Path(path).stat();return (s.st_size,s.st_mtime_ns)
        except OSError:return None

    def delta(self,baseline):
        current=self.capture()
        if current['cv']<=baseline['cv']:raise ValueError('no_confirmed_placement_command')
        if current['epoch']!=baseline['epoch']:raise ValueError('non_placement_command')
        if current['global']!=baseline['global'] or current['loose']!=baseline['loose']:raise ValueError('scene_or_material_changed')
        if len(current['groups'])!=len(baseline['groups']):raise ValueError('group_structure_changed')
        changes=[]
        for before,after in zip(baseline['groups'],current['groups']):
            if any(before[k]!=after[k] for k in ('identity','parent','path','key','invariant','root_layer')):raise ValueError('group_geometry_or_metadata_changed')
            if before['xform']!=after['xform']:
                if after['billboard'] or not rigid_change(before['xform'],after['xform']):raise ValueError('unsupported_transform')
                # A moved ancestor of a billboard changes its facing semantics.
                if any(r['billboard'] and r['path'][:len(after['path'])]==after['path'] for r in current['groups']):raise ValueError('billboard_subtree')
                changes.append({'key':after['key'],'root_layer':after['root_layer'],'before':before['xform'],'after':after['xform']})
        if not changes:raise ValueError('no_placement_change')
        for path,stamp in baseline['dependencies'].items():
            if self._stat(path)!=stamp:raise ValueError('texture_file_changed')
        current['dependencies']=baseline['dependencies']
        return changes,current
