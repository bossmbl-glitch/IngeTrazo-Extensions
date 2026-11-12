# SPDX-License-Identifier: GPL-3.0-or-later
"""Native host-only light geometry. Export a filtered copy, never hide the source."""
import copy
import math
import uuid
from PySide6.QtGui import QMatrix4x4,QVector3D,QVector4D
from .light_data import KEY,clean_lights

TAG='d5_light_proxy_v1'
MATERIAL_PREFIX='__IngeTrazo_D5_LightProxy_'

def metadata(group):
    ext=getattr(group,'ext',None)
    return ext.get(TAG) if isinstance(ext,dict) else None

def placements(scene):
    def walk(groups,parent,ancestors):
        for group in groups:
            if id(group) in ancestors:raise ValueError('模型群組循環，無法同步')
            matrix=parent*(group.xform if group.xform is not None else QMatrix4x4())
            yield group,matrix,parent,groups
            yield from walk(group.children,matrix,ancestors|{id(group)})
    yield from walk(scene.groups,QMatrix4x4(),set())

def identity(group,data):
    original=data['light']['id']
    return original if group.uid==data['uid'] else uuid.uuid5(uuid.UUID(original),str(group.uid)).hex

def vector(value):return QVector3D(*value)
def values(value):return [value.x(),value.y(),value.z()]

def geometry_frame(group):
    # Native group edit bakes xform into vertices. Face-local reference points
    # survive that operation and IGZ, so derive the affine frame from geometry.
    points={}
    references=metadata(group).get('faces',{})
    for face in group.mesh.faces:
        originals=references.get(face.attrs.get('mat'))
        if originals is not None:
            if len(originals)!=len(face.loop):raise ValueError('定位模型已改變形狀，請復原形狀編輯')
            for original,vertex in zip(originals,face.loop):points[tuple(original)]=vertex.position
    pairs=[(vector(p),q) for p,q in points.items()]
    if len(pairs)<4:raise ValueError('定位模型識別點不足，請復原模型編輯')
    if all((a-b).length()<1e-8 for a,b in pairs):return QMatrix4x4()
    first=pairs[0];chosen=[first]
    for pair in pairs[1:]:
        delta=pair[0]-first[0]
        if len(chosen)==1 and delta.length()>1e-5:chosen.append(pair)
        elif len(chosen)==2 and QVector3D.crossProduct(chosen[1][0]-first[0],delta).length()>1e-6:chosen.append(pair)
        elif len(chosen)==3 and abs(QVector3D.dotProduct(QVector3D.crossProduct(chosen[1][0]-first[0],chosen[2][0]-first[0]),delta))>1e-8:
            chosen.append(pair);break
    if len(chosen)!=4:raise ValueError('定位模型已壓平，請復原縮放')
    def frame(index):
        matrix=QMatrix4x4();origin=chosen[0][index]
        for i in range(3):matrix.setColumn(i,QVector4D(chosen[i+1][index]-origin,0))
        matrix.setColumn(3,QVector4D(origin,1));return matrix
    inverse,ok=frame(0).inverted()
    if not ok:raise ValueError('無法解析定位模型座標')
    return frame(1)*inverse

def records(scene):
    for group,matrix,parent,owner in placements(scene):
        data=metadata(group)
        if data:
            matrix=matrix*geometry_frame(group)
            light=copy.deepcopy(data['light']);light['id']=identity(group,data)
            light['pos']=values(matrix.map(vector(light['pos'])))
            light['end']=values(matrix.map(vector(light['end'])))
            light['direction']=values(matrix.mapVector(vector(light['direction'])).normalized())
            yield light,group,parent,owner

def lights(scene):
    raw=(getattr(scene,'plugin_data',{}) or {}).get(KEY,{}) or {}
    found=[row[0] for row in records(scene)]
    managed=set(raw.get('d5_proxy_ids',[]))|{x['id'] for x in found}
    legacy=[x for x in clean_lights(raw.get('d5_lights',[])) if x['id'] not in managed]
    return clean_lights(legacy+found)

def filtered_scene(scene):
    """Exclude proxies at any nesting level without changing the live document."""
    snapshot=copy.copy(scene);snapshot.mesh=scene.loose_mesh
    def check_mesh(mesh):
        if any(str(face.attrs.get('mat','')).startswith(MATERIAL_PREFIX) for face in mesh.faces):
            raise ValueError('燈具定位模型已被分解；請復原分解後再同步，避免定位幾何傳入 D5')
    check_mesh(snapshot.mesh)
    def filtered(groups,ancestors):
        result=[]
        for group in groups:
            if id(group) in ancestors:raise ValueError('模型群組循環，無法同步')
            if metadata(group):continue
            check_mesh(group.mesh)
            clone=copy.copy(group)
            # These are editing recipes / face bookkeeping, never consumed by
            # the bridge's visual importer. Filter only the cloned ext mapping.
            # Meshes, face attrs, materials, transforms and source ext stay intact.
            ext=getattr(group,'ext',None)
            if isinstance(ext,dict):
                clone.ext={k:v for k,v in ext.items()
                           if k not in {'lime_face_auto','lime_suite_parametric'}}
            clone.children=filtered(group.children,ancestors|{id(group)})
            result.append(clone)
        return result
    snapshot.groups=filtered(scene.groups,set())
    snapshot.kept_components=filtered(scene.kept_components,set())
    snapshot.selection=set();snapshot.edit_group=None;snapshot._edit_stack=[];snapshot._loose_mesh=None
    return snapshot

