"""Static API contract and pure math tests; no IngeTrazo runtime required."""
import ast
from pathlib import Path
src=Path(__file__).with_name('__init__.py').read_text(encoding='utf-8')
tree=ast.parse(src)
classes={c.name:c for c in tree.body if isinstance(c,ast.ClassDef)}
tool=classes['ProfileDrawTool']
methods={m.name:m for m in tool.body if isinstance(m,ast.FunctionDef)}
assert {'on_click','on_hover','on_key','on_cancel','rubber_band_lines','preview_faces','drag_plane','finish'}.issubset(methods)
assert 'int(Qt.Key_Space)' in src
assert 'Group(mesh, name)' in src
assert 'scene.groups.append(group)' in src
assert 'self._preview_mesh = mesh' in src
assert "group.ext = {'quick_profile'" in src
assert 'mesh=scene.mesh\n                materials' not in src
assert 'scene.mesh.add_face(' not in src
# Ensure no group writes in preview-only functions.
method=methods['_refresh_preview']
block=ast.get_source_segment(src,method)
assert 'scene.groups.append' not in block
assert 'history.execute' not in block
print('PASS: v0.8 structure and viewport tool API invariants')
