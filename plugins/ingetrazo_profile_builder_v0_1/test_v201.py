"""Last manually saved tool-state tests (no Qt or IngeTrazo dependencies)."""
from pathlib import Path
from tempfile import TemporaryDirectory
from last_used_state import load, store, validate

sample = dict(version=1, profile='058｜弧形線板', section=[[0,0],[17,0],[17,17],[0,17]],
              settings=dict(width_mm=17.0, height_mm=17.0, rotate_deg=270.0,
                            mirror=True, off_u_mm=2.5, off_v_mm=-3.0, miter=True,
                            anchor='左下', closed_side='內側', reverse_depth=True),
              material='預設材質', tag='預設標籤')
with TemporaryDirectory() as td:
    path = Path(td)/'last_saved_use.json'
    library = Path(td)/'profiles.json'
    library.write_text('{"058":"unchanged"}', encoding='utf-8')
    assert load(path) is None
    store(path, sample)
    assert load(path) == validate(sample)
    modified = dict(sample, settings=dict(sample['settings'], rotate_deg=90.0))
    # Editing without pressing save never updates disk.
    assert load(path)['settings']['rotate_deg'] == 270.0
    store(path, modified)
    assert load(path)['settings']['rotate_deg'] == 90.0
    assert library.read_text(encoding='utf-8') == '{"058":"unchanged"}'
    path.write_text('corrupt json', encoding='utf-8')
    try:
        load(path)
    except ValueError:
        pass
    else:
        raise AssertionError('Bad state should fail validation')
print('v2.0.1 state tests passed')
