# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only raw-mesh inventory, not an SDK export/deletion classifier."""
from array import array
from collections import Counter
import time


def pointer(block):
    try:return str(block.as_pointer())
    except AttributeError:return str(id(block))


def text(value):return str(value or '')[:200]


def audit_materials(bpy,max_materials=3000,max_samples=8):
    started=time.perf_counter();materials=list(bpy.data.materials)
    records={pointer(m):{'session_key':pointer(m),'name':text(m.name),'users':int(getattr(m,'users',0)),
             'fake_user':bool(getattr(m,'use_fake_user',False)),'asset':bool(getattr(m,'asset_data',None)),
             'linked':bool(getattr(m,'library',None)),'document':text(m.get('ingetrazo_doc','')),
             'bridge_material_id':text(m.get('bridge_material_id','')),'slot_objects':0,
             'polygon_objects':0,'empty_mesh_slot_objects':0,'modified_slot_objects':0,
             'objects':[]} for m in materials}
    indices_cache={};objects=list(getattr(bpy.data,'objects',getattr(bpy.context.view_layer,'objects',[])))
    summary=Counter(mesh_objects=0,mesh_objects_without_polygons=0,mesh_objects_with_modifiers=0)
    for obj in objects:
        if getattr(obj,'type',None)!='MESH' or getattr(obj,'data',None) is None:continue
        summary['mesh_objects']+=1;mesh=obj.data;polygons=getattr(mesh,'polygons',[])
        if not polygons:summary['mesh_objects_without_polygons']+=1
        modifiers=bool(getattr(obj,'modifiers',[]))
        if modifiers:summary['mesh_objects_with_modifiers']+=1
        key=pointer(mesh)
        if key not in indices_cache:
            if polygons and hasattr(polygons,'foreach_get'):
                values=array('i',[0])*len(polygons);polygons.foreach_get('material_index',values);used=set(values)
            else:used={getattr(p,'material_index',0) for p in polygons}
            indices_cache[key]=used
        used=indices_cache[key]
        slots=getattr(obj,'material_slots',None)
        slots=[getattr(s,'material',None) for s in slots] if slots is not None else list(getattr(mesh,'materials',[]))
        # Multiple slots can reference one material; object counts remain unique.
        refs={}
        for index,material in enumerate(slots):
            if material is not None:
                k=pointer(material);refs[k]=refs.get(k,False) or index in used
        for k,face_used in refs.items():
            row=records.get(k)
            if row is None:continue
            row['slot_objects']+=1;row['polygon_objects']+=int(face_used)
            row['empty_mesh_slot_objects']+=int(not polygons);row['modified_slot_objects']+=int(modifiers)
            if len(row['objects'])<max_samples:row['objects'].append(text(obj.name))
    categories=Counter()
    for row in records.values():
        if row['polygon_objects']:category='raw_polygon_reference'
        elif row['slot_objects'] and row['empty_mesh_slot_objects']==row['slot_objects']:category='slots_only_on_meshes_without_polygons'
        elif row['slot_objects']:category='slots_not_referenced_by_raw_polygons'
        elif row['users']==0:category='zero_users'
        else:category='other_users_or_dependencies'
        row['category']=category;categories[category]+=1
    rows=sorted(records.values(),key=lambda r:(r['category']=='raw_polygon_reference',r['name']))
    return {'summary':{'total_materials':len(materials),'polygon_referenced_materials':categories['raw_polygon_reference'],
                       'categories':dict(categories),**dict(summary)},
            'materials':rows[:max_materials],'omitted_material_records':max(0,len(rows)-max_materials),
            'seconds':time.perf_counter()-started,
            'basis':'Raw polygons and material slots across bpy.data.objects; modifiers/instances and SDK dependencies may differ. No deletion or export filtering.'}


def compare_audits(before,after):
    keys={r['session_key'] for r in before.get('materials',[])}
    created=[r['name'] for r in after.get('materials',[]) if r['session_key'] not in keys]
    return {'new_material_names':created,'new_materials_observed':len(created),
            'comparison_truncated':bool(before.get('omitted_material_records') or after.get('omitted_material_records'))}
