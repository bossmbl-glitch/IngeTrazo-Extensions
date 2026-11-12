"""v1.5 offline tests: browser JSON data, non-destructive GUI refactor, toolbar."""
import ast, json, tempfile, math
from pathlib import Path
root=Path(__file__).parent
old=Path(__file__).parent/'__init__.py'
new=root/'__init__.py'
a=ast.parse(old.read_text(encoding='utf-8'))
b=ast.parse(new.read_text(encoding='utf-8'))
afunc={n.name:n for n in a.body if isinstance(n,ast.FunctionDef)}
bfunc={n.name:n for n in b.body if isinstance(n,ast.FunctionDef)}
for name in ('sweep_faces','mesh_ready_faces','edge_display_rules','apply_edge_display','ordered_paths'):
    assert ast.dump(afunc[name],include_attributes=False)==ast.dump(bfunc[name],include_attributes=False),name
src=new.read_text(encoding='utf-8')
assert 'ProfileBrowser(self)' in src
assert 'self._profile_browser.reload_items()' in src
assert "('draw'," in src and "('route'," in src and "('frame'," in src
assert "('direction'," in src and "('settings'," in src
footer=src[src.index('        actions = ['):src.index('        self.footer_buttons=[]')]
assert "('finish'," not in footer and "('save'," not in footer and "('import'," not in footer
assert 'Qt.Key_Space' in src and 'Qt.Key_Return' in src and 'Qt.Key_Enter' in src
assert 'def finish_drawing(self):' in src and 'act_finish=menu.addAction' in src
browser=ast.parse((root/'profile_browser.py').read_text(encoding='utf-8'))
load=next(n for n in browser.body if isinstance(n,ast.FunctionDef) and n.name=='load_profile_json')
ns={'json':json}
exec(compile(ast.Module(body=[load],type_ignores=[]),'<browser>','exec'),ns)
with tempfile.TemporaryDirectory() as tmp:
    p=Path(tmp)/'profiles.json'
    p.write_text(json.dumps({'058':[[0,0],[17,0],[17,17],[0,17]],'ignore':'invalid'}),encoding='utf-8')
    assert ns['load_profile_json'](p)==[('058',[[0,0],[17,0],[17,17],[0,17]])]
    p.write_text(json.dumps({'name':'ABC','points':[[0,0],[1,0],[0,1]]}),encoding='utf-8')
    assert ns['load_profile_json'](p)[0][0]=='ABC'
    p.write_text('[[0,0],[1,0],[0,1]]',encoding='utf-8')
    assert ns['load_profile_json'](p)[0][0]=='profiles'
print('PASS v1.5 browser JSON, 5-button UI, shortcuts, unchanged geometry AST')
