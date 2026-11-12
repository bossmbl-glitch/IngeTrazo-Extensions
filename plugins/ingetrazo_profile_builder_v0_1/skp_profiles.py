# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-destructive SKP -> single planar, closed profile extraction.

Uses IngeTrazo's own `formats.skp.parse_skp`; NEVER applies a payload to the
open scene. Only a single planar, hole-free outline is supported in v1.6.
"""
import math
from pathlib import Path

MAX_FILE_BYTES = 24 * 1024 * 1024
MAX_FACES = 20000


class ProfileImportError(ValueError):
    pass


def _vec(p):
    if not isinstance(p, (list, tuple)) or len(p) < 3:
        raise ProfileImportError('SKP 幾何座標格式不正確。')
    q = tuple(float(x) for x in p[:3])
    if not all(math.isfinite(x) for x in q):
        raise ProfileImportError('SKP 含無效座標。')
    return q


def _cross(a,b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _sub(a,b): return tuple(x-y for x,y in zip(a,b))
def _dot(a,b): return sum(x*y for x,y in zip(a,b))
def _length(a): return math.sqrt(_dot(a,a))


def _outer(face):
    if not isinstance(face,(tuple,list)) or len(face)<2:
        raise ProfileImportError('SKP 面資料格式不符合預期。')
    outer,holes=face[0],face[1]
    if holes: raise ProfileImportError('剖面含內孔；v1.6 尚不支援帶孔剖面。')
    points=[_vec(p) for p in outer]
    if len(points)>1 and math.dist(points[0],points[-1])<1e-6: points.pop()
    if len(points)<3: raise ProfileImportError('剖面沒有足夠頂點。')
    return points


def _one_outline(faces):
    """Combine coplanar triangles/faces by cancelling shared edges."""
    rings=[_outer(face) for face in faces]
    # All faces must have one common plane (reject thick 3D shapes).
    reference=None
    allpts=[p for ring in rings for p in ring]
    if not allpts: raise ProfileImportError('SKP 沒有可用的面。')
    origin=allpts[0]
    for ring in rings:
        for j in range(1,len(ring)-1):
            n=_cross(_sub(ring[j],ring[0]),_sub(ring[j+1],ring[0]))
            if _length(n)>1e-7:
                reference=tuple(v/_length(n) for v in n)
                break
        if reference: break
    if reference is None: raise ProfileImportError('SKP 面積過小或全部共線。')
    size=max(math.dist(p,origin) for p in allpts)
    tolerance=max(0.001,size*1e-6)
    if any(abs(_dot(_sub(p,origin), reference))>tolerance for p in allpts):
        raise ProfileImportError('這是 3D 模型而非單一平面剖面，請先建立封閉的 2D 面。')
    # Select axes directly from global coordinates; retain geometric dimensions.
    drop=max(range(3),key=lambda i:abs(reference[i]))
    axes=[i for i in range(3) if i!=drop]
    project=lambda p: (p[axes[0]],p[axes[1]])
    nodepoints={}
    edgecounts={}
    def key(p): return (round(p[0],5),round(p[1],5))
    for ring in rings:
        if len(ring)>4096: raise ProfileImportError('剖面頂點過多。')
        xy=[project(p) for p in ring]
        for k,p in enumerate(xy):
            a=key(p); b=key(xy[(k+1)%len(xy)])
            if a==b: continue
            nodepoints.setdefault(a,p); nodepoints.setdefault(b,xy[(k+1)%len(xy)])
            edge=tuple(sorted((a,b)))
            edgecounts[edge]=edgecounts.get(edge,0)+1
    if any(n>2 for n in edgecounts.values()):
        raise ProfileImportError('剖面存在重疊面，請先清理模型。')
    boundary=[e for e,n in edgecounts.items() if n==1]
    incident={}
    for a,b in boundary:
        incident.setdefault(a,[]).append(b); incident.setdefault(b,[]).append(a)
    if len(incident)<3 or any(len(v)!=2 for v in incident.values()):
        raise ProfileImportError('剖面邊界不封閉或存在分岔。')
    start=min(incident)
    ordered=[start]; previous=None; current=start
    while True:
        neighbors=incident[current]
        nxt=neighbors[0] if neighbors[0]!=previous else neighbors[1]
        if nxt==start: break
        if nxt in ordered or len(ordered)>4096:
            raise ProfileImportError('剖面輪廓發生自相交。')
        ordered.append(nxt);previous,current=current,nxt
    if len(ordered)!=len(incident):
        raise ProfileImportError('SKP 含多個分離輪廓或內孔，無法自動選出單一剖面。')
    poly=[nodepoints[k] for k in ordered]
    area=sum(poly[i][0]*poly[(i+1)%len(poly)][1]-poly[(i+1)%len(poly)][0]*poly[i][1] for i in range(len(poly)))/2
    if abs(area)<1e-5:raise ProfileImportError('剖面面積為零。')
    if area<0:poly.reverse()
    minx=min(p[0] for p in poly);miny=min(p[1] for p in poly)
    return [[round(x-minx,5),round(y-miny,5)] for x,y in poly]


def extract_payload(payload):
    """Extract one usable planar outline, avoiding duplicated unused definitions."""
    if not isinstance(payload,dict): raise ProfileImportError('SKP 解析器沒有回傳有效幾何。')
    groups=[g for g in payload.get('groups',[]) if g.get('faces')]
    protos=[g for g in payload.get('protos',[]) if g.get('faces')]
    # In SKP a placed component often lives in protos, without a root group.
    candidates=groups if groups else protos
    if not candidates: raise ProfileImportError('SKP 沒有可用的封閉面。')
    if len(candidates)!=1:
        raise ProfileImportError('SKP 含多個幾何群組，請先單獨儲存一個 2D 剖面。')
    faces=candidates[0]['faces']
    if len(faces)>MAX_FACES:
        raise ProfileImportError('剖面面數太多，請先簡化。')
    return _one_outline(faces)


def load_skp_profile(path):
    """Read source only: no Scene, import command, or file writes."""
    path=Path(path)
    if path.suffix.casefold()!='.skp' or not path.is_file():
        raise ProfileImportError('請選擇有效的 .skp 檔案。')
    if path.stat().st_size>MAX_FILE_BYTES:
        raise ProfileImportError('SKP 大於 24 MB，請先儲存為單一輕量剖面。')
    try:
        from formats.skp import parse_skp
    except (ImportError, AttributeError) as exc:
        raise ProfileImportError('目前 IngeTrazo 找不到內建的 SKP 解析器。') from exc
    try:
        payload=parse_skp(path)
    except Exception as exc:
        raise ProfileImportError(f'SKP 讀取失敗：{exc}') from exc
    return extract_payload(payload)
