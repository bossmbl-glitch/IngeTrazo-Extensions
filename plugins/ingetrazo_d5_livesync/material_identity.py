# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep bridge-owned material datablocks and SDK properties through exclusion.

Blender does not save zero-user datablocks without a fake user. Keeping only
names in an IGZ ledger cannot preserve the SDK properties on those datablocks.
"""
def retain_material_identities(materials,document):
    retained=0
    for material in materials:
        if material.get('ingetrazo_doc')!=document:continue
        if not (material.get('bridge_material_signature') or material.name.startswith('_scope_')):continue
        material.use_fake_user=True
        material['bridge_identity_retained']=True
        retained+=1
    return retained
