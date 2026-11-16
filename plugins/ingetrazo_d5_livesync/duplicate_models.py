# SPDX-License-Identifier: GPL-3.0-or-later
"""Generic read-only duplicate recognition. Does not create SDK instances."""
from __future__ import annotations
from array import array
from collections import defaultdict,Counter
import hashlib,json,time


class ScanBudgetExceeded(Exception):pass


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def key(block):
    try:return str(block.as_pointer())
    except AttributeError:return str(id(block))


def prop(block,name,default=''):
    try:return block.get(name,default)
    except (AttributeError,TypeError):return default


def bulk(collection,name,width=1,kind='i'):
    data=array(kind,[0])*(len(collection)*width)
    if hasattr(collection,'foreach_get'):collection.foreach_get(name,data)
    else:
        values=[getattr(v,name) for v in collection]
        data=array(kind,(x for v in values for x in v) if width>1 else values)
    return data


def _hash_arrays(*values):
    h=hashlib.sha256()
    for value in values:
        data=value.tobytes() if isinstance(value,array) else json.dumps(value,separators=(',',':'),allow_nan=False).encode()
        h.update(len(data).to_bytes(8,'little'));h.update(data)
    return h.hexdigest()


def describe_mesh(mesh,deadline=None,canonical_limit=20000,canonical=True):
    vertices=bulk(mesh.vertices,'co',3,'f');edges=bulk(mesh.edges,'vertices',2)
    loop_vertices=bulk(mesh.loops,'vertex_index');starts=bulk(mesh.polygons,'loop_start')
    totals=bulk(mesh.polygons,'loop_total');indices=bulk(mesh.polygons,'material_index')
    smooth=bulk(mesh.polygons,'use_smooth',kind='b')
    geometry=_hash_arrays(vertices,edges,loop_vertices,starts,totals)
    uv=[]
    for layer in getattr(mesh,'uv_layers',[]):
        uv.append((layer.name,bool(getattr(layer,'active_render',False)),bulk(layer.data,'uv',2,'f')))
    normals=getattr(mesh,'corner_normals',None)
    normals=bulk(normals,'vector',3,'f') if normals is not None else None
    sharp=bulk(mesh.edges,'use_edge_sharp',kind='b') if len(mesh.edges) and hasattr(mesh.edges[0],'use_edge_sharp') else array('b')
    seams=bulk(mesh.edges,'use_seam',kind='b') if len(mesh.edges) and hasattr(mesh.edges[0],'use_seam') else array('b')
    raw_appearance=_hash_arrays(indices,smooth,sharp,seams,*[x[2] for x in uv],
                                [(x[0],x[1]) for x in uv],normals if normals is not None else 'unavailable')
    try:
        import numpy as np
        coordinates=np.frombuffer(vertices,dtype=np.float32).reshape((-1,3))
        dimensions=tuple(float(x) for x in (coordinates.max(axis=0)-coordinates.min(axis=0))) if len(coordinates) else ()
    except ImportError:
        dimensions=tuple(max(vertices[j::3])-min(vertices[j::3]) for j in range(3)) if vertices else ()
    descriptor={'geometry':geometry,'raw_geometry':geometry,'appearance':raw_appearance,
                'vertices':len(mesh.vertices),'faces':len(mesh.polygons),
                'triangles_estimate':sum(max(0,n-2) for n in totals),
                'method':'exact_indexed_mesh','normals_available':normals is not None,
                'canonical_status':'not_attempted','face_material_indices':tuple(indices),
                'polygon_order':None,'center':(0.,0.,0.),
                'shape_bucket':(len(mesh.vertices),len(mesh.edges),len(mesh.polygons),len(mesh.loops),tuple(round(x,6) for x in dimensions))}
    if not canonical:return descriptor
    if not len(mesh.polygons):return descriptor
    if len(mesh.polygons)>canonical_limit:
        descriptor['canonical_status']='face_limit';return descriptor
    points=[tuple(float(vertices[3*i+j]) for j in range(3)) for i in range(len(mesh.vertices))]
    if not points:return descriptor
    minimum=tuple(min(p[j] for p in points) for j in range(3))
    normalized=[tuple(p[j]-minimum[j] for j in range(3)) for p in points]
    # Coincident vertices can encode different connectivity. Keep indexed
    # matching, instead of guessing an ambiguous vertex correspondence.
    if len(set(normalized))!=len(normalized):
        descriptor['canonical_status']='coincident_vertices';return descriptor
    ordered=sorted(range(len(normalized)),key=lambda i:normalized[i]);rank={v:i for i,v in enumerate(ordered)}
    polygon_rows=[];surface_rows=[];polygon_order=[]
    for p,(start,total) in enumerate(zip(starts,totals)):
        if p%256==0 and deadline is not None and time.perf_counter()>deadline:raise ScanBudgetExceeded()
        corners=list(range(start,start+total));ranks=[rank[loop_vertices[i]] for i in corners]
        if len(set(ranks))!=len(ranks) or len(ranks)<3:
            descriptor['canonical_status']='degenerate_polygon';return descriptor
        shift=ranks.index(min(ranks));corners=corners[shift:]+corners[:shift];cycle=tuple(ranks[shift:]+ranks[:shift])
        polygon_rows.append(cycle);polygon_order.append((cycle,p))
        corner_data=[]
        for i in corners:
            uvs=tuple(tuple(float(layer[2][2*i+j]) for j in range(2)) for layer in uv)
            normal=tuple(float(normals[3*i+j]) for j in range(3)) if normals is not None else None
            corner_data.append((uvs,normal))
        surface_rows.append((cycle,bool(smooth[p]),tuple(corner_data)))
    # Identical overlapping faces need an unambiguous material association.
    if len(set(polygon_rows))!=len(polygon_rows):
        descriptor['canonical_status']='duplicate_faces';return descriptor
    edge_rows=[];edge_surface=[]
    for e in range(len(mesh.edges)):
        pair=tuple(sorted((rank[edges[2*e]],rank[edges[2*e+1]])))
        edge_rows.append(pair);edge_surface.append((pair,int(sharp[e]) if sharp else 0,int(seams[e]) if seams else 0))
    descriptor.update(geometry=digest((sorted(normalized),sorted(edge_rows),sorted(polygon_rows))),
        appearance=digest(([(x[0],x[1]) for x in uv],sorted(surface_rows),sorted(edge_surface))),
        method='exact_canonical_local_translation',canonical_status='verified',
        polygon_order=[p for _,p in sorted(polygon_order)],center=minimum)
    return descriptor


