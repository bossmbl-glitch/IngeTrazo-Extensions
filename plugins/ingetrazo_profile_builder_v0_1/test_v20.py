"""Static regression for v2.0 pipette cursor without PySide6."""
import ast
from pathlib import Path
code=Path(__file__).with_name('__init__.py').read_text(encoding='utf-8')
tree=ast.parse(code)
needle=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_pipette_cursor')
assert 'QPixmap(48, 48)' in ast.get_source_segment(code, needle)
assert 'QCursor(pixmap, int(tip.x()), int(tip.y()))' in ast.get_source_segment(code, needle)
tool=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ProfilePipetteTool')
parts={node.name:ast.get_source_segment(code,node) for node in tool.body if isinstance(node,ast.FunctionDef)}
assert 'self._paint_cursor(viewport, False)' in parts['on_activate']
assert 'viewport.unsetCursor()' in parts['on_deactivate']
assert 'self._paint_cursor(viewport, valid)' in parts['on_hover']
assert 'viewport.set_active_tool(SelectTool())' in parts['_exit']
assert 'viewport.pick_group' in parts['on_hover']
assert 'viewport.pick_group' in parts['on_click']
print('v2.0 cursor regression passed')
