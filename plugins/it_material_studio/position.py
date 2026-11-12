"""Model context menu and direct texture pins, using the host's viewport tool."""
import copy,math
from types import SimpleNamespace
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMenu
from PySide6.QtGui import QVector3D
from core.history import Command, _dirty_group_chunks
from core.group import effective_material
from core.materials import effective_attrs
from core.mesh import Face
from core.snap import face_plane_world
from core.i18n import tr
from tools.paint import clicked_back_side
from tools.texture_position import TexturePositionTool, TextureMap, _rotated
from .engine import KEY


class PositionCommand(Command):
    """Swap attrs so a pending colour worker cannot overwrite the new placement."""
    def __init__(self, face, side, texture, inherited=None):
        self.face=face;self.before=face.attrs;self.after=copy.deepcopy(face.attrs)
        if side=='back':
            base=self.after.get('back')
            base=copy.deepcopy(base if isinstance(base,dict) else inherited or {})
            base['texture']=copy.deepcopy(texture);self.after['back']=base
        else:
            if inherited:self.after.update(copy.deepcopy(inherited))
            self.after['texture']=copy.deepcopy(texture)
    def _put(self,scene,attrs):
        self.face.attrs=attrs;_dirty_group_chunks(scene);scene.version+=1
    def do(self,scene):self._put(scene,self.after)
    def undo(self,scene):self._put(scene,self.before)


class RotateFacesCommand(Command):
    """One history step for all chosen faces; never rotate model geometry."""
    def __init__(self,changes):self.changes=changes
    def _put(self,scene,after):
        for change in self.changes:change.face.attrs=change.after if after else change.before
        _dirty_group_chunks(scene);scene.version+=1
    def do(self,scene):self._put(scene,True)
    def undo(self,scene):self._put(scene,False)


class ModelPositionTool(TexturePositionTool):
    name='貼圖位置'
    description='紅點移動；綠點縮放／旋轉；藍點拉伸。Enter 完成，右鍵有旋轉、翻轉及完成。'
    inherited=None
    def result_texture(self):
        tex=super().result_texture()
        if tex is not None:tex.pop(KEY,None)
        return tex
    def side_command(self,face,side,tex):return PositionCommand(face,side,tex,self.inherited)
    def commit(self,viewport):
        super().commit(viewport);viewport.notify_scene_changed()
    def scale(self,factor):
        if not self.active:return
        self._push_undo();anchor=self.map.world(*self.pin_uv[0])
        self.map=TextureMap(anchor,self.pin_uv[0],self.map.e_u*factor,self.map.e_v*factor)
    def make_menu(self,viewport):
        menu=QMenu(viewport)
        menu.addAction('完成',lambda:self.commit(viewport))
        menu.addAction('還原這次調整',lambda:(self.reset(),viewport.update()))
        rotate=menu.addMenu('旋轉')
        for deg in (90,180,270):
            rotate.addAction(f'{deg}°',lambda checked=False,d=deg:(self.rotate(d),viewport.update()))
        size=menu.addMenu('大小')
        for text,factor in (('放大 2 倍',2.),('縮小一半',.5)):
            size.addAction(text,lambda checked=False,f=factor:(self.scale(f),viewport.update()))
        flip=menu.addMenu('翻轉')
        flip.addAction('左右',lambda:(self.flip(True),viewport.update()))
        flip.addAction('上下',lambda:(self.flip(False),viewport.update()))
        undo=menu.addAction('復原上一步',lambda:(self.undo_step(),viewport.update()))
        undo.setEnabled(bool(self._undo))
        return menu
    def context_menu(self,viewport,global_pos):
        if not self.active:return False
        self.make_menu(viewport).exec(global_pos);return True


