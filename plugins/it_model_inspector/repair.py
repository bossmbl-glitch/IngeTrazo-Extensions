"""Conservative, isolated repairs: prepare detached meshes, commit with one undo."""
import copy
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
import numpy as np
from core.mesh import Mesh, Vertex, Edge, Face, EDGE_FLAG_NAMES
from core.history import Command
from .analysis import normal


@dataclass(frozen=True)
class RepairOptions:
    topology: bool = True
    duplicates: bool = True
    sharp_edges: bool = True
    small_holes: bool = False
    max_hole_mm: float = 50.
    bad_faces: bool = True


def clone_mesh(source):
    """Copy geometry without copying renderer/native caches or recursing through connectivity."""
    mesh = Mesh()
    vertices = {v: Vertex(v.position) for v in source.vertices}
    faces = {f: Face([vertices[v] for v in f.loop],
                     [[vertices[v] for v in ring] for ring in f.hole_loops]) for f in source.faces}
    # Include stale adjacency edges in the map, but not the owning edge list.
    edge_set = set(source.edges)
    for v in source.vertices: edge_set.update(v.edges)
    edges = {e: Edge(vertices[e.v0], vertices[e.v1]) for e in edge_set}
    for f, out in faces.items():
        out.attrs = copy.deepcopy(f.attrs); out.interior = f.interior
    for e, out in edges.items():
        for name in EDGE_FLAG_NAMES: setattr(out, name, copy.deepcopy(getattr(e, name)))
        out.faces = [faces.get(f, f) for f in e.faces]
    for v, out in vertices.items(): out.edges = {edges[e] for e in v.edges}
    mesh.vertices = [vertices[v] for v in source.vertices]
    mesh.edges = [edges[e] for e in source.edges]
    mesh.faces = [faces[f] for f in source.faces]
    mesh._registry = {k: vertices[v] for k, v in source._registry.items() if v in vertices}
    mesh._displaced = {}
    mesh._mut_serial = source._mut_serial + 1
    mesh._chunk_dirty = True
    return mesh


def ring_identity(ring):
    """Exact vertex identity, same winding, rotation independent."""
    ids = [id(v) for v in ring]
    if not ids: return ()
    first = min(ids)
    return min(tuple(ids[i:]+ids[:i]) for i, x in enumerate(ids) if x == first)


def attrs_equal(a, b):
    try:
        if isinstance(a, dict) and isinstance(b, dict):
            return a.keys() == b.keys() and all(attrs_equal(a[k], b[k]) for k in a)
        if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
            return bool(np.array_equal(a, b))
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            return len(a) == len(b) and all(attrs_equal(x,y) for x,y in zip(a,b))
        return bool(a == b)
    except Exception:
        return False


def fix_topology(mesh, counts):
    by_pair = defaultdict(list)
    for e in mesh.edges: by_pair[frozenset((e.v0, e.v1))].append(e)
    incidence = defaultdict(list)
    for f in mesh.faces:
        for ring in [f.loop, *f.hole_loops]:
            for a, b in zip(ring, ring[1:]+ring[:1]):
                if a is b: continue  # degenerate faces need manual review
                key = frozenset((a,b))
                if not by_pair[key]:
                    e = Edge(a,b); mesh.edges.append(e); by_pair[key].append(e)
                    counts['補回缺線'] += 1
                incidence[key].append(f)
    for key, edges in by_pair.items():
        # Ambiguous duplicate edge objects are left for inspection.
        if len(edges) != 1: continue
        e = edges[0]; expected = incidence[key]
        if Counter(e.faces) != Counter(expected):
            e.faces = list(expected); counts['修正面邊關聯'] += 1
    expected_edges = defaultdict(set)
    for e in mesh.edges:
        expected_edges[e.v0].add(e); expected_edges[e.v1].add(e)
    for v in mesh.vertices:
        if v.edges != expected_edges[v]:
            v.edges = expected_edges[v]
            counts['修正頂點邊關聯'] += 1


def world_points(mesh, matrix):
    p = np.asarray([v.position.toTuple() for v in mesh.vertices], dtype=float).reshape((-1,3))
    if matrix is not None and len(p):
        from core.group import np_affine
        rot, trans = np_affine(matrix)
        p = p @ rot.T + trans
    return {v: p[i] for i, v in enumerate(mesh.vertices)}


def simple_cycles(ring):
    """Decompose a closed vertex walk, removing retraced spikes, not its valid lobes."""
    if not ring: return []
    stack=[]; positions={}; cycles=[]
    for v in [*ring,ring[0]]:
        if v in positions:
            i=positions[v]
            cycle=stack[i:]
            if len(cycle)>=3: cycles.append(cycle)
            for removed in stack[i+1:]: positions.pop(removed,None)
            stack=stack[:i+1]
        else:
            positions[v]=len(stack);stack.append(v)
    return cycles


