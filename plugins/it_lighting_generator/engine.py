"""Native model library and undoable placement. All scene lengths are metres."""
import copy
import json
import math
from pathlib import Path
from PySide6.QtGui import QMatrix4x4, QVector3D
from core.group import Group, copy_group, iter_placements
from core.scene import Scene
from core.history import Command, InsertGroupCommand
from core.insert import merge_materials, ensure_layers
from formats import igz
from tools.place_group import PlaceGroupTool

ROOT = Path(__file__).resolve().parent
KEY = 'it_lighting_generator'


def catalog():
    return json.loads((ROOT/'catalog.json').read_text(encoding='utf8'))['models']


def validate(params):
    p = dict(params)
    for name in ('rows', 'cols'):
        v = p.get(name, 1)
        if isinstance(v, bool) or int(v) != v or not 1 <= int(v) <= 100:
            raise ValueError('列數與行數必須是 1–100 的整數。')
        p[name] = int(v)
    if p['rows']*p['cols'] > 400:
        raise ValueError('每次最多產生 400 顆燈具。')
    for name, default in [('spacing_x', 1.2), ('spacing_y', 1.2),
                          ('scale', 1.0), ('angle', 0), ('height', 2.8)]:
        p[name] = float(p.get(name, default))
        if not math.isfinite(p[name]):
            raise ValueError('尺寸與角度必須是有限數值。')
    if not 0.01 <= p['scale'] <= 100:
        raise ValueError('比例必須介於 1% 與 10000%。')
    if p['spacing_x'] <= 0 or p['spacing_y'] <= 0:
        raise ValueError('排列間距必須大於零。')
    return p


def build(model_id, params):
    p = validate(params)
    item = next((x for x in catalog() if x['id'] == model_id), None)
    if item is None:
        raise ValueError('找不到燈具型號。')
    src = Scene()
    igz.load_into(src, ROOT/item['model'])
    if len(src.groups) != 1:
        raise ValueError('模型庫內容不完整。')
    prototype = src.groups[0]
    anchor = QVector3D()
    bounds = item.get('body_bounds_m',item['bounds_m'])
    if p.get('anchor') == 'bottom':
        anchor = QVector3D((bounds[0][0]+bounds[1][0])/2,
                           (bounds[0][1]+bounds[1][1])/2,bounds[0][2])
    elif p.get('anchor') == 'top':
        anchor = QVector3D((bounds[0][0]+bounds[1][0])/2,
                           (bounds[0][1]+bounds[1][1])/2,bounds[1][2])
    pieces = []
    for row in range(p['rows']):
        for col in range(p['cols']):
            g = copy_group(prototype)
            t = QMatrix4x4()
            t.translate(col*p['spacing_x'], row*p['spacing_y'], 0)
            t.scale(p['scale'])
            t.translate(-anchor)
            g.xform = t*g.xform
            pieces.append(g)
    count = len(pieces)
    group = Group(name=model_id if count == 1 else f'{model_id} · {p["cols"]}×{p["rows"]}')
    group.adopt(pieces)
    group.xform = QMatrix4x4()
    group.xform.rotate(p['angle'],0,0,1)
    group.ext = {KEY: {'model':model_id,'params':p,'count':count,'version':'1.0.2'}}
    return group, copy.deepcopy(src.materials)


class PlaceLightingCommand(Command):
    """Geometry and imported material registry share one undo step."""
    keeps_groups = True

    def __init__(self, group, materials):
        self.group = group
        self.materials = materials
        self.insert = InsertGroupCommand(group)
        self.before = self.after = None

    def do(self, scene):
        self.before = (dict(scene.materials), list(scene.layers))
        try:
            if self.after is None:
                merge_materials(copy.deepcopy(self.materials),scene.materials,self.group)
                ensure_layers(scene,self.group)
                self.after = (dict(scene.materials),list(scene.layers))
            else:
                scene.materials.clear(); scene.materials.update(self.after[0])
                scene.layers[:] = self.after[1]
            self.insert.do(scene)
        except Exception:
            self.insert.undo(scene)
            scene.materials.clear(); scene.materials.update(self.before[0])
            scene.layers[:] = self.before[1]
            raise

    def undo(self, scene):
        self.insert.undo(scene)
        scene.materials.clear(); scene.materials.update(self.before[0])
        scene.layers[:] = self.before[1]


