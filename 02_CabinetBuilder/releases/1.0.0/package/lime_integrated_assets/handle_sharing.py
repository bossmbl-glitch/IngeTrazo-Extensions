"""Exact, paint-aware mesh reuse for physical library accessories, with private edits.

Only mesh pointers are shared. Placement/group identity, materials, axes,
hidden/layer state and the hierarchy remain per object. No decimation.
"""
from copy import copy, deepcopy
import hashlib
import json
import struct
import weakref

import numpy as np
from PySide6.QtGui import QMatrix4x4
from core.history import Command, _dirty_group_chunks

MARKER='lime_handle_shared_mesh'
PARAM_KEY='lime_suite_parametric'
POOL=weakref.WeakValueDictionary()
BOUNDS=weakref.WeakKeyDictionary()


def clone_placements(source):
    """Private group objects over immutable source meshes; never share nodes."""
    target=copy(source)
    # Group owns identity and placement even when its mesh is shared.
    from core.group import new_uid
    target.uid=new_uid()
    target.xform=QMatrix4x4(source.xform) if source.xform is not None else None
    target.axes=QMatrix4x4(source.axes) if source.axes is not None else None
    target.ext=deepcopy(source.ext)
    target.material=deepcopy(source.material)
    target.ifc=deepcopy(source.ifc)
    target.children=[clone_placements(child) for child in source.children or ()]
    return target


def copy_mesh(source):
    """Exact private topology copy, including interior/hidden/layer flags."""
    from core.mesh import Mesh, Vertex, Edge, Face
    result=Mesh()
    vertices={v:Vertex(v.position) for v in source.vertices}
    result.vertices=[vertices[v] for v in source.vertices]
    edges={}
    for old in source.edges:
        edge=Edge(vertices[old.v0],vertices[old.v1])
        edge.soft=old.soft;edge.hidden=old.hidden;edge.curve=old.curve;edge.layer=old.layer
        edge.v0.edges.add(edge);edge.v1.edges.add(edge)
        edges[old]=edge;result.edges.append(edge)
    faces={}
    for old in source.faces:
        face=Face([vertices[v] for v in old.loop],[[vertices[v] for v in ring] for ring in old.hole_loops])
        face.attrs=deepcopy(old.attrs);face.interior=old.interior
        faces[old]=face;result.faces.append(face)
    for old,edge in edges.items():edge.faces=[faces[f] for f in old.faces]
    result._registry={key:vertices[v] for key,v in source._registry.items()}
    return result


