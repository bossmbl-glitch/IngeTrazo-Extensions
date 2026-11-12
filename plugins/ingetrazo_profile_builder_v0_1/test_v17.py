"""Static regression: four native vector buttons; unrelated geometry functions unchanged."""
import ast
from pathlib import Path
from zipfile import ZipFile
root = Path(__file__).parent
source = (root/'__init__.py').read_text(encoding='utf-8')
with ZipFile('/mnt/data/ingetrazo_quick_profile_v1_6_skp_profiles.zip') as z:
    old = z.read('ingetrazo_profile_builder_v0_1/__init__.py').decode('utf-8')
a = ast.parse(old)
b = ast.parse(source)
functions = lambda tree: {n.name: ast.dump(n,include_attributes=False) for n in tree.body if isinstance(n,ast.FunctionDef)}
af,bf=functions(a),functions(b)
for name in ('mesh_ready_faces','edge_display_rules','apply_edge_display','ordered_paths'):
    assert af[name] == bf[name], name
assert 'def make_corner_icon(' in source
assert 'def corner_tool(' in source
assert "self.corner_tool('miter'" in source
assert "self.corner_tool('flat'" in source
assert "self.corner_tool('continuous'" in source
assert "self.corner_tool('separate'" in source
assert "self.corner_continuous.setEnabled(False)" in source
assert "self.corner_separate.setEnabled(False)" in source
assert "self.corner_miter.setChecked(miter)" in source
assert "self.corner_flat.setChecked(not miter)" in source
assert 'setFixedSize(36, 36)' in source and 'setIconSize(QSize(28, 28))' in source
assert 'QToolButton:checked' in source
print('PASS v1.7 four vector corner icons; functional modes preserved; geometry unchanged')
