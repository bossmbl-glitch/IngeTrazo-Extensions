"""Window assemblies: original presets, volume conversion and non-destructive drafts."""
from copy import deepcopy
import importlib.util
import numpy as np
from PySide6.QtCore import Qt,QTimer,QRectF,QSize,QEvent
from PySide6.QtGui import QMatrix4x4,QVector3D,QPainter,QColor,QPen,QIcon
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QTabWidget,QComboBox,QLineEdit,QScrollArea,QCheckBox,QDialog,QListWidget,QListWidgetItem,QListView


def install(api,system):
    class Preview(QWidget):
        def __init__(self,window):super().__init__();self.window=window;self.setMinimumSize(230,240);self.cache_key=None
        def paintEvent(self,event):
            painter=QPainter(self);painter.fillRect(self.rect(),QColor('#f5f8fb'));w=self.window;g=w.preview_group or getattr(w,'_ux_last_valid_preview',None)
            if g is None:painter.drawText(self.rect(),Qt.AlignCenter,'完成設定後顯示窗型');painter.end();return
            if not hasattr(self,'renderer'):
                spec=importlib.util.spec_from_file_location('_window_preview',api.ROOT/'lime_integrated_assets'/'geometry_preview.py');self.renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.renderer)
            key=(id(g),self.width(),self.height(),tuple(w.rigid.data()))
            if key!=self.cache_key:self.image=self.renderer.render(api,g,self.width(),self.height(),w.rigid);self.cache_key=key
            painter.drawImage(self.rect(),self.image);painter.end()
    class Section(QWidget):
        def __init__(self,window):super().__init__();self.window=window;self.setMinimumHeight(105)
        def paintEvent(self,event):
            p=self.window.fields;stand=p['stand'].value();depth=p['depth'].value();painter=QPainter(self);painter.fillRect(self.rect(),QColor('#f5f8fb'));painter.setPen(QColor('#315a70'));painter.drawText(QRectF(0,0,self.width(),24),Qt.AlignCenter,'側面｜窗框正面為安裝基準')
            scale=(self.width()-30)/(2*max(60,depth+abs(stand)));wall=self.width()/2
            painter.setPen(QPen(QColor('#83acbd'),5));painter.drawLine(int(wall),29,int(wall),77);painter.setPen(QColor('#496b80'));painter.setBrush(QColor('#b8cbd6'));painter.drawRect(QRectF(wall+(stand-depth)*scale,38,max(2,depth*scale),30));painter.setPen(QColor('#315a70'));painter.drawText(QRectF(0,79,self.width(),24),Qt.AlignCenter,f'牆內 ← 0 → 牆外｜離牆 {stand/10:+g} cm');painter.end()
    class WindowWindow(api.GeometryWindow):
        def __init__(self,app):
            super().__init__(app,'窗戶／窗組',900,850)
            self.kind='window';self.target=None;self.editing=False;self.source=None;self.source_data=None;self.signature=None;self.original_mode=False;self.original_key=None;self._reading_rigid=None;self._reading_params=None;self.rigid=QMatrix4x4();self.preview_group=None;self.fields={};self._loading=True;self._pick=None;self._gallery=None;self._loaded_preset="W01"
            saved=api._workflows.get_preference(api,app,"window_switch",{})
            self._switch_preferences={key:saved.get(key,True) if isinstance(saved.get(key,True),bool) else True for key in ("dimensions","placement","appearance")};self._switch_preferences_dirty=False;self._switch_notice_active=False
            self._refresh_timer=QTimer(self);self._refresh_timer.setSingleShot(True);self._refresh_timer.timeout.connect(self.preview)
            self.setStyleSheet(self.styleSheet()+'QTabWidget::pane {border:0;} QTabBar::tab {padding:9px 12px;background:#eaf0f5;border-radius:4px;margin:2px;} QTabBar::tab:selected {background:#147acc;color:white;}')
            mode=QHBoxLayout()
            for title,callback in [('新增',self.new),('讀取選取',self.read_target),('量體轉換',self.read_volume)]:mode.addWidget(api.button(title,callback))
            self.body.addLayout(mode);self.object_status=api.label('尚未讀取物件｜新增窗組','muted');self.object_status.setObjectName('window_read_object');self.body.addWidget(self.object_status);row=QHBoxLayout();self.body.addLayout(row);self.settings_tabs=QTabWidget();row.addWidget(self.settings_tabs,3);self.forms={}
            for title in ('基本設定','窗框／分格','玻璃／配件'):
                page=QWidget();page.setObjectName('page');scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(page);layout=QVBoxLayout(page);form=QFormLayout();layout.addLayout(form);layout.addStretch();self.settings_tabs.addTab(scroll,title);self.forms[title]=form
            basic=self.forms['基本設定'];self.preset=QComboBox()
            for key,data in system.presets(api).items():self.preset.addItem(data['name'].split('｜',1)[-1],key)
            for key,title in system.CUSTOM.items():self.preset.addItem(title,key)
            self.preset.setIconSize(QSize(48,32));self.preset.setMaxVisibleItems(9);self.preset.setMinimumContentsLength(8);self.preset.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            for i in range(self.preset.count()):self.preset.setItemIcon(i,self.preset_icon(self.preset.itemData(i)));self.preset.setItemData(i,self.preset.itemText(i),Qt.ToolTipRole)
            picker=QWidget();picker_row=QHBoxLayout(picker);picker_row.setContentsMargins(0,0,0,0);picker_row.addWidget(self.preset,1);picker_row.addWidget(api.button('看圖選窗型',self.open_preset_gallery));self.preset_defaults_button=api.button('預設',self.use_preset_defaults);self.preset_defaults_button.setToolTip('套用此窗型的預設尺寸、框料、玻璃與把手；已讀取物件保留安裝位置。');picker_row.addWidget(self.preset_defaults_button);basic.addRow('窗型款式',picker)
            keep=QWidget();keep_row=QHBoxLayout(keep);keep_row.setContentsMargins(0,0,0,0);self._keep_boxes={}
            for key,title,tip in [('dimensions','尺寸','切換時保留外框總寬、總高及框深'),('placement','位置','切換時保留窗台高度、離牆距離及旋轉角度'),('appearance','玻璃／把手','切換時保留玻璃厚度、款式及把手配件／方向')]:
                check=QCheckBox(title);check.setToolTip(tip);check.setChecked(self._switch_preferences[key]);check.toggled.connect(lambda value,k=key:self.keep_changed(k,value));keep_row.addWidget(check);self._keep_boxes[key]=check
            basic.addRow('切換時保留',keep)
            self.switch_notice=api.label('縮圖為原型比例；實際尺寸依右側預覽。','muted');basic.addRow(self.switch_notice)
            labels={'width':'外框總寬','height':'外框總高','sill':'窗台離地高度','stand':'離牆距離','angle':'旋轉角度','depth':'外框深度','left':'左框寬','right':'右框寬','top':'上框高','bottom':'下框高','mullion_width':'中直料寬','cross_width':'中橫料高','cross_position':'橫料位置（由下至上 %）','columns':'欄／扇數','rows':'上下層數','sash_width':'自訂窗扇料寬','glass_thickness':'玻璃厚度'}
            initial=system.defaults(api)
            for key,title in labels.items():
                if key in ('columns','rows'):f=api.number(initial[key],1);f.setDecimals(0);f.setMaximum(6)
                elif key in ('angle','cross_position'):f=api.number(initial[key],-360 if key=='angle' else 5);f.setMaximum(360 if key=='angle' else 95)
                else:f=api.length_number(initial[key],-500 if key=='stand' else -100000 if key=='sill' else 0)
                self.fields[key]=f;page='基本設定' if key in ('width','height','sill','stand','angle') else '玻璃／配件' if key=='glass_thickness' else '窗框／分格';self.forms[page].addRow(title,f);f.valueChanged.connect(self.changed)
            self.front=QComboBox()
            for face in api.CUBOID_FRONTS:self.front.addItem('原量體正面 '+face,face)
            self.front.setCurrentIndex(self.front.findData('-Y'));basic.addRow('量體正面',self.front);self.front.currentIndexChanged.connect(self.front_changed)
            self.front_button=api.button('直接點選量體正面',self.pick_front);basic.addRow(self.front_button);self.cancel_button=api.button('取消量體轉換',self.new);basic.addRow(self.cancel_button)
            self.column_mode=QComboBox()
            for value,title in [('original','沿用窗型比例'),('equal','各欄等分'),('custom','指定各欄淨寬')]:self.column_mode.addItem(title,value)
            self.column_widths=QLineEdit();self.column_widths.setPlaceholderText('例如 50, auto, 50，單位 cm');self.forms['窗框／分格'].addRow('欄寬配置',self.column_mode);self.forms['窗框／分格'].addRow('各欄指定淨寬',self.column_widths)
            self.type_note=api.label('','muted');self.forms['窗框／分格'].addRow(self.type_note)
            self.glass_finish=QComboBox()
            for value,title in [('original','沿用原型玻璃'),('clear','透明玻璃'),('frost','霧面玻璃')]:self.glass_finish.addItem(title,value)
            self.handle_mode=QComboBox()
            for value,title in [('original','沿用窗型把手'),('none','不放把手'),('library','選用資料庫把手')]:self.handle_mode.addItem(title,value)
            self.handle=api.AssetPicker(app,'handles',self.changed);self.opening=QComboBox()
            for value,title in [('opposed','左右對向'),('left','把手靠左'),('right','把手靠右')]:self.opening.addItem(title,value)
            accessories=self.forms['玻璃／配件'];accessories.addRow('玻璃款式',self.glass_finish);accessories.addRow('把手',self.handle_mode);accessories.addRow('資料庫把手',self.handle);accessories.addRow('自訂扇開啟方向',self.opening);accessories.addRow(api.button('管理配件資料庫',lambda:api.show_window(self.app,'profile_library')));self.outer.removeWidget(self.numeric_lock_bar);accessories.addRow(self.numeric_lock_bar)
            for combo in (self.column_mode,self.glass_finish,self.handle_mode,self.opening):combo.currentIndexChanged.connect(self.changed)
            self.column_widths.textChanged.connect(self.changed);self.preset.currentIndexChanged.connect(self.preset_changed)
            preview_box=QVBoxLayout();preview_box.addWidget(api.label('實體正面預覽'));self.geometry_preview=Preview(self);self.mount_section=Section(self);preview_box.addWidget(self.geometry_preview,1);preview_box.addWidget(self.mount_section);self.summary=api.label('','muted');preview_box.addWidget(self.summary);row.addLayout(preview_box,2)
            actions=QHBoxLayout();actions.addWidget(api.button('預覽',self.preview));self.apply_button=api.button('建立',self.apply_model,'primary');actions.addWidget(self.apply_button);self.outer.insertLayout(self.outer.count()-1,actions)
            self.load_values(initial);self._loading=False;self.sync_fields();self.update_pose();self.preview()
            items=list(self.scene.selection)
            if len(items)==1 and (items[0].ext or {}).get(api.PARAM_KEY,{}).get('kind')=='window':self.read_target()
        def load_values(self,p):
            self._loading=True
            try:
                for key,f in self.fields.items():f.blockSignals(True);f.setValue(p[key]);f.blockSignals(False)
                for key in ('preset','column_mode','glass_finish','handle_mode','opening'):
                    combo=getattr(self,key);combo.blockSignals(True);combo.setCurrentIndex(combo.findData(p[key]));combo.blockSignals(False)
                self.column_widths.blockSignals(True);self.column_widths.setText(', '.join('auto' if v is None else f'{v/10:g}' for v in p['column_widths']));self.column_widths.blockSignals(False);self.handle.load(p.get('handle_asset'))
            finally:self._loading=False
            self._loaded_preset=p['preset'];self._switch_notice_active=False;self.switch_notice.hide();self.sync_fields()
        def parameters(self):
            p={key:f.value() for key,f in self.fields.items()}
            p.update({key:getattr(self,key).currentData() for key in ('preset','column_mode','glass_finish','handle_mode','opening')});p['column_widths']=api._door_ui.parse_widths(self.column_widths.text());p['handle_asset']=self.handle.value() if p['handle_mode']=='library' else None
            return system.parameters(api,p)
        def sync_fields(self):
            self.sync_keep_controls()
            stock=system.presets(api).get(self.preset.currentData());has_cross=bool(stock['cross']) if stock else self.fields['rows'].value()>1;columns=int(self.fields['columns'].value());active=self.source is not None
            for key in ('columns','rows','sash_width'):self.fields[key].setEnabled(stock is None)
            self.fields['mullion_width'].setEnabled(bool(stock['bars']) if stock else columns>1 and self.preset.currentData()!='custom_slide')
            self.fields['cross_width'].setEnabled(has_cross);self.fields['cross_position'].setEnabled(has_cross and (stock is not None or self.fields['rows'].value()==2))
            self.column_mode.setEnabled(columns>1);self.column_widths.setEnabled(columns>1 and self.column_mode.currentData()=='custom');self.opening.setEnabled(stock is None and self.preset.currentData()!='custom_fixed');self.handle_mode.setEnabled(self.preset.currentData()!='custom_fixed');self.handle.setEnabled(self.handle_mode.isEnabled() and self.handle_mode.currentData()=='library')
            self.fields['sill'].setEnabled(not active);self.front.setEnabled(active);self.front_button.setEnabled(active);self.cancel_button.setEnabled(active)
            self.type_note.setText(f'原型：{stock["columns"]} 欄／扇、{stock["rows"]} 層；保留框料接頭。其他扇數請選自訂款。' if stock else '自訂分格；框料粗細與玻璃厚度不隨寬高拉伸。')
            for form in self.forms.values():
                for i in range(form.rowCount()):
                    item=form.itemAt(i,QFormLayout.FieldRole)
                    if item and item.widget() and item.widget() is not self.type_note:
                        widget=item.widget();form.setRowVisible(i,widget.isEnabled())
            self.forms['基本設定'].setRowVisible(self.switch_notice,self._switch_notice_active)
            if hasattr(self,'geometry_preview'):self.geometry_preview.update();self.mount_section.update()
        def changed(self,*_):
            if self._loading:return
            self.sync_fields();self.update_pose();self._refresh_timer.start(180)
        def preset_icon(self,key):return QIcon(str(api.ROOT/'lime_integrated_assets'/'window_thumbnails'/(key+'.png')))
        def keep_changed(self,key,value):self._switch_preferences[key]=bool(value);self._switch_preferences_dirty=True
        def sync_keep_controls(self):
            if not hasattr(self,'_keep_boxes'):return
            for key,check in self._keep_boxes.items():
                forced=self.source is not None and key in ('dimensions','placement');was=check.blockSignals(True);check.setChecked(True if forced else self._switch_preferences[key]);check.blockSignals(was);check.setEnabled(not forced)
        def save_switch_preferences(self):
            if self._switch_preferences_dirty:
                api._workflows.set_preference(api,self.app,'window_switch',self._switch_preferences);self._switch_preferences_dirty=False
        def preset_changed(self,*_,use_defaults=False):
            if self._loading:return
            try:
                p=system.defaults(api,self.preset.currentData());kept=[];adjustments=[]
                dimensions=self.source is not None or (not use_defaults and self._switch_preferences['dimensions'])
                placement=self.source is not None or (use_defaults and self.editing) or self._switch_preferences['placement']
                appearance=not use_defaults and self._switch_preferences['appearance']
                if dimensions:
                    for key in ('width','height','depth'):p[key]=self.fields[key].value()
                    kept.append('外尺寸')
                if placement:
                    for key in ('sill','stand','angle'):p[key]=self.fields[key].value()
                    kept.append('安裝位置')
                if appearance:
                    p.update(glass_thickness=self.fields['glass_thickness'].value(),glass_finish=self.glass_finish.currentData(),handle_mode=self.handle_mode.currentData(),opening=self.opening.currentData(),handle_asset=self.handle.value() if self.handle_mode.currentData()=='library' else None);kept.append('玻璃／把手')
                minimum=system.presets(api).get(p['preset'],{}).get('depth',40.)
                if p['depth']<minimum-.02:
                    p['depth']=minimum;adjustments.append(f'此款最小框深 {minimum/10:g} cm，已調整框深。')
                self.load_values(p);self.changed()
                message=('已套用此窗型預設。' if use_defaults else '已切換窗型。')+('保留：'+'、'.join(kept)+'。' if kept else '')+''.join(adjustments)
                if self.source is not None:message+='尺寸與位置跟隨已讀取量體。'
                self._switch_notice_active=True;self.switch_notice.setText(message);self.forms['基本設定'].setRowVisible(self.switch_notice,True);self.refresh_object_status()
            except Exception as exc:
                was=self.preset.blockSignals(True);self.preset.setCurrentIndex(self.preset.findData(self._loaded_preset));self.preset.blockSignals(was);self.message('未切換窗型：'+str(exc),True)
        def use_preset_defaults(self):self.preset_changed(use_defaults=True)
        def open_preset_gallery(self):
            if self._gallery is not None:
                listing=self._gallery.preset_list
                for i in range(listing.count()):
                    if listing.item(i).data(Qt.UserRole)==self.preset.currentData():listing.setCurrentRow(i);break
                self._gallery.show();self._gallery.raise_();return self._gallery
            dialog=QDialog(self);dialog.setWindowTitle('看圖選窗型');dialog.setStyleSheet(self.styleSheet());dialog.resize(740,640);dialog.setMinimumSize(460,420);layout=QVBoxLayout(dialog)
            layout.addWidget(api.label('窗型圖庫','title'));layout.addWidget(api.label('縮圖為原型比例；選用後依「切換時保留」選項帶入設定。','muted'))
            listing=QListWidget();listing.setViewMode(QListView.IconMode);listing.setMovement(QListView.Static);listing.setResizeMode(QListView.Adjust);listing.setIconSize(QSize(200,120));listing.setGridSize(QSize(225,174));listing.setWordWrap(True);layout.addWidget(listing);dialog.preset_list=listing
            for i in range(self.preset.count()):
                key=self.preset.itemData(i);item=QListWidgetItem(self.preset_icon(key),self.preset.itemText(i));item.setData(Qt.UserRole,key);listing.addItem(item)
                row=system.presets(api).get(key);item.setToolTip((f'原型 {row["width"]/10:g} × {row["height"]/10:g} cm；{row["columns"]} 欄／扇、{row["rows"]} 層' if row else '自訂欄數、層數與尺寸'))
                if key==self.preset.currentData():listing.setCurrentItem(item)
            def choose():
                item=listing.currentItem()
                if item is None:return
                self.preset.setCurrentIndex(self.preset.findData(item.data(Qt.UserRole)));dialog.accept()
            buttons=QHBoxLayout();buttons.addWidget(api.button('取消',dialog.reject));dialog.choose_button=api.button('選用此窗型',choose,'primary');buttons.addWidget(dialog.choose_button);layout.addLayout(buttons);listing.itemDoubleClicked.connect(lambda *_:choose())
            self._gallery=dialog;dialog.show();return dialog
        def close_preset_gallery(self):
            dialog=self._gallery;self._gallery=None
            if dialog is not None:dialog.close();dialog.deleteLater()
        def refresh_object_status(self):
            if not hasattr(self,'object_status'):return
            target=self.source if self.source is not None else self.target if self.editing else None
            self.object_status.setVisible(target is not None)
            if target is None:self.object_status.setText('尚未讀取物件｜新增窗組，不會修改目前選取。');self.object_status.setStyleSheet('');return
            kind='長方體量體' if self.source is not None else '原始窗型' if self.original_mode else '參數窗組'
            key=self.original_key if self.original_mode else (target.ext or {}).get(api.PARAM_KEY,{}).get('params',{}).get('preset','')
            current=list(self.scene.selection);stale=len(current)!=1 or current[0] is not target or self.scene.edit_group is not self.context or self.app.viewport is not self.vp or self.vp.scene is not self.scene or self.scene.content_version!=self.version
            text='已讀取：'+(target.name or '未命名物件')+'｜'+kind+('（'+system.CUSTOM.get(key,key)+'）' if key else '')
            if stale:text+='｜選取或場景已變更，請重新讀取。'
            self.object_status.setText(text);self.object_status.setToolTip(text);self.object_status.setStyleSheet('color:#b6483d;' if stale else '')
        def event(self,event):
            result=super().event(event)
            if event.type()==QEvent.WindowActivate and not getattr(self,'_loading',True):self.refresh_object_status()
            return result
        def floor_mm(self):return float(self.app.document_data(default={}).get('floor_m',0))*1000
        def update_pose(self):
            if self.source is not None:
                _,rigid=api.cabinet_volume_placement(self.source_data,self.front.currentData());move=QMatrix4x4();move.translate(0,-self.fields['stand'].value()/1000,0);move.rotate(self.fields['angle'].value(),0,0,1);self.rigid=rigid*move
            elif self.editing and self._reading_rigid is not None:
                old=self._reading_params;world=QMatrix4x4();world.translate(0,0,(self.fields['sill'].value()-old['sill'])/1000);move=QMatrix4x4();move.translate(0,-(self.fields['stand'].value()-old['stand'])/1000,0);move.rotate(self.fields['angle'].value()-old['angle'],0,0,1);self.rigid=world*self._reading_rigid*move
            else:
                self.rigid=QMatrix4x4();self.rigid.translate(0,0,(self.floor_mm()+self.fields['sill'].value())/1000);self.rigid.rotate(self.fields['angle'].value(),0,0,1);self.rigid.translate(0,-self.fields['stand'].value()/1000,0)
        def ensure_target(self):
            if self.source is not None or self.original_mode:
                source=self.source if self.source is not None else self.target
                if api.selection(self.scene,True)!=[source]:raise ValueError('選取已變更，請重新讀取完整窗組或量體。')
                if api.volume_signature(source)!=self.signature:raise ValueError('來源模型已變更，請重新讀取。')
            elif self.editing and api.selection(self.scene,True)!=[self.target]:raise ValueError('選取已變更，請重新讀取窗組。')
        def preview(self):
            try:
                self.check(strict=self.editing);self.ensure_target();p=self.parameters();self.update_pose();group=system.model(api,p)
                if self.original_mode and self.original_key==p['preset']:
                    for child in group.children:
                        if child.ext.get('lime_window_part')=='glass' and p['glass_finish']!='original':continue
                        child.mesh=api.transformed_mesh(child.mesh,QMatrix4x4())
                        for face,index in zip(child.mesh.faces,child.ext.get('lime_window_source_faces',[])):face.attrs=deepcopy(self.target.mesh.faces[index].attrs)
                    api.bind_face_materials(group)
                group.xform=QMatrix4x4(self.rigid);self.preview_group=group;self.preview_lines=api.parametric_preview(group);self.version=self.scene.content_version;lay=system.layout(api,p)
                self.summary.setText(f'{p["width"]/10:g} × {p["height"]/10:g} cm｜{lay["panes"]} 片玻璃｜框深 {p["depth"]/10:g} cm');self.apply_button.setEnabled(True);self.message('預覽完成；確認尺寸、正面與窗台高度後建立／更新。')
            except Exception as exc:self.preview_group=None;self.preview_lines=None;self.apply_button.setEnabled(False);self.message(str(exc),True)
            self.geometry_preview.update();self.mount_section.update();self.vp.update()
        def new(self):
            self.stop_front();self.source=None;self.target=None;self.editing=False;self.original_mode=False;self._reading_rigid=None;self._reading_params=None;self.context=self.scene.edit_group;self.version=self.scene.content_version;self.update_pose();self.sync_fields();self.preview()
        def read_target(self):
            def read():
                self.check();self.stop_front();items=api.selection(self.scene,True)
                if len(items)!=1:raise ValueError('請選取一個完整窗組。')
                target=items[0];is_original=False;original_key=None;signature=None
                if (target.ext or {}).get(api.PARAM_KEY,{}).get('kind')=='window':_,p,rigid=api.effective_parameters(target,'window')
                else:
                    match=system.recognize(api,target)
                    if match is None:raise ValueError('不是本工具窗組或提供檔案中的原始窗型；新增請選窗型，或讀取長方體轉換。')
                    key,rigid=match;p=system.defaults(api,key)
                    a=np.asarray(rigid.data()).reshape(4,4).T;scale=np.linalg.norm(a[:3,:3],axis=0)
                    if np.any(scale<1e-6):raise ValueError('窗組不可縮放至零。')
                    if not np.isclose(scale[1],1.,atol=1e-6):raise ValueError('原始窗戶深度已被縮放，請先還原深度縮放；框深可在參數窗組內調整。')
                    unit=a[:3,:3]/scale
                    if not np.allclose(unit.T@unit,np.eye(3),atol=1e-5):raise ValueError('原始窗戶含斜切變形，請先還原。')
                    p['width']*=float(scale[0]);p['height']*=float(scale[2]);a[:3,:3]=unit;rigid=QMatrix4x4(*map(float,a.flatten()));is_original=True;original_key=key;signature=api.volume_signature(target)
                system.layout(api,p);self.source=None;self.original_mode=is_original;self.original_key=original_key;self.signature=signature
                self.target=target;self.editing=True;self.version=self.scene.content_version;p['sill']=float(rigid.map(QVector3D()).z())*1000-self.floor_mm();self._reading_params=deepcopy(p);self._reading_rigid=QMatrix4x4(rigid);self.rigid=QMatrix4x4(rigid);self.load_values(p);self.preview()
                if self.preview_group is None:raise ValueError(self.status.text())
                return '已讀取窗型；按更新才寫入，原位置與物件識別保留。'
            self.run(read)
        def read_volume(self):
            def read():
                self.check();self.stop_front();items=api.selection(self.scene,True)
                if len(items)!=1:raise ValueError('請選取一個封閉長方體量體。')
                data=api.detect_cabinet_volume(items[0]);self.source=items[0];self.source_data=data;self.signature=api.volume_signature(self.source);self.target=None;self.editing=False;self.original_mode=False;self._reading_rigid=None;self.front_changed()
                if self.preview_group is None:raise ValueError(self.status.text())
                return '已讀取量體；選正面、預覽後按轉換，原量體尚未修改。'
            self.run(read)
        def front_changed(self,*_):
            if self.source is None:return
            values,rigid=api.cabinet_volume_placement(self.source_data,self.front.currentData())
            for key in ('width','height','depth'):
                value=values[key]
                if key=='depth':value=max(value,system.presets(api).get(self.preset.currentData(),{}).get('depth',20))
                f=self.fields[key];f.blockSignals(True);f.setValue(value);f.blockSignals(False)
            self.fields['sill'].blockSignals(True);self.fields['sill'].setValue(max(0,float(rigid.map(QVector3D()).z())*1000-self.floor_mm()));self.fields['sill'].blockSignals(False);self.sync_fields();self.update_pose();self.preview()
        def pick_front(self):
            if self.source is None:self.message('請先讀取量體。',True);return
            self.stop_front();old=self.front.currentData();previous=self.vp.active_tool;state={'committed':False};tool=None
            def cancel():self.stop_front()
            def choose(x,y,commit):
                try:
                    self.check();self.ensure_target();o,d=self.vp._pixel_to_ray(x,y);face=api.volume_front_from_ray(self.source_data,api.xyz(o),api.xyz(d))
                    if face:self.front.setCurrentIndex(self.front.findData(face))
                    if face and commit:state['committed']=True;self.stop_front()
                    return bool(face)
                except Exception as exc:self.message(str(exc),True);return False
            tool=api._ray_picker.RayPicker(self.vp,choose,cancel,'點選窗組量體正面');self._pick=(tool,previous,old,state);self.vp.set_active_tool(tool);self.vp.setFocus()
        def stop_front(self):
            pick=self._pick;self._pick=None
            if pick:
                if not pick[3]['committed']:self.front.setCurrentIndex(self.front.findData(pick[2]))
                if self.vp.active_tool is pick[0]:self.vp.set_active_tool(pick[1])
        def apply_model(self):
            def apply():
                self.stop_front();self.check(strict=self.editing);self.ensure_target();self.update_pose();p=self.parameters();group=api._workflows.prepared_model(api,self,p,lambda p:system.model(api,p))
                if self.editing and self.target is not None:group.name=self.target.name
                if self.source is not None or self.original_mode:
                    target=self.source if self.source is not None else self.target;api.execute(self.vp,api.ConvertVolumeCabinet(target,group,self.rigid,self.signature));self.target=target
                elif self.editing:api.execute(self.vp,api.UpdateParametric(self.target,group,self.rigid))
                else:group.xform=QMatrix4x4(self.rigid);api.execute(self.vp,api.AddGenerated(group));self.target=group
                self.source=None;self.original_mode=False;self.editing=True;self.version=self.scene.content_version;self.preview_group=None;self.preview_lines=None;self.read_target();return '已套用窗組；Ctrl＋Z 可復原。'
            self.run(apply)
        def closeEvent(self,event):self.save_switch_preferences();self.close_preset_gallery();self._refresh_timer.stop();self.stop_front();super().closeEvent(event)
        def done(self,result):self.save_switch_preferences();self.close_preset_gallery();self._refresh_timer.stop();self.stop_front();super().done(result)
    return WindowWindow
