"""Qt controls for unified door groups, kept outside the legacy toolbox UI."""
from copy import copy,deepcopy
import json
import numpy as np
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QMatrix4x4
from PySide6.QtWidgets import QComboBox,QCheckBox,QLineEdit,QFormLayout,QHBoxLayout


def parse_widths(text,factor=10):
    if not text.strip():return []
    return [None if token.strip().lower() in ('','auto') else float(token.strip())*factor for token in text.replace('，',',').split(',')]


def install(api,system):
    class UpdateDoor(api.UpdateParametric):
        def __init__(self,target,replacement,rigid):
            if target.ext[api.PARAM_KEY]['kind']=='louver':system.carry_louver_paint(api,target,replacement)
            super().__init__(target,replacement,rigid)
            old=system.migrate(api,target);new=replacement.ext[api.PARAM_KEY]['params']
            same_frame=system.frame_signature(api,old)==system.frame_signature(api,new)
            same_leaf=system.leaf_signature(api,old)==system.leaf_signature(api,new)
            legacy=target.ext[api.PARAM_KEY]['kind']=='door' and target.ext[api.PARAM_KEY].get('geometry_revision',0)<2
            if legacy:
                same_leaf=False
                if old['frame_style']=='profile':same_frame=False
            basis=system.local_basis(target) if target.ext[api.PARAM_KEY]['kind']=='door' else QMatrix4x4()
            inverse,ok=basis.inverted()
            if not ok:raise ValueError('原門組定位不可逆。')
            for i,part in enumerate(list(self.replacement.children or [])):
                role=part.ext.get('lime_door_role')
                candidates=[g for g in target.children or () if (g.ext or {}).get('lime_door_role',(g.ext or {}).get('lime_decorative_role'))==role]
                if role=='frame' and same_frame and candidates:
                    kept=candidates[0]
                elif role=='leaf' and same_leaf and candidates:
                    kept=next((g for g in candidates if g.name==part.name),None)
                    if kept is None:continue
                else:continue
                if basis!=QMatrix4x4() or 'lime_door_role' not in (kept.ext or {}):
                    kept=copy(kept);kept.xform=inverse*(kept.xform if kept.xform is not None else QMatrix4x4())
                    kept.ext={**(kept.ext or {}),'lime_door_role':role}
                self.replacement.children[i]=kept

    class DoorWindow(api.GeometryWindow):
        def __init__(self,app,editing=False):
            super().__init__(app,'門組設定',680,900)
            self.kind='door';self.editing=False;self.target=None;self.rigid=QMatrix4x4();self.preview_group=None
            self.fields={};self.combos={};self._loading=True;self._source_options={};self._frame_choice_explicit=False;self._last_leaf_type='decorative';self._louver_initialized=False
            self._refresh_timer=QTimer(self);self._refresh_timer.setSingleShot(True);self._refresh_timer.timeout.connect(self.preview)
            self.body.addWidget(api.label('門框與門片分開選擇；開口尺寸包含四邊門縫。切換門片時保留未變更的門框、材質及位置。','muted'))
            self.info=api.label('新增：可先建立門框，再加入門片。');self.body.addWidget(self.info)
            self.body.addWidget(api.button('讀取既有門組／門框',self.read_target))
            defaults={**deepcopy(api.DECORATIVE_DEFAULTS),**deepcopy(system.DOOR_DEFAULTS)}
            self._base=deepcopy(defaults)
            choices={'leaf_type':[('只有門框／不設門片','none'),('造型門片','decorative'),('玻璃門片','glass'),('百葉門片','louver')],
                     'frame_style':[('原型百葉框（含下框）','louver_original'),('不設外框','none'),('素門框','plain'),('企口門框','rebate'),('資料庫線板門框','profile')],
                     'leaf_depth_mode':[('依框深置中','center'),('指定正面退入','manual')],
                     'glass_design':[('素玻璃門片','plain'),('原型玻璃格子門','stock')]}
            c=api.card(self.body);form=QFormLayout();c.addLayout(form)
            for key,title in [('frame_style','外框樣式'),('leaf_type','門片類型'),('glass_design','玻璃款式'),('leaf_depth_mode','門片前後定位')]:
                field=QComboBox()
                for label,value in choices[key]:field.addItem(label,value)
                field.setCurrentIndex(field.findData(defaults[key]));self.combos[key]=field;form.addRow(title,field);field.currentIndexChanged.connect(self.changed)
            self.combos['frame_style'].activated.connect(lambda *_:setattr(self,'_frame_choice_explicit',True))
            groups=[('開口與門扇',[('width','框內淨開口寬'),('height','框內淨開口高'),('leaf_count','門扇數'),('leaf_gap','扇間門縫'),('leaf_thickness','造型門片厚'),('glass_thickness','玻璃厚度'),('front_setback','門片正面退入')]),
                    ('外框',[('frame_left','左外框寬'),('frame_right','右外框寬'),('frame_top','上外框寬'),('frame_bottom','下外框寬（0＝不設）'),('frame_depth','門框深度')]),
                    ('四邊門縫',[('gap_left','左門縫'),('gap_right','右門縫'),('gap_top','上門縫'),('gap_bottom','下門縫')]),
                    ('造型門片框料',[('left','左立料'),('right','右立料'),('top','上橫料'),('bottom','下橫料'),('middle','中橫料'),('lower_height','下內框淨高'),('recess_front','正面內凹'),('recess_back','反面內凹')]),
                    ('企口',[('frame_rebate_front_width','正面企口寬'),('frame_rebate_back_width','反面企口寬'),('frame_rebate_front_depth','正面企口深'),('frame_rebate_back_depth','反面企口深')])]
            for title,items in groups:
                c=api.card(self.body);c.addWidget(api.label(title));form=QFormLayout();c.addLayout(form)
                for key,text in items:
                    if key=='leaf_count':field=api.LockedSpinBox();field.setRange(1,20)
                    else:field=api.length_number(defaults[key],0);field.setMaximum(20000)
                    field.setValue(defaults[key]);self.fields[key]=field;form.addRow(text,field);field.valueChanged.connect(self.changed)
            c=api.card(self.body);form=QFormLayout();c.addLayout(form)
            self.widths=QLineEdit();self.widths.setPlaceholderText('留空平均分配；例如 40, auto, 40，單位 cm');form.addRow('各扇指定寬度',self.widths);self.widths.textChanged.connect(self.changed)
            self.with_frame=QCheckBox();self.with_frame.setChecked(True);self.fields['with_frame']=self.with_frame
            self.louver_controls=api.QWidget();lc=QFormLayout(self.louver_controls);self.louver_fields={}
            self.louver_sash=QComboBox();self.louver_sash.addItem('原型框料','original');self.louver_sash.addItem('素框料（尺寸可調）','plain');self.louver_sash.setCurrentIndex(0);self.louver_sash.currentIndexChanged.connect(self.changed);lc.addRow('百葉門扇框樣式',self.louver_sash)
            for key,title in [('middle_enabled','建立中間橫條'),('middle_width','中橫條高度'),('middle_position','橫條位置（由下至上 %）'),('stile_width','左右立料寬'),('rail_width','上下橫料高'),('upper_gap','上區目標淨距'),('lower_gap','下區目標淨距'),('target_gap','無橫條目標淨距'),('angle','葉片角度')]:
                value=api.stock_data('louver')['template']['blade_reference_angle'] if key=='angle' else system.LOUVER_DEFAULTS.get(key,8.)
                if key=='middle_enabled':field=QCheckBox();field.setChecked(value);field.toggled.connect(self.changed)
                else:
                    field=api.number(value,-90 if key=='angle' else 0) if key in ('angle','middle_position') else api.length_number(value,0)
                    field.setMaximum(90 if key=='angle' else 100 if key=='middle_position' else 20000);field.valueChanged.connect(self.changed)
                lc.addRow(title,field);self.louver_fields[key]=field
            self.middle_positions=QLineEdit();self.middle_positions.setPlaceholderText('留空則各扇對齊；可輸入 40, 50, 50, 60 (%)')
            lc.addRow('各扇橫條位置',self.middle_positions);self.middle_positions.textChanged.connect(self.changed)
            c.addWidget(self.louver_controls)
            self.accessories=api.DecorativeAccessories(self)
            self.accessories.frame.combo.setItemText(0,'請選擇門框線板剖面')
            c=api.card(self.body);self.frame_profile_card=c.parentWidget();c.addWidget(api.label('門框線板剖面（請選擇）'));c.addWidget(self.accessories.frame);c.addWidget(self.accessories.frame_side);c.addWidget(api.button('開啟配件資料庫',lambda:api.show_window(self.app,'profile_library')))
            self.frame_profile_card.hide()
            self.summary=api.label('');self.body.addWidget(self.summary);self.body.addStretch()
            row=QHBoxLayout();row.addWidget(api.button('預覽',self.preview));self.apply_button=api.button('建立門組',self.apply_model,'primary');row.addWidget(self.apply_button);self.outer.insertLayout(self.outer.count()-1,row)
            self._loading=False;self.sync_fields()
            if editing:self.read_target()
            else:self.preview()
        def changed(self,*args):
            if self._loading:return
            self.sync_fields();self._refresh_timer.start(250)
        def sync_fields(self):
            kind=self.combos['leaf_type'].currentData()
            if kind=='louver' and not self._louver_initialized and not self._loading:
                self.louver_sash.blockSignals(True);self.louver_sash.setCurrentIndex(0);self.louver_sash.blockSignals(False);self._louver_initialized=True
            if kind=='louver' and self._last_leaf_type!='louver' and not self.editing and not self._frame_choice_explicit and not self._loading:
                combo=self.combos['frame_style'];combo.blockSignals(True);combo.setCurrentIndex(combo.findData('louver_original'));combo.blockSignals(False)
            self._last_leaf_type=kind
            original=self.combos['frame_style'].currentData()=='louver_original'
            self.combos['leaf_depth_mode'].setItemText(self.combos['leaf_depth_mode'].findData('center'),'依原型框定位' if original and kind=='louver' and self.louver_sash.currentData()=='original' else '依框深置中')
            for key in ('frame_left','frame_right','frame_top','frame_bottom','frame_depth','frame_rebate_front_width','frame_rebate_back_width','frame_rebate_front_depth','frame_rebate_back_depth'):self.fields[key].setEnabled(not original)
            framed=self.combos['frame_style'].currentData()!='none'
            for key in ('frame_left','frame_right','frame_top','frame_bottom','frame_depth','frame_rebate_front_width','frame_rebate_back_width','frame_rebate_front_depth','frame_rebate_back_depth'):self.fields[key].setEnabled(framed and not original)
            for key in ('leaf_count','leaf_gap'):self.fields[key].setEnabled(kind!='none')
            self.with_frame.setChecked(framed);self.louver_controls.setVisible(kind=='louver');self.combos['glass_design'].setEnabled(kind=='glass')
            for key in ('left','right','top','bottom','middle','lower_height','recess_front','recess_back'):self.fields[key].setEnabled(kind=='decorative')
            self.fields['glass_thickness'].setEnabled(kind=='glass');self.fields['leaf_thickness'].setEnabled(kind=='decorative')
            self.fields['front_setback'].setEnabled(framed and self.combos['leaf_depth_mode'].currentData()=='manual')
            active=kind=='louver'
            if hasattr(self,'louver_basic'):self.louver_basic.setVisible(active);self.louver_frames.setVisible(active);self.louver_advanced.setVisible(active)
            mid=self.louver_fields['middle_enabled'].isChecked()
            for key in ('middle_width','middle_position','upper_gap','lower_gap'):self.louver_fields[key].setEnabled(active and mid)
            self.louver_fields['target_gap'].setEnabled(active and not mid)
            self.middle_positions.setEnabled(active and mid)
            if hasattr(self,'frame_profile_card'):self.frame_profile_card.setVisible(self.combos['frame_style'].currentData()=='profile')
            if self.combos['frame_style'].currentData()=='profile' and hasattr(self,'accessories') and not self.accessories.frame.value():self.message('請在基本設定的「門框線板剖面」選擇資料庫剖面，選好後會更新預覽。',True)
            if hasattr(self,'accessories'):
                self.accessories.leaf.setEnabled(kind=='decorative');self.accessories.frame.setEnabled(self.combos['frame_style'].currentData()=='profile')
                self.accessories.handle.setEnabled(kind!='none')
                self.accessories.sync_fields();self.accessories.mount.setEnabled(kind=='decorative')
                for key in ('stile_width','rail_width'):self.louver_fields[key].setEnabled(active and self.louver_sash.currentData()=='plain')
        def parameters(self):
            p={**deepcopy(self._base),**{key:(field.isChecked() if isinstance(field,QCheckBox) else field.value()) for key,field in self.fields.items()},**{key:field.currentData() for key,field in self.combos.items()}}
            p['leaf_widths']=parse_widths(self.widths.text());p.update(self.accessories.parameters())
            lou=deepcopy(self._source_options.get('louver_options',{}));lou.update({key:(field.isChecked() if isinstance(field,QCheckBox) else field.value()) for key,field in self.louver_fields.items()})
            lou['sash_style']=self.louver_sash.currentData();lou['middle_positions']=parse_widths(self.middle_positions.text(),1)
            p['louver_options']=lou;p['glass_options']=deepcopy(self._source_options.get('glass_options',{}))
            return p
        def load_values(self,p):
            self._loading=True;self._frame_choice_explicit=True;self._louver_initialized=p['leaf_type']=='louver';self._base=deepcopy(p);self._source_options=deepcopy(p)
            for key,field in self.fields.items():
                field.blockSignals(True)
                if isinstance(field,QCheckBox):field.setChecked(bool(p.get(key,False)))
                else:field.setValue(p[key])
                field.blockSignals(False)
            for key,field in self.combos.items():field.setCurrentIndex(field.findData(p[key]))
            self.widths.setText(', '.join('auto' if v is None else f'{v/10:g}' for v in p['leaf_widths']))
            lou={**system.LOUVER_DEFAULTS,**p.get('louver_options',{})}
            for key,field in self.louver_fields.items():
                if isinstance(field,QCheckBox):field.setChecked(lou[key])
                else:field.setValue(lou.get(key,api.stock_data('louver')['template']['blade_reference_angle'] if key=='angle' else 0.))
            self.louver_sash.setCurrentIndex(self.louver_sash.findData(lou['sash_style']))
            self.middle_positions.setText(', '.join('auto' if v is None else str(v) for v in lou['middle_positions']))
            self.accessories.load(p);self._loading=False;self.sync_fields()
        def read_target(self):
            def read():
                self.check();items=list(self.scene.selection)
                if len(items)!=1 or not isinstance(items[0],api.Group):raise ValueError('請選取一個門組或門框。')
                target=items[0]
                if api.PARAM_KEY not in (target.ext or {}):
                    owner=next((root for top in self.scene.groups for root in api.walk_groups(top) if (root.ext or {}).get(api.PARAM_KEY,{}).get('kind') in ('decorative','door') and target in list(api.walk_groups(root))),None)
                    if owner is None:raise ValueError('此門框沒有可辨識的門組參數，請選取原本的整個門組。')
                    target=owner
                if not api.editable(self.scene,target) or any(not api.editable(self.scene,level['group']) for level in self.scene._edit_stack):raise ValueError('門組或外層群組已鎖定／隱藏。')
                context=self.scene.groups if self.scene.edit_group is None else self.scene.edit_group.children or []
                while target not in context and self.scene.edit_group is not None:
                    leave=getattr(self.vp,'end_one_group_edit',self.scene.end_one_group_edit);leave()
                    context=self.scene.groups if self.scene.edit_group is None else self.scene.edit_group.children or []
                if target not in context:raise ValueError('請先開啟門組所在的外層群組。')
                self.context=self.scene.edit_group;self.scene.selection.clear();self.scene.selection.add(target)
                p=system.migrate(api,target)
                kind,original,self.rigid=api.effective_parameters(target)
                if kind=='door':p.update(width=original['width'],height=original['height'],leaf_widths=deepcopy(original.get('leaf_widths',p['leaf_widths'])))
                self.target=target;self.editing=True;self.version=self.scene.content_version;self.load_values(p)
                self.apply_button.setText('更新門組');self.info.setText('正在修改：'+target.name);self.preview()
                return '已讀取；切換門片類型後先預覽，再按更新。'
            self.run(read)
        def preview(self):
            try:
                self.check(strict=self.editing)
                model=system.door_model(api,self.parameters());model.xform=QMatrix4x4(self.rigid)
                self.preview_group=model;self.preview_lines=api.parametric_preview(model);self.version=self.scene.content_version
                layout=system.door_layout(api,self.parameters());self.summary.setText(f'外尺寸 {layout["outer_width"]/10:g} × {layout["outer_height"]/10:g} cm｜各扇獨立、外框可保留')
                self.message('預覽完成；選取整個門組拉伸寬高，框料粗細維持設定。')
            except Exception as exc:self.preview_group=None;self.preview_lines=None;self.summary.setText(str(exc));self.message(str(exc),True);api._diagnostics.record('door.preview','error',error=exc)
            self.vp.update()
        def apply_model(self):
            def apply():
                self.check(strict=self.editing)
                if self.editing and api.selection(self.scene,True)!=[self.target]:raise ValueError('選取已變更，請重新讀取門組。')
                model=api._workflows.prepared_model(api,self,self.parameters(),lambda p:system.door_model(api,p)) if hasattr(api,'_workflows') else system.door_model(api,self.parameters())
                if self.editing:
                    _,_,rigid=api.effective_parameters(self.target)
                    api.execute(self.vp,UpdateDoor(self.target,model,rigid))
                else:
                    model.xform=QMatrix4x4(self.rigid);api.execute(self.vp,api.AddGenerated(model));self.target=model;self.editing=True
                self.version=self.scene.content_version;self.preview_group=None;self.preview_lines=None;self.apply_button.setText('更新門組')
                return '已套用門組；可 Ctrl＋Z 復原。'
            self.run(apply)
        def closeEvent(self,event):self._refresh_timer.stop();super().closeEvent(event)
        def done(self,result):self._refresh_timer.stop();super().done(result)
    return DoorWindow,UpdateDoor
