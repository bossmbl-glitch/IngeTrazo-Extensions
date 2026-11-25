"""Reuse the original controls in shorter, task-oriented tabs; never discard values."""
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QPainter,QPen,QColor
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QTabWidget,QScrollArea,QPushButton,QFrame,QLabel,QCheckBox,QSizePolicy
import numpy as np


def install(api):
    class GeometryPreview(QWidget):
        def __init__(self,window):super().__init__();self.window=window;self.setMinimumSize(200,300)
        def paintEvent(self,event):
            painter=QPainter(self);painter.fillRect(self.rect(),QColor('#f5f8fb'))
            group=getattr(self.window,'preview_group',None) or getattr(self.window,'_ux_last_valid_preview',None)
            if group is None:
                painter.drawText(self.rect().adjusted(8,8,-8,-8),Qt.AlignCenter|Qt.TextWordWrap,'請先完成設定並預覽');painter.end();return
            from importlib.util import spec_from_file_location,module_from_spec
            if not hasattr(self,'renderer'):
                spec=spec_from_file_location('_lime_front_geometry',api.ROOT/'lime_integrated_assets'/'geometry_preview.py');self.renderer=module_from_spec(spec);spec.loader.exec_module(self.renderer)
            rigid=getattr(self.window,'rigid',api.QMatrix4x4());key=(id(group),self.width(),self.height(),tuple(rigid.data()))
            if getattr(self,'cache_key',None)!=key:
                self.cache=self.renderer.render(api,group,self.width(),self.height(),rigid);self.cache_key=key
            painter.drawImage(self.rect(),self.cache);painter.end()

    def organize(window,kind):
        if getattr(window,'settings_tabs',None) is not None:return
        window.resize(900,820);window.setMinimumWidth(760)
        window.setStyleSheet(window.styleSheet()+'''QTabWidget::pane {border:0;} QTabBar::tab {padding:9px 12px;background:#eaf0f5;border-radius:4px;margin:2px;} QTabBar::tab:selected {background:#147acc;color:white;} QLineEdit,QComboBox,QDoubleSpinBox,QSpinBox {padding:5px;min-height:18px;}''')
        window._conversion_cards=[]
        items=[]
        while window.body.count():items.append(window.body.takeAt(0))
        header=api.label({'cabinet':'參數櫃體','door':'門片／門組','glass':'玻璃格子門（進階）','louver':'百葉門（進階）','decorative':'造型門（進階）'}[kind],'title');window.body.addWidget(header)
        mode=QHBoxLayout();mode.addWidget(api.button('新增',lambda:window.new()))
        mode.addWidget(api.button('讀取選取',window.read_target))
        if hasattr(window,'read_volume') and kind!='louver':mode.addWidget(api.button('量體轉換',window.read_volume))
        window.body.addLayout(mode)
        row=QHBoxLayout();window.settings_split=row;window.body.addLayout(row);tabs=QTabWidget();tabs.setObjectName('lime_settings_tabs');window.settings_tabs=tabs;row.addWidget(tabs,3)
        layouts={};forms={};pages={}
        for title in ('基本設定','框料／層板','配件','進階設定'):
            scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.NoFrame);page=QWidget();page.setObjectName('page');layout=QVBoxLayout(page);layout.setContentsMargins(8,10,8,10);layout.setSpacing(10);scroll.setWidget(page);tabs.addTab(scroll,title);layouts[title]=layout;pages[title]=page
        window.settings_pages=pages;window.settings_layouts=layouts
        for title in layouts:
            form=QFormLayout();form.setSpacing(8);layouts[title].addLayout(form);forms[title]=form
        basic={'width','height','depth','leaf_count','leaf_gap','leaf_thickness','glass_thickness','door_mode','glass_columns','center_gap','panels','direction','angle','target_gap','upper_gap','lower_gap','panel_gap'}
        frames={'side_width','belly_height','left','right','top','bottom','middle','lower_height','target_gap','panel_gap','outer_frame_style','outer_frame_width','sash_style','stile_width','rail_width','frame_depth','middle_enabled','middle_width','middle_position','upper_gap','lower_gap'}
        def route(key):
            if key in basic:return '基本設定'
            if key in frames or key.startswith(('frame_','cy_','classical_','glass_frame','shelf_')):return '框料／層板'
            if key.startswith(('handle','pull')):return '配件'
            return '進階設定'
        if hasattr(window,'numeric_lock_bar'):layouts['進階設定'].addWidget(window.numeric_lock_bar)
        field_keys={id(field):key for key,field in getattr(window,'fields',{}).items()}
        def keys(widget):return [field_keys[id(c)] for c in [widget,*widget.findChildren(QWidget)] if id(c) in field_keys]
        # Single-form original tools get their rows split across the same four tabs.
        if kind in ('glass','louver'):
            for item in items:
                widget=item.widget()
                if widget is None:continue
                for form in widget.findChildren(QFormLayout):
                    while form.rowCount():
                        result=form.takeRow(0);field=result.fieldItem;label=result.labelItem
                        if field is None:continue
                        target=field.widget();keylist=keys(target) if target is not None else []
                        title=route(keylist[0]) if keylist else '配件' if target is getattr(window,'library_handle',None) else '框料／層板'
                        if target is not None:
                            if label is not None and label.widget() is not None:forms[title].addRow(label.widget(),target)
                            else:forms[title].addRow(target)
            # The emptied form card is omitted below, while conversion controls remain.
        if kind=='door':
            old_card=window.louver_controls.parentWidget();window.retired_louver_card=old_card
            def move_row(form,widget,new_form,title):
                taken=form.takeRow(widget)
                if taken.labelItem is not None and taken.labelItem.widget() is not None:
                    label=taken.labelItem.widget();label.setText(title);new_form.addRow(label,widget)
                else:new_form.addRow(title,widget)
            old_width_form=next(f for f in old_card.findChildren(QFormLayout) if f.indexOf(window.widths)>=0)
            width_card=api.card(layouts['基本設定']);width_card.addWidget(api.label('各扇寬度'));wf=QFormLayout();width_card.addLayout(wf);move_row(old_width_form,window.widths,wf,'各扇指定寬度')
            old_form=window.louver_controls.layout()
            sections={}
            for attr,title,page in [('louver_basic','百葉葉片','基本設定'),('louver_frames','百葉框料與中橫條','框料／層板'),('louver_advanced','各扇個別配置','進階設定')]:
                card=api.card(layouts[page]);setattr(window,attr,card.parentWidget());card.addWidget(api.label(title));form=QFormLayout();card.addLayout(form);sections[attr]=form
            move_row(old_form,window.louver_sash,sections['louver_frames'],'百葉門扇框樣式')
            for key,widget in window.louver_fields.items():
                category='louver_basic' if key in ('angle','target_gap','upper_gap','lower_gap') else 'louver_frames'
                labels={'angle':'葉片角度','target_gap':'葉片目標淨距','upper_gap':'上區目標淨距','lower_gap':'下區目標淨距','middle_enabled':'建立中橫條','middle_width':'中橫條高度','middle_position':'橫條位置（由下至上 %）','stile_width':'左右立料寬','rail_width':'上下橫料高'}
                move_row(old_form,widget,sections[category],labels[key])
            move_row(old_form,window.middle_positions,sections['louver_advanced'],'各扇橫條位置')
            old_card.hide();window.sync_fields()
        preview_frame=QFrame();preview_layout=QVBoxLayout(preview_frame);preview_title=api.label('預覽');preview_title.setSizePolicy(QSizePolicy.Preferred,QSizePolicy.Fixed);preview_layout.addWidget(preview_title)
        preview=GeometryPreview(window);window.geometry_preview=preview;preview_layout.addWidget(preview)
        if kind=='cabinet':window.elevation_toggle.hide();window.elevation.hide()
        window.settings_preview_frame=preview_frame;preview_frame.setMaximumWidth(290);row.addWidget(preview_frame,2)
        for item in items:
            widget=item.widget()
            if widget is None:
                if item.layout() is not None:layouts['進階設定'].addLayout(item.layout())
                continue
            if widget is getattr(window,'retired_louver_card',None):widget.hide();continue
            if widget is getattr(window,'frame_profile_card',None):layouts['基本設定'].insertWidget(1,widget);continue
            if widget is getattr(window,'elevation',None) or (kind=='cabinet' and widget.isAncestorOf(window.elevation)):widget.hide();continue
            if isinstance(widget,QPushButton) and ('讀取' in widget.text()):widget.hide();continue
            if isinstance(widget,QLabel):
                if widget is getattr(window,'info',None) or widget is getattr(window,'summary',None):layouts['基本設定'].addWidget(widget)
                else:widget.hide()
                continue
            if kind in ('glass','louver'):
                # Keep the conversion card and notes, omit exhausted numeric form cards.
                labels=' '.join(l.text() for l in widget.findChildren(QLabel));buttons=widget.findChildren(QPushButton)
                if not buttons:widget.hide();continue
                title='基本設定' if '轉換' in labels else '進階設定'
            else:
                ks=keys(widget);labels=' '.join(l.text() for l in widget.findChildren(QLabel))
                if (hasattr(window,'door_style') and widget.isAncestorOf(window.door_style)) or (hasattr(window,'combos') and any(widget.isAncestorOf(c) for c in window.combos.values())):title='基本設定'
                elif 'width' in ks or 'leaf_count' in ks:title='基本設定'
                elif any(k.startswith(('frame_','shelf_','glass_frame','classical_','cy_')) or k in frames for k in ks):title='框料／層板'
                elif any('AssetPicker' in type(c).__name__ for c in widget.findChildren(QWidget)) or '線板' in labels:title='配件'
                elif '門片樣式' in labels or '面板配置' in labels or '門片類型' in labels:title='基本設定'
                elif '量體轉' in labels or '塊體轉' in labels:title='基本設定'
                else:title='進階設定'
            if any('轉' in l.text() and ('量體' in l.text() or '塊體' in l.text()) for l in widget.findChildren(QLabel)) and any('點選' in b.text() for b in widget.findChildren(QPushButton)):
                window._conversion_cards.append(widget)
            layouts[title].addWidget(widget)
        if kind=='door':
            basic_layout=layouts['基本設定']
            for index,widget in enumerate([window.combos['leaf_type'].parentWidget(),window.frame_profile_card,window.info,window.fields['width'].parentWidget(),width_card.parentWidget(),window.louver_basic,window.summary]):basic_layout.insertWidget(index,widget)
            advanced=api.card(layouts['進階設定']);advanced.addWidget(api.label('原型工具（獨立門片）','muted'))
            for title,key in [('玻璃格子門細項','glass'),('百葉原型細項','louver'),('造型門原型細項','decorative')]:
                def open_legacy(k=key):
                    selected=list(window.scene.selection);editing=len(selected)==1 and (getattr(selected[0],'ext',{}) or {}).get(api.PARAM_KEY,{}).get('kind')==k
                    return api.show_window(window.app,k+('_edit' if editing else '_new'))
                advanced.addWidget(api.button(title,open_legacy))
        for layout in layouts.values():layout.addStretch()
        def refresh():
            for widget in window._conversion_cards:widget.setVisible(getattr(window,'_conversion_source',None) is not None)
            # Hide complete irrelevant rows instead of showing a page of disabled values.
            for form in window.findChildren(QFormLayout):
                for i in range(form.rowCount()):
                    field=form.itemAt(i,QFormLayout.FieldRole)
                    if field is None or field.widget() is None:continue
                    widget=field.widget()
                    if any(c.property('limeHiddenByDesign') for c in [widget,*widget.findChildren(QWidget)]):form.setRowVisible(i,False);continue
                    controls=[c for c in [widget,*widget.findChildren(QWidget)] if isinstance(c,(api.QComboBox,api.QDoubleSpinBox,api.QSpinBox,QCheckBox,api.QLineEdit))]
                    if controls:
                        enabled=any(c.isEnabled() for c in controls);form.setRowVisible(i,enabled)
                        if enabled:
                            for control in controls:
                                if control.isEnabled():
                                    control.show()
                                    if hasattr(control,'lock_button'):control.lock_button.show()
            preview.update()
        window._settings_refresh=QTimer(window);window._settings_refresh.setInterval(30);window._settings_refresh.setSingleShot(True);window._settings_refresh.timeout.connect(refresh);window._settings_refresh.start();window.destroyed.connect(window._settings_refresh.stop)
        for control in window.findChildren(QWidget):
            signal=getattr(control,'valueChanged',None) or getattr(control,'currentIndexChanged',None) or getattr(control,'toggled',None) or getattr(control,'textChanged',None)
            if signal is not None:signal.connect(lambda *_,w=window:w._settings_refresh.start())
        window.refresh_settings=refresh;refresh()
    def new(window):
        if hasattr(window,'cancel_workflow_volume'):window.cancel_workflow_volume()
        if hasattr(window,'stop_front_pick'):window.stop_front_pick(False)
        if getattr(window,'_conversion_source',None) is not None:window.cancel_conversion()
        window.target=None;window.editing=False;window.rigid=api.QMatrix4x4();window.context=window.scene.edit_group;window.version=window.scene.content_version
        if hasattr(window,'_last_model_snapshot'):window._last_model_snapshot=None
        window.apply_button.setText('建立');window.info.setText('新增：調整後預覽，再按建立。');window.preview()
    def wrap(base,kind):
        class Organized(base):
            def __init__(self,app,*args,**kwargs):
                if kind=='parametric':
                    super().__init__(app,*args,**kwargs);current=args[0] if args else kwargs['kind'];organize(self,current)
                else:
                    super().__init__(app,*args,**kwargs);organize(self,kind)
                    items=list(self.scene.selection)
                    valid=('cabinet',) if kind=='cabinet' else ('door','decorative','glass','louver') if kind=='door' else ('decorative',)
                    if len(items)==1 and (getattr(items[0],'ext',{}) or {}).get(api.PARAM_KEY,{}).get('kind') in valid and not self.editing:self.read_target()
            def showEvent(self,event):
                super().showEvent(event)
                if hasattr(self,'refresh_settings'):self.refresh_settings()
            def done(self,result):
                if hasattr(self,'_settings_refresh'):self._settings_refresh.stop()
                super().done(result)
            def closeEvent(self,event):
                if hasattr(self,'_settings_refresh'):self._settings_refresh.stop()
                super().closeEvent(event)
        Organized.__name__=base.__name__;return Organized
    return wrap(api.CabinetWindow,'cabinet'),wrap(api.DoorWindow,'door'),wrap(api.ParametricWindow,'parametric'),wrap(api.DecorativeWindow,'decorative')
