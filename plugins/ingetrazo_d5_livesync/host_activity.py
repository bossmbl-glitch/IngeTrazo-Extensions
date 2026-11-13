# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only guards for native 0.6.0 interactive tool previews."""
def preview_active(viewport):
    tool=getattr(viewport,'active_tool',None)
    if tool is None:return False
    # The host's own tool-in-progress contract (origin vectors may be falsy).
    if any(getattr(tool,key,None) is not None for key in ('start_point','grab','_grip')):return True
    return any(bool(getattr(tool,key,None)) for key in ('dragging','_drag','_stroke','nodes'))

