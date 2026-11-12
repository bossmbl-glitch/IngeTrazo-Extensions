"""Regression tests: isolated profile preferences, paths, and UI contracts."""
import ast
import json
import tempfile
from pathlib import Path
import importlib.util
import sys

root = Path(__file__).parent
package_name = 'ingetrazo_profile_builder_v0_1'
# Load small pure-Python helpers without Qt or IngeTrazo installed.
import types
pkg=types.ModuleType(package_name)
pkg.__path__=[str(root)]
sys.modules[package_name]=pkg
for name in ('last_used_state','profile_preferences'):
    spec=importlib.util.spec_from_file_location(package_name+'.'+name,root/(name+'.py'))
    mod=importlib.util.module_from_spec(spec)
    sys.modules[package_name+'.'+name]=mod
    spec.loader.exec_module(mod)
from ingetrazo_profile_builder_v0_1 import profile_preferences as pref

base=dict(version=1,profile='058',section=[[0,0],[17,0],[17,17],[0,17]],
          settings=dict(width_mm=17,height_mm=17,rotate_deg=270,mirror=True,
                        off_u_mm=5,off_v_mm=4,miter=True,anchor='左下',closed_side='內側',reverse_depth=True),
          material='預設材質',tag='預設標籤')
with tempfile.TemporaryDirectory() as t:
    p=Path(t)/'state.json'
    a=pref.identity('external','058',Path(t)/'library1'/'058.profile.json')
    b=pref.identity('external','058',Path(t)/'library2'/'058.profile.json')
    c=pref.identity('saved','012')
    assert a!=b and a!=c
    one=json.loads(json.dumps(base))
    two=json.loads(json.dumps(base));two['settings']['rotate_deg']=90
    three=json.loads(json.dumps(base));three['profile']='012';three['settings']['width_mm']=30
    state={'last_key':a,'profiles':{a:one,b:two,c:three}}
    pref.write(p,state)
    loaded=pref.read(p)
    assert len(loaded['profiles'])==3
    assert loaded['profiles'][a]['settings']['rotate_deg']==270
    assert loaded['profiles'][b]['settings']['rotate_deg']==90
    new=pref.identity('external','059',Path(t)/'library1'/'059.profile.json')
    assert pref.move(loaded,a,new,'059')
    assert loaded['last_key']==new
    assert loaded['profiles'][new]['profile']=='059'
    assert b in loaded['profiles']
    pref.forget(loaded,new)
    assert loaded['last_key'] is None and b in loaded['profiles']
    pref.write(p,loaded)
    assert new not in pref.read(p)['profiles']
    try:
        pref.move(loaded,b,c,'012')
    except FileExistsError:
        pass
    else:
        raise AssertionError('Unexpected overwrite')
    assert pref.read(p)['profiles'][b]['settings']['rotate_deg']==90

ui=(root/'profile_browser.py').read_text(encoding='utf-8')
main=(root/'__init__.py').read_text(encoding='utf-8')
assert "self._section(layout,'檔案目錄'" not in ui
assert 'def paintEvent(self, event):' in ui
assert 'source_key=self.owner.browser_identity(item)' in ui
assert 'def _apply_saved_options(self, saved):' in main
assert 'def _reset_profile_options(self):' in main
for file in (root/'__init__.py',root/'profile_browser.py',root/'profile_preferences.py'):
    ast.parse(file.read_text(encoding='utf-8'))
print('v2.1.1 per-profile preferences and browser checks passed')
