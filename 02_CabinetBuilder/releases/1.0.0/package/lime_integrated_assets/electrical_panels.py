"""Parametric switches/outlets, safe volume replacement and repeated wall placement."""
from copy import deepcopy
import math
import time
import json
import numpy as np
from PySide6.QtCore import Qt,QTimer,QRectF
from PySide6.QtGui import QMatrix4x4,QVector3D,QPainter,QColor,QPen
from PySide6.QtWidgets import QFormLayout,QHBoxLayout,QComboBox,QInputDialog,QFileDialog,QWidget,QVBoxLayout,QToolButton

DEFAULTS={'width':86.,'height':86.,'depth':8.,'preset':'library','asset':None,'center_height':1200.,'stand':0.,'angle':0.}


def parameters(api,values=None):
    p={**deepcopy(DEFAULTS),**{k:v for k,v in (values or {}).items() if k in DEFAULTS}}
    if p['preset']=='library':
        if not p.get('asset'):raise ValueError('請先選擇資料庫面板；可從下方收錄或匯入模型。')
        api.handle_bytes(p['asset'])
        p['width'],p['depth'],p['height']=map(float,p['asset']['size_mm'])
    for key in ('width','height','depth','center_height','stand','angle'):
        p[key]=float(p[key])
        if not math.isfinite(p[key]):raise ValueError('面板尺寸與位置須為有限數值。')
    if not 10<=p['width']<=2000 or not 10<=p['height']<=2000 or not 1<=p['depth']<=500:raise ValueError('面板寬高須為 1～200 cm，厚度為 0.1～50 cm。')
    if p['center_height']<0 or not -500<=p['stand']<=500:raise ValueError('離地高度不得為負，離牆距離為 −50～50 cm。')
    if p['preset'] not in ('switch','outlet','library'):raise ValueError('面板款式無效。')
    return p


def model(api,values=None):
    p=parameters(api,values);w,h,d=p['width'],p['height'],p['depth'];root=api.Group(name='開關面板' if p['preset']=='switch' else '插座面板' if p['preset']=='outlet' else p['asset']['name']);children=[]
    if p['preset']=='library':
        asset=api.handle_from_snapshot(p['asset']);lo,hi=api.bounds([asset]);span=hi-lo
        if min(span)<1e-7:raise ValueError('面板模型須有寬、深、高，請先補成實體模型。')
        matrix=QMatrix4x4()
        if p['asset'].get('mount')!='custom':matrix.translate(float(-(lo[0]+hi[0])/2),float(-hi[1]),float(-(lo[2]+hi[2])/2))
        asset.xform=matrix*(asset.xform if asset.xform is not None else QMatrix4x4());asset.ext={**(asset.ext or {}),'lime_handle_id':p['asset']['id']};children.append(asset)
    else:
        plate=api.recessed_solid([-w/2,-h/2,w/2,h/2],[],[],0,0,d*.75,'面板底座');plate.xform=QMatrix4x4();plate.xform.translate(0,-d*.75/1000,0);children.append(plate)
        if p['preset']=='switch':rectangles=[('開關按鍵',[-w*.30,-h*.32,w*.30,h*.32])]
        else:rectangles=[('插孔左',[-w*.18,-h*.16,-w*.12,h*.16]),('插孔右',[w*.12,-h*.16,w*.18,h*.16])]
        for name,rect in rectangles:
            part=api.recessed_solid(rect,[],[],0,0,d*.25,name);part.xform=QMatrix4x4();part.xform.translate(0,-d/1000,0)
            if p['preset']=='outlet':part.material={'color':[.12,.14,.16]}
            children.append(part)
    for child in children:child.ext['lime_panel_local']=list((child.xform if child.xform is not None else QMatrix4x4()).data())
    root.adopt(children);root.ext={api.PARAM_KEY:{'kind':'electrical_panel','version':2,'params':p},'lime_handle_id':'panel:'+p['preset']}
    return api.bind_face_materials(root)


