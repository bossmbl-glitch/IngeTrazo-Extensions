# SPDX-License-Identifier: GPL-3.0-or-later
"""Path productivity operations; every model write remains an undo command."""
import copy,math
import numpy as np
from core.history import Command
from core.group import copy_group,_independent_copy
from .path_editor_enhancements import EnhancedPathSession
from .path_editor import ReplacePathCommand
from .profile_operations import clone_mesh


def point_key(point):return tuple(float(x) for x in point)


class CopyPathCommand(Command):
    keeps_paint=True
    def __init__(self,session,new):self.session=session;self.new=new;self.index=None
    def do(self,scene):
        self.session.assert_unchanged(scene)
        if self.new in scene.groups:raise ValueError('副本已存在，不能重複加入。')
        if self.index is None:self.index=scene.groups.index(self.session.group)+1
        scene.groups.insert(self.index,self.new);scene.selection.discard(self.session.group);scene.selection.add(self.new);scene.version+=1
    def undo(self,scene):
        if self.new not in scene.groups:raise ValueError('副本已不在模型中。')
        scene.groups.remove(self.new);scene.selection.discard(self.new);scene.selection.add(self.session.group);scene.version+=1


class ProductivePathSession(EnhancedPathSession):
    def __init__(self,*args):
        super().__init__(*args)
        indices=self.recipe.get('locked_path_nodes',[])
        self.locks={point_key(self.points[i]) for i in indices if isinstance(i,int) and 0<=i<len(self.points)}
        self.initial_locks=set(self.locks);self.copy_mode=False

    def locked(self,index):return point_key(self.points[index]) in self.locks
    def check_locks(self,points):
        if not self.locks.issubset({point_key(p) for p in points}):raise ValueError('操作會移動或刪除鎖定節點；請先解除鎖定，或改用該點作固定位置。')
    def _record(self):
        self.undo_stack.append((copy.deepcopy(self.points),self.closed,set(self.locks),self.copy_mode));self.redo_stack.clear()
    def _restore(self,source,target):
        if not source:return
        target.append((copy.deepcopy(self.points),self.closed,set(self.locks),self.copy_mode))
        self.points,self.closed,self.locks,self.copy_mode=source.pop()
    def undo(self):self._restore(self.undo_stack,self.redo_stack)
    def redo(self):self._restore(self.redo_stack,self.undo_stack)
    def lock_nodes(self,indices,locked=True):
        if not indices:raise ValueError('請先選取節點。')
        keys={point_key(self.points[i]) for i in indices};new=self.locks|keys if locked else self.locks-keys
        if new!=self.locks:self._record();self.locks=new
    def move(self,index,point):
        if self.locked(index) and math.dist(self.points[index],point)>1e-9:raise ValueError('此節點已鎖定。')
        super().move(index,point)
    def replace_points(self,replacements):
        if any(self.locked(i) and math.dist(self.points[i],p)>1e-9 for i,p in replacements.items()):raise ValueError('操作會移動鎖定節點。')
        super().replace_points({i:self.points[i] if self.locked(i) else point for i,point in replacements.items()})
    def move_many(self,indices,delta):
        movable={i for i in indices if not self.locked(i)}
        if not movable:raise ValueError('選取節點全部已鎖定。')
        super().move_many(movable,delta)
    def delete(self,index):
        if self.locked(index):raise ValueError('鎖定節點不可刪除。')
        super().delete(index)
    def extend_endpoint(self,index):
        if self.locked(index):raise ValueError('請先解除端點鎖定再延長。')
        return super().extend_endpoint(index)
    def arm_copy(self):
        if not self.copy_mode:self._record();self.copy_mode=True
    def cut_segment(self,index):
        if not self.closed:raise ValueError('此操作適用封閉路徑；會移除選取線段並打開首尾。')
        self.segment_indices(index)
        self._record();self.points=self.points[index+1:]+self.points[:index+1];self.closed=False
    def join_ends(self):
        if self.closed:return
        candidate=copy.copy(self);candidate.closed=True;candidate.replacement()
        self._record();self.closed=True
    def divide_segment(self,index,parts):
        if not isinstance(parts,int) or not 2<=parts<=128:raise ValueError('等距分點須為2至128等份。')
        a,b=self.segment_indices(index)
        if len(self.points)+parts-1>2048:raise ValueError('分點後超過2048點上限。')
        start=np.array(self.points[a]);end=np.array(self.points[b])
        if np.linalg.norm(end-start)<1e-8:raise ValueError('不能分割零長度線段。')
        self._record();self.points[index+1:index+1]=[point_key(start+(end-start)*t/parts) for t in range(1,parts)]
    def frame_axes(self):
        points=np.asarray(self.points);origin=points[0]
        if self.world_normal is not None:n=self.world_normal
        else:
            _,singular,axes=np.linalg.svd(points-origin,full_matrices=False)
            if len(singular)<2 or singular[1]<1e-8:raise ValueError('整框尺寸需要至少三個不共線節點。')
            n=axes[-1]
        if np.max(np.abs((points-origin)@n))>1e-5:raise ValueError('整框尺寸與平面輔助限共平面路徑。')
        temp=copy.copy(self);temp.world_normal=n
        return temp.plane_axes()
    def frame_bounds(self):
        u,v=self.frame_axes();points=np.asarray(self.points);uv=np.column_stack((points@u,points@v))
        low=uv.min(axis=0);high=uv.max(axis=0)
        if np.min(high-low)<1e-8:raise ValueError('路徑寬高必須大於零。')
        return u,v,low,high
    def frame_points(self,width,height,anchor='center'):
        if not all(math.isfinite(x) and x>1e-8 for x in (width,height)):raise ValueError('整框寬高須為正值。')
        u,v,low,high=self.frame_bounds()
        anchors={'center':(.5,.5),'bottom_left':(0,0),'bottom_right':(1,0),'top_left':(0,1),'top_right':(1,1)}
        if anchor not in anchors:raise ValueError('固定位置無效。')
        fixed=low+(high-low)*np.asarray(anchors[anchor]);scale=np.array((width,height))/(high-low)
        result=[]
        for p in self.points:
            p=np.asarray(p);uv=np.array((p@u,p@v));new=fixed+(uv-fixed)*scale
            result.append(point_key(p+u*(new[0]-uv[0])+v*(new[1]-uv[1])))
        # Keep exactly fixed coordinates, rather than changing them through rounding.
        result=[old if math.dist(old,new)<1e-9 else new for old,new in zip(self.points,result)]
        self.check_locks(result);return result
    def replacement(self):
        self.check_locks(self.points)
        original,closed=self.original
        same=(self.closed==closed and len(self.points)==len(original) and all(math.dist(a,b)<1e-8 for a,b in zip(self.points,original)))
        if same:
            self.validate();group=_independent_copy(self.group);group.mesh=clone_mesh(self.group.mesh)  # Preserve hidden as well as soft edges.
        else:group=super().replacement()
        from . import PLUGIN_KEY
        group.ext[PLUGIN_KEY]['locked_path_nodes']=[i for i in range(len(self.points)) if self.locked(i)]
        return group
    def dirty(self):
        points,closed=self.original
        return (self.copy_mode or self.locks!=self.initial_locks or self.closed!=closed or len(points)!=len(self.points)
                or any(math.dist(a,b)>1e-8 for a,b in zip(points,self.points)))
    def commit(self,viewport):
        self.assert_unchanged(viewport.scene)
        if not self.dirty():return self.group
        replacement=self.replacement()
        if self.copy_mode:
            source_mesh=replacement.mesh;replacement=copy_group(replacement);replacement.mesh=clone_mesh(source_mesh)
            replacement.name=self.group.name+'（副本）'
            command=CopyPathCommand(self,replacement)
        else:command=ReplacePathCommand(self,replacement)
        viewport.history.execute(command)
        if getattr(viewport.history,'last_error',None):raise RuntimeError(viewport.history.last_error)
        if replacement not in viewport.scene.groups:raise RuntimeError('修改未提交，原線板保持原樣。')
        viewport.notify_scene_changed();return replacement