class UpdateLightingCommand(Command):
    """Refresh selected generator containers while retaining their placement."""
    keeps_groups = True

    def __init__(self, groups):
        self.items=[]
        for group in groups:
            meta=(getattr(group,'ext',None) or {}).get(KEY)
            if not meta:continue
            fresh,materials=build(meta['model'],meta['params'])
            self.items.append((group,fresh,materials,group.children,copy.deepcopy(group.ext)))
        self.before=self.after=None

    def do(self, scene):
        self.before=(dict(scene.materials),list(scene.layers))
        try:
            for group,fresh,materials,old_children,old_ext in self.items:
                if self.after is None:
                    merge_materials(copy.deepcopy(materials),scene.materials,fresh)
                    ensure_layers(scene,fresh)
                group.children=fresh.children
                group.ext={**(group.ext or {}),KEY:copy.deepcopy(fresh.ext[KEY])}
            if self.after is None:
                self.after=(dict(scene.materials),list(scene.layers))
            else:
                scene.materials.clear();scene.materials.update(self.after[0]);scene.layers[:]=self.after[1]
            scene.version+=1
        except Exception:
            self.undo(scene)
            raise

    def undo(self, scene):
        for group,fresh,materials,old_children,old_ext in self.items:
            group.children=old_children;group.ext=copy.deepcopy(old_ext)
        scene.materials.clear();scene.materials.update(self.before[0]);scene.layers[:]=self.before[1]
        scene.version+=1


class LightingPlaceTool(PlaceGroupTool):
    name = '放置燈具'

    def __init__(self, group, materials, params):
        super().__init__(group, anchor=QVector3D())
        self._params = dict(params)
        self._materials = materials
        self._template = copy_group(group)
        self._preview_on = False

    def on_activate(self, viewport):
        self._start_preview(viewport)

    def _start_preview(self, viewport):
        begin = getattr(viewport,'begin_groups_preview',None)
        if callable(begin) and self._group is not None:
            begin([self._group],external=True)
            self._preview_on = True
            viewport.set_groups_preview_offset(self._offset)

    def _end_preview(self, viewport):
        if self._preview_on:
            viewport.end_groups_preview()
            self._preview_on = False

    def on_deactivate(self, viewport):
        self._end_preview(viewport)
        super().on_deactivate(viewport)

    def rubber_band_lines(self):
        # The native external-group pass draws both surfaces and real edges.
        return [] if self._preview_on else super().rubber_band_lines()

    def target(self, world):
        q = QVector3D(world)
        if self._params.get('fixed_height',True):
            q.setZ(float(self._params.get('height',2.8)))
        return q

    def on_hover(self, ctx):
        self._offset = self.target(ctx.world)
        if self._preview_on:
            ctx.viewport.set_groups_preview_offset(self._offset)
        ctx.viewport.update()

    def on_click(self, ctx):
        self.place_at(ctx.viewport, ctx.world)

    def place_at(self, viewport, world):
        if self._group is None:
            return
        self._end_preview(viewport)
        previous = QMatrix4x4(self._group.xform)
        t = QMatrix4x4(); t.translate(self.target(world))
        self._group.xform = t*self._group.xform
        viewport.history.execute(PlaceLightingCommand(self._group,self._materials))
        if getattr(viewport.history,'last_error',None) or getattr(viewport.history,'last_rejected',None):
            self._group.xform = previous
            self._start_preview(viewport)
            viewport.flash_status('放置失敗：'+str(viewport.history.last_error or viewport.history.last_rejected),8000)
            return
        viewport.notify_scene_changed()
        viewport.flash_status('已放置燈具；Ctrl+Z 復原，Esc 結束。',4000)
        if self._params.get('repeat',False):
            self._group = copy_group(self._template)
            self._start_preview(viewport)
        else:
            self._group = None
            window = viewport.window()
            if hasattr(window,'_activate_tool'):
                window._activate_tool('select')
        viewport.update()

    def on_cancel(self, viewport):
        self._end_preview(viewport)
        super().on_cancel(viewport)
        window = viewport.window()
        if hasattr(window,'_activate_tool'):
            window._activate_tool('select')