def mesh_key(mesh):
    """Hash all stored geometry, topology, paint and edge metadata exactly.
    Different paint/UVs remain separate variants; no tolerance welding.
    """
    digest=hashlib.sha256()
    indices={vertex:i for i,vertex in enumerate(mesh.vertices)}
    digest.update(np.asarray([v.position.toTuple() for v in mesh.vertices],dtype='<f4').tobytes())
    digest.update(np.asarray([(indices[e.v0],indices[e.v1]) for e in mesh.edges],dtype='<i4').tobytes())
    edge_meta=[(bool(e.soft),bool(e.hidden),e.curve,e.layer) for e in mesh.edges]
    digest.update(json.dumps(edge_meta,sort_keys=True,separators=(',',':'),allow_nan=False).encode())
    for face in mesh.faces:
        rings=[face.loop,*face.hole_loops]
        digest.update(struct.pack('<I?',len(rings),bool(face.interior)))
        for ring in rings:
            digest.update(struct.pack('<I',len(ring)))
            digest.update(np.asarray([indices[v] for v in ring],dtype='<i4').tobytes())
        attrs=json.dumps(face.attrs,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        digest.update(struct.pack('<I',len(attrs)));digest.update(attrs)
    return digest.hexdigest()


def handle_nodes(roots,scene=None,report=None):
    """Tagged physical accessories in any model; built-in cabinet C handles."""
    result=[];seen=set();skipped=0
    def visit(node,cabinet=False,handle=False,locked=False,hidden=False):
        nonlocal skipped
        if id(node) in seen:return
        seen.add(id(node))
        ext=node.ext or {};info=ext.get(PARAM_KEY) or {}
        cabinet=cabinet or info.get('kind') in ('cabinet','composite')
        handle=handle or 'lime_handle_id' in ext or bool(ext.get('lime_cabinet_handle_pair')) or (cabinet and ext.get('lime_kitchen_part')=='c_handle')
        layer_locked=scene._layer_state(node)[1] if scene is not None else False
        locked=locked or bool(node.locked) or bool(layer_locked)
        hidden=hidden or bool(node.hidden) or (scene is not None and not scene.entity_visible(node))
        if handle and node.mesh.faces:
            if locked or hidden:
                skipped+=1
                if report is not None:
                    key='鎖定' if locked else '隱藏';report[key]=report.get(key,0)+1
            else:result.append(node)
        for child in node.children or ():visit(child,cabinet,handle,locked,hidden)
    for root in roots:visit(root)
    return result,skipped


def plan_nodes(nodes,progress=None,cancel=None):
    memo={};canonical={};before=[];after=[]
    for index,node in enumerate(nodes):
        if cancel and cancel():raise ValueError('已取消最佳化；模型未變更。')
        if progress:progress(index,len(nodes))
        if cancel and cancel():raise ValueError('已取消最佳化；模型未變更。')
        mesh=node.mesh
        if id(mesh) not in memo:memo[id(mesh)]=mesh_key(mesh)
        key=memo[id(mesh)]
        shared=canonical.get(key)
        if shared is None:
            shared=POOL.get(key)
            # Isolate the pool from unmanaged components which may still
            # reference an old handle mesh and edit it using host semantics.
            if shared is None:shared=copy_mesh(mesh)
            canonical[key]=shared;POOL[key]=shared
        before.append((node,mesh,node.xform,node.component,node.ext))
        transform=node.xform if node.xform is not None else QMatrix4x4()
        # Placed prototypes use the host's fast instance path. The scoped
        # edit guard below supplies independent edit semantics for ours.
        after.append((node,shared,transform,True,{**(node.ext or {}),MARKER:1}))
    old={id(item[1]):item[1] for item in before};new={id(item[1]):item[1] for item in after}
    stats={'mesh_nodes':len(nodes),'unique_meshes_before':len(old),'unique_meshes_after':len(new),
           'unique_faces_before':sum(len(m.faces) for m in old.values()),
           'unique_faces_after':sum(len(m.faces) for m in new.values()),
           'reassigned_nodes':sum(a[1] is not b[1] for a,b in zip(before,after))}
    return before,after,stats


def assign(states):
    for node,mesh,transform,component,ext in states:
        node.mesh=mesh;node.xform=transform;node.component=component;node.ext=ext


def share_tree(root):
    nodes,_=handle_nodes([root])
    if nodes:
        _,after,_=plan_nodes(nodes)
        assign(after)
    return root


class OptimizeHandles(Command):
    name='實體配件－最佳化現有把手'
    keeps_paint=True
    def __init__(self,scene,progress=None,cancel=None):
        if scene.edit_group is not None:raise ValueError('請先離開群組內部編輯，再最佳化現有把手。')
        report={};nodes,skipped=handle_nodes(scene.groups,scene,report)
        self.before,self.after,self.stats=plan_nodes(nodes,progress,cancel)
        self.stats['locked_mesh_nodes_skipped']=skipped;self.stats['skipped_reasons']=report
    def do(self,scene):
        assign(self.after);_dirty_group_chunks(scene);scene.version+=1
    def undo(self,scene):
        assign(self.before);_dirty_group_chunks(scene);scene.version+=1


def install_edit_guard():
    """The host shares edits back for *any* placed mesh. Detach ours first.
    Applies to only marked handle meshes, including files reopened later;
    ordinary groups/components keep their existing host edit semantics.
    """
    from core.scene import Scene
    original=Scene.begin_group_edit
    if getattr(original,'_lime_handle_guard',False):return
    def carry_metadata(source,target,matrix):
        # The 0.6.0 host transform paths omit hidden/layer edge flags and
        # back UV transforms. Preserve them across entering/leaving an edit.
        from core.group import transformed_attrs
        for old in source.edges:
            a,b=target.vertex_at(matrix.map(old.a)),target.vertex_at(matrix.map(old.b))
            edge=target.find_edge(a,b) if a is not None and b is not None else None
            if edge is not None:
                edge.soft=old.soft;edge.hidden=old.hidden;edge.curve=old.curve;edge.layer=old.layer
        if len(source.faces)==len(target.faces):
            for old,new in zip(source.faces,target.faces):
                new.interior=old.interior
                if isinstance(old.attrs.get('back'),dict):new.attrs['back']=transformed_attrs(deepcopy(old.attrs['back']),matrix)
    def begin(scene,group):
        private=None;placement=None
        if (group.ext or {}).get(MARKER) and group.mesh.faces:
            # The guard also serves non-viewport callers. The subsequent host
            # share-back targets this private prototype, never its siblings.
            group.mesh=copy_mesh(group.mesh)
            private=group.mesh;placement=QMatrix4x4(group.xform) if group.xform is not None else None
        result=original(scene,group)
        if private is not None and placement is not None and not group.children:
            carry_metadata(private,group.mesh,placement)
        return result
    begin._lime_handle_guard=True
    Scene.begin_group_edit=begin
    original_share=Scene.share_back
    def share_back(group,proto,placement,edited):
        original_share(group,proto,placement,edited)
        if (group.ext or {}).get(MARKER):
            inverse,ok=placement.inverted()
            if ok:carry_metadata(edited,proto,inverse)
    Scene.share_back=staticmethod(share_back)
