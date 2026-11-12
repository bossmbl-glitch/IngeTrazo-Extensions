# SPDX-License-Identifier: GPL-3.0-or-later
"""Selective profile editing and repeatable filled-profile placement."""
import copy
import math
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QVBoxLayout,QGridLayout,QCheckBox,QPushButton,QHBoxLayout,QLabel
from core.mesh import Mesh
from core.group import Group
from core.history import SnapshotImport
from tools.base import Tool

EDIT_FIELDS = [
    ('profile','輪廓／名稱／寬／高'),('anchor','放置點'),
    ('rotate_deg','旋轉'),('mirror','鏡像'),
    ('off_u_mm','水平偏移'),('off_v_mm','垂直偏移'),
    ('material','材質'),('tag','標籤'),
    ('miter','轉角連接'),('extrusion','擠出模式（一般）')]
PAINT_KEYS=('mat','color','texture','opacity','uv','uvs','uvw')


def merged_recipe(old, current, fields):
    """Only checked properties change; direction/path and unselected data survive."""
    from . import clean_polygon
    new=copy.deepcopy(old)
    if not isinstance(new.get('settings'),dict):raise ValueError('原群組缺少有效參數。')
    if 'profile' in fields:
        new['profile']=current['profile']
        new['temporary_profile']=bool(current.get('temporary_profile'))
        new['section']=clean_polygon(current['section'])
        for key in ('width_mm','height_mm'):new['settings'][key]=current['settings'][key]
    for key in ('anchor','rotate_deg','mirror','off_u_mm','off_v_mm','miter'):
        if key in fields:new['settings'][key]=current['settings'][key]
    for key in ('material','tag'):
        if key in fields:new[key]=current[key]
    if 'extrusion' in fields:new['extrusion']='一般'
    return new


def recipe_faces(recipe):
    from . import sweep_faces,experimental_sweep_faces
    opts=dict(recipe['settings'])
    # Whitelist: future/foreign metadata must not become executable kwargs.
    allowed={'horizontal','fixed_side','wall_frame','wall_depth_sign','plane_normal_override'}
    opts.update({k:v for k,v in recipe.get('mouse_orientation',{}).items() if k in allowed})
    if recipe.get('kind')=='stamp':
        return [stamp_polygon(recipe['section'],recipe['settings'],recipe['origin'],recipe['normal'])]
    func=experimental_sweep_faces if recipe.get('experimental') else sweep_faces
    if recipe.get('experimental') and 'plane_normal_override' in opts:
        opts['wall_normal']=opts.pop('plane_normal_override')
        opts.pop('wall_frame',None);opts.pop('wall_depth_sign',None)
    return func(recipe['path'],recipe['section'],bool(recipe.get('closed')),
                stable_mouse_plane=bool(recipe.get('mouse_draw', recipe.get('mouse_orientation'))),
                preview_side=bool(recipe.get('mouse_draw',recipe.get('mouse_orientation'))) and not bool(recipe.get('closed')),**opts)


def build_mesh(recipe, attrs=None, legacy_stamp=False):
    from . import mesh_ready_faces,edge_display_rules,apply_edge_display,qvec
    raw=recipe_faces(recipe)
    # A stamp is one editable planar face. Renderer triangulation stays inside
    # Face.triangulate(); it must never become separate editable triangle faces.
    polys=raw if recipe.get('kind')=='stamp' and not legacy_stamp else mesh_ready_faces(raw)
    if not polys:raise ValueError('剖面沒有生成有效面。')
    mesh=Mesh()
    for poly in polys:
        face=mesh.add_face([qvec(p) for p in poly])
        if face is None:raise ValueError('無法建立剖面面。')
        face.attrs.update(copy.deepcopy(attrs or {}))
    if recipe.get('kind')!='stamp':
        hidden,smooth=edge_display_rules(raw,polys,bool(recipe.get('closed')))
        apply_edge_display(mesh,hidden,smooth)
    elif legacy_stamp:
        # Reconstruct v2.2.0 stamps only to recover their existing placement.

        for edge in mesh.edges:
            if len(edge.faces)==2:edge.hidden=True
    return mesh


def clone_mesh(source):
    # Avoid deepcopy's cyclic topology traversal; preserve vertex order for edits.
    from PySide6.QtGui import QVector3D
    mesh=Mesh()
    for v in source.vertices:mesh.vertex(QVector3D(v.position))
    for face in source.faces:
        f=mesh.add_face([QVector3D(v.position) for v in face.loop],
                        [[QVector3D(v.position) for v in loop] for loop in face.hole_loops])
        f.attrs.update(copy.deepcopy(face.attrs));f.interior=face.interior
    for edge in source.edges:
        e=mesh.add_edge(QVector3D(edge.v0.position),QVector3D(edge.v1.position))
        e.hidden=edge.hidden;e.soft=edge.soft;e.layer=edge.layer;e.curve=edge.curve
    return mesh