def matrix_values(matrix):
    return tuple(float(x) for row in matrix for x in row) if matrix is not None else ()


def material_keys(obj):
    slots=getattr(obj,'material_slots',None)
    materials=[getattr(s,'material',None) for s in slots] if slots is not None else list(getattr(obj.data,'materials',[]))
    return tuple((str(prop(m,'bridge_material_id')) or 'session:'+key(m)) if m is not None else '' for m in materials)


def review_reasons(obj,descriptor):
    reasons=[]
    for attr,label in [('modifiers','modifiers'),('constraints','constraints'),('animation_data','animation'),('vertex_groups','vertex_groups'),('library','linked_object')]:
        if getattr(obj,attr,None):reasons.append(label)
    for attr,label in [('shape_keys','shape_keys'),('library','linked_mesh'),('color_attributes','color_attributes')]:
        if getattr(obj.data,attr,None):reasons.append(label)
    if not descriptor['normals_available']:reasons.append('corner_normals_unavailable')
    matrix=getattr(obj,'matrix_world',None)
    if matrix is not None and hasattr(matrix,'determinant') and matrix.determinant()<0:reasons.append('mirrored_transform')
    if getattr(obj,'hide_render',False) or getattr(obj,'hide_viewport',False):reasons.append('visibility_override')
    return reasons