def local_basis(root):
    if not root.children:return QMatrix4x4()
    child=root.children[0];data=child.ext.get('lime_panel_local')
    if not data:return QMatrix4x4()
    old=QMatrix4x4(*np.asarray(data).reshape(4,4).T.flatten().tolist());inverse,ok=old.inverted()
    if not ok:raise ValueError('面板定位不可逆。')
    return (child.xform if child.xform is not None else QMatrix4x4())*inverse


def volume_pose(api,data,front,stand=0.,angle=0.):
    values,rigid=api.cabinet_volume_placement(data,front)
    move=QMatrix4x4();move.translate(values['width']/2000,-float(stand)/1000,values['height']/2000);move.rotate(-float(angle),0,1,0)
    return values,rigid*move


def contains(face,point):
    for tri in face.triangulate():
        a,b,c=[np.asarray(v.toTuple(),float) for v in tri];u=b-a;v=c-a;r=point-a
        det=float(u@u*(v@v)-(u@v)**2)
        if abs(det)<1e-18:continue
        s=float((r@u*(v@v)-r@v*(u@v))/det);t=float((r@v*(u@u)-r@u*(u@v))/det)
        if s>=-1e-6 and t>=-1e-6 and s+t<=1+1e-6:return True
    return False


class WallSurface:
    """World-space wall triangles, prepared once per face/pose/content revision."""
    def __init__(self,api,face,placement):
        matrix=placement.xform if placement is not None and placement.xform is not None else QMatrix4x4()
        mesh=api.Mesh();copy=mesh.add_face([matrix.map(v) for v in face.vertices],[[matrix.map(v.position) for v in ring] for ring in face.hole_loops] or None)
        self.normal=api.xyz(copy.normal());self.normal/=max(np.linalg.norm(self.normal),1e-12)
        self.start=api.xyz(copy.vertices[0])
        triangles=np.asarray([[v.toTuple() for v in tri] for tri in copy.triangulate()],float).reshape(-1,3,3)
        self.a=triangles[:,0];self.u=triangles[:,1]-self.a;self.v=triangles[:,2]-self.a
        self.uu=np.einsum('ij,ij->i',self.u,self.u);self.vv=np.einsum('ij,ij->i',self.v,self.v);self.uv=np.einsum('ij,ij->i',self.u,self.v)
        self.det=self.uu*self.vv-self.uv**2;self.valid=np.abs(self.det)>=1e-18
    def contains(self,point):
        r=point-self.a;ru=np.einsum('ij,ij->i',r,self.u);rv=np.einsum('ij,ij->i',r,self.v)
        det=np.where(self.valid,self.det,1.)
        s=(ru*self.vv-rv*self.uv)/det;t=(rv*self.uu-ru*self.uv)/det
        return bool(np.any(self.valid & (s>=-1e-6) & (t>=-1e-6) & (s+t<=1+1e-6)))


def wall_pose(api,face,placement,origin,direction,height,floor,stand=0.,angle=0.,surface=None):
    surface=surface if surface is not None else WallSurface(api,face,placement)
    normal=surface.normal.copy();ray=api.xyz(direction);eye=api.xyz(origin);den=float(ray@normal)
    if abs(den)<1e-9:raise ValueError('視線與牆面平行，請轉動視角。')
    if abs(normal[2])>1e-4:raise ValueError('直接放置請點垂直牆面；其他方向可使用量體轉換。')
    if den>0:normal=-normal;den=-den
    distance=float((surface.start-eye)@normal/den)
    if distance<0:raise ValueError('牆面位於視線後方。')
    point=eye+distance*ray;point[2]=(floor+height)/1000
    if not surface.contains(point):raise ValueError('指定離地高度不在這個牆面範圍內，請調整高度。')
    depth=-normal;up=np.array([0.,0.,1.]);right=np.cross(depth,up);point+=normal*stand/1000
    a=np.eye(4);a[:3,:3]=np.stack([right,depth,up],axis=1);a[:3,3]=point
    result=QMatrix4x4(*map(float,a.flatten()));turn=QMatrix4x4();turn.rotate(-angle,0,1,0)
    return result*turn


