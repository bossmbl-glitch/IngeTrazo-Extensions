# SPDX-License-Identifier: GPL-3.0-or-later
"""Worker-only cross-revision cache. Source geometry is never simplified.

Cache entries are content-addressed; prototype indexes alone are not identities.
Only live Blender datablocks survive a completed import. The normal importer
still handles object IDs, UVs, visibility, hierarchy and deletion.
"""
import json
import hashlib
import pickle
import time
import bpy
from mathutils import Matrix
from ingetrazo_io import igz
from ingetrazo_io.build import Builder, ORIGIN_PROP


def digest(value):
    return hashlib.sha256(pickle.dumps(value, protocol=4)).hexdigest()


def alive(block):
    try:
        return block is not None and bool(block.name)
    except ReferenceError:
        return False


class IncrementalImporter:
    def __init__(self):
        self.geometry_cache = {}
        self.meshes = {}
        self.materials = {};self.signature_duplicates=set()
        self.images = {}
        self.named_materials={};self.named_duplicates=set()
        for material in bpy.data.materials:
            target=material.get('bridge_named_target')
            if target:
                if target in self.named_materials:self.named_duplicates.add(target)
                self.named_materials[target]=material
        self.persisted_images = {image.get('bridge_image_key'): image for image in bpy.data.images
                                 if image.get('bridge_image_key')}
        self.persisted_meshes = {}
        for me in bpy.data.meshes:
            key = me.get('bridge_mesh_key')
            if key:
                try:
                    attrs = json.loads(me.get('bridge_mesh_material_attrs', '[]'))
                    self.persisted_meshes[key] = (me, attrs)
                except (TypeError, ValueError):
                    pass
        for mat in bpy.data.materials:
            sig = mat.get('bridge_material_signature')
            if sig:
                if sig in self.materials:self.signature_duplicates.add(sig)
                self.materials[sig] = mat

    def load(self, context, path):
        started = time.perf_counter()
        next_geometry_cache = {}
        # Read-through of the previous revision only: bound memory to the
        # previous/current document, not every geometry edit ever made.
        previous_geometry_cache = self.geometry_cache
        class RevisionCache(dict):
            def __contains__(self, key):
                return dict.__contains__(self, key) or key in previous_geometry_cache
            def __getitem__(self, key):
                if not dict.__contains__(self, key):
                    self[key] = previous_geometry_cache[key]
                return dict.__getitem__(self, key)
        next_geometry_cache = RevisionCache()
        doc = igz.Document(path, geometry_cache=next_geometry_cache)
        parsed = time.perf_counter()
        try:
            builder = CachedBuilder(context, doc, self)
            builder.build()
            from visibility import apply_visibility
            builder.stats["visibility_objects"]=apply_visibility(bpy.data,doc.scene,builder.doc_path)
            from material_identity import retain_material_identities
            retained=retain_material_identities(bpy.data.materials,builder.doc_path)
            builder.stats['identity_materials_retained']=retained
            # The base builder removes obsolete owned meshes. Drop their
            # references and never retain meshes after all their users vanish.
            self.meshes = {k: v for k, v in self.meshes.items()
                           if alive(v[0]) and v[0].users > 0}
            # Drop only this document's zero-user generated scope materials.
            # Live, frozen, fake-user and asset materials remain untouched.
            removed=0
            for material in list(bpy.data.materials):
                if (material.name.startswith('_scope_') and material.users==0
                        and material.get('ingetrazo_doc')==builder.doc_path
                        and not material.get('bridge_identity_retained')
                        and not getattr(material,'asset_data',None)
                        and not getattr(material,'use_fake_user',False)):
                    bpy.data.materials.remove(material);removed+=1
            builder.stats['orphan_scope_materials_removed']=removed
            self.materials = {k: v for k, v in self.materials.items() if alive(v)}
            self.images = {k: v for k, v in self.images.items() if alive(v)}
            self.geometry_cache = dict(next_geometry_cache)
            builder.stats.update(doc.geometry_stats)
            builder.stats.update(parse_seconds=parsed-started,
                                 build_seconds=time.perf_counter()-parsed)
            return builder.stats
        finally:
            doc.close()