def assembly_groups(objects,rows,max_groups,max_members):
    """Verify whole hierarchies using child-local transforms, not names."""
    by_id={key(o):o for o in objects};by_row={key(r['object']):r for r in rows};children=defaultdict(list)
    for obj in objects:
        parent=getattr(obj,'parent',None)
        if parent is not None and key(parent) in by_id:children[key(parent)].append(key(obj))
    memo={};visiting=set();errors=[]
    def visit(oid,depth=0):
        if oid in memo:return memo[oid]
        if oid in visiting or depth>128:raise ValueError('Cyclic or excessively deep hierarchy')
        visiting.add(oid);obj=by_id[oid];row=by_row.get(oid);child_nodes=[]
        if getattr(obj,'type',None) not in ('MESH','EMPTY'):raise ValueError('Non-mesh hierarchy needs review')
        if getattr(obj,'type',None)=='MESH' and len(getattr(obj.data,'polygons',[])) and row is None:raise ValueError('Unscanned child mesh')
        if getattr(obj,'type',None)=='MESH' and row is None and len(getattr(obj.data,'vertices',[])):raise ValueError('Non-face mesh in hierarchy needs review')
        count=1 if row else 0;tris=row['descriptor']['triangles_estimate'] if row else 0
        reasons=set(row['member']['review_reasons'] if row else review_reasons(obj,{'normals_available':True}))
        for child_id in children[oid]:
            child=by_id[child_id];local=getattr(child,'matrix_local',None)
            if local is None:raise ValueError('Child local transform unavailable')
            node=visit(child_id,depth+1);count+=node['meshes'];tris+=node['triangles'];reasons.update(node['reasons'])
            child_nodes.append((matrix_values(local),node['geometry'],node['appearance']))
        geometric=(getattr(obj,'type',''),row['descriptor']['raw_geometry'] if row else '',sorted((m,g) for m,g,_ in child_nodes))
        appearance=(row['variant'] if row else '',bool(getattr(obj,'hide_viewport',False)),bool(getattr(obj,'hide_render',False)),sorted(child_nodes))
        node={'geometry':digest(geometric),'appearance':digest(appearance),'meshes':count,'triangles':tris,'reasons':sorted(reasons)}
        visiting.remove(oid);memo[oid]=node;return node
    buckets=defaultdict(list)
    for oid in children.copy():
        if not children[oid]:continue
        try:
            node=visit(oid)
            if node['meshes']:buckets[node['geometry']].append((by_id[oid],node))
        except ValueError as exc:
            visiting.clear()
            if len(errors)<8:errors.append(str(exc))
    groups=[]
    for signature,items in buckets.items():
        if len(items)<2:continue
        variants=defaultdict(list)
        for obj,node in items:variants[node['appearance']].append((obj,node))
        versions=[]
        for fingerprint,members in variants.items():
            reasons=sorted({r for _,n in members for r in n['reasons']})
            versions.append({'id':fingerprint[:16],'count':len(members),'review_reasons':reasons,
                'candidate_for_sdk_trial':len(members)>1 and not reasons,
                'members':[{'name':str(o.name)[:200],'object_id':str(prop(o,'ingetrazo_key')),
                    'session_key':key(o),'matrix_world':matrix_values(getattr(o,'matrix_world',None)),
                    'parent':str(getattr(getattr(o,'parent',None),'name',''))[:200],
                    'collections':[str(c.name)[:200] for c in getattr(o,'users_collection',[])]} for o,_ in members[:max_members]],
                'omitted_members':max(0,len(members)-max_members)})
        triangles=items[0][1]['triangles']
        groups.append({'kind':'assembly','id':signature[:16],'representative':str(items[0][0].name)[:200],
            'count':len(items),'basis':'verified_subtree','material_surface_variants':len(variants),
            'mesh_parts_per_assembly':items[0][1]['meshes'],'triangles_per_object_estimate':triangles,
            'repeated_triangles_estimate':triangles*(len(items)-1),'variants':versions})
    groups.sort(key=lambda g:-g['repeated_triangles_estimate'])
    return groups[:max_groups],len(groups),errors