class ModelTexturePosition:
    key='it_material_position'
    def __init__(self,app,panel):
        self.app=app;self.panel=panel;self.tool=ModelPositionTool()
        self._request=0
        app.window._tools[self.key]=self.tool
    def target(self,pixel):
        vp=self.app.viewport
        if pixel is not None:
            face,placement=vp.pick_face_placement(*pixel)
        else:
            faces=[f for f in self.app.scene.selection if isinstance(f,Face)]
            face=faces[0] if len(faces)==1 else None;placement=None
        if face is None:return None
        material=effective_material(placement) or effective_material(self.app.scene.edit_group)
        attrs=effective_attrs(face.attrs,material)
        proxy=SimpleNamespace(attrs=attrs)
        clicked='back' if pixel is not None and clicked_back_side(vp,face,placement,*pixel) else 'front'
        for side in (clicked,'front' if clicked=='back' else 'back'):
            tex=TexturePositionTool.side_texture(proxy,side)
            if tex:
                own=TexturePositionTool.side_texture(face,side)
                inherited=None
                if own is None:
                    inherited=attrs.get('back') if side=='back' else {k:v for k,v in attrs.items() if k!='back'}
                return face,side,tex,inherited
        return None
    def enter(self,pixel):
        # Enter via the viewport so picking caches and group-edit undo are valid.
        if self.target(pixel) is None:return None
        if pixel is not None:
            for _ in range(64):
                face,group=self.app.viewport.pick_face_any(*pixel)
                if face is None:return None
                if group is None:break
                before=self.app.scene.edit_group
                self.app.viewport.begin_group_edit(group)
                if self.app.scene.edit_group is before:
                    self.app.viewport.flash_status('此群組被鎖定，無法調整貼圖。',5000);return None
            else:return None
        return self.target(pixel)
    def start(self,pixel):
        self.panel.end_color()
        if self.panel.pending_jobs:return self.after_color(lambda:self.start(pixel))
        hit=self.enter(pixel)
        if hit is None:return False
        face,side,tex,inherited=hit;vp=self.app.viewport;at=None
        if pixel is not None:
            origin,direction=vp._pixel_to_ray(*pixel)
            if origin is not None:at=vp._ray_plane(origin,direction,*face_plane_world(face,None))
        self.app.scene.select([face]);self.app.window._activate_tool(self.key)
        self.tool.inherited=inherited
        original=face.attrs
        try:
            # begin() reads paint once. Inherited paint remains untouched until Done.
            if inherited:
                face.attrs=copy.deepcopy(original)
                if side=='back':face.attrs['back']=copy.deepcopy(inherited)
                else:face.attrs.update(copy.deepcopy(inherited))
            ok=self.tool.begin(vp,face,at,side=side)
        finally:face.attrs=original
        if not ok:
            self.app.window._activate_tool('select');vp.flash_status('這個面的貼圖投影無法定位。',5000)
        else:vp.flash_status(self.tool.description,12000);vp.setFocus();vp.update()
        return ok
    def reset(self,pixel):
        self.panel.end_color()
        if self.panel.pending_jobs:return self.after_color(lambda:self.reset(pixel))
        hit=self.enter(pixel)
        if hit is None:return False
        face,side,tex,inherited=hit
        flat={k:v for k,v in tex.items() if k not in ('uvw','rot',KEY)}
        self.app.viewport.history.execute(PositionCommand(face,side,flat,inherited))
        self.app.viewport.notify_scene_changed();return True
    def selected_faces(self):
        scene=self.app.scene
        current=set(scene.mesh.faces)
        return [f for f in scene.selection if isinstance(f,Face) and f in current and scene.entity_selectable(f)]
    def rotate_selected(self,degrees,pixel=None):
        self.panel.end_color()
        if self.panel.pending_jobs:return self.after_color(lambda:self.rotate_selected(degrees,pixel))
        vp=self.app.viewport
        if self.tool.active:
            self.tool.rotate(degrees);self.tool.commit(vp);return 1
        faces=self.selected_faces()
        if not faces and pixel is not None:
            hit=self.enter(pixel)
            if hit is not None:faces=[hit[0]]
        if not faces:
            vp.flash_status('請先進入群組，選取要旋轉貼圖的面；可一次選取多個面。',6000);return 0
        material=effective_material(self.app.scene.edit_group);changes=[]
        for face in faces:
            if not self.app.scene.entity_selectable(face):continue
            normal=face.normal()
            if normal.lengthSquared()<1e-18:continue
            center=QVector3D()
            for point in face.vertices:center+=point
            center/=max(1,len(face.vertices))
            attrs=effective_attrs(face.attrs,material);proxy=SimpleNamespace(attrs=attrs)
            screen=vp._world_to_pixel(center)
            side='back' if screen is not None and clicked_back_side(vp,face,None,*screen) else 'front'
            for candidate in (side,'front' if side=='back' else 'back'):
                tex=TexturePositionTool.side_texture(proxy,candidate)
                if tex is None:continue
                mapping=TextureMap.from_face(face,tex,center)
                if mapping is None:continue
                angle=math.radians(degrees);axis=normal.normalized()
                rotated=TextureMap(center,mapping.uv_of(center),
                    _rotated(mapping.e_u,axis,angle),_rotated(mapping.e_v,axis,angle))
                result={k:copy.deepcopy(v) for k,v in tex.items() if k not in ('rot',KEY)}
                result['uvw']=rotated.uvw()
                inherited=None
                if TexturePositionTool.side_texture(face,candidate) is None:
                    inherited=attrs.get('back') if candidate=='back' else {k:v for k,v in attrs.items() if k!='back'}
                changes.append(PositionCommand(face,candidate,result,inherited));break
        if not changes:
            vp.flash_status('選取的面沒有可旋轉的圖片貼圖。',5000);return 0
        vp.history.execute(RotateFacesCommand(changes));vp.notify_scene_changed()
        skipped=len(faces)-len(changes)
        message=f'已旋轉 {len(changes)} 個面的貼圖 {degrees}°。Ctrl+Z 可一次復原。'
        if skipped:message+=f' 略過 {skipped} 個無有效圖片貼圖的面。'
        vp.flash_status(message,6000);return len(changes)
    def after_color(self,operation):
        # Avoid saving the small colour-preview image into a new UV command.
        self._request+=1;request=self._request;scene=self.app.scene
        selected=list(scene.selection)
        self.app.viewport.flash_status('正在完成材質原圖，完成後執行貼圖調整。',4000)
        def ready():
            if request!=self._request or self.app.scene is not scene or list(scene.selection)!=selected:return
            if self.panel.pending_jobs:QTimer.singleShot(40,ready)
            else:operation()
        QTimer.singleShot(40,ready);return True
    def add_menu(self,menu,selection):
        pixel=getattr(self.app.viewport,'_context_pixel',None)
        pixel=tuple(pixel) if pixel is not None else None
        hit=self.target(pixel);faces=self.selected_faces()
        if not selection or (hit is None and not faces):return
        root=menu
        while isinstance(root.parent(),QMenu):root=root.parent()
        texture=None
        for action in root.actions():
            if action.menu() and action.text().replace('&','') in (tr('Texture'),'Texture','纹理','紋理'):
                texture=action.menu();break
        if texture is None:texture=root.addMenu('紋理')
        else:texture.setTitle('紋理');texture.clear()
        texture.addAction('位置',lambda:QTimer.singleShot(0,lambda:self.start(pixel))).setEnabled(hit is not None and len(faces)<=1)
        texture.addAction('重設位置',lambda:QTimer.singleShot(0,lambda:self.reset(pixel))).setEnabled(hit is not None and len(faces)<=1)
        texture.addSeparator()
        for deg in (90,180):
            texture.addAction(f'旋轉貼圖 {deg}°',lambda checked=False,d=deg:QTimer.singleShot(0,lambda:self.rotate_selected(d,pixel)))
