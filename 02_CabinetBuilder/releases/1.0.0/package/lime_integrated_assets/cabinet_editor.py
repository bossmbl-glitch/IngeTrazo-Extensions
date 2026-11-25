"""Selection-aware cabinet UI. Patches are opt-in by editing, never by loading.

Single-cabinet face changes stay in the draft; multiple cabinets use one history
transaction. All geometry and placement adapters remain in the existing suite.
"""
from copy import deepcopy
from types import SimpleNamespace
import importlib.util
import json
import math
from PySide6.QtCore import Qt,QTimer,QSignalBlocker,QRectF,QPointF
from PySide6.QtGui import QPainter,QColor,QPen
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,
    QPushButton,QComboBox,QCheckBox,QLineEdit,QTabWidget,QScrollArea,QFrame,
    QProgressDialog,QApplication,QSizePolicy)


def install(ns):
    api=SimpleNamespace(**ns);release=api._release_tools;original_single=api.CabinetWindow

    def cabinet_selection(app):
        targets,skipped=ns['selection_report'](app.scene,descend=True)
        result=[]
        for group in targets:
            if ns['parametric_info'](group)['kind']=='cabinet':
                try:ns['effective_parameters'](group,'cabinet');result.append(group)
                except Exception as exc:skipped.append((group,'無法編輯：'+str(exc)))
            else:skipped.append((group,'非櫃體'))
        result.sort(key=lambda g:(g.name,str(g.uid)))
        return result,skipped

    def is_multiple(app):
        targets,skipped=cabinet_selection(app)
        has_cabinet=bool(targets) or any((getattr(g,'ext',{}) or {}).get(api.PARAM_KEY,{}).get('kind')=='cabinet' for g,_ in skipped)
        return has_cabinet and len(targets)+len(skipped)>1

    class Preview(QWidget):
        def __init__(self,window):
            super().__init__();self.window=window;self.cache_key=None;self.setMinimumSize(220,260)
            self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Expanding)
        def paintEvent(self,event):
            p=QPainter(self);p.fillRect(self.rect(),QColor('#f4f8fc'));group=self.window.preview_group
            if group is None:p.drawText(self.rect(),Qt.AlignCenter,'選取櫃體後顯示預覽');p.end();return
            if not hasattr(self,'renderer'):
                spec=importlib.util.spec_from_file_location('_lime_selection_preview',api.ROOT/'lime_integrated_assets/geometry_preview.py');self.renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.renderer)
            rigid=self.window.rigid;key=(id(group),self.width(),self.height(),tuple(rigid.data()),self.window.scene.content_version)
            if self.cache_key!=key:
                self.cache=self.renderer.render(api,group,self.width(),self.height(),rigid);self.cache_key=key
            p.drawImage(self.rect(),self.cache);p.end()

    class FaceDiagram(QWidget):
        def __init__(self,window):
            super().__init__();self.window=window;self.boxes=[];self.setMinimumSize(235,220)
            self.setToolTip('直接點選面板；輸入高度後自動固定，其餘面板等分。')
        def paintEvent(self,event):
            w=self.window;self.paint_error=None;p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.fillRect(self.rect(),QColor('#f4f8fc'));self.boxes=[]
            try:
                # Draw the last valid model, including while numeric input is invalid.
                valid_group=w.preview_group or getattr(w,'_ux_last_valid_preview',None)
                params=(valid_group.ext[api.PARAM_KEY]['params'] if valid_group is not None else w.parameters())
                data=ns['cabinet_layout'](params);params=data['params'];width,height=params['width'],params['height']
                scale=min((self.width()-50)/width,(self.height()-95)/height);left=(self.width()-width*scale)/2;top=20
                for i,(rect,field) in enumerate(data['fronts']):
                    x,z,x2,z2=rect;box=QRectF(left+x*scale,top+(height-z2)*scale,(x2-x)*scale,(z2-z)*scale);self.boxes.append((box,i))
                    selected=i==w.selected_front;p.setPen(QPen(QColor('#167bc7' if selected else '#567283'),2 if selected else 1));p.setBrush(QColor('#dceffd' if selected else '#edf2f5'));p.drawRect(box)
                    cfg=params.get('front_settings',{}).get(str(i),{});style=cfg.get('door_style',params['door_style'])
                    if style in ('cy','classical','original','plain_glass') and box.height()>65:
                        p.setBrush(Qt.NoBrush);p.setPen(QPen(QColor('#abc0cc'),1));p.drawRect(box.adjusted(9,14,-9,-14))
                    asset=cfg.get('handle_asset',params.get('handle_asset'))
                    if asset:
                        pos=cfg.get('handle_position',params.get('handle_position','center_right'));r,c=pos.split('_');px=box.center().x() if c=='center' else box.left()+15 if c=='left' else box.right()-15;py=box.top()+14 if r=='top' else box.bottom()-14 if r=='bottom' else box.center().y()
                        p.setPen(QPen(QColor('#936c35'),4));p.drawLine(int(px-9),int(py),int(px+9),int(py))
                    p.setPen(QColor('#225778' if selected else '#4b6576'));p.drawText(box.adjusted(4,3,-4,-3),Qt.AlignCenter,f'第 {i+1} 片\n{(z2-z)/10:g} cm')
                p.setPen(QColor('#58748a'));p.drawText(QRectF(0,self.height()-24,self.width(),20),Qt.AlignCenter,'正面分格｜面板實際高度')
                self.handle_dimensions=None
                if self.boxes and 0<=w.selected_front<len(data['fronts']):
                    index=w.selected_front;rect=data['fronts'][index][0];cfg=params.get('front_settings',{}).get(str(index),{});effective={**params,**cfg}
                    if effective.get('handle_asset'):
                        if cfg:
                            a,b,c,d=rect;single={**effective,'width':c-a+4,'height':d-b+4,'panels':1,'face_mode':'doors','front_settings':{},'drawer_column_heights':{},'handle_z_overrides':{}}
                            location=api.cabinet_handle_locations(ns['cabinet_layout'](single))[0];hx,hz=location[1]+a-2,location[3]+b-2
                        else:
                            location=api.cabinet_handle_locations(data)[index];hx,hz=location[1],location[3]
                        box=self.boxes[index][0];px=left+hx*scale;py=top+(height-hz)*scale;self.handle_dimensions=(hx-rect[0],hz-rect[1],effective.get('handle_stand',0))
                        p.setPen(QPen(QColor('#a87622'),1,Qt.DashLine));p.drawLine(QPointF(box.left(),py),QPointF(px,py));p.drawLine(QPointF(px,py),QPointF(px,box.bottom()));p.setBrush(QColor('#a87622'));p.drawEllipse(QPointF(px,py),3,3)
                        dx,dz,stand=self.handle_dimensions;p.drawText(QRectF(3,self.height()-72,self.width()-6,45),Qt.AlignCenter,f'把手基準：距左 {dx/10:g}／距下 {dz/10:g} cm\n離安裝面 {stand/10:g} cm')
            except Exception as exc:self.paint_error=str(exc);p.drawText(self.rect(),Qt.AlignCenter|Qt.TextWordWrap,str(exc))
            p.end()
        def mousePressEvent(self,event):
            for box,index in self.boxes:
                if box.contains(event.position()):self.window.select_front(index);break

    class SingleCabinet(original_single):
        def __init__(self,app,*args,**kwargs):
            self._front_loading=True;self._auto_front_timer=None;self._front_rejected=False;self._front_patch_keys=set();self._single_ready=False
            super().__init__(app,*args,**kwargs)
            self._single_ready=True;self._front_loading=False;self._front_edit_baseline=self.front_editor_key()
            self.settings_tabs.currentChanged.connect(self.update_front_layout);self.face_mode.currentIndexChanged.connect(self.update_front_layout)
            self.update_front_layout();self.mode_banner.setText(('正在修改 1 個櫃體｜' if self.editing else '新增櫃體｜')+'調整立即預覽，按下方建立／更新才寫入')
        def install_front_editor(self):
            self.front_editor=QWidget();self.front_editor.setStyleSheet('QPushButton:checked {background:#e3f1ff;color:#116bb0;border:1px solid #167bc7;}');main=QHBoxLayout(self.front_editor);main.setContentsMargins(0,0,0,0);main.setSpacing(18)
            diagram_box=QWidget();left=QVBoxLayout(diagram_box);left.setContentsMargins(8,6,8,6);left.addWidget(api.label('點選要修改的面板'));self.drawer_diagram=FaceDiagram(self);left.addWidget(self.drawer_diagram,1);main.addWidget(diagram_box,1)
            editor=QWidget();right=QVBoxLayout(editor);right.setContentsMargins(4,6,8,6);right.setSpacing(12);self.front_title=api.label('第 1 片面板');right.addWidget(self.front_title)
            form=QFormLayout();form.setSpacing(10);right.addLayout(form);self.front_choice=QComboBox();form.addRow('選取面板',self.front_choice)
            self.front_height=api.length_number(300,20);form.addRow('面板高度',self.front_height)
            self.fixed_height=QCheckBox(editor);self.fixed_height.hide() # Read old APIs/templates, not a user action.
            self.fixed_badge=api.label('自動等分','muted');right.addWidget(self.fixed_badge)
            right.addWidget(api.label('輸入高度即自動固定，其餘面板等分。','muted'))
            self.auto_height_button=api.button('這片改回自動等分',self.release_front_height);right.addWidget(self.auto_height_button)
            self.front_style=QComboBox();self.front_style.addItem('沿用整櫃款式',None)
            for title,key in [('素面框板','original'),('古典','classical'),('CY 鄉村','cy'),('C 把手門','kitchen_c'),('素框玻璃','plain_glass')]:self.front_style.addItem(title,key)
            form.addRow('這片款式',self.front_style)
            self.front_mode=QComboBox()
            for title,key in [('沿用整櫃把手','inherit'),('不安裝','none'),('選資料庫款式','library')]:self.front_mode.addItem(title,key)
            form.addRow('這片把手',self.front_mode);self.front_handle=api.AssetPicker(self.app,'handles',lambda:self.front_edited('handle_asset'));form.addRow('把手款式',self.front_handle)
            self.override_handle=QCheckBox(editor);self.override_handle.hide()
            self.front_position=QComboBox(editor)
            for title,key in [('沿用整櫃位置','inherit'),('上方置中','top_center'),('中央','center_center'),('下方置中','bottom_center'),('自訂','custom')]:self.front_position.addItem(title,key)
            self.front_position.hide();self.position_buttons={};position_row=QHBoxLayout()
            for title,key in [('沿用','inherit'),('上方','top_center'),('中央','center_center'),('下方','bottom_center'),('自訂','custom')]:
                b=api.button(title,lambda checked=False,k=key:self.front_position.setCurrentIndex(self.front_position.findData(k)));b.setCheckable(True);position_row.addWidget(b);self.position_buttons[key]=b
            right.addWidget(api.label('把手位置'));right.addLayout(position_row)
            self.position_details=QWidget();position_form=QFormLayout(self.position_details);position_form.setContentsMargins(0,0,0,0)
            self.custom_anchor=QComboBox()
            for title,key in [('上左','top_left'),('上中','top_center'),('上右','top_right'),('中左','center_left'),('正中','center_center'),('中右','center_right'),('下左','bottom_left'),('下中','bottom_center'),('下右','bottom_right')]:self.custom_anchor.addItem(title,key)
            position_form.addRow('定位基準',self.custom_anchor);self.front_x=api.length_number(40,0);self.front_z=api.length_number(40,0);position_form.addRow('水平邊距',self.front_x);right.addWidget(self.position_details)
            self.edge_row=QWidget();edge_layout=QHBoxLayout(self.edge_row);edge_layout.setContentsMargins(0,0,0,0);edge_layout.addWidget(api.label('距上／下緣'));edge_layout.addWidget(self.front_z,1);right.addWidget(self.edge_row)
            self.copy_front_button=api.button('把手設定套用到此欄',lambda:self.copy_front_handles(False));right.addWidget(self.copy_front_button)
            self.copy_all_button=api.button('把手設定套用到全部面板',lambda:self.copy_front_handles(True));right.addWidget(self.copy_all_button)
            self.color_details=QWidget();color_form=QFormLayout(self.color_details);color_form.setContentsMargins(0,0,0,0);self.front_color=QLineEdit();self.front_color.setPlaceholderText('沿用材質，或填 #B8B8B8');color_form.addRow('這片顏色',self.front_color)
            color_toggle=QPushButton('更多外觀設定 ▾');color_toggle.setCheckable(True);color_toggle.toggled.connect(self.color_details.setVisible);right.addWidget(color_toggle);right.addWidget(self.color_details);self.color_details.hide()
            self.front_note=api.label('','muted');right.addWidget(self.front_note);right.addStretch();self.front_scroll=QScrollArea();self.front_scroll.setWidgetResizable(True);self.front_scroll.setFrameShape(QFrame.NoFrame);self.front_scroll.setWidget(editor);main.addWidget(self.front_scroll,1)
            page=self.settings_layouts['框料／層板'];page.insertWidget(0,self.front_editor)
            self._auto_front_timer=QTimer(self);self._auto_front_timer.setSingleShot(True);self._auto_front_timer.setInterval(180);self._auto_front_timer.timeout.connect(self.flush_front_edit)
            self.front_choice.currentIndexChanged.connect(lambda *_:self.select_front(self.front_choice.currentData() or 0))
            self.front_height.valueChanged.connect(lambda *_:self.front_edited('height'))
            self.front_style.currentIndexChanged.connect(lambda *_:self.front_edited('door_style'));self.front_mode.currentIndexChanged.connect(lambda *_:self.front_edited('handle_asset'))
            self.front_position.currentIndexChanged.connect(lambda *_:self.front_edited('position'));self.custom_anchor.currentIndexChanged.connect(lambda *_:self.front_edited('position'))
            self.front_x.valueChanged.connect(lambda *_:self.front_edited('handle_x'));self.front_z.valueChanged.connect(lambda *_:self.front_edited('handle_z'));self.front_color.editingFinished.connect(lambda:self.front_edited('material'))
            self.heights.setProperty('limeHiddenByDesign',True)
            self.face_mode.currentIndexChanged.connect(lambda *_:self.settings_tabs.setCurrentIndex(1) if self.face_mode.currentData()=='drawers' else None)
        def update_front_layout(self,*_):
            if not getattr(self,'_single_ready',False):return
            active=self.face_mode.currentData()=='drawers' and self.settings_tabs.currentIndex()==1
            self.settings_preview_frame.setVisible(not active)
            if active:
                self.settings_tabs.setFixedHeight(max(330,self.height()-420));self.front_editor.setFixedHeight(max(285,self.settings_tabs.height()-45))
                if not hasattr(self,'_hidden_frame_cards'):self._hidden_frame_cards=[]
                page=self.settings_layouts['框料／層板']
                for index in range(page.count()):
                    widget=page.itemAt(index).widget()
                    if widget is not None and widget is not self.front_editor and widget not in self._hidden_frame_cards and not widget.isHidden():self._hidden_frame_cards.append(widget);widget.hide()
            else:
                self.settings_tabs.setFixedHeight(max(300,self.height()-390));self.front_editor.setMinimumHeight(0);self.front_editor.setMaximumHeight(16777215)
                for widget in getattr(self,'_hidden_frame_cards',[]):widget.show()
                self._hidden_frame_cards=[]
        def resizeEvent(self,event):
            super().resizeEvent(event)
            if getattr(self,'_single_ready',False):
                from PySide6.QtWidgets import QBoxLayout
                self.front_editor.layout().setDirection(QBoxLayout.TopToBottom if self.width()<600 else QBoxLayout.LeftToRight)
                self.update_front_layout()
        def front_editor_key(self):
            original=super().front_editor_key()
            if original is None or not hasattr(self,'front_position'):return original
            return release.state_key([original,self.front_mode.currentData(),self.front_position.currentData(),self.custom_anchor.currentData(),self.front_x.value(),self.front_z.value()])
        def sync_position_ui(self):
            pos=self.front_position.currentData();mode=self.front_mode.currentData();self.front_handle.setVisible(mode=='library')
            self.position_details.setVisible(pos=='custom');self.edge_row.setVisible(pos in ('top_center','bottom_center','custom'))
            for key,button in self.position_buttons.items():button.setChecked(key==pos)
        def select_front(self,index):
            if not hasattr(self,'front_position'):return
            if not self._front_loading and self._auto_front_timer is not None and self._auto_front_timer.isActive():
                if not self.flush_front_edit():
                    with QSignalBlocker(self.front_choice):self.front_choice.setCurrentIndex(self.front_choice.findData(self.selected_front))
                    return
            previous=self._front_loading;self._front_loading=True
            try:
                widgets=[self.front_height,self.fixed_height,self.front_style,self.front_mode,self.front_handle.combo,self.front_position,self.custom_anchor,self.front_x,self.front_z,self.front_color]
                blockers=[QSignalBlocker(w) for w in widgets]
                super().select_front(index);config=self._fronts.get(str(self.selected_front),{});p=self.parameters()
                mode='inherit' if 'handle_asset' not in config else 'none' if config['handle_asset'] is None else 'library';self.front_mode.setCurrentIndex(self.front_mode.findData(mode));self.override_handle.setChecked(mode!='inherit')
                pos=config.get('handle_position','inherit');self.front_position.setCurrentIndex(self.front_position.findData(pos) if self.front_position.findData(pos)>=0 else self.front_position.findData('custom'));self.custom_anchor.setCurrentIndex(max(0,self.custom_anchor.findData(config.get('handle_position',p['handle_position']))))
                self.front_x.setValue(config.get('handle_x',p['handle_x']));self.front_z.setValue(config.get('handle_z',p['handle_z']));del blockers
                self.front_title.setText(f'第 {self.selected_front+1} 片面板');self.fixed_badge.setText('已固定高度' if self.fixed_height.isChecked() else '自動等分');self.auto_height_button.setEnabled(self.fixed_height.isChecked());self.sync_position_ui();self._front_patch_keys.clear();self._front_edit_baseline=self.front_editor_key()
            finally:self._front_loading=previous
        def front_edited(self,key):
            if self._front_loading or not self._single_ready or self.face_mode.currentData()!='drawers':return
            self._front_patch_keys.add(key)
            if key=='height':self.fixed_height.setChecked(True);self.fixed_badge.setText('已固定高度')
            self.override_handle.setChecked(self.front_mode.currentData()!='inherit');self.sync_position_ui();self._auto_front_timer.start();self.front_note.setText('正在更新預覽…')
        def commit_front(self):
            if self._auto_front_timer is not None:self._auto_front_timer.stop()
            p=self.parameters();rows=int(p['drawer_rows']);col,row=divmod(self.selected_front,rows);raw=list(self._columns.get(str(col),p.get('drawer_heights',[])));raw=(raw+[None]*rows)[:rows]
            raw[row]=self.front_height.value()+4 if self.fixed_height.isChecked() else None;release.drawer_sizes(p['height'],rows,raw)
            config=deepcopy(self._fronts.get(str(self.selected_front),{}));style=self.front_style.currentData()
            if style is None:config.pop('door_style',None)
            else:config['door_style']=style
            mode=self.front_mode.currentData()
            effective_style=config.get('door_style',p['door_style'])
            if effective_style=='kitchen_c' and (mode!='inherit' or 'position' in self._front_patch_keys):raise ValueError('這片是內建 C 把手造型，不能另裝配件或改配件位置；請先更換門片款式。')
            if mode=='inherit':config.pop('handle_asset',None)
            elif mode=='none':config['handle_asset']=None
            else:
                asset=self.front_handle.value()
                if asset is None:raise ValueError('請選擇資料庫把手款式，或改選不安裝。')
                config['handle_asset']=asset
            pos=self.front_position.currentData()
            if 'position' in self._front_patch_keys:
                if pos=='inherit':
                    for key in ('handle_position','handle_x','handle_z'):config.pop(key,None)
                else:config['handle_position']=self.custom_anchor.currentData() if pos=='custom' else pos
            if 'handle_x' in self._front_patch_keys:config['handle_x']=self.front_x.value()
            if 'handle_z' in self._front_patch_keys:config['handle_z']=self.front_z.value()
            if self.front_color.text().strip():config['material']=self.front_color.text().strip()
            else:config.pop('material',None)
            candidate={**p,'drawer_column_heights':{**self._columns,str(col):raw},'front_settings':{**self._fronts,str(self.selected_front):config}}
            model=ns['cabinet_model'](candidate) # Validate before changing the draft.
            self._columns=candidate['drawer_column_heights'];self._fronts=candidate['front_settings'];self._front_rejected=False;self.front_note.setStyleSheet('');self.apply_button.setEnabled(True)
            self.preview();self.select_front(self.selected_front);self.front_note.setText('預覽已更新；按底部更新才寫入模型。')
            return '面板預覽已更新。'
        def flush_front_edit(self):
            if self._front_loading:return True
            try:self.commit_front();return True
            except Exception as exc:
                self._front_rejected=True;self.apply_button.setEnabled(False);self.front_note.setText(str(exc));self.front_note.setStyleSheet('color:#ad4438;');self.message(str(exc),True);return False
        def preview(self,*args,**kwargs):
            if getattr(self,'_front_rejected',False):
                self.drawer_diagram.update();return
            result=super().preview(*args,**kwargs)
            if self._single_ready:self.update_front_layout()
            return result
        def release_front_height(self):
            if self._auto_front_timer is not None:self._auto_front_timer.stop()
            self.fixed_height.setChecked(False);self.flush_front_edit()
        def copy_front_handles(self,all_panels=False):
            if not self.flush_front_edit():return
            old=deepcopy(self._fronts);config=self._fronts.get(str(self.selected_front),{});p=self.parameters();rows=int(p['drawer_rows']);count=rows*int(p['drawer_columns']);col=self.selected_front//rows
            keys=[key for key in api._release_tools.FRONT_KEYS if key.startswith('handle_')]
            for index in range(count):
                if not all_panels and index//rows!=col:continue
                target=self._fronts.setdefault(str(index),{})
                for key in keys:
                    if key in config:target[key]=deepcopy(config[key])
                    else:target.pop(key,None)
            try:ns['cabinet_model'](self.parameters())
            except Exception as exc:self._fronts=old;self.message(str(exc),True);return
            self.preview();self.select_front(self.selected_front);self.front_note.setText('已預覽：把手設定套用到'+('全部面板' if all_panels else '此欄')+'；尚未寫入模型。')
        def apply_model(self):
            if self._single_ready and (self._front_rejected or self._auto_front_timer.isActive() or self.front_editor_dirty()):
                if not self.flush_front_edit():return
            return super().apply_model()
        def read_target(self):
            if self._single_ready and not self.confirm_switch():return
            loading=self._front_loading;switching=getattr(self,'_switching',False);self._front_loading=True;self._switching=True;self._front_rejected=False
            if self._auto_front_timer is not None:self._auto_front_timer.stop()
            try:return super().read_target()
            finally:self._front_loading=loading;self._switching=switching
        def closeEvent(self,event):
            if self._auto_front_timer is not None:self._auto_front_timer.stop()
            super().closeEvent(event)
        def done(self,result):
            if self._auto_front_timer is not None:self._auto_front_timer.stop()
            super().done(result)

    class MultiCabinet(api.StyledWindow):
        def __init__(self,app):
            super().__init__(app,'參數櫃體',960,830);self.scene=app.scene;self.vp=app.viewport;self.context=self.scene.edit_group;self.preview_group=None;self.rigid=api.QMatrix4x4();self.patch={};self.busy=False;self.loading=False;self._valid=True;self._invalid_fields=set();self._closing=False
            self.setStyleSheet(self.styleSheet()+' QPushButton:checked {background:#e3f1ff;color:#116bb0;border:1px solid #167bc7;} QTabWidget::pane {border:0;} QTabBar::tab {padding:9px 14px;background:#e8eef6;border-radius:5px;margin:2px;} QTabBar::tab:selected {background:#147acc;color:white;}');self.header=api.label('參數櫃體','title');self.body.addWidget(self.header)
            top=QHBoxLayout();self.selection_label=api.label('');top.addWidget(self.selection_label,1);top.addWidget(api.button('讀取目前選取',self.read_selection));self.body.addLayout(top)
            self.details_toggle=QPushButton('查看選取清單 ▾');self.details_toggle.setCheckable(True);self.body.addWidget(self.details_toggle);self.selection_list=api.QListWidget();self.selection_list.setMaximumHeight(175);self.selection_list.hide();self.body.addWidget(self.selection_list);self.details_toggle.toggled.connect(self.selection_list.setVisible)
            self.tabs=QTabWidget();self.settings_tabs=self.tabs;self.content=QHBoxLayout();self.body.addLayout(self.content,1);self.content.addWidget(self.tabs,3)
            handle_page=QWidget();hl=QVBoxLayout(handle_page);hl.setSpacing(12);self.tabs.addTab(handle_page,'把手配件')
            hl.addWidget(api.label('選款式就預覽，不用另外勾選啟用。','muted'));self.handle_modes={};mode_row=QHBoxLayout()
            for title,key in [('不修改把手','keep'),('不安裝','none'),('選資料庫款式','library')]:
                b=api.button(title,lambda checked=False,k=key:self.choose_handle_mode(k));b.setCheckable(True);mode_row.addWidget(b);self.handle_modes[key]=b
            hl.addLayout(mode_row);self.handle_picker=api.AssetPicker(app,'handles',self.handle_changed);hl.addWidget(self.handle_picker);self.handle_picker.hide()
            hl.addWidget(api.label('安裝位置'));positions=QHBoxLayout();self.position_buttons={}
            for title,key in [('沿用各自原位置',None),('上方置中','top_center'),('中央','center_center'),('下方置中','bottom_center')]:
                b=api.button(title,lambda checked=False,k=key:self.choose_position(k));b.setCheckable(True);positions.addWidget(b);self.position_buttons[key]=b
            hl.addLayout(positions)
            form=QFormLayout();hl.addLayout(form);self.offset=QLineEdit();self.offset.setPlaceholderText('各自保留；輸入 cm 或 mm 才修改');form.addRow('離安裝面',self.offset);self.edge=QLineEdit();self.edge.setPlaceholderText('各自保留；輸入距上／下緣 cm');form.addRow('垂直邊距',self.edge)
            self.offset.editingFinished.connect(lambda:self.numeric_patch('handle_stand',self.offset));self.edge.editingFinished.connect(lambda:self.numeric_patch('handle_z',self.edge));hl.addStretch()
            size_page=QWidget();sl=QVBoxLayout(size_page);self.tabs.addTab(size_page,'尺寸與款式');sl.addWidget(api.label('留空就保留各櫃體原值，不帶入第一個櫃體的尺寸。','muted'));sf=QFormLayout();sl.addLayout(sf);self.fields={}
            for key,title in [('width','寬度'),('height','高度'),('depth','深度')]:
                field=QLineEdit();field.setPlaceholderText('各自不同／保留原值');sf.addRow(title,field);self.fields[key]=field;field.editingFinished.connect(lambda k=key,f=field:self.numeric_patch(k,f))
            self.style=QComboBox();self.style.addItem('保留各自原款式',None)
            for title,key in [('素面框板','original'),('古典','classical'),('CY 鄉村','cy'),('C 把手門','kitchen_c'),('素框玻璃','plain_glass')]:self.style.addItem(title,key)
            sf.addRow('門片款式',self.style);self.style.currentIndexChanged.connect(self.style_changed);sl.addStretch()
            preview_box=QWidget();pv=QVBoxLayout(preview_box);self.preview_caption=api.label('代表櫃體預覽');pv.addWidget(self.preview_caption);self.geometry_preview=Preview(self);pv.addWidget(self.geometry_preview,1);self.preserved=api.label('寬、高、深、款式與分格各自保留。','muted');pv.addWidget(self.preserved);self.content.addWidget(preview_box,2)
            self.summary=api.label('沒有待套用的修改。');self.body.addWidget(self.summary);self.apply_button=api.button('套用到選取櫃體',self.apply_model,'primary');self.outer.insertWidget(self.outer.count()-1,self.apply_button)
            self._preview_timer=QTimer(self);self._preview_timer.setSingleShot(True);self._preview_timer.setInterval(220);self._preview_timer.timeout.connect(self.preview)
            self._guard_timer=QTimer(self);self._guard_timer.setInterval(400);self._guard_timer.timeout.connect(self.check_selection);self.read_selection(initial=True);self._guard_timer.start()
        def confirm_switch(self):
            if self._closing or not self.patch:return True
            choice=release.ask_pending(self)
            if choice=='discard':return True
            if choice=='apply':self.apply_model();return not self.patch
            return False
        def read_selection(self,initial=False):
            if not initial and not self.confirm_switch():return
            if self.vp.scene is not self.app.scene:self.message('專案已變更，請關閉後重新開啟設定。',True);return
            self.loading=True
            try:
                self.scene=self.app.scene;self.context=self.scene.edit_group;self.targets,self.skipped=cabinet_selection(self.app);self._selection={id(g) for g in self.scene.selection};self._revision=self.scene.content_version;self.patch={};self._invalid_fields.clear();self._valid=True;self._stale=False
                self.selection_list.clear()
                for g in self.targets:
                    _,p,_=ns['effective_parameters'](g);self.selection_list.addItem(g.name+'｜'+ ' × '.join(f'{p[k]/10:g}' for k in ('width','height','depth'))+' cm｜可修改')
                for g,reason in self.skipped:self.selection_list.addItem(getattr(g,'name','非群組')+'｜略過：'+reason)
                self.selection_label.setText(f'正在修改 {len(self.targets)} 個櫃體｜'+release.report_text(self.targets,self.skipped))
                self.details_toggle.setText(f'查看選取清單（可修改 {len(self.targets)}／略過 {len(self.skipped)}） ▾')
                self.handle_mode='keep';self.handle_picker.hide();self.handle_picker.load(None);self.offset.clear();self.edge.clear()
                for key,f in self.fields.items():
                    f.clear();values=[ns['effective_parameters'](g)[1][key] for g in self.targets]
                    f.setPlaceholderText((f'原值 {values[0]/10:g} cm・保留' if values and len(set(values))==1 else '各自不同・保留原值'))
                self.style.setCurrentIndex(0)
                if self.targets:
                    first=self.targets[0];self.preview_group=first;_,_,self.rigid=ns['effective_parameters'](first);self.preview_caption.setText('代表：'+first.name+(f'｜另有 {len(self.targets)-1} 個' if len(self.targets)>1 else ''))
                else:self.preview_group=None
                self.geometry_preview.cache_key=None;self.geometry_preview.update();self.update_summary();self.message('只會修改你實際調整的欄位；預覽不改動模型。')
            finally:self.loading=False
        def set_patch(self,key,value,remove=False):
            if self.loading:return
            if remove:self.patch.pop(key,None)
            else:self.patch[key]=deepcopy(value)
            self._valid=not self._invalid_fields;self._preview_timer.start();self.update_summary()
        def choose_handle_mode(self,mode):
            if self.loading:return
            self.handle_mode=mode;self._invalid_fields.discard('handle_asset');self.handle_picker.setVisible(mode=='library')
            if mode=='keep':
                for key in list(self.patch):
                    if key.startswith('handle_'):self.patch.pop(key,None)
                self.offset.clear();self.edge.clear();self.set_patch('handle_asset',None,True)
            elif mode=='none':self.set_patch('handle_asset',None)
            else:self.handle_changed()
            self.update_summary()
        def handle_changed(self):
            if self.loading or getattr(self,'handle_mode','keep')!='library':return
            try:
                asset=self.handle_picker.value()
                if asset is None:self.patch.pop('handle_asset',None);self._invalid_fields.add('handle_asset');self._valid=False;self.message('請選擇資料庫把手款式。');self.update_summary();return
                self._invalid_fields.discard('handle_asset');self.set_patch('handle_asset',asset)
            except Exception as exc:self._valid=False;self.message(str(exc),True);self.update_summary()
        def choose_position(self,value):self.set_patch('handle_position',value,value is None)
        def numeric_patch(self,key,field):
            if self.loading:return
            if not field.text().strip():self._invalid_fields.discard(key);field.setStyleSheet('');self.set_patch(key,None,True);return
            try:
                value=release.parse_length(field.text())
                if key in ('width','height','depth') and value<=0:raise ValueError('尺寸必須大於零。')
                if key=='handle_z' and value<0:raise ValueError('邊距不能小於零。')
                self._invalid_fields.discard(key);self.set_patch(key,value);field.setStyleSheet('')
            except Exception as exc:self._invalid_fields.add(key);self._valid=False;field.setStyleSheet('border:1px solid #b84d42;');self.message(str(exc),True);self.update_summary()
        def style_changed(self,*_):self.set_patch('door_style',self.style.currentData(),self.style.currentData() is None)
        def check_selection(self):
            stale=self.app.scene is not self.scene or self.vp.scene is not self.scene or self.scene.edit_group is not self.context or {id(g) for g in self.scene.selection}!=self._selection or self.scene.content_version!=self._revision
            if stale:
                self._stale=True;self.apply_button.setEnabled(False);self.message('選取或模型已變更，請按「讀取目前選取」後再修改。',True)
            return not stale
        def update_summary(self):
            names={'width':'寬度','height':'高度','depth':'深度','door_style':'門片款式','handle_asset':'把手款式','handle_position':'把手位置','handle_stand':'離安裝面','handle_z':'垂直邊距'}
            self.summary.setText('本次只修改：'+ '、'.join(names.get(k,k) for k in self.patch)+f'｜{len(getattr(self,"targets",[]))} 個櫃體' if self.patch else '沒有待套用的修改。')
            untouched=[title for key,title in [('width','寬'),('height','高'),('depth','深'),('door_style','門片款式')] if key not in self.patch];self.preserved.setText('、'.join([*untouched,'分格'])+'保留各自原設定。')
            for mode,b in self.handle_modes.items():b.setChecked(mode==getattr(self,'handle_mode','keep'))
            for key,b in self.position_buttons.items():b.setChecked(key==self.patch.get('handle_position'))
            self.apply_button.setText(f'套用到 {len(getattr(self,"targets",[]))} 個櫃體');self.apply_button.setEnabled(bool(self.patch) and bool(getattr(self,'targets',[])) and self._valid and not getattr(self,'_stale',False))
        def representative_parameters(self):
            g=self.targets[0];_,p,rigid=ns['effective_parameters'](g);p.update(deepcopy(self.patch));hardware={k:v for k,v in self.patch.items() if k.startswith('handle_')}
            for config in p.get('front_settings',{}).values():config.update(deepcopy(hardware))
            if set(hardware)&{'handle_position','handle_x','handle_z'}:p['handle_z_overrides']={}
            return p,rigid
        def preview(self):
            if self.loading or not self.targets or not self.check_selection():return
            try:
                if not self.patch:self.preview_group=self.targets[0]
                else:
                    p,self.rigid=self.representative_parameters();model=ns['cabinet_model'](p);model.xform=api.QMatrix4x4(self.rigid);self.preview_group=api.bind_face_materials(ns['carry_parametric_paint'](self.targets[0],model))
                self._valid=not self._invalid_fields;self.geometry_preview.update();self.message('代表櫃體預覽已更新；套用時逐一驗證全部選取櫃體。')
            except Exception as exc:self._valid=False;self.message(str(exc),True)
            self.update_summary()
        def apply_model(self):
            if self.busy:return
            # Editing-finished may not have fired for programmatic/keyboard apply.
            for key,field in [*self.fields.items(),('handle_stand',self.offset),('handle_z',self.edge)]:
                if field.text().strip():self.numeric_patch(key,field)
            if not self.patch or not self._valid or not self.check_selection():return
            self._preview_timer.stop();self.busy=True;self.apply_button.setEnabled(False);progress=QProgressDialog('正在驗證選取櫃體，尚未修改…','取消',0,100,self);progress.setWindowModality(Qt.WindowModal);progress.setMinimumDuration(0)
            try:
                def update(i,n):progress.setLabelText(f'正在驗證 {i}/{n}（可取消；尚未寫入）');progress.setValue(min(99,int(100*i/max(n,1))));QApplication.processEvents()
                plan=ns['prepare_batch'](self.app,self.patch,progress=update,cancel=progress.wasCanceled,kinds={'cabinet'},all_fronts=True)
                if progress.wasCanceled():raise ValueError('已取消；所有櫃體保留原狀。')
                progress.setLabelText('正在套用（無法中途取消）');progress.setCancelButton(None);text=ns['apply_batch'](self.app,plan);self._revision=self.scene.content_version;self.patch={};self.read_selection(initial=True);self.message(text)
            except Exception as exc:self.message(str(exc),True)
            finally:progress.close();self.busy=False;self.update_summary()
        def closeEvent(self,event):
            if self.busy:event.ignore();return
            if not self.confirm_switch():event.ignore();return
            self._preview_timer.stop();self._guard_timer.stop();self.preview_group=None;self._closing=True
            try:super().closeEvent(event)
            finally:self._closing=False
        def done(self,result):
            if self.busy or not self.confirm_switch():return
            self._preview_timer.stop();self._guard_timer.stop();self.preview_group=None;super().done(result)

    class CabinetWindow(SingleCabinet):
        def __new__(cls,app,*args,**kwargs):
            if is_multiple(app):return MultiCabinet(app)
            return super().__new__(cls)

    CabinetWindow.__name__='CabinetWindow';ns['CabinetWindow']=CabinetWindow;ns['MultiCabinetWindow']=MultiCabinet;ns['SingleCabinetWindow']=SingleCabinet
    old_show=ns['show_window'];old_dispatch=ns['dispatch']
    def show(app,kind):
        if kind in ('cabinet','cabinet_edit'):
            key='_lime_suite_cabinet';window=getattr(app.window,key,None);targets,skipped=cabinet_selection(app);multiple=is_multiple(app)
            if window is not None:
                try:visible=window.isVisible()
                except RuntimeError:visible=False
                wrong=visible and isinstance(window,MultiCabinet)!=multiple
                if wrong:
                    if not window.confirm_switch():return window
                    window._switching=True;window.patch={} if isinstance(window,MultiCabinet) else getattr(window,'patch',{})
                    window.close();setattr(app.window,key,None)
                elif visible:
                    if isinstance(window,MultiCabinet):window.read_selection()
                    elif len(targets)==1 and getattr(window,'target',None) is not targets[0]:window.read_target()
        return old_show(app,kind)
    def dispatch(app,key):
        if key=='batch_edit':
            targets,_=cabinet_selection(app)
            if targets:return show(app,'cabinet')
        if key=='edit_selected':
            targets,skipped=cabinet_selection(app)
            if is_multiple(app):return show(app,'cabinet')
        return old_dispatch(app,key)
    ns['show_window']=show;ns['dispatch']=dispatch
    for adapter in (ns['_door_api'],ns['_feature_api'],ns['_window_api']):adapter.show_window=show
    ns['_cabinet_selection']=cabinet_selection
    old_toolbox=ns['ToolboxWindow']
    class Toolbox(old_toolbox):
        def __init__(self,app):
            super().__init__(app)
            for button in self.findChildren(QPushButton):
                if button.text()=='批次修改':button.hide()
    Toolbox.__name__='ToolboxWindow';ns['ToolboxWindow']=Toolbox