def point_in_ring(point, polygon):
    inside=False
    for a,b in zip(polygon,np.roll(polygon,-1,axis=0)):
        ab=b-a;ap=point-a
        cross=ab[0]*ap[1]-ab[1]*ap[0]
        if abs(cross)<1e-12 and np.dot(ap,point-b)<=1e-12: return 0
        if (a[1]>point[1]) != (b[1]>point[1]):
            x=a[0]+(point[1]-a[1])*(b[0]-a[0])/(b[1]-a[1])
            if point[0]<x: inside=not inside
    return 1 if inside else -1


def split_crossings(ring, mesh, world, matrix):
    """Recover planar bow-ties by inserting their actual crossing point, never a hull."""
    ring=list(ring)
    if not 4<=len(ring)<=256: return ring,False
    p=np.array([world[v] for v in ring])
    if not np.isfinite(p).all(): return ring,False
    _,_,basis=np.linalg.svd(p-p[0],full_matrices=False)
    if np.max(np.abs((p-p[0])@basis[2]))>1e-5:return ring,False
    changed=False
    for _ in range(64):
        q=np.array([world[v]-p[0] for v in ring])@basis[:2].T
        found=False
        for i in range(len(ring)):
            a,b=q[i],q[(i+1)%len(ring)];ab=b-a
            for j in range(i+2,len(ring)):
                if i==0 and j==len(ring)-1:continue
                c,d=q[j],q[(j+1)%len(ring)];cd=d-c
                cross=lambda u,v:u[0]*v[1]-u[1]*v[0]
                det=cross(ab,cd)
                if abs(det)<1e-16:continue
                t=cross(c-a,cd)/det;u=cross(c-a,ab)/det
                if not(1e-8<t<1-1e-8 and 1e-8<u<1-1e-8):continue
                local=ring[i].position*(1-t)+ring[(i+1)%len(ring)].position*t
                v=mesh.vertex(local)
                mapped=matrix.map(v.position) if matrix is not None else v.position
                target=world[ring[i]]*(1-t)+world[ring[(i+1)%len(ring)]]*t
                if np.linalg.norm(np.array(mapped.toTuple())-target)>1e-6:return list(ring),False
                world[v]=np.array(mapped.toTuple())
                ring.insert(j+1,v);ring.insert(i+1,v)
                changed=found=True;break
            if found:break
        if not found:return ring,changed
    return ring,False


def clean_bad_faces(mesh, matrix, counts):
    """Normalize repeated walks, preserve hole islands, delete only truly collapsed faces."""
    world=world_points(mesh,matrix)
    old_face_edges={e for e in mesh.edges if e.faces}
    changed=False
    def collinear(ring):
        pts=np.array([world[v] for v in ring])
        if len(pts)<3: return True
        if not np.isfinite(pts).all(): return False
        # A bow-tie can have zero signed area but a real surface: never delete it as a line.
        singular=np.linalg.svd(pts-pts[0],compute_uv=False)
        return len(singular)<2 or singular[1]<=1e-10
    for f in list(mesh.faces):
        original=[f.loop,*f.hole_loops]
        repeated=any(len(set(r))!=len(r) for r in original)
        collapsed=collinear(f.loop)
        signed_degenerate=normal(np.array([world[v] for v in f.loop]))[1]<1e-12
        if not repeated and not collapsed and not signed_degenerate: continue
        if collapsed:
            mesh.faces.remove(f);counts['移除退化面']+=1;changed=True;continue
        recovered=False
        prepared=[]
        for ring in original:
            if normal(np.array([world[v] for v in ring]))[1]<1e-12 and not collinear(ring):
                ring,split=split_crossings(ring,mesh,world,matrix);recovered|=split
            prepared.append(ring)
        if not repeated and not recovered:continue
        cycles=[c for ring in prepared for c in simple_cycles(ring) if not collinear(c)]
        if not cycles:
            # Retraced non-collinear walks can have no enclosed region at all.
            mesh.faces.remove(f);counts['移除退化面']+=1;changed=True;continue
        normals=[normal(np.array([world[v] for v in c])) for c in cycles]
        if any(area<1e-12 for n,area in normals): continue
        dominant=max(range(len(cycles)),key=lambda i:normals[i][1])
        n=normals[dominant][0];origin=world[cycles[dominant][0]]
        if any(abs(float(np.dot(world[v]-origin,n)))>1e-5 for c in cycles for v in c): continue
        axis=int(np.argmax(np.abs(n)))
        polygons=[np.array([np.delete(world[v],axis) for v in c]) for c in cycles]
        areas=[item[1] for item in normals]
        # Containment converts a bridged hole back into an actual hole, never a cap.
        parents=[]
        for i,polygon in enumerate(polygons):
            containers=[]
            for j,outer in enumerate(polygons):
                if areas[j]<=areas[i]+1e-12: continue
                states=[point_in_ring(p,outer) for p in polygon]
                if all(x==1 for x in states): containers.append(j)
            parents.append(min(containers,key=lambda j:areas[j]) if containers else None)
        def depth(i):
            d=0
            while parents[i] is not None: d+=1;i=parents[i]
            return d
        new_faces=[]
        for i,ring in enumerate(cycles):
            if depth(i)%2: continue
            holes=[cycles[j] for j in range(len(cycles)) if parents[j]==i and depth(j)%2]
            out=Face(ring,holes);out.attrs=copy.deepcopy(f.attrs);out.interior=f.interior
            new_faces.append(out)
        mesh.faces.remove(f);mesh.faces.extend(new_faces)
        counts['修正重複頂點面' if repeated else '重建退化面']+=1
        if len(new_faces)>1: counts['重建有效面']+=len(new_faces)
        changed=True
    if changed:
        fix_topology(mesh,counts)
        # Remove edges left solely by a collapsed/retraced face; preserve pre-existing loose lines.
        for e in list(mesh.edges):
            if not e.faces and (e in old_face_edges or e.v0 is e.v1):
                mesh.remove_edge(e)
        mesh._chunk_dirty=True;mesh._mut_serial+=1


