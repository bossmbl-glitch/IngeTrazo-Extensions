# SPDX-License-Identifier: GPL-3.0-or-later
"""Conservative classifier: only pure placement edits use the D5 fast path.

Any object/mesh/material/collection/visibility/name change retains the original
full send. State is committed only after a successful receipt.
"""
def plan(previous, current):
    if previous is None or previous.keys() != current.keys():
        return 'full', []
    changed=[];visible=[]
    for key, value in current.items():
        if value[0] != previous[key][0]:
            a=value[0];b=previous[key][0]
            if a[:5]+a[7:]!=b[:5]+b[7:] or value[1]!=previous[key][1]:return 'full', []
            visible.append(key)
        if value[1] != previous[key][1]:
            changed.append(key)
    if visible:return ('visibility',visible) if not changed else ('full',[])
    return ('transforms', changed) if changed else ('unchanged', [])


def capture(objects):
    state={}
    for obj in objects:
        if obj.type not in {'MESH','EMPTY'}:
            continue
        data=obj.data
        materials=tuple((m.as_pointer(),m.get('bridge_material_signature',''))
                        if m else None for m in data.materials) if obj.type=='MESH' else ()
        collections=tuple(sorted((c.as_pointer(),c.name,c.hide_viewport,c.hide_render)
                                  for c in obj.users_collection))
        invariant=(obj.name,obj.type,data.as_pointer() if data else None,
                   obj.parent.as_pointer() if obj.parent else None,
                   obj.hide_viewport,obj.hide_render,materials,collections)
        matrix=tuple(float(x) for row in obj.matrix_world for x in row)
        state[obj.as_pointer()]=(invariant,matrix)
    return state