def make_group(light):
    from core.group import Group
    from core.mesh import Mesh
    mesh=Mesh();local=copy.deepcopy(light);origin=vector(light['pos'])
    references={}
    local['pos']=[0.,0.,0.];local['end']=values(vector(light['end'])-origin)
    def face(points):
        result=mesh.add_face([vector(p) if isinstance(p,(list,tuple)) else p for p in points])
        name=MATERIAL_PREFIX+light['id']+'_'+str(len(references))
        references[name]=[values(vertex.position) for vertex in result.loop]
        result.attrs.update({'mat':name,'color':[1.,.68,.12]})
    direction=vector(local['direction']).normalized()
    if light['kind']=='point':
        r=.14;ring=[QVector3D(r,0,0),QVector3D(0,r,0),QVector3D(-r,0,0),QVector3D(0,-r,0)]
        for i in range(4):
            face([QVector3D(0,0,r),ring[i],ring[(i+1)%4]])
            face([QVector3D(0,0,-r),ring[(i+1)%4],ring[i]])
    elif light['kind']=='spot':
        u=QVector3D.crossProduct(direction,QVector3D(0,0,1))
        if u.length()<.01:u=QVector3D.crossProduct(direction,QVector3D(0,1,0))
        u.normalize();v=QVector3D.crossProduct(direction,u)
        ring=[direction*.32+(u*math.cos(i*math.tau/12)+v*math.sin(i*math.tau/12))*.16 for i in range(12)]
        for i in range(12):face([QVector3D(),ring[i],ring[(i+1)%12]])
        face(list(reversed(ring)))
    else:
        end=vector(local['end']);axis=end.normalized()
        u=QVector3D.crossProduct(axis,direction)
        if u.length()<.01:u=QVector3D.crossProduct(axis,QVector3D(0,1,0))
        if u.length()<.01:u=QVector3D.crossProduct(axis,QVector3D(1,0,0))
        u.normalize();u*=max(.08,light['width'])/2
        v=QVector3D.crossProduct(axis,u).normalized()*.04
        corners=[base+u*s+v*t for base in (QVector3D(),end) for s,t in ((-1,-1),(1,-1),(1,1),(-1,1))]
        for indices in ((3,2,1,0),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)):
            face([corners[i] for i in indices])
    group=Group(mesh=mesh,name=light['name']+'〔定位用〕');group.component=False
    group.xform=QMatrix4x4();group.xform.translate(origin)
    group.axes=QMatrix4x4()
    group.ext={TAG:{'uid':group.uid,'light':local,'faces':references}}
    return group

def apply_lights(app,desired):
    """Properties, geometry and light ownership change in one undo command."""
    from core.history import Command,CompoundCommand,SetPluginDataCommand,DeleteGroupCommand
    desired=clean_lights(desired);scene=app.scene;existing={x[0]['id']:x for x in records(scene)}
    commands=[]
    class InsertRoot(Command):
        def __init__(self,group):self.group=group
        def do(self,scene):scene.groups.append(self.group);scene.version+=1
        def undo(self,scene):scene.groups.remove(self.group);scene.selection.discard(self.group);scene.version+=1
    class UpdateProxy(Command):
        def __init__(self,group,replacement):
            self.group=group;self.old=(group.mesh,group.xform,group.ext,group.name,group.axes)
            self.new=(replacement.mesh,replacement.xform,replacement.ext,replacement.name,replacement.axes)
        def set(self,scene,data):
            self.group.mesh,self.group.xform,self.group.ext,self.group.name,self.group.axes=data;scene.version+=1
        def do(self,scene):self.set(scene,self.new)
        def undo(self,scene):self.set(scene,self.old)
    wanted={x['id'] for x in desired}
    for key,(_,group,_,_) in existing.items():
        if key not in wanted:commands.append(DeleteGroupCommand(group))
    for light in desired:
        row=existing.get(light['id'])
        if row and row[0]==light:continue
        new=make_group(light)
        if row:
            _,group,parent,_=row;inverse,ok=parent.inverted()
            if not ok:raise ValueError('定位群組的父層縮放為零，請先還原縮放')
            new.xform=inverse*new.xform;new.ext[TAG]['uid']=group.uid
            commands.append(UpdateProxy(group,new))
        else:commands.append(InsertRoot(new))
    raw=app.document_data({}) or {};old=copy.deepcopy(raw)
    known=set(raw.get('d5_light_ids',[]))|set(existing)|wanted
    raw['d5_proxy_ids']=sorted(set(raw.get('d5_proxy_ids',[]))|set(existing)|wanted)
    raw['d5_light_ids']=sorted(known);raw['d5_lights']=desired
    if old!=raw:commands.append(SetPluginDataCommand(KEY,raw))
    if commands:
        app.viewport.history.execute(CompoundCommand(commands));app.viewport.notify_scene_changed();app.viewport.update()
