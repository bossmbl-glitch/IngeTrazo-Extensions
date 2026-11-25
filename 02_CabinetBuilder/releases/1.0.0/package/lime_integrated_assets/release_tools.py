"""1.0 candidate workflows. Geometry uses IngeTrazo 0.6.0, not SketchUp APIs.

All scene writes go through history. Preparation, previews and cancellation are
read-only. Saved optional fields default to the legacy behavior.
"""
from copy import deepcopy
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import math
import re
import time
from PySide6.QtCore import Qt, QTimer, QEvent, QObject, QSignalBlocker
from PySide6.QtGui import QValidator, QPainter, QColor, QPen
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QComboBox, QCheckBox, QPushButton, QMessageBox, QProgressDialog,
    QScrollArea, QTabWidget, QListWidgetItem, QLineEdit, QFileDialog)

SCHEMA = 2
KINDS = {'cabinet','door','glass','louver','decorative','window','electrical_panel','composite'}
FRONT_KEYS = {'door_style','handle_asset','handle_position','handle_x','handle_z',
              'handle_angle','handle_stand','handle_count','handle_spacing','handle_pair'}
BATCH_KEYS = {'cabinet': FRONT_KEYS | {'width','height','depth','panels','face_mode','drawer_rows','drawer_columns'},
    'door': {'handle_asset','width','height','leaf_type','frame_style'},
    'window': {'handle_asset','width','height','glass_finish'},
    'decorative': {'handle_asset','width','height'}, 'glass': {'handle_asset','width','height'},
    'louver': {'handle_asset','width','height','angle','target_gap'},
    'electrical_panel': {'stand','rotation','height'}, 'composite': {'width','height','depth'}}


def drawer_sizes(total, count, raw):
    if not isinstance(raw,list) or len(raw)>count:
        raise ValueError('抽屜指定高度數量不可超過格數。')
    values=[raw[i] if i<len(raw) else None for i in range(count)]
    if any(v is not None and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<24) for v in values):
        raise ValueError('抽屜面板高度至少 2 cm，另需保留上下各 0.2 cm 邊縫。')
    free=values.count(None);remaining=total-sum(v for v in values if v is not None)
    if not free and abs(remaining)>.01:raise ValueError('指定高度總和與櫃高不符，請解除至少一格固定。')
    result=[remaining/free if v is None else v for v in values]
    if min(result)<24:raise ValueError('剩餘抽屜面板空間不足，請減少固定高度或格數。')
    return result


def parse_length(text, default_unit='cm'):
    match=re.fullmatch(r'\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(mm|cm|m|毫米|公分|公尺)?\s*',str(text).lower())
    if not match:raise ValueError('請輸入有效長度，例如 12.5 cm 或 125 mm。')
    value=float(match[1])*{'mm':1,'毫米':1,'cm':10,'公分':10,'m':1000,'公尺':1000}[match[2] or default_unit]
    if not math.isfinite(value):raise ValueError('尺寸須為有限數值。')
    return value


def state_key(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False)


def hierarchy(scene):
    """Bounded walk with ancestor state, including hidden groups (not placements())."""
    result={};stack=[(g,[]) for g in scene.groups]
    while stack:
        group,parents=stack.pop()
        if id(group) in result:continue
        if len(result)>50000 or len(parents)>64:raise ValueError('模型群組層級或數量超過安全範圍。')
        result[id(group)]=(group,parents)
        stack.extend((child,[*parents,group]) for child in group.children or ())
    return result


def skip_reason(api,scene,group,parents):
    for node in [*parents,group]:
        visible,locked=scene._layer_state(node)
        if getattr(node,'locked',False) or locked:return '鎖定'
        if getattr(node,'hidden',False) or not visible or not scene.entity_visible(node):return '隱藏'
    if not isinstance(group,api.Group):return '非群組'
    info=(group.ext or {}).get(api.PARAM_KEY)
    if not isinstance(info,dict) or info.get('kind') not in KINDS:return '非參數物件'
    if not isinstance(info.get('params'),dict):return '參數資料不完整'
    if info.get('schema_version',1)>SCHEMA:return '較新版本資料，請更新工具箱'
    return None


def selection_report(api,scene,items=None,descend=False):
    tree=hierarchy(scene);items=list(scene.selection) if items is None else list(items)
    targets=[];skipped=[];seen=set()
    def visit(item):
        if id(item) in seen:return
        seen.add(id(item));entry=tree.get(id(item))
        if entry is None:skipped.append((item,'不在目前模型'));return
        group,parents=entry;reason=skip_reason(api,scene,group,parents)
        if descend and reason=='非參數物件' and group.children:
            for child in group.children:visit(child)
        elif reason:skipped.append((group,reason))
        else:targets.append(group)
    for item in items:visit(item)
    # Never rebuild both a parametric parent and one of its parts in one operation.
    identities={id(g) for g in targets}
    targets=[g for g in targets if not any(id(p) in identities for p in tree[id(g)][1])]
    return targets,skipped


def report_text(targets,skipped,complete=False):
    counts={}
    for _,reason in skipped:counts[reason]=counts.get(reason,0)+1
    return (f'已修改 {len(targets)} 個' if complete else f'將修改 {len(targets)} 個')+('；略過 '+ '、'.join(f'{k} {v} 個' for k,v in counts.items()) if skipped else '；無略過物件')+'。'


def ask_pending(window):
    box=QMessageBox(window);box.setWindowTitle('尚未套用的修改');box.setText('目前設定尚未套用。')
    apply=box.addButton('套用',QMessageBox.AcceptRole);discard=box.addButton('捨棄',QMessageBox.DestructiveRole)
    box.addButton('繼續編輯',QMessageBox.RejectRole);box.exec()
    return 'discard' if box.clickedButton() is discard else 'apply' if box.clickedButton() is apply else 'continue'


