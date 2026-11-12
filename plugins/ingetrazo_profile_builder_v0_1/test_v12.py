"""v1.2 direction continuity regression tests, standalone without IngeTrazo."""
import ast,math
from pathlib import Path
src=Path(__file__).with_name('__init__.py').read_text(encoding='utf-8')
mod=ast.parse(src)
funcs={x.name:x for x in mod.body if isinstance(x,ast.FunctionDef)}
names=['v_add','v_sub','v_mul','v_dot','v_cross','v_norm','v_unit','clean_polygon','sweep_faces']
ns={'math':math,'EPS':1e-8}
exec(compile(ast.Module(body=[funcs[n] for n in names],type_ignores=[]),'test','exec'),ns)
sweep=ns['sweep_faces']
section=[(0,0),(17,0),(17,4),(12,4),(12,9),(6,9),(6,17),(0,17)]
# Ring at middle corner is the end of previous span, beginning of next span.
def ring_from_faces(faces, seg_i, start, section_len=len(section)):
    face=faces[seg_i*section_len]
    # segment side face is [ringA[j], ringA[k], ringB[k], ringB[j]]
    return face[0] if start else face[3]
for points in [
    [(0,0,0),(0.5,0,0),(0.5,0,0.35),(0,0,0.35)],
    [(0,0,0),(0.5,0,0),(0.5,0.35,0),(0,0.35,0)],
    [(0,0,0),(0,0.5,0),(0,0.5,0.35),(0,0,0.35)],
]:
    for direction in [points,list(reversed(points))]:
        for side in ['內側','外側']:
            kw=dict(width_mm=17,height_mm=17,closed_side=side)
            final=sweep(direction,section,closed=True,**kw)
            preview=sweep(direction,section,closed=False,preview_side=True,**kw)
            # Internal joint 1&2 has to be EXACTLY identical before/after
            # closure, not just 'visually similar'.
            for joint in (1,2):
                for k in range(len(section)):
                    point_idx=joint*len(section)+k
                    # face for segment joint, starting ring
                    face_preview=preview[point_idx]
                    face_final=final[point_idx]
                    for a,b in zip(face_preview[:2],face_final[:2]):
                        assert math.dist(a,b)<1e-8,(direction,side,joint,a,b)
            # Neighbouring side meshes share one *identical* polygon ring.
            for seg in range(len(direction)-2):
                for k in range(len(section)):
                    left=preview[seg*len(section)+k][3]
                    right=preview[(seg+1)*len(section)+k][0]
                    assert math.dist(left,right)<1e-8
            # Ensure no zero-area faces on normal 90-degree corners.
            for face in final:
                u=ns['v_sub'](face[1],face[0]);v=ns['v_sub'](face[3],face[0])
                assert ns['v_norm'](ns['v_cross'](u,v))>1e-12
assert 'preview_side=not closed' in src
print('PASS v1.2: 3 planes x 2 windings x 2 sides, intermediate rings match closed output')
