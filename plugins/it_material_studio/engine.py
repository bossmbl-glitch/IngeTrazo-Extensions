"""Material-only, reversible edits. Tile lengths are expressed in world cm."""
import copy
import math
from pathlib import Path
from PySide6.QtGui import QVector3D, QMatrix4x4, QImage
from core.mesh import Face, Mesh, edge_flags, stamp_edge_flags, face_count
from core.history import Command
from core.materials import Material
from core.texture import projection_axes
from .textures import tint_texture,signature,readable

KEY = 'it_material_studio'
PAINT_KEYS = ('color','texture','opacity','mat')

def default_recipe():
    return dict(name='新材質',color=[.8,.8,.8],path='',tint=False,tint_mode=0,opacity=1.,width=60.,height=60.,
                angle=0.,offset_u=0.,offset_v=0.,flip_u=False,flip_v=False,projection='face')

def validate(recipe):
    r=copy.deepcopy(recipe)
    if not 0<=float(r.get('opacity',1))<=1:raise ValueError('不透明度須介於 0 與 100%。')
    if not r['name'].strip():
        raise ValueError('請輸入材質名稱。')
    for k in ('width','height','angle','offset_u','offset_v'):
        r[k]=float(r[k])
        if not math.isfinite(r[k]): raise ValueError('尺寸或角度無效。')
    if not .01<=r['width']<=100000 or not .01<=r['height']<=100000:
        raise ValueError('貼圖寬高須介於 0.01 與 100000 cm。')
    if len(r['color'])!=3 or any(not math.isfinite(c) or not 0<=c<=1 for c in r['color']):
        raise ValueError('顏色無效。')
    if r['projection'] not in ('face','xz','xy','yz'): raise ValueError('投影方式無效。')
    if r['path'] and (not Path(r['path']).is_file() or not readable(signature(r['path']))):
        raise ValueError('貼圖不存在或無法讀取，請重新選擇圖片。')
    return r

def texture_for(r):
    if not r['path']: return None
    tex={'path':r['path'],'sw':r['width']/100,'sh':r['height']/100,'rot':r['angle']}
    if r['tint']: tex=tint_texture(tex,r['color'],r.get('tint_mode',1))
    tex[KEY]={k:copy.deepcopy(r[k]) for k in ('width','height','angle','offset_u','offset_v','flip_u','flip_v','projection')}
    return tex

def material_for(r):
    return Material(r['name'].strip(),None if r['path'] else tuple(r['color']),texture_for(r),r.get('opacity',1))

def face_paint(face,world,r,base):
    attrs=base.face_attrs()
    if attrs.get('texture'):
        inv,ok=world.inverted()
        if not ok: raise ValueError('物件被壓成零厚度，無法計算貼圖座標。')
        n=inv.transposed().mapVector(face.normal()).normalized()
        if r['projection']=='face':
            u,v=projection_axes(n,0.)
        else:
            u,v={'xz':(QVector3D(1,0,0),QVector3D(0,0,1)),
                 'xy':(QVector3D(1,0,0),QVector3D(0,1,0)),
                 'yz':(QVector3D(0,1,0),QVector3D(0,0,1))}[r['projection']]
        a=math.radians(r['angle']);c,s=math.cos(a),math.sin(a)
        ur,vr=u*c+v*s,-u*s+v*c
        su=-1 if r['flip_u'] else 1
        sv=-1 if r['flip_v'] else 1
        gu,gv=ur*(su*100/r['width']),vr*(sv*100/r['height'])
        origin=world.map(QVector3D())
        cu=QVector3D.dotProduct(gu,origin)-su*r['offset_u']/r['width']
        cv=QVector3D.dotProduct(gv,origin)-sv*r['offset_v']/r['height']
        lu,lv=world.transposed().mapVector(gu),world.transposed().mapVector(gv)
        attrs['texture']['uvw']=[*lu.toTuple(),cu,*lv.toTuple(),cv]
    return attrs

def from_attrs(attrs,normal=None):
    r=default_recipe()
    r['name']=attrs.get('mat') or '自訂材質'
    r['opacity']=attrs.get('opacity',1.)
    r['color']=list(attrs.get('color') or [.8,.8,.8])
    t=attrs.get('texture')
    if t:
        r.update(path=t.get('base') or t.get('path',''),width=t.get('sw',1)*100,
                 height=t.get('sh',1)*100,angle=t.get('rot',0))
        if t.get('tint'):
            r['tint']=True;r['color']=list(t['tint']);r['tint_mode']=t.get('tint_mode',1)
        else:r['color']=image_color(r['path'])
        if KEY in t:
            r.update(copy.deepcopy(t[KEY]))
        elif normal is not None and t.get('uvw'):
            from core.texture import placement_of
            placement=placement_of(t,normal)
            if placement:r.update(width=placement[0]*100,height=placement[1]*100,angle=placement[2])
    return r