class CachedBuilder(Builder):
    def __init__(self, context, doc, cache):
        super().__init__(context, doc, update_materials=True)
        self.cache = cache
        self.local_digests = {}
        self.mesh_context = ("loose",)
        self.counted_meshes = set()
        self.base_contexts = {}
        self.texture_signatures = {}
        self.recording = None
        self.reused = set()
        self.stats.update(meshes_built=0, meshes_reused=0)

    def texture_signature(self, tex):
        if not isinstance(tex, dict):
            return None
        key = (tex.get('embed'), tex.get('path'))
        if key not in self.texture_signatures:
            got = self.doc.image_bytes(tex)
            self.texture_signatures[key] = hashlib.sha256(got[1]).hexdigest() if got else None
        return self.texture_signatures[key]

    def image(self, tex):
        signature = self.texture_signature(tex)
        key = (tex.get('embed'), tex.get('path'), signature)
        cached = self.cache.images.get(key) or self.cache.persisted_images.get(digest(key))
        if alive(cached):
            return cached
        image = super().image(tex)
        if image is not None:
            image["bridge_image_key"] = digest(key)
            self.cache.images[key] = image
        return image

    def material(self, attrs):
        # Material identity excludes face vertices and UV placement. Those
        # remain in the mesh fingerprint, so UV edits still rebuild geometry.
        named=attrs.get('_bridge_named_target')
        if named and named in getattr(self.cache,'named_duplicates',set()):raise ValueError('同名材質有多個已保存識別，已停止以保護 D5 關聯')
        existing=self.cache.named_materials.get(named) if named else None
        if alive(existing) and existing.get('ingetrazo_doc')!=self.doc_path:raise ValueError('名稱材質不屬於目前橋接文件')
        if alive(existing):
            self.materials[('bridge_named',named)]=existing
            if self.recording is not None:self.recording[existing.get('bridge_material_signature','named:'+named)]={k:attrs[k] for k in ('color','opacity','mat','texture','_bridge_named_target') if k in attrs}
            return existing
        visual = {k: attrs[k] for k in ('color', 'opacity', 'mat') if k in attrs}
        tex = attrs.get('texture')
        if isinstance(tex, dict):
            visual['texture'] = {k: tex[k] for k in ('embed', 'path') if k in tex}
        sig = digest((visual, self.registry.get(attrs.get('mat')),
                      self.texture_signature(tex)))
        if self.recording is not None:
            self.recording[sig] = visual
        if named and sig in getattr(self.cache,'signature_duplicates',set()):raise ValueError('舊材質外觀相同但有多個已保存身分，已停止以避免合併')
        mat = self.cache.materials.get(sig)
        if named and alive(mat) and mat.get('ingetrazo_doc')!=self.doc_path:raise ValueError('舊名稱材質不屬於目前文件')
        if not alive(mat) or mat.get('bridge_material_signature') != sig:
            mat = super().material(attrs)
            mat['bridge_material_signature'] = sig
            self.cache.materials[sig] = mat
        if named:
            mat['bridge_named_target']=named;self.cache.named_materials[named]=mat
        self.materials[('bridge_cache', sig)] = mat
        return mat

    def mesh(self, mjson, container, name, src=None):
        local = (id(mjson), digest(container))
        if local not in self.local_digests:
            binary_signature = mjson.get('_bridge_geometry_digest')
            if binary_signature:
                self.local_digests[local] = digest(('binary-v1', binary_signature, container))
            else:
                self.local_digests[local] = digest((mjson.get('faces', []), mjson.get('edges', []), container))
        key = (src, self.local_digests[local], self.mesh_context)
        cached = self.cache.meshes.get(key) or self.cache.persisted_meshes.get(digest(key))
        if cached and alive(cached[0]):
            me, materials = cached
            for attrs in materials:
                self.material(attrs)
            self.reused.add(me.as_pointer())
            # Keep the base importer's per-call prototype-sharing behavior.
            ckey = repr(sorted((k, repr(v)) for k, v in container.items())) if container else None
            self.meshes[(id(mjson), ckey)] = me
            if me.as_pointer() not in self.counted_meshes:
                self.stats['faces'] += len(me.polygons)
                self.counted_meshes.add(me.as_pointer())
            self.stats['meshes_reused'] += 1
            return me
        # If this mesh has already been requested within this revision, the
        # base importer can return it without creating a second datablock.
        previous = self.recording
        self.recording = {}
        try:
            ckey = repr(sorted((k, repr(v)) for k, v in container.items())) if container else None
            base_key = (id(mjson), ckey)
            if self.base_contexts.get(base_key, self.mesh_context) != self.mesh_context:
                self.meshes.pop(base_key, None)
            self.base_contexts[base_key] = self.mesh_context
            me = super().mesh(mjson, container, name, src)
            self.counted_meshes.add(me.as_pointer())
            attrs = list(self.recording.values())
        finally:
            self.recording = previous
        me['bridge_mesh_key'] = digest(key)
        me['bridge_mesh_material_attrs'] = json.dumps(attrs, ensure_ascii=False)
        self.cache.meshes[key] = (me, attrs)
        self.stats['meshes_built'] += 1
        return me

    def key_for(self, g, path_key):
        pinned = g.get('_bridge_scope_key')
        return pinned if isinstance(pinned, str) and pinned else super().key_for(g, path_key)

    def group(self, g, *args, **kwargs):
        previous = self.mesh_context
        self.mesh_context = (g.get('xform') is not None, g.get('billboard'))
        try:
            return super().group(g, *args, **kwargs)
        finally:
            self.mesh_context = previous

    def recentre(self, me, turn=False):
        # A reused classic group's mesh is already recentered. Restore its
        # original placement matrix instead of transforming its vertices again.
        if me is not None and me.as_pointer() in self.reused and ORIGIN_PROP in me:
            flat = me[ORIGIN_PROP]
            return Matrix([flat[i:i+4] for i in range(0, 16, 4)])
        return super().recentre(me, turn)