def fill_small_holes(mesh, world, max_mm, counts):
    # Only simple closed boundary cycles; existing face holes are intentional openings.
    inner_vertices = {v for f in mesh.faces for ring in f.hole_loops for v in ring}
    boundaries = [e for e in mesh.edges if len(e.faces) == 1]
    adj = defaultdict(list)
    for e in boundaries: adj[e.v0].append(e); adj[e.v1].append(e)
    unseen = set(boundaries)
    while unseen:
        todo = [unseen.pop()]; component = []; verts = set()
        while todo:
            e = todo.pop(); component.append(e)
            for v in (e.v0,e.v1):
                verts.add(v)
                for nxt in adj[v]:
                    if nxt in unseen: unseen.remove(nxt); todo.append(nxt)
        if not 3 <= len(component) <= 128: continue
        if any(len(adj[v]) != 2 or v in inner_vertices for v in verts): continue
        start = component[0].v0; current = start; previous = None; ring = []
        while current not in ring:
            ring.append(current)
            edge = next(e for e in adj[current] if e is not previous)
            current = edge.other(current); previous = edge
        if current is not start or len(ring) != len(component): continue
        p = np.array([world[v] for v in ring])
        if not np.isfinite(p).all(): continue
        diameter = np.linalg.norm(p.max(0)-p.min(0))
        if diameter*1000 > max_mm: continue
        n, area = normal(p)
        if area <= 1e-12 or np.max(np.abs((p-p[0])@n)) > 1e-5: continue
        # Do not lay another face over a thin planar surface or a window cut in a face.
        neighbors = {e.faces[0] for e in component}
        if any(abs(float(np.dot(normal(np.array([world[v] for v in f.loop]))[0], n))) > .9999 for f in neighbors): continue
        # Strictly simple polygon; skip touching/crossing non-adjacent segments.
        axis = int(np.argmax(np.abs(n))); q = np.delete(p, axis, axis=1)
        def cross(a,b): return a[0]*b[1]-a[1]*b[0]
        simple = True
        for i in range(len(q)):
            a,b = q[i],q[(i+1)%len(q)]
            for j in range(i+2,len(q)):
                if i==0 and j==len(q)-1: continue
                c,d = q[j],q[(j+1)%len(q)]
                if np.all(np.maximum(np.minimum(a,b),np.minimum(c,d)) <= np.minimum(np.maximum(a,b),np.maximum(c,d))+1e-10):
                    if cross(b-a,c-a)*cross(b-a,d-a)<=1e-20 and cross(d-c,a-c)*cross(d-c,b-c)<=1e-20:
                        simple=False;break
            if not simple: break
        if not simple: continue
        # Every neighbor must agree on the cap winding.
        directions=[]
        for a,b in zip(ring,ring[1:]+ring[:1]):
            edge=next(e for e in adj[a] if e.has(b))
            f=edge.faces[0]
            dirs=[x is a for lp in [f.loop,*f.hole_loops] for x,y in zip(lp,lp[1:]+lp[:1]) if {x,y}=={a,b}]
            if len(dirs)!=1: directions=[]; break
            directions.append(dirs[0])
        if not directions or len(set(directions))!=1: continue
        if directions[0]: ring.reverse()
        cap = mesh.add_face([v.position for v in ring])
        # A new face inherits paint only when all neighboring paints agree.
        neighbor_list=list(neighbors)
        if all(attrs_equal(neighbor_list[0].attrs,f.attrs) for f in neighbor_list[1:]):
            cap.attrs=copy.deepcopy(neighbor_list[0].attrs)
        counts['補小型破洞'] += 1