def move_lines(lines,rigid):
    matrix=np.asarray(rigid.data(),float).reshape(4,4).T
    return lines @ matrix[:3,:3].T + matrix[:3,3]


def install(api,ray_picker):
    class PreparedPanelPlacement(api.AddGenerated):
        # Only accepts our already bound, immutable shared template clones.
        # Keep AddGenerated's edit-context, lock, selection and undo semantics.
        def __init__(self,group):
            self.group=group;self.owner=None;self.before_selection=None
    class MountSection(QWidget):
        def __init__(self,window):
            super().__init__();self.window=window;self.setMinimumWidth(135);self.setMaximumWidth(170);self.setMinimumHeight(200)
        def paintEvent(self,event):
            painter=QPainter(self);painter.fillRect(self.rect(),QColor('#f4f7fa'));painter.setPen(QColor('#315a70'))
            painter.drawText(QRectF(0,4,self.width(),24),Qt.AlignCenter,'側面定位示意')
            stand=self.window.fields['stand'].value();depth=(getattr(self.window,'_placement_p',None) or {}).get('depth',8.)
            span=max(30.,abs(stand)+depth);scale=(self.width()-30)/(span*2);wall=self.width()/2;top=55.;height=max(30.,self.height()-125)
            painter.setPen(QPen(QColor('#7398aa'),5));painter.drawLine(int(wall),int(top-10),int(wall),int(top+height+10))
            painter.setPen(QColor('#9a7131'));painter.setBrush(QColor('#d6ba81'));painter.drawRect(QRectF(wall+stand*scale,top,max(2.,depth*scale),height))
            painter.setPen(QColor('#315a70'));painter.drawText(QRectF(0,29,self.width(),20),Qt.AlignCenter,'牆內 ← 0 → 牆外')
            painter.drawText(QRectF(0,self.height()-59,self.width(),24),Qt.AlignCenter,f'{stand/10:+g} cm')
            painter.drawText(QRectF(5,self.height()-35,self.width()-10,30),Qt.AlignCenter|Qt.TextWordWrap,'背側安裝基準');painter.end()
    class PanelWindow(api.GeometryWindow):
        def __init__(self,app):
            super().__init__(app,'開關／插座面板',620,790)
            self.kind='electrical_panel';self.fields={};self.target=None;self.editing=False;self.source=None;self.source_data=None;self.signature=None;self.rigid=QMatrix4x4();self.active_picker=None;self.preview_group=None
            self._template=None;self._geometry_key=None;self._local_lines=None;self._placement_p=None;self._surface_key=None;self._surface=None;self._last_wall_pixel=None;self._placement_history=[]
            self.store=api.AccessoryStore(api.store_for(app).directory/'electrical_panels')
            self.preferences_path=api.store_for(app).directory/'panel_placement_settings.json'
            self._remembered_stand=0.;self._preferences_dirty=False;self._asset_stands={}
            try:
                saved=json.loads(self.preferences_path.read_text(encoding='utf-8'))
                if saved.get('schema')==2:
                    for key,value in saved.get('asset_stands_mm',{}).items():
                        value=float(value)
                        if math.isfinite(value) and -500<=value<=500:self._asset_stands[str(key)]=value
                # Version 1 was a global value: never assign it to every asset.
            except (OSError,ValueError,TypeError,KeyError,AttributeError):pass
            self._preferences_timer=QTimer(self);self._preferences_timer.setSingleShot(True);self._preferences_timer.timeout.connect(self.flush_preferences)
            self.body.addWidget(api.label('選資料庫模型，沿用原尺寸；量體只決定安裝位置及正面。直接放置預設中心離地 120 cm。','muted'))
            row=QHBoxLayout();row.addWidget(api.button('新增',self.new));row.addWidget(api.button('讀取選取',self.read_target));row.addWidget(api.button('量體轉換',self.read_volume));self.body.addLayout(row)
            c=api.card(self.body);form=QFormLayout();c.addLayout(form);self.preset=QComboBox();form.addRow('面板款式',self.preset);self.refresh_assets()
            self.placement_form=form;self.legacy_parameters={}
            for key,title in [('center_height','中心離地高度'),('stand','離牆距離'),('angle','旋轉角度')]:
                field=api.number(DEFAULTS[key]) if key=='angle' else api.length_number(self._remembered_stand if key=='stand' else DEFAULTS[key],-500 if key=='stand' else 0);self.fields[key]=field;form.addRow(title,field);field.valueChanged.connect(self.changed)
            self.fields['stand'].setMaximum(500);self.fields['stand'].setToolTip('正值往牆外移，負值嵌入牆內；會自動記住下次使用的距離。')
            self.fields['stand'].valueChanged.connect(self.remember_stand)
            form.addRow(api.label('離牆距離：正值往外、負值嵌入；每款面板分開記憶。','muted'))
            self.front=QComboBox()
            for key in api.CUBOID_FRONTS:self.front.addItem('原模型正面 '+key,key)
            self.front.setCurrentIndex(self.front.findData('-Y'));self.volume_controls=QWidget();vf=QFormLayout(self.volume_controls);vf.addRow('量體正面',self.front);vf.addRow(api.button('直接點選量體正面',self.start_front));c.addWidget(self.volume_controls);self.volume_controls.hide()
            self.asset_preview=api.AssetPreview();self.mount_section=MountSection(self);preview_row=QHBoxLayout();preview_row.addWidget(self.asset_preview,2);preview_row.addWidget(self.mount_section,1);c.addLayout(preview_row)
            self.library_button=api.button('管理面板資料庫',self.open_library);self.library_button.setFixedWidth(140)
            taken=form.takeRow(self.preset);picker_row=QWidget();picker_layout=QHBoxLayout(picker_row);picker_layout.setContentsMargins(0,0,0,0);picker_layout.addWidget(self.preset,1);picker_layout.addWidget(self.library_button);form.insertRow(0,taken.labelItem.widget(),picker_row)
            self.capture_controls=QWidget(self);self.capture_controls.hide();self.capture_front=QComboBox(self.capture_controls)
            for key in api.CUBOID_FRONTS:self.capture_front.addItem('原模型正面 '+key,key)
            self.capture_front.setCurrentIndex(self.capture_front.findData('-Y'));self._library_dialog=None
            c=api.card(self.body);c.addWidget(api.label('量體轉換：以所選正面中心定位，面板不隨量體縮放；預覽後按套用。','muted'));c.addWidget(api.button('點牆連續放置',self.start_place,'primary'));c.addWidget(api.button('結束點選／取消轉換',self.new))
            self.summary=api.label('');self.body.addWidget(self.summary);self.body.addStretch()
            row=QHBoxLayout();row.addWidget(api.button('預覽',self.preview));self.apply_button=api.button('點牆連續放置',self.apply_model,'primary');row.addWidget(self.apply_button);self.outer.insertLayout(self.outer.count()-1,row)
            self.preset.currentIndexChanged.connect(self.preset_changed);self.front.currentIndexChanged.connect(self.change_front)
            self.preview()
            items=list(self.scene.selection)
            if len(items)==1 and (getattr(items[0],'ext',{}) or {}).get(api.PARAM_KEY,{}).get('kind')=='electrical_panel':self.read_target()
        def remember_stand(self,value):
            key=self.preset.currentData()
            if key is None:return
            self._remembered_stand=float(value);self._asset_stands[str(key)]=float(value);self._preferences_dirty=True;self._preferences_timer.start(250)
        def flush_preferences(self):
            self._preferences_timer.stop()
            if not self._preferences_dirty:return True
            try:
                api._atomic_json(self.preferences_path,{'schema':2,'asset_stands_mm':self._asset_stands});self._preferences_dirty=False;return True
            except Exception as exc:
                api._diagnostics.record('panel.preferences','error',error=exc);self.message('離牆距離可使用，但設定儲存失敗：'+str(exc),True);return False
        def refresh_assets(self):
            current=self.preset.currentData();self.preset.blockSignals(True);self.preset.clear();self.preset.addItem('請選擇資料庫面板',None)
            for row in self.store.load()['handles']:self.preset.addItem(row['name'],row['id'])
            pinned=getattr(self,'saved_asset',None) if self.target is not None else None
            if pinned and self.preset.findData(pinned['id'])<0:self.preset.addItem(pinned['name']+'（模型快照）',pinned['id'])
            self.preset.setCurrentIndex(max(0,self.preset.findData(current)));self.preset.blockSignals(False)
            if current!=self.preset.currentData() and 'stand' in self.fields:self.restore_asset_stand()
        def open_library(self):
            self.stop();window=api.show_window(self.app,'profile_library');window.kind.setCurrentIndex(window.kind.findData('panels'));window.search.clear()
            if self._library_dialog is not window:
                self.detach_library();self._library_dialog=window;window.records_changed.connect(self.library_changed)
            return window
        def detach_library(self):
            if self._library_dialog is not None:
                try:self._library_dialog.records_changed.disconnect(self.library_changed)
                except (RuntimeError,TypeError):pass
                self._library_dialog=None
        def library_changed(self):
            self.refresh_assets()
            self.preview()
        def select_asset(self,snapshot):
            self.saved_asset=deepcopy(snapshot);self.refresh_assets();key=snapshot['id']
            if self.preset.findData(key)<0:self.preset.addItem(snapshot['name']+'（模型快照）',key)
            self.preset.setCurrentIndex(self.preset.findData(key));self.preview()
        def showEvent(self,event):
            self.refresh_assets();super().showEvent(event)
        def parameters(self):
            key=self.preset.currentData();p={**self.legacy_parameters,**{k:f.value() for k,f in self.fields.items()}}
            if key in ('switch','outlet'):p['preset']=key
            else:
                row=next((r for r in self.store.load()['handles'] if r['id']==key),None)
                p['preset']='library';p['asset']=self.store.snapshot_handle(row) if row else deepcopy(getattr(self,'saved_asset',None)) if key is not None else None
            return parameters(api,p)
        def restore_asset_stand(self):
            self._remembered_stand=self._asset_stands.get(str(self.preset.currentData()),0.)
            field=self.fields['stand'];field.blockSignals(True);field.setValue(self._remembered_stand);field.blockSignals(False)
        def preset_changed(self,*_):
            if not getattr(self,'_reading_target',False):self.restore_asset_stand()
            self.changed()
        def changed(self,*_):
            if self.source is not None:self.update_volume_pose()
            self.preview()
            if self.active_picker is not None and self.active_picker.name=='連續放置面板' and self._last_wall_pixel is not None:self.active_picker.choose(*self._last_wall_pixel,False)
        def update_volume_pose(self):
            _,self.rigid=volume_pose(api,self.source_data,self.front.currentData(),self.fields['stand'].value(),self.fields['angle'].value())
        def prepare(self):
            p=self.parameters()
            key={k:v for k,v in p.items() if k not in ('center_height','stand','angle')}
            if self._template is None or key!=self._geometry_key:
                template=model(api,p)
                lines=api.parametric_preview(template,limit=1200)
                self.asset_preview.installation(api._handle_sharing.clone_placements(template))
                self._template=template;self._geometry_key=deepcopy(key);self._local_lines=lines
            self.asset_preview.set_installation_offset(p['stand']);self._placement_p=p;self.mount_section.update()
            return self._template
        def clear_cache(self):
            self._template=None;self._geometry_key=None;self._local_lines=None;self._placement_p=None;self._surface_key=None;self._surface=None
        def preview(self):
            self.sync_state()
            if self.preset.currentData() is None:
                self.clear_cache();self.preview_group=None;self.preview_lines=None;self.asset_preview.clear();self.summary.setText('先選擇資料庫模型；尚無模型時請按「管理面板資料庫」收錄或匯入。');self.message('面板沿用資料庫原尺寸，不需要輸入寬、高、厚。');self.vp.update();return
            try:
                self.check();template=self.prepare();group=api._handle_sharing.clone_placements(template);group.ext[api.PARAM_KEY]['params']=deepcopy(self._placement_p);group.xform=QMatrix4x4(self.rigid);self.preview_group=group
                self.preview_lines=move_lines(self._local_lines,self.rigid) if self.active_picker is None else None;self.version=self.scene.content_version
                self.summary.setText('量體轉換：以所選正面中心定位，保持資料庫原尺寸' if self.source else '修改選取面板，保持資料庫原尺寸' if self.target else '直接放置：中心離地高度可調，模型不縮放')
                self.message('預覽完成；按套用或點牆連續放置。')
            except Exception as exc:self.clear_cache();self.preview_group=None;self.preview_lines=None;self.asset_preview.clear();self.message(str(exc),True)
            self.vp.update()
        def sync_state(self):
            active=self.source is not None;self.volume_controls.setVisible(active)
            field=self.fields['center_height'];field.setEnabled(not active);self.placement_form.setRowVisible(getattr(field,'_lock_wrapper',None) or field,not active)
            if not active:
                field.show()
                if hasattr(field,'lock_button'):field.lock_button.show()
        def stop(self):
            tool=self.active_picker;self.active_picker=None
            if tool is not None:
                self.preview_lines=None;self.vp.update()
                if self.vp.active_tool is tool:self.vp.set_active_tool(self.previous_tool)
        def new(self):
            self.flush_preferences();self.stop();self.source=None;self.source_data=None;self.signature=None;self.target=None;self.editing=False;self.rigid=QMatrix4x4()
            for field in self.fields.values():field.setEnabled(True)
            self.fields['center_height'].blockSignals(True);self.fields['center_height'].setValue(1200.);self.fields['center_height'].blockSignals(False)
            self.fields['stand'].blockSignals(True);self.fields['stand'].setValue(self._asset_stands.get(str(self.preset.currentData()),0.));self.fields['stand'].blockSignals(False)
            self.preview()
        def read_volume(self):
            def read():
                self.check();self.stop();items=api.selection(self.scene,True)
                if len(items)!=1:raise ValueError('請選取一個封閉長方體量體。')
                self.source_data=api.detect_cabinet_volume(items[0]);self.source=items[0];self.signature=api.volume_signature(self.source);self.target=None;self.editing=False
                self.change_front();return '已讀取量體位置；選資料庫模型、指定正面，按套用才轉換，模型不縮放。'
            self.run(read)
        def change_front(self,*_):
            if self.source is not None:self.update_volume_pose()
            self.preview()
        def start_front(self):
            def start():
                self.check()
                if self.source is None:raise ValueError('請先讀取量體。')
                original=self.front.currentData()
                def cancel():
                    if self.active_picker is None:return
                    self.stop();self.front.setCurrentIndex(self.front.findData(original))
                def choose(x,y,commit):
                    try:
                        self.check();o,d=self.vp._pixel_to_ray(x,y)
                        face=api.volume_front_from_ray(self.source_data,api.xyz(o),api.xyz(d)) if o is not None and d is not None else None
                        if face:self.front.setCurrentIndex(self.front.findData(face))
                        if face and commit:self.stop();self.message('已確認量體正面。')
                        return bool(face)
                    except Exception as exc:self.message(str(exc),True);cancel();return False
                self.stop();self.previous_tool=self.vp.active_tool;self.active_picker=ray_picker.RayPicker(self.vp,choose,cancel,'點選面板量體正面');self.vp.set_active_tool(self.active_picker);self.vp.setFocus();return '點量體表面確認正面；Esc 取消。'
            self.run(start)
        def read_target(self):
            def read():
                self.check();self.stop();items=api.selection(self.scene,True)
                if len(items)!=1:raise ValueError('請選取一個本工具建立的面板。')
                kind,p,self.rigid=api.effective_parameters(items[0],'electrical_panel');self.target=items[0];self.source=None;self.editing=True;self.saved_parameters=deepcopy(p)
                for key,field in self.fields.items():field.blockSignals(True);field.setEnabled(True);field.setValue(p[key]);field.blockSignals(False)
                if p['preset']=='library':
                    self.saved_asset=p['asset'];key=p['asset']['id']
                    if self.preset.findData(key)<0:self.preset.addItem(p['asset']['name']+'（模型快照）',key)
                else:
                    key=p['preset']
                    if self.preset.findData(key)<0:self.preset.addItem('舊版內建面板（保留）',key)
                    self.legacy_parameters=deepcopy(p)
                floor=float(self.app.document_data(default={}).get('floor_m',0))*1000;field=self.fields['center_height'];field.blockSignals(True);field.setValue(float(self.rigid.map(QVector3D()).z())*1000-floor);field.blockSignals(False);self.read_height=field.value()
                self._reading_target=True
                try:self.preset.setCurrentIndex(self.preset.findData(key))
                finally:self._reading_target=False
                self._remembered_stand=self._asset_stands.get(str(key),0.);self.preview();return '已讀取面板；使用資料庫原尺寸，可調整位置後套用。'
            self.run(read)
        def apply_model(self):
            def apply():
                self.stop();self.check();group=model(api,self.parameters())
                if self.source is not None:
                    if api.selection(self.scene,True)!=[self.source]:raise ValueError('選取已變更，請重新讀取量體。')
                    api.execute(self.vp,api.ConvertVolumeCabinet(self.source,group,self.rigid,self.signature));self.target=self.source;self.source=None
                elif self.target is not None:
                    if api.selection(self.scene,True)!=[self.target]:raise ValueError('選取已變更，請重新讀取面板。')
                    _,_,rigid=api.effective_parameters(self.target)
                    before=self.saved_parameters
                    world=QMatrix4x4();world.translate(0,0,(self.fields['center_height'].value()-self.read_height)/1000)
                    local=QMatrix4x4();local.translate(0,-(self.fields['stand'].value()-before.get('stand',0))/1000,0);local.rotate(-(self.fields['angle'].value()-before.get('angle',0)),0,1,0)
                    rigid=world*rigid*local;api.execute(self.vp,api.UpdateParametric(self.target,group,rigid))
                else:raise ValueError('請先讀取量體，或按「點牆連續放置」。')
                self.editing=True;self.version=self.scene.content_version;self.preview_lines=None;self.read_target()
                return '已套用；Ctrl+Z 可復原。'
            self.run(apply)
        def start_place(self):
            def start():
                self.check();self.stop()
                if self.source is not None or self.target is not None:self.new()
                self.flush_preferences();self.prepare();self._placement_history=[];self._last_wall_pixel=None;self._surface_key=None;self._surface=None;self.previous_tool=self.vp.active_tool
                def choose(x,y,commit):
                    try:
                        self._last_wall_pixel=(x,y);self.check();pick_started=time.perf_counter();face,placement=self.vp.pick_face_placement(x,y)
                        api._diagnostics.record('selection.face',details={'elapsed_ms':(time.perf_counter()-pick_started)*1000},throttle=1)
                        if face is None:raise ValueError('請直接點牆面。')
                        if placement is not None and not api.editable(self.scene,placement):raise ValueError('牆面已鎖定／隱藏。')
                        o,d=self.vp._pixel_to_ray(x,y);p=self._placement_p
                        if self._template is None or p is None:raise ValueError('請先選擇有效的資料庫面板。')
                        floor=float(self.app.document_data(default={}).get('floor_m',0))*1000
                        matrix=placement.xform if placement is not None and placement.xform is not None else QMatrix4x4()
                        surface_key=(face,tuple(matrix.data()),self.scene.content_version)
                        if self._surface_key!=surface_key:
                            self._surface=WallSurface(api,face,placement);self._surface_key=surface_key
                        rigid=wall_pose(api,face,placement,o,d,p['center_height'],floor,p['stand'],p['angle'],self._surface)
                        self.preview_lines=move_lines(self._local_lines,rigid);self.version=self.scene.content_version
                        if commit:
                            group=api._handle_sharing.clone_placements(self._template);group.ext[api.PARAM_KEY]['params']=deepcopy(p);group.xform=rigid
                            placement_command=PreparedPanelPlacement(group);api.execute(self.vp,placement_command);self._placement_history.append(placement_command);self.version=self.scene.content_version
                            self.message(f'本次已放置 {len(self._placement_history)} 個；繼續點牆，Backspace 復原最後一個，Esc 結束。')
                        self.vp.update();return True
                    except Exception as exc:
                        self.preview_lines=None;self.vp.update()
                        if commit:self.message(str(exc),True)
                        return False
                self.active_picker=ray_picker.RayPicker(self.vp,choose,self.stop,'連續放置面板',consume_hover=True,hover_interval_ms=33);self.active_picker.undo_placement=self.undo_last_placement;self.vp.set_active_tool(self.active_picker);self.vp.setFocus();return '點垂直牆面連續放置；使用目前地板標高與面板中心高度，Esc 結束。'
            self.run(start)
        def undo_last_placement(self):
            if not self._placement_history: self.message('本次尚未放置面板。');return False
            command=self._placement_history[-1]
            if self.vp.scene is not self.scene or not self.vp.history.undo_stack or self.vp.history.undo_stack[-1] is not command:
                self.message('期間有其他操作，未復原其他物件；請使用主程式復原。',True);return False
            if not self.vp.history.undo():self.message('無法復原最後放置，請匯出診斷包。',True);return False
            self._placement_history.pop();self.version=self.scene.content_version;self._surface_key=None;self.vp.notify_scene_changed();self.message(f'本次已放置 {len(self._placement_history)} 個；可繼續點牆。');return True
        def save_asset(self,group,name):
            row=self.store.add_handle(name,group,self.capture_front.currentData(),'center');self.refresh_assets();self.preset.setCurrentIndex(self.preset.findData(row['id']));return row
        def capture(self):
            def save():
                self.check();items=api.selection(self.scene,True)
                if len(items)!=1:raise ValueError('請選取一個面板模型。')
                name,ok=QInputDialog.getText(self,'收錄面板','名稱',text=items[0].name or '新面板')
                if not ok:return '已取消。'
                self.save_asset(items[0],name);return '已收錄面板；可重複選用及共用資料。'
            self.run(save)
        def import_asset(self):
            path,_=QFileDialog.getOpenFileName(self,'匯入面板','','模型 (*.igz *.skp)')
            if path:self.run(lambda:(self.save_asset(api.read_asset_group(path),api.Path(path).stem),'已匯入面板。')[1])
        def closeEvent(self,event):self.flush_preferences();self.detach_library();self.stop();self.clear_cache();super().closeEvent(event)
        def done(self,result):self.flush_preferences();self.detach_library();self.stop();self.clear_cache();super().done(result)
    return PanelWindow