def image_color(path):
    """Representative visible image colour for a neutral starting slider value."""
    from PySide6.QtCore import Qt
    im=QImage(str(path)).scaled(32,32,Qt.KeepAspectRatio,Qt.SmoothTransformation)
    total=0.;rgb=[0.,0.,0.]
    for y in range(im.height()):
        for x in range(im.width()):
            c=im.pixelColor(x,y);a=c.alphaF();total+=a
            for i,v in enumerate((c.redF(),c.greenF(),c.blueF())):rgb[i]+=v*a
    return [v/total for v in rgb] if total else [.8,.8,.8]

class ColorEditCommand(Command):
    """Edit one material everywhere, preserving UVs, geometry and sharing.

    A live gesture may replace its latest colour while this command remains
    the history tip. Its original snapshots remain the single undo boundary.
    """
    def __init__(self,scene,name,recipe,preview=False,scope=None):
        from core.group import iter_placements
        self.name=name;self.registry=dict(scene.materials);self.targets=[];self.meshes=[]
        seen=set();groups=[]
        meshes=[scene.loose_mesh,scene.mesh] if scope is None else []
        for root in (scene.groups if scope is None else scope[0]):
            for group,_ in iter_placements(root):
                groups.append(group);meshes.append(group.mesh)
        for mesh in meshes:
            if mesh is None or id(mesh) in seen:continue
            seen.add(id(mesh));affected=False
            for f in mesh.faces:
                a=f.attrs;b=a.get('back')
                if a.get('mat')==name or isinstance(b,dict) and b.get('mat')==name:
                    self.targets.append((f,'attrs',copy.deepcopy(a)))
                    affected=True
            if affected:self.meshes.append(mesh)
        if scope is not None:
            for face in scope[1]:
                self.targets.append((face,'attrs',copy.deepcopy(face.attrs)))
            if scope[1] and scene.mesh not in self.meshes:self.meshes.append(scene.mesh)
        seen.clear()
        for g in groups:
            if id(g) not in seen and isinstance(g.material,dict) and g.material.get('mat')==name:
                seen.add(id(g));self.targets.append((g,'material',copy.deepcopy(g.material)))
                for child,_,_ in walk(g):
                    if child.mesh not in self.meshes:self.meshes.append(child.mesh)
        if not self.targets and name not in self.registry:raise ValueError('先把材質塗到模型，再切到「編輯」調色。')
        self.revision=0;self.recolor(recipe,preview=preview)

    def recolor(self,recipe,preview=False,prepared=None):
        r=validate(recipe)
        cache={}
        def paint(a):
            out=copy.deepcopy(a)
            if a.get('mat')==self.name:
                out['opacity']=r.get('opacity',1.)
                if a.get('texture'):
                    from core.texture import untinted_texture
                    t=untinted_texture(a['texture'])
                    if r['tint']:
                        base=t.get('path')
                        if base not in cache:cache[base]=(prepared[base] if prepared is not None else tint_texture({'path':base},r['color'],r.get('tint_mode',0),preview))
                        t.update(cache[base])
                    out['texture']=t
                    out.pop('color',None)
                else:out['color']=tuple(r['color'])
            if isinstance(a.get('back'),dict):out['back']=paint(a['back'])
            return out
        updates=[paint(old) for obj,key,old in self.targets]
        oldmat=self.registry.get(self.name)
        material=copy.deepcopy(oldmat) if oldmat else material_for(r)
        material.name=self.name
        stamp=paint(dict(material.face_attrs(),mat=self.name))
        material.color=stamp.get('color');material.texture=stamp.get('texture');material.opacity=stamp.get('opacity')
        self.material=material;self.updates=updates;self.recipe=copy.deepcopy(r)
        self.preview=preview and bool(r['tint']);self.revision+=1

    def full_sources(self):
        paths=set()
        def visit(a):
            if a.get('mat')==self.name and a.get('texture'):
                t=a['texture'];paths.add(t.get('base') or t['path'])
            if isinstance(a.get('back'),dict):visit(a['back'])
        for obj,key,old in self.targets:visit(old)
        if self.name in self.registry:visit(self.registry[self.name].face_attrs())
        return paths

    def install_full(self,scene,prepared):
        old_updates=self.updates;old_material=self.material
        self.recolor(self.recipe,prepared=prepared)
        changed=False
        for (obj,key,_),old,new in zip(self.targets,old_updates,self.updates):
            if getattr(obj,key) is old:setattr(obj,key,new);changed=True
            # Later commands (such as Delete) retain this dict for undo.
            # Finish their saved preview too, without reinstating deleted paint.
            old.clear();old.update(copy.deepcopy(new))
        if scene.materials.get(self.name) is old_material:scene.materials[self.name]=self.material;changed=True
        old_material.color=self.material.color;old_material.texture=copy.deepcopy(self.material.texture)
        old_material.opacity=self.material.opacity
        if changed:
            for mesh in self.meshes:mesh._chunk_dirty=True;mesh._mut_serial+=1
            scene.version+=1
        return changed

    def _put(self,scene,after):
        for (obj,key,old),new in zip(self.targets,self.updates):setattr(obj,key,new if after else old)
        scene.materials.clear();scene.materials.update(self.registry)
        if after:scene.materials[self.name]=self.material
        for mesh in self.meshes:mesh._chunk_dirty=True;mesh._mut_serial+=1
        scene.version+=1

    def do(self,scene):self._put(scene,True)
    def undo(self,scene):self._put(scene,False)