def scan_duplicates(bpy,max_seconds=3.,max_objects=12000,max_groups=500,max_members=200,cache=None):
    started=time.perf_counter();deadline=started+max_seconds
    objects=list(getattr(bpy.context.view_layer,'objects',[]));descriptors={};rows=[];skipped=Counter();errors=[]
    previous_cache=cache if cache is not None else {};next_cache={};mesh_records={};cache_hits=0
    for obj in objects[:max_objects]:
        if time.perf_counter()>deadline:skipped['time_budget']+=1;break
        if getattr(obj,'type',None)!='MESH' or getattr(obj,'data',None) is None:continue
        mesh=obj.data
        if not len(getattr(mesh,'polygons',[])):
            skipped['no_polygons']+=1;continue
        mk=key(mesh)
        if mk not in descriptors:
            content_key=prop(mesh,'bridge_mesh_key') if prop(mesh,'ingetrazo_doc') else ''
            ck=(mk,str(content_key),len(mesh.vertices),len(mesh.edges),len(mesh.polygons),len(mesh.loops),
                bool(getattr(mesh,'has_custom_normals',False)),
                tuple((u.name,bool(getattr(u,'active_render',False))) for u in getattr(mesh,'uv_layers',[]))) if content_key else None
            cached=previous_cache.get(ck) if ck is not None else None
            try:
                if cached is not None:record=cached;cache_hits+=1
                else:record={'raw':describe_mesh(mesh,deadline,canonical=False),'canonical':None}
                descriptors[mk]=record['raw'];mesh_records[mk]=(mesh,record)
                if ck is not None:next_cache[ck]=record
            except ScanBudgetExceeded:skipped['time_budget']+=1;break
            except Exception as exc:
                skipped['mesh_read_error']+=1
                if len(errors)<8:errors.append(str(exc)[:200])
                continue
        d=descriptors[mk];mats=material_keys(obj)
        src=str(prop(mesh,'ingetrazo_src'));document=str(prop(mesh,'ingetrazo_doc'))
        source=(document,src) if src.startswith('proto/') else None
        parent=getattr(obj,'parent',None)
        rows.append({'object':obj,'mesh_key':mk,'descriptor':d,'geometry':d['geometry'],'materials':mats,
            'source':source,'member':{'name':str(obj.name)[:200],'object_id':str(prop(obj,'ingetrazo_key')),
            'session_key':key(obj),'parent':str(getattr(parent,'name',''))[:200],
            'matrix_world':matrix_values(getattr(obj,'matrix_world',None)),
            'prototype_local_offset':d['center'],'mesh':str(mesh.name)[:200],
            'source_component':source,'collections':[str(c.name)[:200] for c in getattr(obj,'users_collection',[])],
            'material_ids':mats,'review_reasons':review_reasons(obj,d)}})
    if len(objects)>max_objects:skipped['object_limit']=len(objects)-max_objects
    # First recognize all byte-identical meshes quickly. Spend only the
    # remaining budget on origin/vertex-order differences, atomically per
    # shape bucket so already-confirmed duplicates never get split mid-scan.
    candidates=defaultdict(list);canonical_complete=True
    # Follow-up edits prioritize responsiveness; continue costly comparisons
    # in small bounded slices while reusing importer-owned content keys.
    canonical_deadline=min(deadline,started+.35) if cache_hits else deadline
    for mk,d in descriptors.items():candidates[d['shape_bucket']].append(mk)
    for keys in candidates.values():
        if len({descriptors[k]['raw_geometry'] for k in keys})<2:continue
        completed=True
        for mk in keys:
            mesh,record=mesh_records[mk]
            if record['canonical'] is not None:continue
            if time.perf_counter()>canonical_deadline:completed=False;canonical_complete=False;break
            try:record['canonical']=describe_mesh(mesh,canonical_deadline)
            except ScanBudgetExceeded:completed=False;canonical_complete=False;break
            except Exception as exc:
                completed=False
                if len(errors)<8:errors.append(str(exc)[:200])
                canonical_complete=False;break
        if completed:
            for mk in keys:descriptors[mk]=mesh_records[mk][1]['canonical']
    if cache is not None:cache.clear();cache.update(next_cache)
    variant_cache={}
    for row in rows:
        d=descriptors[row['mesh_key']];mats=row['materials'];row['descriptor']=d;row['geometry']=d['geometry']
        row['member']['prototype_local_offset']=d['center']
        vk=(row['mesh_key'],d['appearance'],mats,tuple(getattr(row['object'],'color',())))
        if vk not in variant_cache:
            order=d['polygon_order'] if d['polygon_order'] is not None else range(len(d['face_material_indices']))
            painted_faces=tuple(mats[d['face_material_indices'][i]] if 0<=d['face_material_indices'][i]<len(mats) else '' for i in order)
            variant_cache[vk]=digest((d['appearance'],painted_faces,mats,vk[-1]))
        row['variant']=variant_cache[vk]
    buckets=defaultdict(list)
    for row in rows:buckets[row['geometry']].append(row)
    groups=[]
    for geometry,members in buckets.items():
        if len(members)<2:continue
        variants=defaultdict(list)
        for row in members:variants[row['variant']].append(row)
        source_ids={row['source'] for row in members if row['source'] is not None}
        meshes={row['mesh_key'] for row in members}
        basis='shared_mesh' if len(meshes)==1 else ('source_component_and_geometry' if len(source_ids)==1 and all(row['source'] for row in members) else 'geometry_fingerprint')
        variant_rows=[]
        for fingerprint,items in variants.items():
            reasons=sorted({reason for row in items for reason in row['member']['review_reasons']})
            variant_rows.append({'id':fingerprint[:16],'count':len(items),'review_reasons':reasons,
                'candidate_for_sdk_trial':len(items)>1 and not reasons,
                'members':[row['member'] for row in items[:max_members]],'omitted_members':max(0,len(items)-max_members)})
        tris=members[0]['descriptor']['triangles_estimate']
        groups.append({'id':geometry[:16],'representative':members[0]['member']['name'],'count':len(members),
            'basis':basis,'unique_mesh_data':len(meshes),'material_surface_variants':len(variants),
            'triangles_per_object_estimate':tris,'repeated_triangles_estimate':tris*(len(members)-1),
            'methods':sorted({row['descriptor']['method'] for row in members}),
            'variants':sorted(variant_rows,key=lambda v:-v['count'])})
    groups.sort(key=lambda g:(-g['repeated_triangles_estimate'],g['id']))
    assemblies,assembly_count,assembly_errors=assembly_groups(objects[:max_objects],rows,max_groups,max_members)
    total=len(groups);matched=sum(g['count'] for g in groups)
    return {'version':'0.9.10-test.6','summary':{'scanned_mesh_objects':len(rows),'unique_mesh_data_scanned':len(descriptors),
        'duplicate_groups':total,'objects_in_duplicate_groups':matched,
        'repeated_triangles_estimate':sum(g['repeated_triangles_estimate'] for g in groups),
        'sdk_trial_variants':sum(v['candidate_for_sdk_trial'] for g in groups for v in g['variants']),
        'seconds':time.perf_counter()-started,'skipped':dict(skipped),'descriptor_cache_hits':cache_hits,
        'canonical_complete':canonical_complete,
        'assembly_duplicate_groups':assembly_count,'assembly_report_errors':len(assembly_errors),
        'omitted_groups':max(0,total-max_groups),'complete':not any(k in skipped for k in ('time_budget','object_limit','mesh_read_error'))},
        'groups':groups[:max_groups],'assembly_groups':assemblies,'errors':errors,'assembly_errors':assembly_errors,
        'applied':False,'sdk_instancing_verified':False,
        'basis':'Exact raw mesh parts and verified hierarchies in active view layer. World placements excluded, child-local transforms retained. Material/UV/normal variants separate. No evaluated-geometry merge.'}


