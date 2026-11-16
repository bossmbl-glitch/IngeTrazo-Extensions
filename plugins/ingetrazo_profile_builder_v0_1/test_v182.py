"""v1.8.2 file-management regression: actual disk I/O, safe rename, delete-backup."""
import ast
import json
import tempfile
from pathlib import Path
from profile_storage import save_profile,find_owned,read_owned,rename_owned,archive_owned,filename_for

with tempfile.TemporaryDirectory() as d:
    base=Path(d)
    sample=[[0,0],[17,0],[17,17],[0,17]]
    (base/'058.skp').write_bytes(b'original skp protected')
    x=save_profile(base,'059',sample)
    assert x.exists() and x.name=='059.profile.json'
    assert json.loads(x.read_text())['points']==sample
    assert find_owned(base,'059')==x
    assert read_owned(x)['format']=='ingetrazo.quick_profile.v1'
    try: save_profile(base,'059',sample)
    except FileExistsError: pass
    else: raise AssertionError('Overwrite must require permission')
    y=rename_owned(x,'059_new')
    assert y.exists() and find_owned(base,'059') is None
    assert list((base/'.quick_profile_removed').glob('059.profile.json.*.bak'))
    z=archive_owned(y)
    assert not y.exists() and z.exists()
    assert (base/'058.skp').read_bytes()==b'original skp protected'
    (base/'foreign.profile.json').write_text('original unrelated')
    try:save_profile(base,'foreign',sample)
    except FileExistsError:pass
    else:raise AssertionError('Foreign filename protected')
    try:archive_owned(base/'foreign.profile.json')
    except ValueError:pass
    else:raise AssertionError('Foreign JSON protected')
    for bad in ('../x','CON','name/other',''):
        try:filename_for(bad)
        except ValueError:pass
        else: raise AssertionError('Invalid name allowed:'+bad)

code=(Path(__file__).parent/'__init__.py').read_text(encoding='utf-8')
browser=(Path(__file__).parent/'profile_browser.py').read_text(encoding='utf-8')
# v2.2.4 separates capture from the explicit named-save operation.
tree=ast.parse(code)
panel=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ProfilePanel')
capture=ast.get_source_segment(code,next(n for n in panel.body if isinstance(n,ast.FunctionDef) and n.name=='save_face'))
assert 'QInputDialog' not in capture and '_save_named_to_library' not in capture
assert 'self._save_named_to_library(name,self.profiles[current])' in code
assert 'self.owner.delete_browser_profile(item)' in browser
assert 'self.owner.rename_browser_profile(item,name.strip())' in browser
assert 'sync_legacy_profiles' in browser
assert 'self._load_skp_item(item)' in browser
ast.parse(code); ast.parse(browser)
print('PASS v1.8.2 managed saves, ownership protection, archive delete, rename, disk verification, UI integration')
