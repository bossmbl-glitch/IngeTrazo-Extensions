"""Regression checks for browser not opening, SKP lazy parsing, and error reporting."""
import ast
from pathlib import Path
text=(Path(__file__).parent/'profile_browser.py').read_text(encoding='utf-8')
code=(Path(__file__).parent/'__init__.py').read_text(encoding='utf-8')
tree=ast.parse(text)
cls=next(c for c in tree.body if isinstance(c,ast.ClassDef) and c.name=='ProfileBrowser')
methods={m.name:ast.get_source_segment(text,m) for m in cls.body if isinstance(m,ast.FunctionDef)}
assert 'self.reload_items()' not in methods['__init__']
assert 'QTimer.singleShot(120, self._safe_reload)' in methods['showEvent']
assert 'self._skp_timer.start(' not in methods['reload_items']
assert 'self._load_skp_item(item)' in methods['apply_selection']
assert 'self._cache[key]=record' in methods['reload_items']
assert 'QMessageBox.warning' in methods['_safe_reload']
assert 'browser.show()' in code
assert 'ingetrazo_quick_profile_browser_error.log' in code
print('PASS v1.8.1 startup, no eager SKP parse, and visible errors')
