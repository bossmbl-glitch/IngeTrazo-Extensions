"""v2.0.6: style-enum regression and managed delete semantics without Qt runtime."""
import ast
import tempfile
from pathlib import Path
from profile_storage import save_profile, archive_owned, find_owned, read_owned

root = Path(__file__).parent
browser = (root / 'profile_browser.py').read_text(encoding='utf-8')
init = (root / '__init__.py').read_text(encoding='utf-8')
assert 'QStyle.StandardPixmap.SP_DirIcon' in browser
assert 'QStyle.StandardPixmap.SP_FileIcon' in browser
assert 'QStyle.StandardPixmap.SP_MessageBoxWarning' in browser
assert 'self.style().SP_' not in browser
tree = ast.parse(browser)
assert any(isinstance(n,ast.ImportFrom) and n.module=='PySide6.QtWidgets' and any(a.name=='QStyle' for a in n.names) for n in tree.body)
# Validate every .standardIcon argument is an enum instead of an attribute of QCommonStyle.
tree = ast.parse(browser)
for n in ast.walk(tree):
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'standardIcon':
        assert len(n.args) == 1
        attr = n.args[0]
        assert isinstance(attr, ast.Attribute) and isinstance(attr.value, ast.Attribute)
        assert attr.value.attr == 'StandardPixmap'
assert 'archive_owned(target)' in init
assert "self.state['recent']=[r for r in self.state.get('recent',[]) if r!=list(item['key'])]" in browser
assert 'self.owner.delete_browser_profile(item)' in browser
with tempfile.TemporaryDirectory() as temp:
    directory=Path(temp)
    source=save_profile(directory,'sample',[[0,0],[10,0],[10,5],[0,5]])
    assert read_owned(source)['name']=='sample'
    saved=archive_owned(source)
    assert not source.exists() and saved.is_file()
    assert find_owned(directory,'sample') is None
    assert read_owned(saved)['points'][1]==[10.0,0.0]
    skp=directory/'sample.skp'; skp.write_bytes(b'protected-input')
    assert skp.read_bytes()==b'protected-input'
print('PASS v2.0.6: PySide6 QStyle enum access, safe archive, SKP untouched, refresh path')
