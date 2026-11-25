"""Common modeling workflow, local named presets and unchanged-preview reuse."""
from copy import deepcopy
from functools import wraps
import hashlib
import json
import math
import time
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QPushButton,QWidget,QComboBox,QCheckBox,QLineEdit,QDoubleSpinBox,QSpinBox,QHBoxLayout,QInputDialog


def read_json(path,default):
    try:
        if path.stat().st_size>32*1024*1024:return deepcopy(default)
        data=json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data,dict) else deepcopy(default)
    except (OSError,ValueError):return deepcopy(default)


def preferences(api,app):return api.store_for(app).directory/'toolbox_preferences.json'
def get_preference(api,app,key,default=None):
    value=read_json(preferences(api,app),{}).get(key,default)
    return value if default is None or isinstance(value,type(default)) else deepcopy(default)
def set_preference(api,app,key,value):
    path=preferences(api,app);data=read_json(path,{});data[key]=value
    try:api._atomic_json(path,data)
    except Exception as exc:api._diagnostics.record('preferences.write','error',{'key':key},error=exc)


def controls(window):
    result={}
    for key,value in vars(window).items():
        if isinstance(value,(QComboBox,QCheckBox,QLineEdit,QDoubleSpinBox,QSpinBox)) and not key.startswith('_') and key!='template_combo':result[key]=value
    for group in ('fields','combos','louver_fields'):
        for key,value in getattr(window,group,{}).items():result[group+'.'+key]=value
    return result


def control_value(widget):
    if isinstance(widget,QComboBox):return widget.currentData()
    if isinstance(widget,QCheckBox):return widget.isChecked()
    if isinstance(widget,QLineEdit):return widget.text()
    return widget.value()


def set_control(widget,value):
    if isinstance(widget,QComboBox):
        index=widget.findData(value)
        if index<0:raise ValueError('範本包含目前不支援的選項。')
        widget.setCurrentIndex(index)
    elif isinstance(widget,QCheckBox):
        if not isinstance(value,bool):raise ValueError('範本勾選值無效。')
        widget.setChecked(value)
    elif isinstance(widget,QLineEdit):
        if not isinstance(value,str) or len(value)>10000:raise ValueError('範本文字無效。')
        widget.setText(value)
    else:
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not widget.minimum()<=value<=widget.maximum():raise ValueError('範本數值超出欄位範圍。')
        widget.setValue(value)


