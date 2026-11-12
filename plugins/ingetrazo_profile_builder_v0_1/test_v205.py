"""Regression checks for delete import and actual aspect-ratio locking."""
import ast
import math
from pathlib import Path
source = Path(__file__).with_name('__init__.py').read_text(encoding='utf-8')
root = ast.parse(source)
imports = [a.name for node in root.body if isinstance(node, ast.ImportFrom) and node.module == 'profile_storage' for a in node.names]
assert 'read_owned' in imports, 'Deletion references read_owned, but it was not imported'
cls = next(n for n in root.body if isinstance(n, ast.ClassDef) and n.name == 'ProfilePanel')
methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
assert 'read_owned(' in ast.get_source_segment(source, methods['delete_browser_profile'])
ns = {'math':math, 'EPS':1e-8}
for key in ('on_dimension_link_toggled', 'sync_dimension'):
    exec(compile(ast.fix_missing_locations(ast.Module(body=[methods[key]], type_ignores=[])), '<test>', 'exec'), ns)
class FakeCheck:
    def __init__(self): self.checked=False
    def isChecked(self): return self.checked
class FakeSpin:
    def __init__(self,n): self.n=float(n)
    def value(self): return self.n
    def setValue(self,v): self.n=round(float(v),2)
class Panel:
    _dim_update=False
    _locked_aspect_ratio=1.0
    def __init__(self,w,h):
        self.width=FakeSpin(w); self.height=FakeSpin(h); self.link_dimensions=FakeCheck()
p=Panel(449.64,554.01)
# Enabling ratio lock must never alter the two displayed dimensions.
p.link_dimensions.checked=True
ns['on_dimension_link_toggled'](p,True)
assert (p.width.value(),p.height.value())==(449.64,554.01)
ns['sync_dimension'](p,'w',500)
assert abs(p.height.value()-500*554.01/449.64)<0.011
p.width.setValue(500)
ns['sync_dimension'](p,'h',600)
assert abs(p.width.value()-600*449.64/554.01)<0.011
# Disabling link means user can edit the other field independently.
p.link_dimensions.checked=False
prev=p.height.value()
ns['sync_dimension'](p,'w',300)
assert p.height.value()==prev
# Re-enable captures the *current* ratio, not 1:1.
p.width.setValue(40.22);p.height.setValue(21.84)
p.link_dimensions.checked=True
ns['on_dimension_link_toggled'](p,True)
ns['sync_dimension'](p,'w',60)
assert abs(p.height.value()-60*21.84/40.22)<0.011
print('PASS v2.0.5: deletion import, ratio lock, width/height changes, unlock and relock')
