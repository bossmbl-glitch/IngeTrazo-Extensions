"""Preview orientation regression, independent of Qt/IngeTrazo."""
import ast
import math
from pathlib import Path
source=Path(__file__).with_name('__init__.py').read_text(encoding='utf-8')
tree=ast.parse(source)
names={'v_add','v_sub','v_mul','v_dot','v_cross','v_norm','v_unit',
       'clean_polygon','section_dimensions_mm','face_section_points_mm'}
funcs=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
ns={'math':math,'EPS':1e-8}
exec(compile(ast.Module(body=funcs,type_ignores=[]),'<capture>', 'exec'),ns)
project=ns['face_section_points_mm']; dim=ns['section_dimensions_mm']

def bounds(poly):
    xs=[p[0] for p in poly]; ys=[p[1] for p in poly]
    return tuple(round(v,5) for v in (min(xs),max(xs),min(ys),max(ys)))

def check_order_invariance(coords, expected_dimensions):
    initial=project(coords)
    for shifted in range(len(coords)):
        ordered=coords[shifted:]+coords[:shifted]
        for seq in (ordered,list(reversed(ordered))):
            out=project(seq)
            assert tuple(round(x,5) for x in dim(out)) == tuple(round(x,5) for x in dim(initial))
            # Compare 2D vertices by their original 3D identity, not by chosen origin.
            idx={point:i for i,point in enumerate(coords)}
            for point,p2 in zip(seq,out):
                j=idx[point]
                baseline=initial[j]
                offset=[out[0][k]-initial[idx[seq[0]]][k] for k in range(2)]
                assert all(abs(p2[k]-baseline[k]-offset[k])<1e-5 for k in range(2))
            assert all(abs(a-b)<1e-5 for a,b in zip(dim(out),expected_dimensions)),dim(out)
    return initial

# Horizontal XY face: X right, Y up; winding and starting vertex irrelevant.
xy=[(0,0,0),(.04072,0,0),(.04072,.02181,0),(0,.02181,0)]
check_order_invariance(xy,(40.72,21.81))
# Upright XZ face: +Z is upwards, even with reverse face winding.
xz=[(0,0,0),(.04072,0,-.004),(.04072,0,-.02581),(0,0,-.02181)]
r=check_order_invariance(xz,(40.72,25.81))
assert r[0][1] > r[3][1],r  # left edge rises relative to sloping right edge
# Upright YZ face: horizontal axis is +Y rather than collapsed X.
yz=[(0,0,0),(0,.04072,-.004),(0,.04072,-.02581),(0,0,-.02181)]
check_order_invariance(yz,(40.72,25.81))
# A translated face must not change visual relative layout or size.
shift=(4.2,-3.1,7.5)
translated=[tuple(p[i]+shift[i] for i in range(3)) for p in xz]
assert all(abs(a-b)<1e-4 for a,b in zip(dim(project(translated)),dim(r)))
# A face with first 3 collinear vertices should still project correctly.
more=[(0,0,0),(.02036,0,-.002),(.04072,0,-.004),(.04072,0,-.02581),(0,0,-.02181)]
check_order_invariance(more,(40.72,25.81))
# Source must be unchanged.
assert xz==[(0,0,0),(.04072,0,-.004),(.04072,0,-.02581),(0,0,-.02181)]
print('PASS v2.0.3: XY/XZ/YZ, winding, point origin, translation, collinear first edge')
