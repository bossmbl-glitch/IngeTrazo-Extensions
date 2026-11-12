"""Delete material paint, never geometry; include backs and kept definitions."""
import copy
from core.history import Command
from core.mesh import PAINT_KEYS
from core.purge import iter_meshes,iter_groups

def groups(scene):
    return iter_groups(list(scene.groups)+list(getattr(scene,'kept_components',[]) or []))

def names_in(attrs):
    if not isinstance(attrs,dict):return set()
    names={attrs['mat']} if attrs.get('mat') else set()
    return names|names_in(attrs.get('back'))

def usage(scene):
    counts={}
    for mesh in iter_meshes(scene):
        for face in mesh.faces:
            for name in names_in(face.attrs):counts.setdefault(name,[0,0])[0]+=1
    for group in groups(scene):
        for name in names_in(group.material):counts.setdefault(name,[0,0])[1]+=1
    return counts

class DeleteMaterialsCommand(Command):
    def __init__(self,scene,names):
        self.names=set(names);self.registry=dict(scene.materials);self.targets=[];self.meshes=set()
        self.face_count=0;self.group_count=0
        def stripped(attrs):
            out=copy.deepcopy(attrs)
            if attrs.get('mat') in self.names:
                for key in PAINT_KEYS:out.pop(key,None)
            if isinstance(attrs.get('back'),dict):
                back=stripped(attrs['back'])
                if back:out['back']=back
                else:out.pop('back',None)
            return out
        for mesh in iter_meshes(scene):
            for face in mesh.faces:
                if names_in(face.attrs)&self.names:
                    self.targets.append((face,'attrs',face.attrs,stripped(face.attrs)))
                    self.meshes.add(mesh);self.face_count+=1
        for group in groups(scene):
            if names_in(group.material)&self.names:
                self.targets.append((group,'material',group.material,stripped(group.material) or None))
                self.group_count+=1
                for child in iter_groups([group]):self.meshes.add(child.mesh)
    def _put(self,scene,after):
        for obj,key,old,new in self.targets:setattr(obj,key,new if after else old)
        scene.materials.clear();scene.materials.update(self.registry)
        if after:
            for name in self.names:scene.materials.pop(name,None)
        for mesh in self.meshes:mesh._chunk_dirty=True;mesh._mut_serial+=1
        scene.version+=1
    def do(self,scene):self._put(scene,True)
    def undo(self,scene):self._put(scene,False)
