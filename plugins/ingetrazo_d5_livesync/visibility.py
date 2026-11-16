# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-owner visibility: freeze display without renaming layers or materials."""
import copy

VERSION_KEY='_bridge_visibility_version'
VISIBLE='_bridge_visible_v1'
LOOSE='_bridge_loose_visible_v1'


def layer_visibility(scene):
    # Native IGZ omits default visible layer definitions. Absence means visible.
    return {row['name']:row.get('visible',True) is not False
            for row in scene.get('layers',[]) or [] if isinstance(row,dict) and isinstance(row.get('name'),str)}


def stamp_groups(groups,scene,preserve=False):
    groups=copy.deepcopy(groups);layers=layer_visibility(scene)
    frozen=preserve and scene.get(VERSION_KEY)==1
    def walk(nodes,parent=True):
        for group in nodes:
            if frozen:
                visible=group.get(VISIBLE)
                if type(visible) is not bool:raise ValueError('保留群組的確認可見性遺失，已停止以保護 D5 舊內容')
            else:visible=parent and not bool(group.get('hidden')) and layers.get(group.get('layer') or 'Layer 0',True)
            group[VISIBLE]=visible
            walk(group.get('children',[]) or [],visible)
    walk(groups);return groups


def loose_visibility(scene,preserve=False):
    if preserve and scene.get(VERSION_KEY)==1:
        visible=scene.get(LOOSE)
        if type(visible) is not bool:raise ValueError('保留散面可見性遺失，已停止同步')
        return visible
    return layer_visibility(scene).get('Layer 0',True)


def apply_visibility(data,scene,document):
    """After import, validate all owned groups before changing display flags.

    Do not recreate objects, change collection memberships, materials or IDs.
    Legacy documents are upgraded by full compose before this becomes required.
    """
    if scene.get(VERSION_KEY)!=1:return 0
    expected={}
    def walk(groups):
        for group in groups:
            key=group.get('_bridge_scope_key');visible=group.get(VISIBLE)
            if not isinstance(key,str) or not key or key in expected or type(visible) is not bool:
                raise ValueError('群組可見性識別無效，已停止同步')
            expected[key]=visible;walk(group.get('children',[]) or [])
    walk(scene.get('groups',[]) or [])
    loose=scene.get(LOOSE)
    if type(loose) is not bool:raise ValueError('散面可見性識別無效')
    objects={};duplicates=set()
    for obj in data.objects:
        if obj.get('ingetrazo_doc')!=document:continue
        key=obj.get('ingetrazo_key')
        if key in objects:duplicates.add(key)
        objects[key]=obj
    if any(key not in objects or key in duplicates for key in expected):
        raise ValueError('找不到唯一的保留群組，已停止以避免錯誤隱藏或材質關聯改變')
    # A single tag collection may contain held and live roots/descendants.
    # Their flags must be per object; a hidden shared collection overrides both.
    for collection in data.collections:
        if collection.get('ingetrazo_doc')==document and collection.get('ingetrazo_tag') is not None:
            collection.hide_viewport=False;collection.hide_render=False
    count=0
    for key,visible in expected.items():
        obj=objects[key];obj.hide_viewport=not visible;obj.hide_render=not visible;count+=1
    if 'loose' in objects:
        obj=objects['loose'];obj.hide_viewport=not loose;obj.hide_render=not loose;count+=1
    return count