def selected_groups(scene,entities):
    context=scene.edit_group
    pool=context.children if context is not None else scene.groups
    return [g for g in pool if g in entities]

def walk(group,world=None,inherited=None):
    world=world if world is not None else QMatrix4x4()
    if group.xform is not None:world=world*group.xform
    inherited=group.material or inherited
    yield group,world,inherited
    for child in group.children:yield from walk(child,world,inherited)

def clone_tree(group):
    result=copy.copy(group)
    # Replaces the existing node: keep its UID so History preserves its layer.
    result.ext=copy.deepcopy(group.ext)
    result.material=copy.deepcopy(group.material)
    result.mesh=Mesh()
    for face in group.mesh.faces:
        f=result.mesh.add_face(face.vertices,face.holes)
        f.attrs=copy.deepcopy(face.attrs);f.interior=face.interior
    for edge in group.mesh.edges:
        stamp_edge_flags(result.mesh.add_edge(edge.a,edge.b),edge_flags(edge))
    result.children=[clone_tree(c) for c in group.children]
    result.component=None
    return result

class PaintCommand(Command):
    """Keep meshes/transforms in their own coordinates and never touch siblings."""
    def __init__(self,scene,recipe,entities=None,matching=None,both=True,preserve_uv=False):
        self.recipe=validate(recipe);self.material=material_for(self.recipe)
        self.old_registry=dict(scene.materials)
        self.groups=[];self.faces=[];self.count=0
        entities=list(scene.selection) if entities is None else list(entities)
        roots=selected_groups(scene,entities)
        entity_set=set(entities)
        loose=[f for f in scene.mesh.faces if f in entity_set]
        if matching:
            if scene.edit_group is not None:raise ValueError('更新整份模型的同名材質前，請先退出群組編輯。')
            roots=list(scene.groups)
            loose=[f for f in scene.mesh.faces if f.attrs.get('mat')==matching or (isinstance(f.attrs.get('back'),dict) and f.attrs['back'].get('mat')==matching)]
        def update(face,world,inherited=None):
            old=face.attrs;front=old.get('mat')==matching if matching else True
            if matching and not any(k in old for k in ('color','texture')):
                front=bool(inherited and inherited.get('mat')==matching)
            back=old.get('back')
            rear=matching and isinstance(back,dict) and back.get('mat')==matching
            if not front and not rear:return None
            painted=face_paint(face,world,self.recipe,self.material)
            if preserve_uv and old.get('texture') and painted.get('texture'):
                tex=old['texture']
                if (tex.get('base') or tex.get('path'))==self.recipe['path']:
                    for key in ('uvw','sw','sh','rot',KEY):
                        if key in tex:painted['texture'][key]=copy.deepcopy(tex[key])
            new=copy.deepcopy(old)
            if front:
                for k in PAINT_KEYS:new.pop(k,None)
                new.update(painted)
                if both:new['back']=True
            if rear and not (front and both):new['back']=copy.deepcopy(painted)
            return new
        for root in roots:
            if sum(face_count(g.mesh) for g,_,_ in walk(root))>150000:
                raise ValueError('單個物件超過 150,000 面，請縮小選取範圍。')
            if not matching and not scene.entity_selectable(root):raise ValueError('物件不可編輯，請先解除鎖定或顯示。')
            candidate=clone_tree(root);changed=0
            locked_meshes={id(n.mesh) for g,_,_ in walk(candidate) if g.locked
                           for n,_,_ in walk(g)}
            for g,world,inherited in walk(candidate):
                for face in g.mesh.faces:
                    new=update(face,world,inherited)
                    if new is not None:
                        if id(g.mesh) in locked_meshes:raise ValueError('選取範圍有鎖定物件，請先解鎖。')
                        face.attrs=new;changed+=1
            if changed:
                self.groups.append((root,(root.mesh,root.children,root.component),(candidate.mesh,candidate.children,None)))
                self.count+=changed
        for face in loose:
            if not scene.entity_selectable(face):raise ValueError('選取範圍含不可編輯的面。')
            new=update(face,QMatrix4x4())
            if new is not None:self.faces.append((face,face.attrs,new));self.count+=1
        if not self.count:raise ValueError('請先選取面或完整群組；也可以按「油漆桶」直接點模型。')

    def _put(self,scene,after):
        for group,old,new in self.groups:
            group.mesh,group.children,group.component=new if after else old
        for face,old,new in self.faces:face.attrs=new if after else old
        scene.materials.clear();scene.materials.update(self.old_registry)
        if after:scene.materials[self.material.name]=self.material
        for group,_,_ in self.groups:
            for g,_,_ in walk(group):g.mesh._chunk_dirty=True;g.mesh._mut_serial+=1
        scene.mesh._chunk_dirty=True;scene.mesh._mut_serial+=1
        scene.version+=1

    def do(self,scene):self._put(scene,True)
    def undo(self,scene):self._put(scene,False)