LABELS={'shared_mesh':'原本共用網格','source_component_and_geometry':'元件關聯與幾何吻合','geometry_fingerprint':'幾何指紋吻合','verified_subtree':'子物件幾何與相對位置吻合'}


def duplicate_text(report):
    if not report:return '尚無重複模型辨識結果，請先同步一次。\n'
    summary=report.get('summary',{})
    if summary.get('disabled'):return '本輪已停用重複模型辨識；停止同步後可在設定啟用。\n'
    lines=['重複模型辨識報告','提交版本：'+str(report.get('revision','—')),
        '辨識到 '+str(summary.get('duplicate_groups',0))+' 組，包含 '+str(summary.get('objects_in_duplicate_groups',0))+' 個網格物件。',
        '這是只讀分組；尚未轉換成D5代理／實例，也未確認傳送量下降。',
        '分組依據幾何資料，名稱只用來顯示。',
        '另辨識 '+str(summary.get('assembly_duplicate_groups',0))+' 組完整群組／元件；它們與零件分組可能重疊，不能把估算量重複相加。']
    if not summary.get('complete',True):lines.append('本輪掃描未完成，略過原因：'+str(summary.get('skipped',{})))
    if not summary.get('canonical_complete',True):lines.append('原點／頂點順序的進階比對尚未完成；已確認的索引相同分組仍有效。下一輪會利用內容快取續查。')
    if summary.get('omitted_groups'):lines.append('報告另省略 '+str(summary['omitted_groups'])+' 組。')
    if report.get('errors'):lines.append('辨識問題：'+'；'.join(report['errors']))
    if report.get('assembly_errors'):lines.append('完整群組待檢查原因範例：'+'；'.join(sorted(set(report['assembly_errors']))))
    for g in report.get('assembly_groups',[])+report.get('groups',[]):
        if g.get('kind')=='assembly':lines.append('\n【完整群組／元件】')
        lines+=['',f"{g['representative']}：{g['count']} 件；{g['material_surface_variants']} 個材質／UV／法線版本；{LABELS.get(g['basis'],g['basis'])}",
                f"每件約 {g['triangles_per_object_estimate']} 三角形；重複部分約 {g['repeated_triangles_estimate']}（非實測節省量）"]
        for v in g['variants']:
            lines.append(f"  版本 {v['id'][:8]}：{v['count']} 件；"+('可列入SDK實例試驗' if v['candidate_for_sdk_trial'] else '保持獨立／待檢查'))
            if v['review_reasons']:lines.append('  待檢查：'+', '.join(v['review_reasons']))
            lines.append('  成員：'+'、'.join(m['name'] for m in v['members']))
            if v['omitted_members']:lines.append('  另省略 '+str(v['omitted_members'])+' 個成員。')
    lines+=['','辨識耗時：'+str(round(summary.get('seconds',0),3))+' 秒。',
            '重複座標、退化面或大量網格會採較保守的索引比對；頂點重新編號可能因此漏判。',
            '局部座標中的平移可正規化；已烘焙進頂點的旋轉／縮放不自動判為同原型。',
            'D5中獨立修改的外觀不能僅由此報告完整得知，實例化前仍需驗證。']
    return '\n'.join(lines)+'\n'