def install(ns):
    api=SimpleNamespace(**ns)
    # Backward-compatible schema; do not silently rewrite older objects on read.
    old_info=ns['parametric_info']
    def info(group,kind=None):
        value=old_info(group,kind)
        if not isinstance(value.get('params'),dict):raise ValueError('物件參數資料不完整；請匯出診斷包，原物件保留。')
        if value.get('schema_version',1)>SCHEMA:raise ValueError('物件由較新版本建立，請先更新工具箱。')
        return value
    ns['parametric_info']=info;api.parametric_info=info
    for target in (ns['_door_api'],ns['_feature_api'],ns['_window_api']):target.parametric_info=info

    old_layout=ns['cabinet_layout'];old_model=ns['cabinet_model']
    def layout(values=None):
        p=deepcopy(values or {});columns=p.get('drawer_column_heights',{});settings=p.get('front_settings',{})
        if not isinstance(columns,dict) or len(columns)>20:raise ValueError('抽屜各欄高度資料無效。')
        count=int(p.get('drawer_columns',1));rows=int(p.get('drawer_rows',3))
        for key,value in columns.items():
            if not isinstance(key,str) or not key.isdigit() or not 0<=int(key)<count:raise ValueError('抽屜欄識別無效，請重新設定分格。')
            drawer_sizes(float(p.get('height',1800)),rows,value)
        if not isinstance(settings,dict) or len(settings)>100:raise ValueError('面板獨立設定無效。')
        result=old_layout(p);n=len(result['fronts'])
        for key,config in settings.items():
            if not isinstance(key,str) or not key.isdigit() or not 0<=int(key)<n or not isinstance(config,dict) or set(config)-FRONT_KEYS-{'material'}:raise ValueError('面板獨立設定與目前格數不符，請清除或重新指定。')
            if 'material' in config:
                color=QColor(config['material'])
                if not color.isValid():raise ValueError('面板顏色格式無效。')
        result['params'].update(drawer_column_heights=columns,front_settings=settings)
        return result
    def stamp(group):
        data=(group.ext or {}).get(api.PARAM_KEY)
        if data is not None:data.update(schema_version=SCHEMA,tool_version=api.VERSION)
        return group
    def cabinet(values=None):
        data=layout(values);p=data['params'];root=old_model(p)
        front_by_index={i:g for i,g in enumerate(g for g in root.children or () if (g.ext or {}).get('lime_cabinet_part')=='front')}
        for i,g in front_by_index.items():g.ext['lime_front_slot']=str(i)
        # Per-front overrides generate a canonical one-front model and keep its
        # front/accessory parts only. The original body/part coordinates remain.
        for text,config in p['front_settings'].items():
            if not config:continue
            index=int(text);rect=data['fronts'][index][0];r0,z0,r1,z1=rect
            single={**deepcopy(p),**{k:v for k,v in config.items() if k!='material'},'front_settings':{},'drawer_column_heights':{},
                'width':r1-r0+4,'height':z1-z0+4,'panels':1,'face_mode':'doors','handle_z_overrides':{}}
            model=old_model(single);new=[]
            for part in model.children or ():
                if (part.ext or {}).get('lime_cabinet_part')=='body':continue
                move=api.QMatrix4x4();move.translate((r0-2)/1000,0,(z0-2)/1000)
                part.xform=move*(part.xform if part.xform is not None else api.QMatrix4x4())
                if (part.ext or {}).get('lime_cabinet_part')=='front':
                    placement=part.xform;part.mesh=api.transformed_mesh(part.mesh,placement)
                    for child in part.children or ():
                        child.xform=placement*(child.xform if child.xform is not None else api.QMatrix4x4())
                    part.xform=api.QMatrix4x4()
                if (part.ext or {}).get('lime_cabinet_part')=='front' and 'material' in config:
                    color=QColor(config['material']);attrs={'color':[color.redF(),color.greenF(),color.blueF()]}
                    for node in api.walk_groups(part):
                        node.mesh=api.transformed_mesh(node.mesh,api.QMatrix4x4())
                        for face in node.mesh.faces:face.attrs={**(face.attrs or {}),**deepcopy(attrs),'back':deepcopy(attrs)}
                    part.ext['lime_explicit_color']=config['material']
                part.ext={**(part.ext or {}),'lime_front_index':index};new.append(part)
            # Generated names have stable 1-based two-digit suffixes.
            suffix=f'{index+1:02d}';children=[]
            for part in root.children or ():
                if (part.ext or {}).get('lime_cabinet_part')=='front':
                    remove=part is front_by_index[index]
                else:remove=part.name.endswith(suffix) and part.name.startswith(('把手 ','內框線板 '))
                if not remove:children.append(part)
            for part in new:
                part.name=part.name.replace('01',suffix,1)
                if (part.ext or {}).get('lime_cabinet_part')=='front':part.ext['lime_front_slot']=str(index)
            root.adopt([*children,*new])
        root.ext[api.PARAM_KEY]['params']=deepcopy(p)
        return stamp(api.bind_face_materials(root))
    ns['cabinet_layout']=layout;ns['cabinet_model']=cabinet;api.cabinet_layout=layout;api.cabinet_model=cabinet
    ns['_feature_api'].cabinet_layout=layout
    # Keep layout order (not replacement child order) stable for material matching.
    old_paint=ns['carry_parametric_paint']
    def carry(old,new):
        if (old.ext or {}).get(api.PARAM_KEY,{}).get('kind')=='cabinet':
            fronts=[g for g in old.children or () if (g.ext or {}).get('lime_cabinet_part')=='front']
            if any('lime_front_slot' not in (g.ext or {}) for g in fronts):
                old=api._handle_sharing.clone_placements(old)
                for i,g in enumerate(g for g in old.children or () if (g.ext or {}).get('lime_cabinet_part')=='front'):g.ext['lime_front_slot']=str(i)
        explicit={id(g):[(node,deepcopy([f.attrs for f in node.mesh.faces])) for node in api.walk_groups(g)] for g in new.children or () if (g.ext or {}).get('lime_explicit_color')}
        result=old_paint(old,new)
        for parts in explicit.values():
            for node,attrs in parts:
                for face,paint in zip(node.mesh.faces,attrs):face.attrs=paint
        return result
    ns['carry_parametric_paint']=carry;api.carry_parametric_paint=carry
    for adapter in (ns['_door_api'],ns['_feature_api'],ns['_window_api']):adapter.carry_parametric_paint=carry
    for name in ('door_model','glass_model','louver_model','decorative_model','window_model','electrical_model','composite_model'):
        previous=ns[name]
        def factory(values=None,_previous=previous):return stamp(_previous(values))
        ns[name]=factory;setattr(api,name,factory)
    ns['_window_api'].window_model=ns['window_model'];ns['_door_api'].door_model=ns['door_model']

    def build(kind,params):
        if kind=='cabinet':return cabinet(params)
        return ns[kind+'_model'](params)
    ns['build_parametric']=build;api.build_parametric=build

    class BatchUpdate(api.Command):
        def __init__(self,plans):self.plans=plans;self.done=[]
        def do(self,scene):
            self.done=[]
            try:
                for command in self.plans:command.do(scene);self.done.append(command)
            except Exception:
                for command in reversed(self.done):command.undo(scene)
                self.done=[];raise
        def undo(self,scene):
            for command in reversed(self.done):command.undo(scene)
    ns['BatchUpdate']=BatchUpdate

    def prepare_batch(app,patch=None,material=None,refresh_assets=False,progress=None,cancel=None,kinds=None,all_fronts=False,targets_override=None):
        patch=deepcopy(patch or {});targets,skipped=selection_report(api,app.scene,descend=True)
        if kinds is not None:
            skipped.extend((g,'非櫃體') for g in targets if info(g)['kind'] not in kinds)
            targets=[g for g in targets if info(g)['kind'] in kinds]
            checked=[]
            for g in targets:
                try:ns['effective_parameters'](g);checked.append(g)
                except Exception as exc:skipped.append((g,'無法編輯：'+str(exc)))
            targets=checked
        if targets_override is not None:
            wanted={id(g) for g in targets_override};skipped.extend((g,'使用者排除') for g in targets if id(g) not in wanted);targets=[g for g in targets if id(g) in wanted]
        if not patch and material is None and not refresh_assets:raise ValueError('請指定至少一個要修改的欄位。')
        if material is not None and not QColor(material).isValid():raise ValueError('材質顏色格式無效。')
        commands=[];details=[];unchanged=[];revision=app.scene.content_version;context=app.scene.edit_group
        library=ns['unified_store_for'](app) if refresh_assets else None
        catalog=library.load() if library else None
        for index,g in enumerate(targets):
            if cancel and cancel():raise ValueError('已取消；所有物件保留原狀。')
            kind,p,rigid=ns['effective_parameters'](g);original_parameters=deepcopy(p);invalid=set(patch)-BATCH_KEYS[kind]
            if invalid:raise ValueError(g.name+'：不支援欄位 '+', '.join(sorted(invalid))+'；整批未套用。')
            p.update(deepcopy(patch))
            if all_fronts and kind=='cabinet':
                if 'door_style' in patch:
                    for config in p.get('front_settings',{}).values():
                        if 'door_style' in config:config['door_style']=patch['door_style']
                hardware={key:deepcopy(value) for key,value in patch.items() if key.startswith('handle_')}
                if hardware:
                    if p.get('face_mode')=='open':raise ValueError(g.name+'：開放櫃沒有可修改的門片或抽屜把手。')
                    for index in range(len(ns['cabinet_layout'](p)['fronts'])):
                        style=p.get('front_settings',{}).get(str(index),{}).get('door_style',p['door_style'])
                        if style=='kitchen_c':raise ValueError(g.name+f'：第 {index+1} 片是內建 C 把手造型，不能以配件設定替換或移除；請先調整門片款式。')
                    for config in p.get('front_settings',{}).values():
                        for key,value in hardware.items():
                            if key in config:config[key]=deepcopy(value)
                    if set(hardware)&{'handle_position','handle_x','handle_z'}:p['handle_z_overrides']={}
            if refresh_assets:
                changed=False
                def refresh(value):
                    nonlocal changed
                    if isinstance(value,dict):
                        if 'points_mm' in value and 'id' in value:
                            row=next((r for r in catalog['profiles'] if r['id']==value['id']),None)
                            if row is None:raise ValueError(g.name+'：資料庫找不到原剖面，保留內嵌版本。')
                            return api.profile_snapshot(row)
                        if 'blob_b64' in value and 'sha256' in value and 'id' in value:
                            category='panels' if kind=='electrical_panel' else 'handles'
                            row=next((r for r in catalog[category] if r['id']==value['id']),None)
                            if row is None:raise ValueError(g.name+'：資料庫找不到原配件，模型保留內嵌版本。')
                            snapshot=library.snapshot_handle(row,category);changed|=snapshot!=value;return snapshot
                        return {k:refresh(v) for k,v in value.items()}
                    if isinstance(value,list):return [refresh(v) for v in value]
                    return value
                p=refresh(p)
            if p==original_parameters and material is None:
                skipped.append((g,'設定相同（未重建）'));unchanged.append(g);continue
            try:model=build(kind,p)
            except Exception as exc:raise ValueError(g.name+'：'+str(exc)) from exc
            if all_fronts:model.name=g.name # Editing a property must not rename the user's cabinet.
            command=ns['UpdateParametric'](g,model,rigid)
            if material is not None:
                color=QColor(material);attrs={'color':[color.redF(),color.greenF(),color.blueF()]}
                hardware=set()
                for part in api.walk_groups(command.replacement):
                    if (part.ext or {}).get('lime_handle_id') or (part.ext or {}).get('lime_kitchen_part')=='c_handle':hardware.update(id(g) for g in api.walk_groups(part))
                for node in api.walk_groups(command.replacement):
                    # Color the structure/front, preserve transparent glass and hardware.
                    if '玻璃' in node.name or id(node) in hardware:continue
                    node.mesh=api.transformed_mesh(node.mesh,api.QMatrix4x4());node.material=None
                    for face in node.mesh.faces:face.attrs={**(face.attrs or {}),**deepcopy(attrs),'back':deepcopy(attrs)}
            commands.append(command);details.append({'uid':g.uid,'name':g.name,'kind':kind,'changed':sorted(patch),'material':material,'refresh_assets':refresh_assets})
            if progress:progress(index+1,len(targets))
        return {'command':BatchUpdate(commands),'targets':[g for g in targets if g not in unchanged],'skipped':skipped,'details':details,'revision':revision,'context':context,
                'selection':{id(g) for g in app.scene.selection},'scene':app.scene,'signature':[api.volume_signature(g) for g in targets if g not in unchanged]}
    def apply_batch(app,plan):
        if app.scene is not plan['scene'] or app.scene.content_version!=plan['revision'] or app.scene.edit_group is not plan['context'] or {id(g) for g in app.scene.selection}!=plan['selection']:
            raise ValueError('模型或選取已變更，請重新檢查影響範圍。')
        if [api.volume_signature(g) for g in plan['targets']]!=plan['signature']:raise ValueError('物件已變更，請重新檢查。')
        targets,skipped=selection_report(api,app.scene,plan['targets'])
        if skipped:raise ValueError('物件已隱藏或鎖定，請重新檢查影響範圍。')
        if not targets:
            if any('設定相同' in reason for _,reason in plan['skipped']):return '沒有需要更新的設定。'+report_text([],plan['skipped'],True)
            raise ValueError('沒有可修改的參數物件。'+report_text(targets,plan['skipped']))
        ns['execute'](app.viewport,plan['command'])
        api._diagnostics.record('batch.apply','success',{'objects':plan['details'],'skipped':[r for _,r in plan['skipped']]})
        return report_text(targets,plan['skipped'],True)+' Ctrl＋Z 可一次復原。'
    ns.update(prepare_batch=prepare_batch,apply_batch=apply_batch,selection_report=lambda scene,items=None,descend=False:selection_report(api,scene,items,descend))

    class BatchWindow(api.StyledWindow):
        def __init__(self,app):
            super().__init__(app,'批次修改',700,720);self.plan=None;self.busy=False
            self.body.addWidget(api.label('批次修改','title'));self.body.addWidget(api.label('只勾選要修改的項目。隱藏、鎖定物件會略過並列出原因。','muted'))
            self.values={};self.enabled={};form=QFormLayout();self.body.addLayout(form)
            for key,title in [('width','寬度'),('height','高度'),('depth','深度')]:
                tick=QCheckBox(title);field=api.length_number(600,1);field.setEnabled(False);tick.toggled.connect(field.setEnabled);form.addRow(tick,field);self.values[key]=field;self.enabled[key]=tick
            self.style=QComboBox()
            for title,key in [('素面框板','original'),('古典','classical'),('CY鄉村','cy'),('C把手門','kitchen_c'),('素框玻璃','plain_glass')]:self.style.addItem(title,key)
            tick=QCheckBox('櫃體預設面板款式');tick.toggled.connect(self.style.setEnabled);self.style.setEnabled(False);form.addRow(tick,self.style);self.enabled['door_style']=tick
            self.handle=api.AssetPicker(app,'handles',lambda:self.invalidate());tick=QCheckBox('把手（可選無）');tick.toggled.connect(self.handle.setEnabled);self.handle.setEnabled(False);form.addRow(tick,self.handle);self.enabled['handle_asset']=tick
            self.color=QLineEdit('#B8B8B8');tick=QCheckBox('結構／面板顏色');tick.toggled.connect(self.color.setEnabled);self.color.setEnabled(False);form.addRow(tick,self.color);self.color_tick=tick
            self.refresh_assets=QCheckBox('以資料庫目前版本更新既有配件（主動勾選才更新）');self.body.addWidget(self.refresh_assets)
            self.mixed=api.label('','muted');self.body.addWidget(self.mixed);self.rows=api.QListWidget();self.body.addWidget(self.rows)
            self.body.addWidget(api.button('檢查影響範圍',self.prepare,'primary'));self.apply_button=api.button('套用已檢查的修改',self.apply,'primary');self.apply_button.setEnabled(False);self.outer.insertWidget(self.outer.count()-1,self.apply_button)
            for control in [*self.values.values(),*self.enabled.values(),self.style,self.color,self.color_tick,self.refresh_assets]:
                signal=getattr(control,'valueChanged',None) or getattr(control,'toggled',None) or getattr(control,'currentIndexChanged',None) or getattr(control,'textChanged',None)
                if signal is not None:signal.connect(self.invalidate)
            self.describe_selection()
        def describe_selection(self):
            targets,skipped=selection_report(api,self.app.scene,descend=True);parts=[]
            for key in ('width','height','depth','door_style'):
                values=[info(g)['params'].get(key) for g in targets];different=len({state_key(v) for v in values})>1
                if different:parts.append({'width':'寬度','height':'高度','depth':'深度','door_style':'面板款式'}[key]+'：多種值')
                elif values and isinstance(values[0],(int,float)) and key in self.values:self.values[key].setValue(values[0])
            self.mixed.setText(report_text(targets,skipped)+'\n'+ '｜'.join(parts))
        def invalidate(self,*_):self.plan=None;self.apply_button.setEnabled(False)
        def prepare(self):
            if self.busy:return
            self.busy=True;self.invalidate();self.rows.clear();progress=QProgressDialog('正在準備，尚未修改模型…','取消',0,100,self);progress.setWindowModality(Qt.WindowModal);progress.setMinimumDuration(0)
            try:
                patch={k:f.value() for k,f in self.values.items() if self.enabled[k].isChecked()}
                if self.enabled['door_style'].isChecked():patch['door_style']=self.style.currentData()
                if self.enabled['handle_asset'].isChecked():patch['handle_asset']=self.handle.value()
                def update(i,n):progress.setLabelText(f'正在驗證 {i}/{n}（可取消；尚未寫入）');progress.setValue(min(99,int(100*i/max(1,n))));QApplication.processEvents()
                self.plan=prepare_batch(self.app,patch,self.color.text() if self.color_tick.isChecked() else None,self.refresh_assets.isChecked(),update,progress.wasCanceled)
                for g in self.plan['targets']:self.rows.addItem('將修改｜'+g.name)
                for g,reason in self.plan['skipped']:self.rows.addItem('略過｜'+getattr(g,'name','非群組')+'｜'+reason)
                self.message(report_text(self.plan['targets'],self.plan['skipped']));self.apply_button.setEnabled(bool(self.plan['targets']))
            except Exception as exc:self.message(str(exc),True)
            finally:progress.close();self.busy=False
        def apply(self):
            if self.busy or self.plan is None:return
            self.busy=True;self.apply_button.setEnabled(False)
            try:self.message(apply_batch(self.app,self.plan));self.plan=None
            except Exception as exc:self.message(str(exc),True)
            finally:self.busy=False
    ns['BatchWindow']=BatchWindow

    class DrawerDiagram(QWidget):
        def __init__(self,window):super().__init__();self.window=window;self.boxes=[];self.setMinimumHeight(220);self.setToolTip('點選抽屜面板，調整此片高度、款式、把手及顏色。')
        def paintEvent(self,event):
            painter=QPainter(self);painter.fillRect(self.rect(),QColor('#f5f8fb'));self.boxes=[]
            try:
                data=layout(self.window.parameters());p=data['params'];scale=min((self.width()-24)/p['width'],(self.height()-24)/p['height']);left=(self.width()-p['width']*scale)/2;top=(self.height()-p['height']*scale)/2
                for i,(rect,_) in enumerate(data['fronts']):
                    x,z,x2,z2=rect;box=api.QRectF(left+x*scale,top+(p['height']-z2)*scale,(x2-x)*scale,(z2-z)*scale);self.boxes.append((box,i))
                    painter.setPen(QPen(QColor('#147acc' if i==self.window.selected_front else '#476b7b'),2));painter.setBrush(QColor('#d9edfc' if i==self.window.selected_front else '#e7edef'));painter.drawRect(box)
                    painter.drawText(box,Qt.AlignCenter,f'{i+1}｜{(z2-z)/10:g} cm')
            except Exception as exc:painter.drawText(self.rect(),Qt.AlignCenter|Qt.TextWordWrap,str(exc))
            painter.end()
        def mousePressEvent(self,event):
            for box,i in self.boxes:
                if box.contains(event.position()):self.window.select_front(i);break

    # Standard units, preserving mm internally and accepting explicit unit suffix.
    def value_from_text(self,text):
        if not self._length_mode:return float(self.input_number(text))
        raw=str(text).strip()
        if raw.endswith(' cm') and re.search(r'(mm|cm|m)\s+cm$',raw):raw=raw[:-3]
        return parse_length(raw)
    def validate_length(self,text,position):
        if not self._length_mode:
            try:value=float(self.input_number(text))
            except ValueError:return QValidator.Intermediate,text,position
        else:
            raw=str(text).strip()
            if raw in ('','-','+','.','-.','+.'):return QValidator.Intermediate,text,position
            try:value=value_from_text(self,text)
            except ValueError:return QValidator.Intermediate,text,position
        return (QValidator.Acceptable if self.minimum()<=value<=self.maximum() else QValidator.Intermediate),text,position
    api.LengthSpinBox.valueFromText=value_from_text;api.LengthSpinBox.validate=validate_length

    original_message=api.StyledWindow.message
    def validation_message(self,text,error=False):
        keywords={'width':('寬','width'),'height':('高度','高須','層高','height'),'depth':('厚','深','depth'),
            'drawer_rows':('格數','層數','抽屜'),'panels':('門片數','面板總數'),
            'left':('框料','內框'),'right':('框料','內框'),'top':('框料','內框'),'bottom':('框料','內框'),
            'leaf_gap':('間縫','間隙'),'panel_gap':('間縫','間隙')}
        for key,field in getattr(self,'fields',{}).items():
            bad=error and any(word in str(text) for word in keywords.get(key,()))
            if field.property('limeInvalid')!=bad:
                field.setProperty('limeInvalid',bad);field.style().unpolish(field);field.style().polish(field)
            if bad:field.setToolTip(str(text))
        return original_message(self,text,error)
    api.StyledWindow.message=validation_message
    ns['STYLE']+=' QDoubleSpinBox[limeInvalid="true"],QSpinBox[limeInvalid="true"] {border:2px solid #be5346;}'

    # A common busy guard covers callbacks from every tool; release on failure.
    old_run=api.StyledWindow.run
    def guarded_run(self,callback):
        if getattr(self,'_release_busy',False):
            if getattr(callback,'__name__','')=='refresh':return old_run(self,callback)
            return
        self._release_busy=True
        try:return old_run(self,callback)
        finally:self._release_busy=False
    api.StyledWindow.run=guarded_run

    def wrap_model(base):
        class ReleaseWindow(base):
            def __init__(self,app,*args,**kwargs):
                self._release_ready=False;self._baseline=None;self._switching=False;self._apply_busy=False;self._fronts={};self._columns={};self.selected_front=0
                super().__init__(app,*args,**kwargs)
                self._release_ready=True;self.manual_preview=QCheckBox('手動更新預覽（大型模型）');self.manual_preview.setToolTip('勾選後，按預覽才重建幾何；套用仍會驗證完整參數。');self.body.insertWidget(3,self.manual_preview)
                if self.kind=='cabinet':
                    if self.editing and self.target is not None:
                        saved=info(self.target)['params'];self._fronts=deepcopy(saved.get('front_settings',{}));self._columns=deepcopy(saved.get('drawer_column_heights',{}))
                    self.install_front_editor()
                self.help_button=api.button('操作說明',self.show_help)
                options=QWidget();option_row=QHBoxLayout(options);option_row.setContentsMargins(0,0,0,0);option_row.addWidget(self.help_button)
                self.body.removeWidget(self.manual_preview);option_row.addWidget(self.manual_preview,1)
                if hasattr(self,'template_combo'):
                    for index in range(self.body.count()):
                        item=self.body.itemAt(index);layout_item=item.layout()
                        if layout_item is not None and layout_item.indexOf(self.template_combo)>=0:
                            taken=self.body.takeAt(index);self.template_panel=QWidget();self.template_panel.setLayout(taken.layout());self.template_panel.hide()
                            toggle=QPushButton('常用範本 ▾');toggle.setCheckable(True);toggle.toggled.connect(self.template_panel.setVisible);option_row.addWidget(toggle);self.body.insertWidget(3,self.template_panel);break
                self.body.insertWidget(3,options)
                self._baseline=self.draft_key()
                self._guard_timer=QTimer(self);self._guard_timer.setInterval(400);self._guard_timer.timeout.connect(self.check_context);self._guard_timer.start()
                # Debounced refresh still reads the manual mode before any geometry work.
                screen=QApplication.primaryScreen()
                if screen:
                    area=screen.availableGeometry();self.setMinimumWidth(min(620,max(360,area.width()-40)));self.resize(min(self.width(),area.width()-40),min(self.height(),area.height()-60))
                if self.kind=='cabinet':self.sync_front_visibility()
            def resizeEvent(self,event):
                super().resizeEvent(event)
                split=getattr(self,'settings_split',None)
                if split is not None:
                    from PySide6.QtWidgets import QBoxLayout
                    compact=self.width()<780;split.setDirection(QBoxLayout.TopToBottom if compact else QBoxLayout.LeftToRight)
                    self.settings_preview_frame.setMaximumWidth(16777215 if compact else 290)
                    if not compact:
                        height=max(300,self.height()-390);self.settings_tabs.setFixedHeight(height);self.settings_preview_frame.setMaximumHeight(height);self.geometry_preview.setMinimumHeight(200);split.setAlignment(self.settings_preview_frame,Qt.AlignTop)
                    else:self.settings_tabs.setFixedHeight(400);self.settings_preview_frame.setMaximumHeight(320)
            def read_volume(self):
                if not self.confirm_switch():return
                result=super().read_volume()
                if self._release_ready:self._baseline=self.draft_key()
                return result
            def draft_key(self):
                try:return state_key(self.parameters())
                except Exception:return '<invalid>'+state_key({k:api._workflows.control_value(v) for k,v in api._workflows.controls(self).items()})
            def has_pending_changes(self):return self._baseline is not None and (self.draft_key()!=self._baseline or self.front_editor_dirty())
            def front_editor_key(self):
                if self.kind!='cabinet' or not hasattr(self,'front_color') or self.face_mode.currentData()!='drawers':return None
                entry=self.front_handle.combo.currentData() or {};asset=entry.get('snapshot') or entry.get('record') or {}
                return state_key([self.front_height.value(),self.fixed_height.isChecked(),self.front_style.currentData(),self.override_handle.isChecked(),asset.get('id'),asset.get('sha256'),self.front_color.text()])
            def front_editor_dirty(self):return hasattr(self,'_front_edit_baseline') and self.front_editor_key() is not None and self.front_editor_key()!=self._front_edit_baseline
            def confirm_switch(self):
                if not self._release_ready or self._switching or not self.has_pending_changes():return True
                choice=ask_pending(self)
                if choice=='discard':return True
                if choice=='apply':
                    self.apply_model();return not self.has_pending_changes()
                return False
            def show_help(self):
                QMessageBox.information(self,'操作說明','1. 新增：選擇款式、輸入尺寸。\n2. 修改：選取整個物件，再按讀取選取。\n3. 量體轉換：選封閉長方體，指定正面。\n4. 預覽確認後按建立／更新；Ctrl＋Z 復原。\n5. 橘色鎖頭只禁止輸入；抽屜「固定高度」才影響等分。\n6. 大型模型可勾手動更新預覽。\n7. 問題可直接匯出診斷包，無須填表。')
            def parameters(self):
                p=super().parameters()
                if getattr(self,'kind',None)=='cabinet' :p.update(front_settings=deepcopy(self._fronts),drawer_column_heights=deepcopy(self._columns))
                return p
            def preview(self,*args,**kwargs):
                if self._release_ready and self.sender() is not None and isinstance(self.sender(),QTimer) and self.manual_preview.isChecked():return
                result=super().preview(*args,**kwargs)
                if self._release_ready and self.kind=='cabinet':self.drawer_diagram.update();self.sync_front_visibility()
                return result
            def read_target(self):
                if not self.confirm_switch():return
                self._switching=True
                try:
                    result=super().read_target()
                    if self._release_ready:
                        if self.editing and self.target is not None and self.kind=='cabinet':
                            p=info(self.target)['params'];self._fronts=deepcopy(p.get('front_settings',{}));self._columns=deepcopy(p.get('drawer_column_heights',{}));self.select_front(0);self.preview()
                        self._baseline=self.draft_key()
                    return result
                finally:self._switching=False
            def new(self):
                if not self.confirm_switch():return
                self._switching=True
                try:
                    if self.kind in ('window','electrical_panel'):result=super().new()
                    else:
                        self.cancel_owned_tools()
                        if getattr(self,'_conversion_source',None) is not None:self.cancel_conversion()
                        if getattr(self,'_workflow_source',None) is not None:self.cancel_workflow_volume()
                        self.target=None;self.editing=False;self.rigid=api.QMatrix4x4();self.context=self.scene.edit_group;self.version=self.scene.content_version
                        if hasattr(self,'_last_model_snapshot'):self._last_model_snapshot=None
                        self.preview();result=None
                    if self._release_ready:self._baseline=self.draft_key()
                    return result
                finally:self._switching=False
            def apply_model(self):
                if self._apply_busy:return
                if self.front_editor_dirty():
                    try:self.commit_front()
                    except Exception as exc:self.message(str(exc),True);return
                self._apply_busy=True;before_revision=self.scene.content_version
                try:
                    result=super().apply_model()
                    if self._release_ready and self.target is not None and self.scene.content_version!=before_revision:self._baseline=self.draft_key()
                    return result
                finally:self._apply_busy=False
            def load_template(self,name):
                saved=self.template_data().get('templates',{}).get(self.kind,{}).get(name,{})
                result=super().load_template(name)
                if self.kind=='cabinet':
                    params=saved.get('params',{});self._fronts=deepcopy(params.get('front_settings',{}));self._columns=deepcopy(params.get('drawer_column_heights',{}));self.sync_front_visibility();self.select_front(0);self.preview()
                return result
            def check_context(self):
                if not self._release_ready:return
                picker=getattr(self,'active_picker',None)
                if picker is not None and self.vp.active_tool is not picker:self.stop()
                invalid=self.vp.scene is not self.scene or self.scene.edit_group is not self.context
                if self.editing and self.target is not None:
                    tree=hierarchy(self.scene);entry=tree.get(id(self.target));invalid|=entry is None or skip_reason(api,self.scene,self.target,entry[1]) is not None if entry else True
                if invalid:
                    self.cancel_owned_tools();self.preview_group=None;self.preview_lines=None
                    if hasattr(self,'apply_button'):self.apply_button.setEnabled(False)
                    self.message('模型、編輯層級或物件狀態已變更，請重新讀取；預覽已清除。',True);self.vp.update()
            def cancel_owned_tools(self):
                for name,args in [('stop_workflow_picker',()),('stop_front_pick',(False,)),('stop',())]:
                    method=getattr(self,name,None)
                    if method:
                        try:method(*args)
                        except Exception as exc:api._diagnostics.record('tool.cleanup','error',{'kind':self.kind},error=exc)
                self.preview_group=None;self.preview_lines=None;self._preview_fingerprint=None;self._preview_values=None
                preview=getattr(self,'geometry_preview',None)
                if preview is not None:preview.cache=None;preview.cache_key=None
            def closeEvent(self,event):
                if not self.confirm_switch():event.ignore();return
                self._guard_timer.stop();self.cancel_owned_tools();self._switching=True
                try:super().closeEvent(event)
                finally:self._switching=False
            def done(self,result):
                if not self.confirm_switch():return
                self._guard_timer.stop();self.cancel_owned_tools();super().done(result)
            def sync_front_visibility(self):
                active=self.face_mode.currentData()=='drawers';mode_changed=getattr(self,'_front_mode',None)!=active;self._front_mode=active;self.front_editor.setVisible(active);self.settings_tabs.setTabText(1,'抽屜分格' if active else '框料／層板')
                rows=int(self.fields['drawer_rows'].value());cols=int(self.fields['drawer_columns'].value());n=rows*cols
                previous=getattr(self,'_drawer_grid',(cols,rows));self._drawer_grid=(cols,rows)
                if previous!=(cols,rows):
                    old_cols,old_rows=previous;remapped={}
                    for key,value in self._fronts.items():
                        old_col,old_row=divmod(int(key),old_rows)
                        if old_col<cols and old_row<rows:remapped[str(old_col*rows+old_row)]=value
                    removed=len(self._fronts)-len(remapped);self._fronts=remapped
                    if removed:self.message(f'格數已改變，移除範圍外 {removed} 片面板設定；尚未套用。')
                if self.front_choice.count()!=n:
                    with QSignalBlocker(self.front_choice):
                        self.front_choice.clear()
                        for i in range(n):self.front_choice.addItem(f'第 {i//rows+1} 欄・第 {i%rows+1} 格',i)
                    self._fronts={k:v for k,v in self._fronts.items() if int(k)<n};self._columns={k:v[:rows] for k,v in self._columns.items() if int(k)<cols};self.select_front(min(self.selected_front,n-1))
                elif mode_changed:self.select_front(min(self.selected_front,n-1))
                self.drawer_diagram.update()
            def install_front_editor(self):
                page=self.settings_layouts['框料／層板'];self.front_editor=QWidget();outer=QVBoxLayout(self.front_editor);outer.addWidget(api.label('抽屜面板分格｜點選一片調整'))
                self.drawer_diagram=DrawerDiagram(self);outer.addWidget(self.drawer_diagram);form=QFormLayout();outer.addLayout(form);self.front_choice=QComboBox();form.addRow('選取面板',self.front_choice)
                self.front_height=api.length_number(300,20);form.addRow('面板實際高度',self.front_height);self.fixed_height=QCheckBox('固定這片高度，其餘等分');form.addRow(self.fixed_height)
                self.front_style=QComboBox();self.front_style.addItem('沿用整櫃款式',None)
                for title,key in [('素面框板','original'),('古典','classical'),('CY鄉村','cy'),('C把手門','kitchen_c'),('素框玻璃','plain_glass')]:self.front_style.addItem(title,key)
                form.addRow('這片款式',self.front_style);self.front_handle=api.AssetPicker(self.app,'handles',lambda:None);form.addRow('這片把手',self.front_handle);self.override_handle=QCheckBox('使用這片的把手設定（未勾選沿用整櫃）');form.addRow(self.override_handle)
                self.front_color=QLineEdit();self.front_color.setPlaceholderText('沿用材質；或填 #B8B8B8');form.addRow('這片顏色',self.front_color)
                outer.addWidget(api.button('設定這片面板',lambda:self.run(self.commit_front),'primary'));outer.addWidget(api.button('此欄解除固定並等分',lambda:self.run(self.equal_fronts)));self.front_note=api.label('高度已扣除上下各 0.2 cm 邊縫；由上到下編號。','muted');outer.addWidget(self.front_note)
                page.insertWidget(0,self.front_editor);self.face_mode.currentIndexChanged.connect(lambda *_:self.settings_tabs.setCurrentIndex(1) if self.face_mode.currentData()=='drawers' else None);self.front_choice.currentIndexChanged.connect(lambda *_:self.select_front(self.front_choice.currentData() or 0));self.heights.setToolTip('舊版整欄層高設定；各欄個別設定後，以點選面板的設定為準。');self.heights.setProperty('limeHiddenByDesign',True)
            def select_front(self,index):
                if not hasattr(self,'front_choice'):return
                self.selected_front=max(0,int(index));rows=int(self.fields['drawer_rows'].value());col=self.selected_front//rows;row=self.selected_front%rows
                with QSignalBlocker(self.front_choice):self.front_choice.setCurrentIndex(self.front_choice.findData(self.selected_front))
                try:
                    p=self.parameters();raw=self._columns.get(str(col),p.get('drawer_heights',[]));sizes=drawer_sizes(p['height'],rows,raw);self.front_height.setValue(sizes[row]-4);self.fixed_height.setChecked(row<len(raw) and raw[row] is not None)
                except Exception:self.fixed_height.setChecked(False)
                config=self._fronts.get(str(index),{});self.front_style.setCurrentIndex(max(0,self.front_style.findData(config.get('door_style'))));self.override_handle.setChecked('handle_asset' in config);self.front_handle.load(config.get('handle_asset'));self.front_color.setText(config.get('material',''));self._front_edit_baseline=self.front_editor_key();self.drawer_diagram.update()
            def commit_front(self):
                p=self.parameters();rows=p['drawer_rows'];col=self.selected_front//rows;row=self.selected_front%rows;raw=list(self._columns.get(str(col),p.get('drawer_heights',[])));raw=(raw+[None]*rows)[:rows]
                raw[row]=self.front_height.value()+4 if self.fixed_height.isChecked() else None;drawer_sizes(p['height'],rows,raw)
                config={};style=self.front_style.currentData()
                if style is not None:config['door_style']=style
                if self.override_handle.isChecked():config['handle_asset']=self.front_handle.value()
                if self.front_color.text().strip():config['material']=self.front_color.text().strip()
                candidate={**p,'drawer_column_heights':{**self._columns,str(col):raw},'front_settings':{**self._fronts,str(self.selected_front):config}}
                cabinet(candidate) # All geometry must be valid before storing draft.
                self._columns=candidate['drawer_column_heights'];self._fronts=candidate['front_settings'];self.preview();self.select_front(self.selected_front)
                return '已設定這片面板；預覽後按更新才寫入模型。'
            def equal_fronts(self):
                rows=int(self.fields['drawer_rows'].value());col=self.selected_front//rows;self._columns[str(col)]=[None]*rows;self.preview();self.select_front(self.selected_front);return '此欄已解除固定並等分；尚未寫入模型。'
        ReleaseWindow.__name__=base.__name__;return ReleaseWindow
    for name in ('CabinetWindow','DoorWindow','ParametricWindow','DecorativeWindow','PanelWindow','WindowWindow'):ns[name]=wrap_model(ns[name])

    def optimize(app,window=None):
        parent=window or app.window
        if getattr(app.window,'_lime_optimize_busy',False):return
        app.window._lime_optimize_busy=True;progress=QProgressDialog('準備共用配件資料，尚未修改模型…','取消',0,100,parent);progress.setWindowModality(Qt.WindowModal);progress.setMinimumDuration(0)
        try:
            revision=app.scene.content_version
            def update(i,n):progress.setValue(int(99*i/max(1,n)));QApplication.processEvents()
            command=api._handle_sharing.OptimizeHandles(app.scene,update,progress.wasCanceled)
            if progress.wasCanceled():raise ValueError('已取消最佳化，模型未變更。')
            if app.scene.content_version!=revision:raise ValueError('模型已變更，請重新最佳化。')
            stats=command.stats
            if stats['mesh_nodes']:ns['execute'](app.viewport,command)
            text=f"已最佳化把手：整理 {stats['mesh_nodes']} 個配件網格；共用資料 {stats['unique_meshes_before']} → {stats['unique_meshes_after']} 份。"
            text+=''.join(f' 略過{reason} {count} 個。' for reason,count in stats['skipped_reasons'].items())
            text+=' Ctrl＋Z 可一次復原。';api._diagnostics.record('handles.optimize','success',stats)
            if window:window.message(text)
            else:app.viewport.flash_status(text,10000)
            return stats
        except Exception as exc:
            api._diagnostics.record('handles.optimize','error',error=exc)
            if window:window.message(str(exc),True)
            else:app.viewport.flash_status(str(exc),10000)
        finally:progress.close();app.window._lime_optimize_busy=False
    ns['optimize_existing_handles']=optimize

    def replace_library_asset(app,kind,identity,source,front='-Y',mount='center'):
        if kind not in ('handles','panels'):raise ValueError('模型替換只適用把手與面板。')
        store=ns['unified_store_for'](app);base=store.raw_store(kind);data,token=base.begin();row=next((r for r in data['handles'] if r['id']==identity),None)
        if row is None:raise ValueError('找不到要替換的配件，請重新整理。')
        group=api.normalize_handle(source,front,'center' if kind=='panels' else mount);payload=api.group_igz_bytes(group)
        if len(payload)>api.ASSET_MAX_BYTES:raise ValueError('配件模型過大，請先簡化。')
        lo,hi=api.bounds([group]);updated={**deepcopy(row),'sha256':hashlib.sha256(payload).hexdigest(),'size_mm':((hi-lo)*1000).tolist(),'front':front,'mount':'center' if kind=='panels' else mount}
        import base64
        api.handle_from_snapshot({**updated,'blob_b64':base64.b64encode(payload).decode()})
        target=base.directory/(identity+'.igz');before=target.read_bytes()
        if hashlib.sha256(before).hexdigest()!=row['sha256']:raise ValueError('原配件核對失敗，請先修復來源。')
        from PySide6.QtCore import QLockFile
        lock=QLockFile(str(base.path)+'.lock');lock.setStaleLockTime(30000)
        if not lock.tryLock(1000):raise ValueError('資料庫正在使用，請稍後重試。')
        try:
            if base.path.read_bytes()!=token or target.read_bytes()!=before:raise ValueError('配件已變更，請重新整理。')
            row.update(updated)
            try:
                api._unified_library.atomic_bytes(target.with_suffix('.igz.bak'),before)
                api._unified_library.atomic_bytes(base.path.with_suffix('.json.bak'),token)
                api._unified_library.atomic_bytes(target,payload);api._atomic_json(base.path,data)
            except Exception:
                api._unified_library.atomic_bytes(target,before);api._unified_library.atomic_bytes(base.path,token);raise
        finally:lock.unlock()
        api._diagnostics.record('library.replace_model','success',{'kind':kind,'id':identity,'sha256':updated['sha256']})
        return updated
    ns['replace_library_asset']=replace_library_asset

    # Library filters are metadata only: no model load when toggling categories.
    base_library=api.AccessoryWindow
    class Library(base_library):
        def __init__(self,app):
            self._filter_ready=False;super().__init__(app);self._filter_ready=True
            self.favorite_only=QCheckBox('只看收藏');self.recent_only=QCheckBox('最近使用');self.favorite_button=api.button('收藏／取消收藏',self.toggle_favorite)
            row=QHBoxLayout();row.addWidget(self.favorite_only);row.addWidget(self.recent_only);row.addWidget(self.favorite_button);self.body.insertLayout(3,row)
            self.favorite_only.toggled.connect(self.rebuild_list);self.recent_only.toggled.connect(self.rebuild_list)
            self.list.itemClicked.connect(lambda *_:self.remember_current());self.list.itemActivated.connect(lambda *_:self.remember_current())
            self.replace_button=api.button('以預覽模型更新目前配件（保留識別碼）',self.replace_pending);self.replace_button.setEnabled(False);self.body.insertWidget(4,self.replace_button)
        def select_record(self,*args):
            result=super().select_record(*args)
            if hasattr(self,'replace_button'):self.replace_button.setEnabled(False);self.replace_button.setVisible(self.kind.currentData()!='profiles')
            return result
        def pending_preview(self,*args):
            result=super().pending_preview(*args)
            if hasattr(self,'replace_button'):self.replace_button.setEnabled(self.pending_handle is not None and self.list.currentItem() is not None and self.commit_handle_button.isEnabled())
            return result
        def replace_pending(self):
            if self.pending_handle is None:self.message('請先載入要替換的新模型預覽。',True);return
            try:record=self.current()
            except Exception as exc:self.message(str(exc),True);return
            if QMessageBox.question(self,'更新資料庫模型','更新「'+record['name']+'」的來源模型？\n既有設計保留原快照，需主動批次更新才會套用新版本。')!=QMessageBox.Yes:return
            def work():
                replace_library_asset(self.app,self.kind.currentData(),record['id'],self.pending_handle,self.front.currentData(),self.mount.currentData());self.pending_handle=None;self.clear_preview_cache();self.refresh();self.records_changed.emit();return '已更新資料庫配件；識別碼保留，既有模型未自動改動。'
            self.run(work)
        def import_backup(self):
            path,_=QFileDialog.getOpenFileName(self,'匯入配件備份','','配件備份 (*.json)')
            if not path:return
            def work():
                progress=QProgressDialog('驗證配件備份，尚未寫入…','取消',0,100,self);progress.setWindowModality(Qt.WindowModal);progress.setMinimumDuration(0)
                try:
                    def update(i,n):progress.setValue(min(99,int(100*i/max(1,n))));QApplication.processEvents()
                    count=self.store.import_json(path,update,progress.wasCanceled)
                    # Refresh after guarded operation ends (run itself is reentry-safe).
                    self.rows=self.store.load()[self.kind.currentData()];self.rebuild_list()
                    return f'已匯入 {count} 個配件；同名衝突會保留原資料並提示。'
                finally:progress.close()
            self.run(work)
        def library_meta(self):return api._workflows.get_preference(api,self.app,'asset_lists',{'favorites':[],'recent':[]})
        def toggle_favorite(self):
            def work():
                row=self.current();data=self.library_meta();key=self.kind.currentData()+':'+row['id'];favorites=data['favorites']
                if key in favorites:favorites.remove(key)
                else:favorites.append(key)
                api._workflows.set_preference(api,self.app,'asset_lists',data);self.remember_current();self.rebuild_list();return '已更新收藏。'
            self.run(work)
        def rebuild_list(self,*_):
            if not self._filter_ready:return super().rebuild_list(*_)
            original=self.rows;data=self.library_meta();kind=self.kind.currentData();rows=list(original)
            if self.favorite_only.isChecked():rows=[r for r in rows if kind+':'+r['id'] in data['favorites']]
            if self.recent_only.isChecked():rows=[r for r in rows if kind+':'+r['id'] in data['recent']];rows.sort(key=lambda r:data['recent'].index(kind+':'+r['id']))
            self.rows=rows
            try:super().rebuild_list(*_)
            finally:self.rows=original
        def remember_current(self):
            if self.list.currentItem() is None:return
            row=self.current();key=self.kind.currentData()+':'+row['id'];data=self.library_meta();data['recent']=[key,*[k for k in data['recent'] if k!=key]][:40];api._workflows.set_preference(api,self.app,'asset_lists',data)
        def insert(self):
            self.remember_current();return super().insert()
    Library.__name__='AccessoryWindow';ns['AccessoryWindow']=Library

    class SupportWindow(api.StyledWindow):
        def __init__(self,app):
            super().__init__(app,'版本與模型檢查',720,720);self.body.addWidget(api.label('1.0.0 發布候選版','title'))
            self.body.addWidget(api.label('目標：IngeTrazo 0.6.0／Windows x64。離線測試與 Windows 實機驗收分開記錄。\n抽屜分格、批次修改、配件備份請先在副本確認。','muted'))
            self.result=api.label('');self.body.addWidget(self.result);self.body.addWidget(api.button('檢查目前模型',self.audit,'primary'));self.body.addWidget(api.button('開啟使用說明',lambda:api.QDesktopServices.openUrl(api.QUrl.fromLocalFile(str(api.ROOT/'使用說明.txt')))))
        def audit(self):
            def work():
                tree=hierarchy(self.app.scene);valid=0;issues=[];versions={}
                for group,_ in tree.values():
                    data=(group.ext or {}).get(api.PARAM_KEY)
                    if data is None:continue
                    try:
                        data=info(group);kind,p,_=ns['effective_parameters'](group)
                        # Validation is bounded to parameters; audit never rebuilds scene geometry.
                        if kind=='cabinet':layout(p)
                        elif kind=='door':api._door_system.door_layout(api._door_api,p)
                        elif kind=='window':api._windows.layout(api._window_api,p)
                        valid+=1;version=data.get('tool_version','舊版');versions[version]=versions.get(version,0)+1
                    except Exception as exc:issues.append(group.name+'：'+str(exc))
                self.result.setText(f'辨識 {valid} 個參數物件；問題 {len(issues)} 個。\n版本：'+state_key(versions)+'\n'+'\n'.join(issues[:30]))
                api._diagnostics.record('model.audit',details={'valid':valid,'issues':issues[:30],'versions':versions})
                return '模型檢查完成，未修改模型。'
            self.run(work)
    ns['SupportWindow']=SupportWindow

    old_dispatch=ns['dispatch'];old_show=ns['show_window']
    def show(app,kind):
        canonical={'cabinet_edit':'cabinet','door_edit':'door_new'}.get(kind,kind);key='_lime_suite_'+canonical
        old=getattr(app.window,key,None)
        if old is not None:
            try:visible=old.isVisible()
            except RuntimeError:visible=False
            if not visible:
                try:old.deleteLater()
                except RuntimeError:pass
                setattr(app.window,key,None)
        if canonical in ('batch_edit','release_support'):
            cls=BatchWindow if canonical=='batch_edit' else SupportWindow;window=getattr(app.window,key,None)
            if window is None:window=cls(app);setattr(app.window,key,window)
            window.show();window.raise_()
        else:window=old_show(app,canonical)
        if not getattr(window,'_release_dispose_connected',False):
            window._release_dispose_connected=True;window.setAttribute(Qt.WA_DeleteOnClose,True)
            def cleared():
                if getattr(app.window,key,None) is window:setattr(app.window,key,None)
            window.destroyed.connect(cleared)
        return window
    def dispatch(app,key):
        if key in ('batch_edit','release_support'):return show(app,key)
        if key=='edit_selected':
            targets,skipped=selection_report(api,app.scene,descend=True)
            if len(targets)!=1:
                if len(targets)>1:return show(app,'batch_edit')
                app.viewport.flash_status('無法修改：'+report_text(targets,skipped)+' 請選取完整參數物件；炸開後需重新轉換。',7000);return
            target=targets[0];kind=info(target)['kind'];context=app.scene.groups if app.scene.edit_group is None else app.scene.edit_group.children or []
            if target not in context:
                parents=hierarchy(app.scene)[id(target)][1];current=app.scene.edit_group
                start=parents.index(current)+1 if current in parents else 0
                for parent in parents[start:]:app.scene.begin_group_edit(parent)
                app.scene.selection.clear();app.scene.selection.add(target);app.viewport.notify_scene_changed()
            window=show(app,{'door':'door_new','electrical_panel':'panel_place','composite':'cabinet_composite'}.get(kind,kind if kind in ('cabinet','window') else kind+'_edit'))
            if getattr(window,'target',None) is not target:window.read_target()
            return window
        return old_dispatch(app,key)
    ns['dispatch']=dispatch;ns['show_window']=show
    for adapter in (ns['_door_api'],ns['_feature_api'],ns['_window_api']):adapter.show_window=show
    old_toolbox=ns['ToolboxWindow']
    class Toolbox(old_toolbox):
        def __init__(self,app):
            super().__init__(app);row=QHBoxLayout()
            for title,key in [('修改選取物件','edit_selected'),('批次修改','batch_edit'),('版本／模型檢查','release_support')]:row.addWidget(api.button(title,lambda checked=False,k=key:dispatch(app,k)))
            self.body.insertLayout(2,row)
    Toolbox.__name__='ToolboxWindow';ns['ToolboxWindow']=Toolbox

    # Install once per live application, even if duplicate plugin directories load.
    original_setup=ns['setup']
    def setup(app):
        previous=getattr(app.window,'_lime_release_setup',None)
        if previous is None and app.window.findChild(api.QToolBar,'lime_integrated_tools_port') is not None:previous='其他版本'
        if previous is not None:
            app.viewport.flash_status('萊姆工具箱已載入 '+str(previous)+'；已略過重複載入，請移除舊版外掛資料夾。',7000);return
        original_setup(app);app.window._lime_release_setup=api.VERSION
        for title,key in [('修改選取參數物件','edit_selected'),('批次修改','batch_edit'),('工具箱版本與模型檢查','release_support')]:app.add_menu_action(title,lambda command=key:dispatch(app,command),tip='萊姆工具箱 '+api.VERSION)
    ns['setup']=setup
    ns['_release_api']=api
