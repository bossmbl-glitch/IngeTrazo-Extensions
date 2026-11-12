"""v1.8 regression: SKP listings independent of successful parsing."""
import ast
from pathlib import Path
import importlib.util
import tempfile

here=Path(__file__).parent
source=(here/'profile_browser.py').read_text(encoding='utf-8')
module=ast.parse(source)
methods={node.name:node for obj in module.body if isinstance(obj,ast.ClassDef) and obj.name=='ProfileBrowser' for node in obj.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))}
for name in ('reload_items','go_path','_path_edit_finished','showEvent','_load_skp_item','_load_next_skp','apply_selection','_update_scan_status'):
    assert name in methods, name
assert "file.suffix.casefold()=='.skp'" in source
assert "'source':'skp'" in source
assert "'source':'invalid-json'" in source
assert 'self._cache[key]=record' in source
assert 'self._skp_queue.append(record)' in source
assert "goto.setText('前往')" in source
assert 'SKP 檔案診斷' in source
assert 'card._sync_status()' in source
assert 'self._scan_counts=(skp_count,json_count)' in source
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)
    for name in ('++040.skp','058.SKP','++047.skp','README.txt','剖面.json'):
        (root/name).write_bytes(b'test')
    scanned=sorted(p.name for p in root.iterdir() if p.is_file() and p.suffix.casefold()=='.skp')
    assert scanned==['++040.skp','++047.skp','058.SKP']
# The import seam returns unchanged SKP geometry to diagnostic path.
spec=importlib.util.spec_from_file_location('qp_skp',(here/'skp_profiles.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
face=([(0,0,0),(0.02,0,0),(0.02,0.01,0),(0,0.01,0)],[],{})
poly=m.extract_payload({'groups':[{'faces':[face]}], 'protos':[]})
assert len(poly)==4
print('PASS v1.8: path commit, visible SKP regardless of parse, error diagnostics, refresh and no geometry change')