def _map_mesh(mesh, matrix):
    from . import qvec
    array=np.asarray(matrix,dtype=float)
    if array.shape!=(4,3) or not np.isfinite(array).all():raise ValueError('群組定位矩陣無效。')
    for vertex in list(mesh.vertices):
        p=vertex.position
        xyz=np.array([p.x(),p.y(),p.z(),1.])@array
        mesh.move_vertex(vertex,qvec(xyz)-vertex.position)
    # Preserve outward-facing loops across mirrored placements.
    if np.linalg.det(array[:3])<0:
        for face in mesh.faces:face.loop.reverse()


def _position_fit(old_recipe, group):
    """Recover affine moves/rotations/scales baked into classic Group vertices.

    IngeTrazo classic groups move their vertices, without updating ext metadata.
    Reject hand-edited topology rather than resetting position or losing edits.
    """
    legacy_stamp=(old_recipe.get('kind')=='stamp' and
                  old_recipe.get('stamp_topology')!='single_face_v1' and len(group.mesh.faces)>1)
    baseline=build_mesh(old_recipe,legacy_stamp=legacy_stamp)
    if old_recipe.get('geometry_transform') is not None:_map_mesh(baseline,old_recipe['geometry_transform'])
    if len(baseline.vertices)!=len(group.mesh.vertices):raise ValueError('線板幾何已被手動修改，無法安全重建。')
    a=np.array([[v.position.x(),v.position.y(),v.position.z(),1.] for v in baseline.vertices])
    b=np.array([[v.position.x(),v.position.y(),v.position.z()] for v in group.mesh.vertices])
    affine,_,rank,_=np.linalg.lstsq(a,b,rcond=None)
    if rank<4 and old_recipe.get('kind')!='stamp':raise ValueError('線板的定位資料不足。')
    error=float(np.max(np.abs(a@affine-b)))
    if not math.isfinite(error) or error>max(2e-6,float(np.ptp(b,axis=0).max())*1e-6):
        raise ValueError('線板含非整體變形或幾何修改，請先保留原物件。')
    return affine


def prepare_replacement(group,current,fields,scene):
    from . import PLUGIN_KEY
    old=copy.deepcopy(group.ext[PLUGIN_KEY])
    if getattr(group,'locked',False):raise ValueError('請先解除群組鎖定。')
    if getattr(group,'children',None):raise ValueError('請選擇沒有巢狀子物件的輪廓群組。')
    if not old.get('section'):raise ValueError('此舊群組沒有保存剖面形狀，請先保留原物件。')
    new=merged_recipe(old,current,fields)
    geometry=bool(set(fields)&{'profile','anchor','rotate_deg','mirror','off_u_mm','off_v_mm','miter'})
    replacement=copy.copy(group)
    replacement.ext=copy.deepcopy(group.ext)
    if geometry:
        # Uniform paint is retained when material is not checked.
        attrs=[copy.deepcopy(f.attrs) for f in group.mesh.faces]
        if 'material' not in fields and attrs and any(a!=attrs[0] for a in attrs):
            raise ValueError('線板含逐面不同材質／屬性；請勾選材質統一套用，避免重建時遺失。')
        affine=_position_fit(old,group)
        if new.get('kind')=='stamp':new['stamp_topology']='single_face_v1'
        replacement.mesh=build_mesh(new,attrs[0] if attrs else {})
        identity=np.vstack((np.eye(3),np.zeros(3)))
        prior=np.asarray(old.get('geometry_transform',identity))
        _map_mesh(replacement.mesh,prior)
        _map_mesh(replacement.mesh,affine)
        previous4=np.eye(4);previous4[:,:3]=prior
        next4=np.eye(4);next4[:,:3]=affine
        new['geometry_transform']=(previous4@next4)[:,:3].tolist()
    else:
        replacement.mesh=clone_mesh(group.mesh)
    if 'material' in fields:
        material=scene.materials.get(new['material']) if new['material']!='預設材質' else None
        if new['material']!='預設材質' and material is None:raise ValueError('找不到指定材質。')
        for face in replacement.mesh.faces:
            for key in PAINT_KEYS:face.attrs.pop(key,None)
            if material:face.attrs.update(material.face_attrs())
        replacement.material=None
    if 'tag' in fields:
        tag=new['tag']
        if tag!='預設標籤' and tag not in [l.name for l in scene.layers]:raise ValueError('找不到指定標籤。')
        replacement.layer=None if tag=='預設標籤' else tag
        for face in replacement.mesh.faces:
            face.attrs.pop('layer',None)
            if tag!='預設標籤':face.attrs['layer']=tag
    replacement.ext[PLUGIN_KEY]=new
    if 'profile' in fields:replacement.name='輪廓_'+new['profile']
    return replacement


