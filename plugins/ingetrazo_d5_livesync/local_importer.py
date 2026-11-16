# SPDX-License-Identifier: GPL-3.0-or-later
"""Build selected complete roots; never run whole-document deletion cleanup."""
import copy,json,time,zipfile
from pathlib import Path
import bpy
from incremental import CachedBuilder,alive
from ingetrazo_io import igz
from motion_delta import DeltaRejected
from local_update import tree

def load_local(importer,context,path,keys,document=None):
    started=time.perf_counter();path=Path(path)
    archive=zipfile.ZipFile(path)
    try:
        document=document if document is not None else json.loads(archive.read('document.json'))
        source=document['scene'];groups=source.get('groups',[])
        selected=[i for i,g in enumerate(groups) if g.get('_bridge_scope_key') in keys]
        if len(selected)!=len(keys):raise DeltaRejected('局部匯入根群組不完整')
        if not selected:return {'partial_import':True,'partial_roots':0,'objects_touched':0,'meshes_built':0,'blocks_decoded':0,'blocks_reused':0}
        # Expand only geometry reachable from the selected roots. Retain their
        # full-document indexes for classic mesh keys and later full reloads.
        scene=copy.copy(source);scene['groups']=list(groups);scene['protos']=list(source.get('protos',[]))
        needed=set();nodes=[]
        for i in selected:
            scene['groups'][i]=copy.deepcopy(groups[i]);nodes.extend(tree(scene['groups'][i]))
        for g in nodes:
            if 'proto' in g:needed.add(int(g['proto']))
        for i in needed:
            if not 0<=i<len(scene['protos']):raise DeltaRejected('局部剖面索引無效')
            scene['protos'][i]=copy.deepcopy(scene['protos'][i])
        doc=igz.Document.__new__(igz.Document);doc.path=path;doc._archive=archive
        doc.scene=scene;doc.format=document.get('igz_format',1);doc.app_version=document.get('app_version','')
        doc.geometry_stats={'blocks_decoded':0,'blocks_reused':0}
        expand={'groups':[scene['groups'][i] for i in selected],'protos':[scene['protos'][i] for i in sorted(needed)]}
        next_cache=dict(importer.geometry_cache)
        igz._expand_geometry(expand,doc._read_member,next_cache,doc.geometry_stats)
        parsed=time.perf_counter()
        affected={g.get('_bridge_scope_key') for g in nodes}
        if None in affected or len(affected)!=len(nodes):raise DeltaRejected('局部匯入識別重複')
        owned={};duplicates=set()
        for obj in bpy.data.objects:
            if obj.get('ingetrazo_doc')==str(path.resolve()):
                k=obj.get('ingetrazo_key')
                if k in owned:duplicates.add(k)
                owned[k]=obj
        for g in nodes:
            k=g['_bridge_scope_key'];obj=owned.get(k)
            if obj is None or k in duplicates or obj.constraints or obj.modifiers or (obj.type=='MESH' and obj.data.shape_keys):raise DeltaRejected('背景群組需完整重建')
            mesh=scene['protos'][int(g['proto'])] if 'proto' in g else g
            if bool(mesh.get('faces') or mesh.get('edges'))!=(obj.data is not None):raise DeltaRejected('群組幾何類型已變更，需完整同步')
        builder=CachedBuilder(context,doc,importer);root=builder.root_collection()
        builder.old=builder.existing_objects();builder.old={k:v for k,v in builder.old.items() if k in affected}
        builder.old_by_name={k:v for k,v in builder.old_by_name.items() if v.get('ingetrazo_key') in affected}
        for i,g,nk in builder._named(scene['groups'],'g'):
            if i in selected:builder.group(g,builder.key_for(g,f'g/{i}'),root,name_key=nk,ipath=(i,))
        if builder.old:raise DeltaRejected('局部匯入未能對應所有原群組')
        ee=getattr(context.scene,'eevee',None)
        if ee is not None and hasattr(ee,'use_raytracing') and any(m.get('ingetrazo_finish')=='glass' for m in builder.materials.values()):ee.use_raytracing=True
        # No cameras, lights, ambience, visibility or unrelated object cleanup.
        importer.geometry_cache=next_cache
        importer.meshes={k:v for k,v in importer.meshes.items() if alive(v[0]) and v[0].users>0}
        importer.materials={k:v for k,v in importer.materials.items() if alive(v)}
        from material_identity import retain_material_identities
        builder.stats['identity_materials_retained']=retain_material_identities(bpy.data.materials,builder.doc_path)
        from visibility import apply_visibility
        builder.stats['visibility_objects']=apply_visibility(bpy.data,source,builder.doc_path)
        stats=builder.stats;stats['objects_touched']=stats.pop('objects');stats['faces_processed']=stats.pop('faces')
        stats.update(doc.geometry_stats);stats.update(partial_import=True,partial_roots=len(selected),
                    parse_seconds=parsed-started,build_seconds=time.perf_counter()-parsed)
        return stats
    finally:archive.close()
