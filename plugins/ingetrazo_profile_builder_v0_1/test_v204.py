"""Static and numeric regression for section thumbnail aspect ratio."""
import ast,math
from pathlib import Path
source=Path(__file__).with_name('__init__.py').read_text(encoding='utf-8')
tree=ast.parse(source)
preview=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ProfilePreview')
paint=next(n for n in preview.body if isinstance(n,ast.FunctionDef) and n.name=='paintEvent')
transform=next(n for n in ast.walk(paint) if isinstance(n,ast.FunctionDef) and n.name=='transform')
assert 'x-xmin)-ax*w' in ast.get_source_segment(source,transform)
assert 'y-ymin)-ay*h' in ast.get_source_segment(source,transform)
assert '(x-xmin)/w' not in ast.get_source_segment(source,transform)
# Extract the actual nested transform function and validate physical aspect.
func=ast.FunctionDef(name='transform',args=transform.args,body=transform.body,decorator_list=[])
ns={'xmin':0.,'ymin':0.,'w':40.22,'h':21.84,'ax':0.,'ay':0.,'cos':1.,'sin':0.,'self':type('Preview',(),{'mirror':False})()}
exec(compile(ast.fix_missing_locations(ast.Module(body=[func],type_ignores=[])),'<preview>', 'exec'),ns)
points=[(0.,0.),(40.22,0.),(40.22,21.84),(0.,21.84)]
for angle in (0,90,180,270):
    ns['cos']=math.cos(math.radians(angle));ns['sin']=math.sin(math.radians(angle))
    for mirror in (False,True):
        ns['self'].mirror=mirror
        result=[ns['transform'](*v) for v in points]
        width=max(x for x,y in result)-min(x for x,y in result)
        height=max(y for x,y in result)-min(y for x,y in result)
        expected=(40.22/21.84 if angle in (0,180) else 21.84/40.22)
        assert abs(width/height-expected)<1e-9,(angle,mirror,width,height)
# Red anchor is a geometric origin; orientation and mirror leave it at origin.
for anchor in ((0.,0.),(.5,.5),(1.,1.)):
    ns['ax'],ns['ay']=anchor
    ax,ay=ns['transform'](anchor[0]*ns['w'],anchor[1]*ns['h'])
    assert abs(ax)<1e-9 and abs(ay)<1e-9
print('PASS v2.0.4: true aspect ratio, all quarter rotations, mirror, and anchor')