class CreateColorCommand(Command):
    """Paint the entire selection once, then recolor only those faces per drag."""
    def __init__(self,scene,recipe,both=True):
        self.recipe=selection_recipe(scene,recipe)
        self.name=self.recipe['name']
        initial=copy.deepcopy(self.recipe);initial['tint']=False
        self.paint=PaintCommand(scene,initial,both=both,preserve_uv=True);self.color=None
    def recolor(self,recipe,preview=False,prepared=None):self.color.recolor(recipe,preview,prepared)
    def do(self,scene):
        self.paint.do(scene)
        if self.color is None:
            scope=([g for g,old,new in self.paint.groups],[f for f,old,new in self.paint.faces])
            self.color=ColorEditCommand(scene,self.name,self.recipe,preview=True,scope=scope)
        self.color.do(scene)
    def undo(self,scene):
        self.color.undo(scene);self.paint.undo(scene)

def selection_recipe(scene,recipe):
    """Do not edit an unselected surface merely because its material is shared."""
    result=copy.deepcopy(recipe);name=result['name']
    if name not in scene.materials:return result
    selected=set(scene.selection);roots=set(selected_groups(scene,selected))
    def used(attrs):
        return attrs.get('mat')==name or (isinstance(attrs.get('back'),dict) and attrs['back'].get('mat')==name)
    def outside(group):
        if group in roots:return False
        return (used(group.material or {})
                or any(f not in selected and used(f.attrs) for f in group.mesh.faces)
                or any(outside(child) for child in group.children))
    external=any(f not in selected and used(f.attrs) for f in scene.loose_mesh.faces)
    external=external or any(outside(g) for g in scene.groups)
    if external:
        i=1
        while f'{name}（選取 {i}）' in scene.materials:i+=1
        result['name']=f'{name}（選取 {i}）'
    return result

def paint_selection(scene):
    """Only editable faces/groups, not a selection of construction edges."""
    selected=set(scene.selection)
    return selected_groups(scene,selected)+[f for f in scene.mesh.faces if f in selected]

class NewMaterialCommand(Command):
    def __init__(self,material):self.material=material;self.old=None
    def do(self,scene):
        self.old=scene.materials.get(self.material.name);scene.materials[self.material.name]=self.material;scene.version+=1
    def undo(self,scene):
        if self.old is None:scene.materials.pop(self.material.name,None)
        else:scene.materials[self.material.name]=self.old
        scene.version+=1

def material_used(scene,name):
    def matches(a):
        return a.get('mat')==name or isinstance(a.get('back'),dict) and a['back'].get('mat')==name
    if any(matches(f.attrs) for f in scene.loose_mesh.faces):return True
    for root in scene.groups:
        for g,_,_ in walk(root):
            if matches(g.material or {}) or any(matches(f.attrs) for f in g.mesh.faces):return True
    return False

def apply(app,recipe,entities=None,matching=None,both=True):
    command=PaintCommand(app.scene,recipe,entities,matching,both)
    app.viewport.history.execute(command)
    if app.viewport.history.last_error:raise ValueError(app.viewport.history.last_error)
    app.viewport.notify_scene_changed()
    return command.count
