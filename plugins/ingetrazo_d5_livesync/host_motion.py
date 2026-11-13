# SPDX-License-Identifier: GPL-3.0-or-later
"""Host-only certificate for native placement commands; unknown edits fall back."""
import hashlib,json,math,os,sys,time
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

def uses_material(value,names):
    if isinstance(value,dict):
        return value.get('mat') in names or any(uses_material(v,names) for v in value.values())
    return isinstance(value,(list,tuple)) and any(uses_material(v,names) for v in value)


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
                    try:
                        result=_original(*args,**kwargs)
                        # Native History.execute reports rolled-back failures
                        # through fields rather than re-raising the exception.
                        if _name=='execute' and (getattr(self.history,'last_error',None) or getattr(self.history,'last_rejected',None)):
                            safe=False;local=False;record['command_failed']=True
                        return result
                    except BaseException:
                        safe=False;local=False;record['command_failed']=True
                        raise
                    finally:
                        try:
                            finished=self._record(command,_name,before)
                            for key in ('faces','face_count','target_overflow'):record[key]=finished[key]
                            if record.get('paint_certified'):
                                record['loose_after']=finished.get('loose_before')
                                if not finished.get('paint_certified'):local=False
                            record['placement_after']=finished.get('placement_before',[])
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
        if self._studio_paint(command):return True
        if type(command).__name__=='SnapshotMutation' and type(command) in self.local_types:
            return command.mutate is None
        if type(command) in self.safe+self.local_types:return True
        return type(command) is self.compound and bool(command.commands) and all(self._local(c) for c in command.commands)

    def _studio_paint(self,command):
        # Fail closed for other plugins, subclasses or changed source. Native
        # History restores the command mesh for undo/redo after leaving edit.
        # Reviewed engine's _put bumps scene.mesh even with no loose face edits.
        cls=type(command);module=sys.modules.get(cls.__module__)
        if (cls.__module__!='ingetrazo_plugin_it_material_studio.engine' or cls.__name__!='PaintCommand'
                or getattr(module,'PaintCommand',None) is not cls):return False
        try:
            source=Path(module.__file__).read_text(encoding='utf-8').replace('\r\n','\n')
            return hashlib.sha256(source.encode()).hexdigest()=='d18b80b8a43ab1a093288978b6bf4cda85f4359a495eaac697eb64323b32dd2a'
        except (OSError,AttributeError,UnicodeError):return False

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
        paint=self._studio_paint(command)
        if paint:targeted.extend(row[0] for row in command.faces);face_count=len(targeted)
        return {'time':time.time(),'action':action,'command':type(command).__name__,
                'placement_before':[self._placement_state(c.group) for c in children if type(c) in self.safe][:30],
                'baked_targets':[id(c.group) for c in children if type(c) in self.safe and c.group.xform is None],
                'paint_certified':paint,
                'paint_roots':[id(row[0]) for row in command.groups] if paint else [],
                'loose_before':(id(self.scene.loose_mesh),getattr(self.scene.loose_mesh,'_mut_serial',None)) if paint else None,
                'module':type(command).__module__,
                'face_count':face_count,'target_overflow':face_count>20000,
                'commands':[type(c).__name__ for c in children][:100],
                'targets':[{'name':str(getattr(c.group,'name','')),'uid':str(getattr(c.group,'uid',''))} for c in children if hasattr(c,'group')][:30],
                'faces':[id(f) for f in targeted] if face_count<=20000 else [],
                'content_before':before}

    @staticmethod
    def _placement_state(group):
        samples=[]
        for face in group.mesh.faces:
            for vertex in face.loop:
                p=vertex.position;samples.append([p.x(),p.y(),p.z()])
                if len(samples)==8:break
            if len(samples)==8:break
        return {'uid':str(getattr(group,'uid','')),'mesh_identity':id(group.mesh),
                'mesh_serial':getattr(group.mesh,'_mut_serial',None),
                'axes':matrix(getattr(group,'axes',None)),'xform':matrix(getattr(group,'xform',None)),
                'vertex_samples':samples,'samples_are_not_geometry_proof':True}

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
        # camera_home follows navigation in the host and is not a model edit.
        # Explicit saved views and scene settings retain their existing guards.
        for key in ('saved_views','image_planes','shadows','units','active_layer',
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
            result['loose_tracking_changed']=now['loose']!=baseline['loose']
            result['content_versions']={'before':baseline['cv'],'after':now['cv']}
            for row in result['changed_groups']:
                a=json.loads(old[row['key']]['metadata']);b=json.loads(new[row['key']]['metadata'])
                row['metadata_fields']=[k for k in set(a)|set(b) if a.get(k)!=b.get(k)]
                row['geometry_evidence']='mesh_identity_or_mutation_serial_only'
        except (ValueError,AttributeError,TypeError) as exc:result['inspection_unavailable']=str(exc)
        return result

    def unchanged(self,baseline):
        """Skip only an exact, acknowledged host state, including dependencies."""
        return self.unchanged_report(baseline)['unchanged']

    def unchanged_report(self,baseline):
        current=self.capture(allow_edit=True)
        keys=('cv','epoch','local_epoch','trace_sequence','global','loose','groups')
        changed=[k for k in keys if current[k]!=baseline[k]]
        before=json.loads(baseline['environment']);after=json.loads(current['environment'])
        environment_fields=sorted(k for k in set(before)|set(after) if before.get(k)!=after.get(k))
        dependencies=[{'file':Path(p).name,'reason':'unavailable' if stamp is None or self._stat(p) is None else 'changed'}
                      for p,stamp in baseline['dependencies'].items() if stamp is None or self._stat(p)!=stamp]
        group_checks=[]
        for a,b in zip(baseline['groups'],current['groups']):
            if a==b:continue
            left=json.loads(a['metadata']);right=json.loads(b['metadata'])
            group_checks.append({'key':a['key'],'name':b['name'],
                                 'fields':[k for k in ('identity','parent','path','key','root_layer','invariant','xform') if a[k]!=b[k]],
                                 'metadata_fields':sorted(k for k in set(left)|set(right) if left.get(k)!=right.get(k))})
        return {'unchanged':not changed and not dependencies,'changed_checks':changed,
                'environment_fields':environment_fields,'dependency_count':len(dependencies),
                'dependencies':dependencies[:20],'group_checks':group_checks[:30],
                'content_versions':{'before':baseline['cv'],'after':current['cv']}}

    def tracking_only(self,baseline,current):
        # Called only after partial_plan has certified the command history.
        # Rebase host tracking alone; keep the receiver's revision/hash intact.
        return (all(current[k]==baseline[k] for k in ('environment','groups','loose'))
                and all(stamp is not None and self._stat(p)==stamp for p,stamp in baseline['dependencies'].items()))

    def partial_plan(self,baseline):
        current=self.capture(allow_edit=True)
        if current['cv']<=baseline['cv']:raise ValueError('no_content_change')
        if self.trace and baseline.get('trace_sequence',0)<self.trace[0]['sequence']-1:raise ValueError('command_history_overflow')
        if current['environment']!=baseline['environment']:raise ValueError('global_settings_changed')
        records=[r for r in self.trace if r['sequence']>baseline.get('trace_sequence',0)]
        paint_records=[r for r in records if r.get('paint_certified') and not r.get('command_failed')]
        if current['loose']!=baseline['loose']:
            # Account for every serial change using the reviewed command, never
            # infer unchanged geometry just from an empty target list.
            loose=baseline['loose']
            for r in records:
                if r not in paint_records or r.get('faces') or r.get('loose_before')!=loose:break
                after=r.get('loose_after')
                if after is None or after[0]!=loose[0] or not all(isinstance(v,int) for v in (loose[1],after[1])):break
                loose=after
            else:
                if loose==current['loose']:loose=None
            if loose is not None:raise ValueError('loose_geometry_changed')
        if len(current['groups'])!=len(baseline['groups']):raise ValueError('group_structure_changed')
        if current['local_epoch']!=baseline['local_epoch']:raise ValueError('unsupported_command_or_external_change')
        selected=set()
        painted_ids={identity for r in paint_records for identity in r.get('paint_roots',[])}
        painted_paths=[r['path'] for r in baseline['groups'] if r['identity'] in painted_ids]
        # v0.6.0 Move/RotateGroupCommand carries axes alongside baked vertices
        # and UV maps. Re-export this root with the native serializer; never
        # reinterpret the mesh change as an instance placement.
        baked_ids={identity for r in records if not r.get('command_failed') for identity in r.get('baked_targets',[])}
        metadata='metadata_without_axes' if any(r['sequence']>baseline.get('trace_sequence',0) and r.get('action')=='edit_context' for r in self.trace) else 'metadata'
        for a,b in zip(baseline['groups'],current['groups']):
            painted=any(a['path'][:len(path)]==path for path in painted_paths)
            checked_metadata='metadata_without_axes' if a['identity'] in baked_ids and a['xform'] is None and b['xform'] is None else metadata
            fields=('path','key','root_layer',checked_metadata) if painted else ('identity','parent','path','key','root_layer',checked_metadata)
            if painted:
                left=json.loads(a[checked_metadata]);right=json.loads(b[checked_metadata])
                # clone_tree deliberately makes a painted copy unique. The
                # native component field is a bool, not a prototype identity.
                if all(value is None or type(value) is bool for value in (left.get('component'),right.get('component'))):
                    left.pop('component',None);right.pop('component',None)
                    if left==right:fields=tuple(k for k in fields if k!=checked_metadata)
            if any(a[k]!=b[k] for k in fields):raise ValueError('group_structure_or_metadata_changed')
            if a['invariant']!=b['invariant'] or a['xform']!=b['xform']:selected.add(b['root_key'])
        faces={f for r in self.trace if r['sequence']>baseline.get('trace_sequence',0) for f in r.get('faces',[])}
        changed_materials={k for k in set(current['materials'])|set(baseline['materials']) if current['materials'].get(k)!=baseline['materials'].get(k)}
        if faces or changed_materials:
            def affected(g):
                paint=getattr(g,'material',None) or {}
                return (uses_material(paint,changed_materials) or any(id(f) in faces or uses_material(f.attrs,changed_materials) for f in g.mesh.faces)
                        or any(affected(c) for c in g.children))
            if any(id(f) in faces or uses_material(f.attrs,changed_materials) for f in self.scene.loose_mesh.faces):raise ValueError('loose_material_changed')
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
        if current['epoch']!=baseline['epoch']:
            records=[r for r in self.trace if r['sequence']>baseline.get('trace_sequence',0)]
            if (records and any(r.get('baked_targets') for r in records)
                    and all(r.get('commands') and len(r.get('placement_before',[]))==len(r['commands'])
                            and not r.get('command_failed') for r in records)):
                raise ValueError('baked_group_placement')
            raise ValueError('non_placement_command')
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
