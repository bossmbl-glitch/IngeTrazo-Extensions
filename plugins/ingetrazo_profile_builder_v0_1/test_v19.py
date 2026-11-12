"""v1.9 pipette regression without requiring IngeTrazo/PySide6 on CI."""
import ast
import math
from pathlib import Path
from types import SimpleNamespace

source = Path(__file__).with_name('__init__.py').read_text(encoding='utf-8')
tree=ast.parse(source)
cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ProfilePanel')
method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='apply_picked_group')
code=compile(ast.Module(body=[method],type_ignores=[]),'<pipette>', 'exec')
ns={'math': math, 'PLUGIN_KEY': 'quick_profile', 'clean_polygon': lambda v: [list(p) for p in v]}
exec(code,ns)
apply=ns['apply_picked_group']

class Spin:
    def __init__(self):self.v=None
    def setValue(self,v):self.v=v
class Check:
    def __init__(self):self.v=None
    def setChecked(self,v):self.v=v
class Combo:
    def __init__(self,items):self.items=items;self.v=items[0]
    def findText(self,v):return self.items.index(v) if v in self.items else -1
    def setCurrentText(self,v):self.v=v
class Panel:
    def __init__(self):
        self.profiles={'058':[[0,0],[17,0],[17,17]]}
        self.width=Spin();self.height=Spin();self.rotation=Spin();self.offsetu=Spin();self.offsetv=Spin()
        self.mirror=Check();self.reverse_depth=Check();self.corner=None
        self.anchor=Combo(['左下','左上']);self.closed_side=Combo(['內側','外側'])
        self.material=Combo(['預設材質','Wood']);self.tag=Combo(['預設標籤','Layer 1'])
        self.selected=None
    def _refresh_profiles(self,name):self.selected=name
    def refresh_scene_options(self):pass
    def set_corner(self,v):self.corner=v

p=Panel()
old=SimpleNamespace(name='輪廓_058_001',layer='Layer 1',ext={'quick_profile': {
    'profile':'058', 'settings':{'width_mm':33,'height_mm':19,'rotate_deg':180,
    'off_u_mm':3,'off_v_mm':-5,'anchor':'左上','mirror':True,'miter':False,
    'reverse_depth':False,'closed_side':'外側'}, 'tag':'Layer 1','material':'Wood'}})
original=repr(old.ext)
ok,msg=apply(p,old)
assert ok,msg
assert p.selected=='058' and p.width.v==33 and p.height.v==19
assert p.rotation.v==180 and p.offsetu.v==3 and p.offsetv.v==-5
assert p.mirror.v is True and p.reverse_depth.v is False and p.corner is False
assert p.anchor.v=='左上' and p.closed_side.v=='外側'
assert p.material.v=='Wood' and p.tag.v=='Layer 1'
assert repr(old.ext)==original,'must never mutate picked group'

p2=Panel()
new=SimpleNamespace(name='custom',layer=None,ext={'quick_profile':{
 'profile':'custom','section':[[0,0],[25,0],[25,7],[0,7]], 'settings':{'width_mm':25}}})
ok,msg=apply(p2,new)
assert ok,msg
assert p2.profiles['custom']==[[0,0],[25,0],[25,7],[0,7]] and p2.selected=='custom'

p3=Panel()
missing=SimpleNamespace(name='gone', layer=None, ext={'quick_profile': {'profile':'no-longer-in-library','settings':{}}})
ok,msg=apply(p3,missing)
assert not ok and '資料庫找不到' in msg and p3.selected is None

bad=SimpleNamespace(name='wall',layer=None,ext=None)
ok,msg=apply(p3,bad)
assert not ok and '不是' in msg and p3.selected is None

tool=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ProfilePipetteTool')
assert {'on_click','on_hover','on_key','on_cancel','_exit'}.issubset({n.name for n in tool.body if isinstance(n,ast.FunctionDef)})
assert "('pipette'" in source
assert "'section': [list(point) for point in section]" in source
assert "'material': selected_mat" in source and "'tag': selected_tag" in source
print('PASS v1.9: legacy/new samples, full parameter restore, unknown profile rejection, read-only and tool wiring')