def install(api,classes=None):
    def wrap(base):
        class Workflow(base):
            def __init__(self,app,*args,**kwargs):
                self._workflow_ready=False;self._workflow_source=None;self._preview_fingerprint=None
                super().__init__(app,*args,**kwargs)
                self.mode_banner=api.label('');self.mode_banner.setObjectName('lime_workflow_mode');self.body.insertWidget(1,self.mode_banner)
                self.selection_hint=api.label('','muted');self.body.insertWidget(2,self.selection_hint)
                if hasattr(self,'settings_tabs'):
                    row=QHBoxLayout();self.template_combo=QComboBox();self.template_combo.setMinimumWidth(120);row.addWidget(self.template_combo,1)
                    row.addWidget(api.button('套用範本',lambda:self.run(lambda:self.load_template(self.template_combo.currentData()))))
                    row.addWidget(api.button('另存範本',self.prompt_template));row.addWidget(api.button('刪除範本',lambda:self.run(lambda:self.delete_template(self.template_combo.currentData()))));self.body.insertLayout(3,row)
                    self.refresh_templates()
                if self.kind=='door':
                    self.workflow_front=QComboBox()
                    for face in api.CUBOID_FRONTS:self.workflow_front.addItem('量體正面 '+face,face)
                    self.workflow_front.setCurrentIndex(self.workflow_front.findData('-Y'));self.workflow_front.currentIndexChanged.connect(self.update_workflow_volume)
                    row=QHBoxLayout();row.addWidget(api.button('量體轉換',self.read_volume));row.addWidget(self.workflow_front);row.addWidget(api.button('直接點選正面',self.pick_workflow_front));row.addWidget(api.button('取消轉換',self.cancel_workflow_volume));self.body.insertLayout(4,row)
                for button in self.findChildren(QPushButton):
                    if '最佳化現有把手' in button.text():button.hide()
                self._workflow_ready=True
                if self.preview_group is not None:
                    try:
                        values=self.parameters();self._preview_fingerprint=self.fingerprint(values);self._preview_values=deepcopy(values);self._preview_revision=self.scene.content_version;self._preview_rigid=list(self.rigid.data())
                    except Exception:pass
                self.refresh_workflow()
                # Existing signals still own geometry updates; this adds immediate mode/row feedback.
                for widget in controls(self).values():
                    signal=getattr(widget,'currentIndexChanged',None) or getattr(widget,'toggled',None)
                    if signal is not None:signal.connect(lambda *_,w=self:w.refresh_workflow())
            def refresh_workflow(self):
                if not self._workflow_ready:return
                source=self._workflow_source or getattr(self,'_conversion_source',None) or getattr(self,'source',None)
                placing=getattr(self,'active_picker',None) is not None and getattr(self.active_picker,'name','')=='連續放置面板'
                mode='連續放置' if placing else '量體轉換' if source is not None else '修改選取' if getattr(self,'editing',False) and getattr(self,'target',None) is not None else '新增'
                self.mode_banner.setText('目前模式：'+mode+'｜'+('選款式與高度，再按「點牆連續放置」' if self.kind=='electrical_panel' and mode=='新增' else '點牆放置，Esc 結束' if placing else '預覽確認後再按轉換' if source is not None else '預覽確認後再按更新' if mode=='修改選取' else '調整設定，預覽後建立'))
                self.mode_banner.setStyleSheet('background:#e6f3fa;padding:8px;border-radius:5px;color:#15516c;')
                items=list(self.scene.selection);kinds={str((getattr(g,'ext',{}) or {}).get(api.PARAM_KEY,{}).get('kind','')) for g in items}
                names={'cabinet':'參數櫃體','door':'門片／門組','electrical_panel':'開關／插座面板','glass':'玻璃格子門','louver':'百葉門','decorative':'造型門','window':'窗戶／窗組'}
                wrong=len(items)==1 and kinds and self.kind not in kinds and next(iter(kinds)) in names
                self.selection_hint.setVisible(bool(wrong));self.mode_banner.setToolTip('讀取修改時請選取整個群組；新增與預覽不會改動既有模型。')
                self.selection_hint.setText('選取的是'+names[next(iter(kinds))]+'；請使用對應工具的「讀取選取」。' if wrong else '讀取修改時請選取整個群組；新增與預覽不會改動既有模型。')
                if hasattr(self,'apply_button'):self.apply_button.setText('轉換選取量體' if source is not None else '更新模型' if mode=='修改選取' else '點牆連續放置' if self.kind=='electrical_panel' else '建立')
                if hasattr(self,'workflow_front'):self.workflow_front.setEnabled(self._workflow_source is not None)
                if hasattr(self,'refresh_settings'):self.refresh_settings()
                if hasattr(self,'refresh_object_status'):self.refresh_object_status()
            def fingerprint(self,values):
                data=[values,list(self.rigid.data()),self.scene.content_version,id(self.context),id(self.target),getattr(self,'floor_mm',getattr(getattr(self,'accessories',None),'floor_mm',lambda:0))()]
                return hashlib.sha256(json.dumps(data,sort_keys=True,allow_nan=False).encode()).hexdigest()
            def preview(self):
                if not getattr(self,'_workflow_ready',False):return super().preview()
                started=time.perf_counter()
                try:
                    self.check(strict=self.editing)
                    if hasattr(self,'ensure_target'):self.ensure_target()
                    if self.editing and api.selection(self.scene,True)!=[self.target]:raise ValueError('選取已變更，請重新讀取要修改的整個模型。')
                    if self._workflow_source is not None and api.selection(self.scene,True)!=[self._workflow_source]:raise ValueError('量體選取已變更，請重新讀取。')
                    if self._workflow_source is not None and api.volume_signature(self._workflow_source)!=self._workflow_signature:raise ValueError('來源量體已變更，請重新讀取。')
                    values=self.parameters();key=self.fingerprint(values)
                    if self.preview_group is not None and key==self._preview_fingerprint:
                        api._diagnostics.record('preview.reuse',details={'kind':self.kind,'elapsed_ms':(time.perf_counter()-started)*1000},throttle=1);self.refresh_workflow();return
                    super().preview()
                    self._preview_values=deepcopy(values);self._preview_revision=self.scene.content_version;self._preview_rigid=list(self.rigid.data())
                    if self.preview_group is not None and self.editing and self.target is not None and self.kind!='electrical_panel':api.bind_face_materials(api.carry_parametric_paint(self.target,self.preview_group))
                    self._preview_fingerprint=key if self.preview_group is not None else None
                    api._diagnostics.record('preview.build',details={'kind':self.kind,'elapsed_ms':(time.perf_counter()-started)*1000})
                except Exception as exc:
                    self.preview_group=None;self.preview_lines=None;self._preview_fingerprint=None
                    if hasattr(self,'clear_cache'):self.clear_cache()
                    if hasattr(self,'asset_preview'):self.asset_preview.clear()
                    if hasattr(self,'geometry_preview'):self.geometry_preview.update()
                    if self.kind=='window' and hasattr(self,'apply_button'):self.apply_button.setEnabled(False)
                    self.message(str(exc),True)
                self.refresh_workflow()
            def stop(self):
                result=super().stop()
                if getattr(self,'_workflow_ready',False):self.refresh_workflow()
                return result
            def start_place(self):
                result=super().start_place();self.refresh_workflow();return result
            def new(self):
                result=super().new();self.refresh_workflow();return result
            def read_target(self):
                if self._workflow_source is not None:self.cancel_workflow_volume()
                result=super().read_target()
                if self._workflow_ready:self.refresh_workflow()
                return result
            def apply_model(self):
                if self.kind=='electrical_panel' and not self.editing and getattr(self,'source',None) is None:
                    if getattr(self,'active_picker',None) is None:self.start_place()
                    else:self.message('繼續點牆可再放一個面板；Esc 結束。')
                    return
                if self._workflow_source is not None:
                    def apply():
                        self.stop_workflow_picker();self.check();source=self._workflow_source
                        if not self.apply_button.isEnabled():raise ValueError('量體尺寸不適用，請重新讀取或調整門框。')
                        if api.selection(self.scene,True)!=[source]:raise ValueError('選取已變更，請重新讀取量體。')
                        model=api.door_model(self.parameters());api.execute(self.vp,api.ConvertVolumeCabinet(source,model,self.rigid,self._workflow_signature))
                        self._workflow_source=None;self.target=source;self.editing=True;self.version=self.scene.content_version;self.preview_group=None;self.preview_lines=None
                        return '已轉換量體；Ctrl＋Z 可復原。'
                    self.run(apply)
                else:super().apply_model()
                if self._workflow_ready:self.refresh_workflow()
            def read_volume(self):
                if self.kind!='door':
                    result=super().read_volume()
                    if self._workflow_ready:self.refresh_workflow()
                    return result
                def read():
                    self.check();items=api.selection(self.scene,True)
                    if len(items)!=1:raise ValueError('請選取一個封閉長方體群組，再按量體轉換。')
                    data=api.detect_cabinet_volume(items[0]);self._workflow_source=items[0];self._workflow_data=data;self._workflow_signature=api.volume_signature(items[0]);self.target=None;self.editing=False
                    if not self.update_workflow_volume():raise ValueError('量體小於門框所需尺寸，請調整框料或改選較大量體。')
                    return '已讀取量體；指定正面、預覽後按轉換。'
                self.run(read);self.refresh_workflow()
            def update_workflow_volume(self,*_):
                if self._workflow_source is None:return
                try:
                    values,self.rigid=api.cabinet_volume_placement(self._workflow_data,self.workflow_front.currentData());p=self.parameters();layout=api._door_system.door_layout(api._door_api,p);p=layout['params']
                    desired=[('width',values['width']-(p['frame_left']+p['frame_right'] if p['with_frame'] else 0)),('height',values['height']-(p['frame_top']+p['frame_bottom'] if p['with_frame'] else 0))]
                    if any(not self.fields[key].minimum()<=value<=self.fields[key].maximum() for key,value in desired):raise ValueError('量體小於門框所需尺寸；請先縮小框料或改選較大量體。')
                    for key,value in desired:
                        f=self.fields[key];f.blockSignals(True);f.setValue(value);f.blockSignals(False)
                    self.apply_button.setEnabled(True);self.apply_button.setText('轉換選取量體');self.preview();return self.preview_group is not None
                except Exception as exc:self.preview_group=None;self.preview_lines=None;self.apply_button.setEnabled(False);self.message(str(exc),True);return False
            def pick_workflow_front(self):
                if self._workflow_source is None:self.message('請先讀取量體，再點選正面。',True);return
                self.stop_workflow_picker();vp=self.vp;old=vp.active_tool;saved=self.workflow_front.currentData();tool=None
                state={'committed':False}
                def stop():self.stop_workflow_picker()
                def choose(x,y,commit):
                    try:
                        self.check();o,d=vp._pixel_to_ray(x,y);face=api.volume_front_from_ray(self._workflow_data,api.xyz(o),api.xyz(d))
                        if face:self.workflow_front.setCurrentIndex(self.workflow_front.findData(face))
                        if face and commit:state['committed']=True;stop()
                        return bool(face)
                    except Exception as exc:self.message(str(exc),True);return False
                tool=api._ray_picker.RayPicker(vp,choose,stop,'點選門組量體正面');self._workflow_picker=(tool,old,saved,state);vp.set_active_tool(tool)
            def cancel_workflow_volume(self):
                was_active=self._workflow_source is not None
                self.stop_workflow_picker();self._workflow_source=None;self._preview_fingerprint=None
                if was_active:self.rigid=api.QMatrix4x4();self.preview_group=None;self.preview_lines=None;self.apply_button.setEnabled(True)
                if hasattr(self,'workflow_front'):self.workflow_front.setEnabled(False)
                if hasattr(self,'apply_button'):self.apply_button.setText('更新模型' if self.editing else '建立')
                if self._workflow_ready:self.refresh_workflow()
            def stop_workflow_picker(self):
                picker=getattr(self,'_workflow_picker',None);self._workflow_picker=None
                if picker is not None:
                    if not picker[3]['committed'] and self._workflow_source is not None:self.workflow_front.setCurrentIndex(self.workflow_front.findData(picker[2]))
                    if self.vp.active_tool is picker[0]:self.vp.set_active_tool(picker[1])
            def template_path(self):return api.store_for(self.app).directory/'toolbox_templates.json'
            def template_data(self):
                data=read_json(self.template_path(),{'schema':1,'templates':{}})
                if data.get('schema')!=1 or not isinstance(data.get('templates'),dict):return {'schema':1,'templates':{}}
                data['templates']={key:rows for key,rows in data['templates'].items() if isinstance(rows,dict)}
                return data
            def refresh_templates(self):
                selected=self.template_combo.currentData();self.template_combo.clear();self.template_combo.addItem('選擇常用範本',None)
                for name in sorted(self.template_data().get('templates',{}).get(self.kind,{})):self.template_combo.addItem(name,name)
                self.template_combo.setCurrentIndex(max(0,self.template_combo.findData(selected)))
            def save_template(self,name):
                name=str(name).strip()
                if not name or len(name)>60:raise ValueError('請輸入 1～60 字的範本名稱。')
                values=self.parameters();self.preview()
                if self.preview_group is None:raise ValueError('設定無法預覽，請先修正再儲存範本。')
                data=self.template_data();data.setdefault('schema',1);rows=data.setdefault('templates',{}).setdefault(self.kind,{})
                if name not in rows and len(rows)>=32:raise ValueError('每類最多 32 個範本，請先刪除不需要的項目。')
                rows[name]={'params':deepcopy(values),'controls':{k:control_value(v) for k,v in controls(self).items()}}
                if len(json.dumps(data,allow_nan=False).encode())>32*1024*1024:raise ValueError('範本資料過大，請減少項目。')
                api._atomic_json(self.template_path(),data);self.refresh_templates();self.template_combo.setCurrentIndex(self.template_combo.findData(name));return '已儲存範本：'+name
            def prompt_template(self):
                name,ok=QInputDialog.getText(self,'另存常用範本','名稱')
                if ok:self.run(lambda:self.save_template(name))
            def load_template(self,name):
                row=self.template_data().get('templates',{}).get(self.kind,{}).get(name)
                if not isinstance(row,dict) or not isinstance(row.get('controls'),dict) or not isinstance(row.get('params'),dict):raise ValueError('請選擇有效範本。')
                params=deepcopy(row['params']);api.validate_accessory_parameters(params)
                if self.kind=='door':api._door_system.door_layout(api._door_api,params)
                elif self.kind=='cabinet':api.cabinet_layout(params)
                elif self.kind=='decorative':api.decorative_layout(params)
                elif self.kind=='glass':api.glass_layout(params)
                elif self.kind=='louver':api.louver_layout(params)
                elif self.kind=='window':api.window_layout(params)
                widgets=controls(self);before={k:control_value(w) for k,w in widgets.items()};blockers=[QSignalBlocker(w) for w in {id(v):v for v in widgets.values()}.values()]
                try:
                    for key,value in row['controls'].items():
                        if key in widgets:set_control(widgets[key],value)
                    if self.kind in ('door','window'):self.load_values(deepcopy(row['params']))
                    elif hasattr(self,'accessories'):self.accessories.load(deepcopy(row['params']))
                except Exception:
                    for key,value in before.items():set_control(widgets[key],value)
                    raise
                finally:del blockers
                self._preview_fingerprint=None
                if hasattr(self,'sync_fields'):self.sync_fields()
                self.preview();return '範本已帶入設定；確認預覽後再建立／更新。'
            def delete_template(self,name):
                data=self.template_data();rows=data.get('templates',{}).get(self.kind,{})
                if name not in rows:raise ValueError('請先選擇要刪除的範本。')
                del rows[name];api._atomic_json(self.template_path(),data);self.refresh_templates();return '已刪除範本；模型未變更。'
            def showEvent(self,event):
                super().showEvent(event)
                if self._workflow_ready:self.refresh_workflow()
            def closeEvent(self,event):self.stop_workflow_picker();super().closeEvent(event)
            def done(self,result):self.stop_workflow_picker();super().done(result)
        Workflow.__name__=base.__name__
        return Workflow
    return tuple(wrap(c) for c in (classes if classes is not None else (api.CabinetWindow,api.DoorWindow,api.ParametricWindow,api.DecorativeWindow,api.PanelWindow)))


def prepared_model(api,window,values,builder):
    if (getattr(window,'preview_group',None) is not None and getattr(window,'_preview_values',None)==values
        and getattr(window,'_preview_revision',None)==window.scene.content_version
        and getattr(window,'_preview_rigid',None)==list(window.rigid.data())):
        # The receiving command still binds/isolates paint and validates context.
        result=api._handle_sharing.clone_placements(window.preview_group);result.xform=api.QMatrix4x4()
        api._diagnostics.record('model.reuse_for_apply',details={'kind':window.kind})
        return result
    return builder(values)
