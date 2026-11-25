"""Explicit material scopes and reversible UV rotation over IngeTrazo meshes."""
from copy import deepcopy
import secrets
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QComboBox,QHBoxLayout,QDoubleSpinBox,QWidget,QVBoxLayout,QPushButton


def install(ns,ux):
    api=ns['_release_api']
    def nodes(root):
        pending=[(root,'all')]
        while pending:
            group,inherited=pending.pop();role=ux.part_role(group,inherited)
            source=(group.ext or {}).get('lime_cy_source')
            if inherited=='fronts' and group.name in ('左框','右框','上框','下框'):role='frames'
            if source:role='fronts' if source in ('cy_backing','cy_panel') else 'frames'
            yield group,role
            pending.extend((child,role) for child in group.children or ())

    class ScopedFinish(ns['Command']):
        def __init__(self,groups,scope='all',material=None,angle=None,mode='all'):
            self.groups=list(groups);self.scope=scope;self.material=material;self.angle=angle;self.mode=mode;self.before=None;self.after=None
            if scope not in ('all','body','fronts','frames','handles'):raise ValueError('材質範圍無效。')
            matches=[(g,role) for root in self.groups for g,role in nodes(root) if (scope=='all' or role==scope) and (g.mesh.faces or g.material)]
            if not matches:raise ValueError('所選模型沒有此獨立部位；請選其他範圍。')
        def restore(self,scene,states,registry):
            for root,mesh,children,material,ext in states:
                root.mesh=mesh;root.adopt(children or []);root.material=deepcopy(material);root.ext=deepcopy(ext)
            scene.materials.clear();scene.materials.update(registry);ns['_dirty_group_chunks'](scene);scene.version+=1
        def do(self,scene):
            if self.after is not None:self.restore(scene,self.after,self.registry_after);return
            self.before=[(g,g.mesh,g.children,deepcopy(g.material),deepcopy(g.ext)) for g in self.groups];self.registry_before=dict(scene.materials);prepared=[];self.changed_faces=0;material_updates=0
            try:
                for root in self.groups:
                    copy=ns['_handle_sharing'].clone_placements(root)
                    def retain_ids(old,new):
                        new.uid=old.uid
                        for a,b in zip(old.children or (),new.children or ()):retain_ids(a,b)
                    retain_ids(root,copy)
                    inherited_materials={}
                    def collect(g,inherited=None):
                        own=g.material or inherited;inherited_materials[id(g)]=own
                        for child in g.children or ():collect(child,own)
                    collect(copy)
                    attrs=None
                    if self.material is not None:
                        mat=self.material
                        if callable(mat):mat=mat()
                        name=ns['register'](scene.materials,mat);attrs=deepcopy(scene.materials[name].face_attrs());attrs['mat']=name
                    for node,role in nodes(copy):
                        if self.scope!='all' and role!=self.scope:continue
                        if self.angle is None and self.mode=='blank' and inherited_materials[id(node)]:continue
                        node.mesh=ns['_handle_sharing'].copy_mesh(node.mesh)
                        if self.angle is None:ns['restore_node_markers'](node,node.mesh)
                        if self.angle is None:
                            if self.mode!='blank' or not node.material:
                                material_updates+=node.material!=attrs;node.material=deepcopy(attrs)
                            if self.mode=='outer':continue
                        for face in node.mesh.faces:
                            if self.angle is not None:
                                inherited=inherited_materials[id(node)] or {};changed=False
                                for side in ('front','back'):
                                    target=face.attrs if side=='front' else face.attrs.get('back')
                                    if not isinstance(target,dict):continue
                                    texture=target.get('texture')
                                    if texture is None and target.get('mat') in scene.materials:texture=scene.materials[target['mat']].face_attrs().get('texture')
                                    if texture is None and not ns['has_own_material'](target):
                                        texture=inherited.get('texture')
                                        if texture is None and inherited.get('mat') in scene.materials:texture=scene.materials[inherited['mat']].face_attrs().get('texture')
                                    if texture:
                                        target['texture']=ux.texture_rotated(texture,face.normal(),self.angle);target.pop('mat',None);face.attrs.pop('_lime_auto_'+side,None);changed=True
                                if changed:self.changed_faces+=1
                            elif self.mode!='blank' or not ns['has_own_material'](face.attrs):
                                for key in ('color','texture','opacity','mat'):face.attrs.pop(key,None)
                                face.attrs.update(deepcopy(attrs));face.attrs['back']=deepcopy(attrs);self.changed_faces+=1
                    if self.angle is None:ns['stash_auto_materials'](copy)
                    prepared.append((root,copy.mesh,copy.children,copy.material,copy.ext))
                if self.angle is None and not self.changed_faces and not material_updates:raise ValueError('範圍內已有材質，未上色，也未新增復原紀錄。')
                if self.angle is not None and not self.changed_faces:raise ValueError('選取範圍內沒有貼圖，沒有更動材質或模型。')
                self.after=prepared;self.registry_after=dict(scene.materials)
            except Exception:
                scene.materials.clear();scene.materials.update(self.registry_before);self.before=None;raise
            self.restore(scene,self.after,self.registry_after)
        def undo(self,scene):self.restore(scene,self.before,self.registry_before)
    ns['ScopedFinish']=ScopedFinish

    def material_for(window):
        options=window.options()
        if options['source']=='model':return window.app.scene.materials[options['model_name']]
        if options['source']=='random':
            def random_material():
                color=QColor(f'#{secrets.randbelow(0x1000000):06x}');return ns['Material'](name='局部隨機色',color=(color.redF(),color.greenF(),color.blueF()))
            return random_material
        color=QColor(options['color']);return ns['Material'](name='局部顏色 '+color.name(),color=(color.redF(),color.greenF(),color.blueF()))

    base=ns['PaintWindow']
    class Paint(base):
        def __init__(self,app):
            super().__init__(app)
            self.finish_scope=QComboBox()
            for title,key in [('整組物件','all'),('只改櫃身','body'),('只改門片','fronts'),('只改獨立框料','frames'),('只改把手','handles')]:self.finish_scope.addItem(title,key)
            row=QHBoxLayout();row.addWidget(ns['label']('材質範圍'));row.addWidget(self.finish_scope,1);self.body.insertLayout(2,row)
            row=QHBoxLayout();self.uv_angle=QDoubleSpinBox();self.uv_angle.setRange(-360,360);self.uv_angle.setSingleStep(90);self.uv_angle.setSuffix(' °');row.addWidget(self.uv_angle)
            row.addWidget(ns['button']('查看木紋方向',self.inspect_uv));row.addWidget(ns['button']('旋轉選取範圍貼圖',self.rotate_uv));panel=QWidget();panel.setLayout(row);panel.hide();toggle=QPushButton('木紋方向設定 ▾');toggle.setCheckable(True);toggle.toggled.connect(panel.setVisible);self.body.insertWidget(3,toggle);self.body.insertWidget(4,panel)
            footer=self.apply_button.parentWidget()
            if footer is not self:self.outer.removeWidget(footer);self.outer.insertWidget(self.outer.indexOf(self.status),footer)
        def apply_targets(self,targets=None):
            if not hasattr(self,'finish_scope') or self.finish_scope.currentData()=='all':return super().apply_targets(targets)
            try:
                groups=list(targets) if targets is not None else ns['selection'](self.app.scene,True)
                for root in groups:
                    if any(not ns['editable'](self.app.scene,g) for g,_ in nodes(root)):raise ValueError('範圍內有鎖定或隱藏物件，未上色。')
                command=ScopedFinish(groups,self.finish_scope.currentData(),material_for(self),mode=self.mode());ns['execute'](self.app.viewport,command);self.message(f'已更新 {len(groups)} 個物件的指定部位，其他部位保留；Ctrl＋Z 可復原。');return True
            except Exception as exc:self.message(str(exc),True);return False
        def inspect_uv(self):
            rows=[]
            try:
                from core.texture import placement_of
                for root in ns['selection'](self.app.scene,True):
                    for node,role in nodes(root):
                        for face in node.mesh.faces:
                            texture=face.attrs.get('texture') or (node.material or {}).get('texture')
                            if texture:
                                placement=placement_of(texture,face.normal());angle=placement[2] if placement else texture.get('rot',0);rows.append((root.name,node.name,role,f'{angle:g}°'));break
                return ux.dialog_table(self,'材質與木紋方向',['物件','部位','分類','目前角度'],rows or [('—','範圍內沒有貼圖','—','—')])
            except Exception as exc:self.message(str(exc),True)
        def rotate_uv(self):
            try:
                groups=ns['selection'](self.app.scene,True)
                if any(not ns['editable'](self.app.scene,g) for root in groups for g,_ in nodes(root)):raise ValueError('範圍內有鎖定或隱藏物件。')
                if not self.uv_angle.value():self.message('旋轉為 0°，未新增復原紀錄。');return
                command=ScopedFinish(groups,self.finish_scope.currentData(),angle=self.uv_angle.value());ns['execute'](self.app.viewport,command);self.message(f'已旋轉 {command.changed_faces} 個面的貼圖；幾何保留，Ctrl＋Z 可復原。')
            except Exception as exc:self.message(str(exc),True)
    Paint.__name__='PaintWindow';ns['PaintWindow']=Paint