def repair_mesh(source, matrix, options):
    mesh = clone_mesh(source)
    counts = Counter()
    if options.bad_faces: clean_bad_faces(mesh,matrix,counts)
    if options.topology: fix_topology(mesh, counts)
    if options.duplicates:
        seen = defaultdict(list)
        for f in list(mesh.faces):
            key = (ring_identity(f.loop),tuple(sorted(ring_identity(r) for r in f.hole_loops)))
            if any(f.interior == other.interior and attrs_equal(f.attrs, other.attrs) for other in seen[key]):
                mesh.remove_face(f); counts['移除完全重複面'] += 1
            else: seen[key].append(f)
    if options.sharp_edges or options.small_holes:
        points = world_points(mesh,matrix)
        if not all(np.isfinite(p).all() for p in points.values()):
            return (mesh if counts else source), counts
        if options.sharp_edges:
            normals = {f: normal(np.array([points[v] for v in f.loop]))[0] for f in mesh.faces}
            for e in mesh.edges:
                if not (e.hidden or e.soft) or len(e.faces)!=2: continue
                if any(f not in normals or np.linalg.norm(normals[f])<.5 for f in e.faces): continue
                angle=math.degrees(math.acos(float(np.clip(abs(np.dot(normals[e.faces[0]],normals[e.faces[1]])),0,1))))
                if angle >= 30:
                    e.hidden=False; e.soft=False; counts['顯示折角線'] += 1
        if options.small_holes: fill_small_holes(mesh,points,options.max_hole_mm,counts)
    if counts:
        mesh._chunk_dirty=True; mesh._mut_serial+=1
    return (mesh if counts else source), counts


class RepairModelsCommand(Command):
    """Prepare all copies before committing; no edits to shared unselected definitions."""
    def __init__(self, scene, selection, options):
        if scene.edit_group is not None:
            raise ValueError('請先退出群組編輯，再選取整個模型進行自動修正。')
        if not any((options.topology,options.duplicates,options.sharp_edges,options.small_holes,options.bad_faces)):
            raise ValueError('請至少勾選一種自動修正項目。')
        if not math.isfinite(options.max_hole_mm) or options.max_hole_mm<=0:
            raise ValueError('補洞跨距必須大於零。')
        roots=[g for g in scene.groups if g in set(selection)]
        if not roots: raise ValueError('請先選取要修正的模型群組或元件。')
        self.items=[]; self.counts=Counter(); self.skipped_locked=0
        self.version=scene.content_version; self.first=True
        def prepare(group, matrix, ancestors):
            if id(group) in ancestors: raise ValueError('模型有循環群組，無法修正。')
            if group.locked:
                self.skipped_locked+=1; return group
            if (group.ext or {}).get('it_lighting_guide'): return group
            local=group.xform
            world=matrix if local is None else (local if matrix is None else matrix*local)
            mesh,counts=repair_mesh(group.mesh,world,options)
            self.counts.update(counts)
            children=[prepare(c,world,ancestors|{id(group)}) for c in group.children]
            if mesh is group.mesh and all(a is b for a,b in zip(children,group.children)): return group
            out=copy.copy(group); out.mesh=mesh; out.children=children
            return out
        for group in roots:
            fresh=prepare(group,None,set())
            if fresh is not group:
                self.items.append((group, group.mesh, group.children, fresh.mesh, fresh.children))

    def do(self, scene):
        if self.first and scene.content_version != self.version:
            raise ValueError('模型已變更，請重新執行修正。')
        if any(g not in scene.groups or g.mesh is not old or g.children is not children for g,old,children,_,_ in self.items):
            raise ValueError('模型結構已變更，修正已取消。')
        applied=[]
        try:
            for g,old,children,new,new_children in self.items:
                applied.append((g,old,children)); g.mesh=new; g.children=new_children
        except Exception:
            for g,old,children in applied: g.mesh=old; g.children=children
            raise
        self.first=False
        scene.version+=1

    def undo(self, scene):
        for g,old,children,_,_ in self.items: g.mesh=old; g.children=children
        scene.version+=1

    def summary(self):
        details='、'.join(f'{name} {count} 處' for name,count in self.counts.items())
        text=f'已修正 {len(self.items)} 個選取模型：{details}。Ctrl+Z 可一次復原。' if self.items else '依照勾選項目，沒有可自動修正的問題。'
        if self.skipped_locked: text+=f' 已略過 {self.skipped_locked} 個鎖定群組。'
        return text
