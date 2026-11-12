"""v2.0.2 section-size detection regression, independent of Qt/IngeTrazo."""
import ast
import math
from pathlib import Path
source=Path(__file__).with_name('__init__.py').read_text(encoding='utf-8')
tree=ast.parse(source)
functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('clean_polygon','section_dimensions_mm')]
ns={'math':math}
exec(compile(ast.Module(body=functions,type_ignores=[]), '<section-size>', 'exec'),ns)
dim=ns['section_dimensions_mm']
assert dim([[0,0],[38,0],[38,12],[0,12]]) == (38,12)
assert dim([[19,-8],[71,-8],[71,16],[19,16]]) == (52,24)
assert dim([[0,0],[0,9],[-21,9],[-21,0]]) == (21,9)
for p in ([[0,0],[2,0],[4,0]], [[0,0],[0,5],[0,10]]):
    try: dim(p)
    except ValueError: pass
    else: raise AssertionError('Degenerate section accepted')
cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ProfilePanel')
method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='on_profile_changed')
exec(compile(ast.Module(body=[method],type_ignores=[]), '<profile-changed>', 'exec'),ns)
class Spin:
    def __init__(self): self.v=17
    def setValue(self, n): self.v=n
class Text:
    def setText(self,v):self.v=v
class Preview:
    def set_points(self,v):self.points=v
class Panel:
    def __init__(self):
        self.profiles={'custom':[[20,10],[63,10],[63,28],[20,28]]}
        self.preview=Preview();self.width=Spin();self.height=Spin();self._dim_update=False
        self.profile_name=Text()
p=Panel()
ns['on_profile_changed'](p,'custom')
assert (p.width.v,p.height.v)==(43,18)
assert p.profile_name.v=='custom' and p._dim_update is False
# Original profile geometry must remain unchanged by dimension detection.
assert p.profiles['custom']==[[20,10],[63,10],[63,28],[20,28]]
print('PASS v2.0.2: bounds, offset origin, reversed coordinate, degenerate sections, UI pair update')