def edit_selected_profiles(panel):
    from . import PLUGIN_KEY
    viewport=panel.app.viewport;scene=viewport.scene
    try:
        if getattr(scene,'edit_group',None) is not None:raise ValueError('請先結束群組編輯。')
        selected=list(scene.selection)
        if not selected or any(g not in scene.groups or not isinstance(getattr(g,'ext',None),dict) or PLUGIN_KEY not in g.ext for g in selected):
            raise ValueError('請只選取本套件建立的輪廓或剖面群組。')
        current={'temporary_profile':panel.profile.currentText() in panel._temporary_profiles,
                 'profile':panel.profile.currentText(),'section':copy.deepcopy(panel.profiles[panel.profile.currentText()]),
                 'settings':panel.opts(),'material':panel.material.currentText(),'tag':panel.tag.currentText()}
        dialog=QDialog(panel);dialog.setWindowTitle('編輯輪廓屬性')
        layout=QVBoxLayout(dialog);layout.addWidget(QLabel(f'更新 {len(selected)} 個群組；只套用勾選項目，保留路徑與位置。'))
        grid=QGridLayout();checks={}
        for i,(key,title) in enumerate(EDIT_FIELDS):
            box=QCheckBox(title);box.setChecked(key=='profile');checks[key]=box
            grid.addWidget(box,i//2,i%2)
        layout.addLayout(grid)
        row=QHBoxLayout()
        for title,callback in [('應用',dialog.accept),('取消',dialog.reject),('全部',lambda:[b.setChecked(True) for b in checks.values()]),('清空',lambda:[b.setChecked(False) for b in checks.values()])]:
            button=QPushButton(title);button.clicked.connect(callback);row.addWidget(button)
        layout.addLayout(row)
        if dialog.exec()!=QDialog.Accepted:return
        fields={key for key,b in checks.items() if b.isChecked()}
        if not fields:return
        # Prepare every object before committing; failure modifies none.
        pairs=[(g,prepare_replacement(g,current,fields,scene)) for g in selected]
        if 'profile' in fields:
            names={g.name for g in scene.groups if g not in selected}
            for old,new in pairs:
                prefix=new.name;number=1
                while f'{prefix}_{number:03d}' in names:number+=1
                new.name=f'{prefix}_{number:03d}';names.add(new.name)
        def mutate(target):
            for old,new in pairs:
                if old not in target.groups:raise ValueError('選取的群組已改變，請重新選取。')
            for old,new in pairs:
                target.groups[target.groups.index(old)]=new
                target.selection.discard(old);target.selection.add(new)
        viewport.history.execute(SnapshotImport(mutate))
        if getattr(viewport.history,'last_error',None):raise RuntimeError(viewport.history.last_error)
        viewport.notify_scene_changed()
        viewport.flash_status(f'已更新 {len(pairs)} 個輪廓群組；可一次復原。',4000)
    except Exception as exc:panel.report(str(exc))


def stamp_polygon(section,settings,origin,normal):
    from . import clean_polygon,v_unit,v_dot,v_sub,v_mul,v_cross,v_add
    section=clean_polygon(section);n=v_unit(normal)
    ref=(1.,0.,0.) if abs(n[0])<.9 else (0.,1.,0.)
    u=v_unit(v_sub(ref,v_mul(n,v_dot(ref,n))));v=v_cross(n,u)
    anchors={'左下':(0,0),'下中':(.5,0),'右下':(1,0),'左中':(0,.5),'中心':(.5,.5),'右中':(1,.5),'左上':(0,1),'上中':(.5,1),'右上':(1,1)}
    au,av=anchors[settings['anchor']]
    minx=min(p[0] for p in section);maxx=max(p[0] for p in section)
    miny=min(p[1] for p in section);maxy=max(p[1] for p in section)
    w=settings['width_mm']/1000.;h=settings['height_mm']/1000.
    if w<=0 or h<=0 or maxx<=minx or maxy<=miny:raise ValueError('剖面尺寸必須大於零。')
    a=math.radians(settings['rotate_deg']);c=math.cos(a);s=math.sin(a)
    result=[]
    for x,y in section:
        x=(x-minx)/(maxx-minx)*w;y=(y-miny)/(maxy-miny)*h
        if settings['mirror']:x=w-x
        x-=au*w;y-=av*h
        x,y=x*c-y*s,x*s+y*c
        x+=settings['off_u_mm']/1000.;y+=settings['off_v_mm']/1000.
        result.append(v_add(origin,v_add(v_mul(u,x),v_mul(v,y))))
    return result


class ProfileStampTool(Tool):
    name='放置剖面'
    description='點擊連續蓋印；方向鍵選平面、向下恢復自動；Esc 結束。'
    shortcut=None
    wireframe_color=(.95,.55,.12,1.)
    def __init__(self,panel):
        self.panel=panel;self.mesh=None;self.recipe=None;self.plane=None;self.last_origin=None
    def on_activate(self,viewport):pass
    def on_deactivate(self,viewport):self.mesh=None;self.recipe=None
    def _refresh(self,ctx):
        from . import as_tuple,experimental_hit_wall_normal
        normal=self.plane or experimental_hit_wall_normal(ctx) or (0.,0.,1.)
        recipe={'temporary_profile':self.panel.profile.currentText() in getattr(self.panel,'_temporary_profiles',set()),
                'kind':'stamp','stamp_topology':'single_face_v1','profile':self.panel.profile.currentText(),
                'section':copy.deepcopy(self.panel.profiles[self.panel.profile.currentText()]),
                'settings':self.panel.opts(),'origin':as_tuple(ctx.world),'normal':normal,
                'material':self.panel.material.currentText(),'tag':self.panel.tag.currentText()}
        mesh=build_mesh(recipe)
        self.recipe=recipe;self.mesh=mesh;self.last_origin=recipe['origin']
    def on_hover(self,ctx):
        try:self._refresh(ctx)
        except Exception as exc:
            self.mesh=None;self.recipe=None;ctx.viewport.flash_status(str(exc),2500)
        ctx.viewport.update()
    def on_click(self,ctx):
        from . import PLUGIN_KEY
        try:
            if getattr(ctx.viewport.scene,'edit_group',None) is not None:raise ValueError('請先結束群組編輯。')
            self._refresh(ctx)
            recipe=copy.deepcopy(self.recipe);scene=ctx.viewport.scene
            mat=scene.materials.get(recipe['material']) if recipe['material']!='預設材質' else None
            if recipe['material']!='預設材質' and mat is None:raise ValueError('找不到指定材質。')
            tag=recipe['tag']
            if tag!='預設標籤' and tag not in [l.name for l in scene.layers]:raise ValueError('找不到指定標籤。')
            mesh=build_mesh(recipe,mat.face_attrs() if mat else {})
            names={g.name for g in scene.groups};num=1
            while f'剖面_{recipe["profile"]}_{num:03d}' in names:num+=1
            group=Group(mesh,f'剖面_{recipe["profile"]}_{num:03d}')
            if tag!='預設標籤':
                group.layer=tag
                for f in mesh.faces:f.attrs['layer']=tag
            group.ext={PLUGIN_KEY:recipe}
            ctx.viewport.history.execute(SnapshotImport(lambda target:target.groups.append(group)))
            if getattr(ctx.viewport.history,'last_error',None):raise RuntimeError(ctx.viewport.history.last_error)
            ctx.viewport.notify_scene_changed()
            ctx.viewport.flash_status('已放置剖面；繼續點擊蓋印，Esc 結束。',2500)
        except Exception as exc:self.panel.report(str(exc))
    def preview_faces(self):return list(self.mesh.faces) if self.mesh else []
    def drag_plane(self,viewport):
        from . import qvec
        if self.plane is not None:return (qvec(self.last_origin or (0,0,0)),qvec(self.plane))
        return None
    def on_key(self,viewport,key,modifiers):
        if key==int(Qt.Key_Escape):self.on_cancel(viewport);return True
        planes={int(Qt.Key_Right):(1.,0.,0.),int(Qt.Key_Left):(0.,1.,0.),int(Qt.Key_Up):(0.,0.,1.),int(Qt.Key_Down):None}
        if key in planes:
            self.plane=planes[key];self.mesh=None;self.recipe=None
            viewport.flash_status('放置剖面：已切換平面；移動滑鼠更新預覽。',2500);viewport.update();return True
        return False
    def on_cancel(self,viewport):
        from tools.select import SelectTool
        self.mesh=None;self.recipe=None;viewport.set_active_tool(SelectTool());viewport.update()
