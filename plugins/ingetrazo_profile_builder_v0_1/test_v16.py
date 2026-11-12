"""Offline tests for read-only SKP plane-profile extraction and browser wiring."""
import ast
import importlib.util
from pathlib import Path
r=Path(__file__).parent
s=importlib.util.spec_from_file_location('skp_profiles_test',r/'skp_profiles.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def face(points): return (points, [], {})
def group(faces): return {'groups':[{'faces':faces}], 'protos':[]}
rect=[(0,0,0),(17,0,0),(17,9,0),(0,9,0)]
def dims(poly):return (max(x for x,y in poly),max(y for x,y in poly))
assert dims(m.extract_payload(group([face(rect)])))==(17,9)
assert dims(m.extract_payload(group([face([(0,0,0),(17,0,0),(17,0,9),(0,0,9)])])))==(17,9)
# Split into two coplanar triangles; interior edge must cancel.
assert dims(m.extract_payload(group([face([rect[0],rect[1],rect[2]]),face([rect[0],rect[2],rect[3]])])))==(17,9)
for bad in (group([face(rect),face([(0,0,5),(17,0,5),(17,9,5)])]),
            {'groups':[{'faces':[face(rect)]},{'faces':[face(rect)]}]},
            group([face(rect[:3]+[(0,9,2)])])):
    try:m.extract_payload(bad)
    except m.ProfileImportError:pass
    else:raise AssertionError('should reject 3D / ambiguous SKP')
new=(r/'__init__.py').read_text()
assert 'ProfileBrowser(self)' in new
browser=(r/'profile_browser.py').read_text()
assert 'load_skp_profile' in browser and 'QTimer' in browser
assert "('skp',str(file))" in browser
assert "'source':'skp'" in browser
assert 'self.owner.apply_browser_profile' in browser
print('PASS: SKP planar / XZ / triangulated boundary, 3D rejected, browser wired')
