"""Shared, explicit toolbox operations. No automatic saving of modeling defaults.

GUI helpers only change drafts. Model/material writes are history commands;
selection highlights, template inspection and database analysis are read-only.
"""
from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
import ast,hashlib,json,math,re,unicodedata
from PySide6.QtCore import Qt,QTimer,QSignalBlocker,QRectF,QPointF
from PySide6.QtGui import QColor,QPainter,QPen,QVector3D,QMatrix4x4
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,
    QPushButton,QLabel,QComboBox,QCheckBox,QLineEdit,QTreeWidget,QTreeWidgetItem,
    QMessageBox,QProgressDialog,QApplication,QDialogButtonBox,QDoubleSpinBox)

ALIASES={'百頁':'百葉','百業':'百葉','插座庫':'配件資料庫','把手庫':'配件資料庫','面板庫':'配件資料庫',
         '拉高':'高度','拉伸':'高度','對中':'置中','對齊中心':'置中','報錯':'診斷','回報':'診斷'}
DIMENSIONS={'width','height','depth'}
POSITION={'angle','stand','sill','center_height','floor_mm','floor_m','position','origin','front'}
NAMES={'width':'寬度','height':'高度','depth':'深度','handle_asset':'把手款式','handle_position':'把手位置',
       'handle_z':'垂直邊距','handle_x':'水平邊距','handle_stand':'離安裝面','door_style':'門片款式'}
LENGTH_KEYS=DIMENSIONS|{'stand','sill','center_height','handle_x','handle_z','handle_stand','handle_spacing',
   'left','right','top','bottom','recess','frame_left','frame_right','frame_top','frame_bottom','glass_thickness'}


def normalize_tool_query(text):
    result=unicodedata.normalize('NFKC',str(text)).strip().casefold()
    for key,value in ALIASES.items():result=result.replace(key,value)
    return result


def calculate_length(text,default_unit='cm'):
    text=unicodedata.normalize('NFKC',str(text)).strip().lower()
    unit=default_unit
    match=re.search(r'(mm|cm|m|毫米|公分|公尺)\s*$',text)
    if match:unit=match[1];text=text[:match.start()].strip()
    if re.search(r'[+\-]{2}',text):raise ValueError('請用括號區分正負號，例如 20-(-3)。')
    if len(text)>100:raise ValueError('計算式過長。')
    try:tree=ast.parse(text,mode='eval')
    except (ValueError,SyntaxError):raise ValueError('請輸入長度或簡單計算，例如 120/3 cm。') from None
    if sum(1 for _ in ast.walk(tree))>40:raise ValueError('計算式過於複雜。')
    def number(node,depth=0):
        if depth>10:raise ValueError('計算式層數過多。')
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):value=float(node.value)
        elif isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):value=number(node.operand,depth+1)*(1 if isinstance(node.op,ast.UAdd) else -1)
        elif isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
            a,b=number(node.left,depth+1),number(node.right,depth+1)
            if isinstance(node.op,ast.Div) and abs(b)<1e-15:raise ValueError('除數不可為零。')
            value=a+b if isinstance(node.op,ast.Add) else a-b if isinstance(node.op,ast.Sub) else a*b if isinstance(node.op,ast.Mult) else a/b
        else:raise ValueError('只支援數字、括號與加減乘除。')
        if not math.isfinite(value) or abs(value)>1e9:raise ValueError('長度超出可計算範圍。')
        return value
    return number(tree.body)*{'mm':1,'毫米':1,'cm':10,'公分':10,'m':1000,'公尺':1000}[unit]


def value_text(key,value):
    if isinstance(value,dict):
        if 'name' in value:return str(value['name'])
        return '、'.join(str(k) for k in value)[:180] or '無'
    if isinstance(value,list):return '、'.join(str(x) for x in value[:6])+('…' if len(value)>6 else '')
    if value is None:return '無／沿用'
    if isinstance(value,bool):return '是' if value else '否'
    if isinstance(value,(int,float)):
        return f'{value/10:g} cm' if key in LENGTH_KEYS or key.startswith(('cy_','classical_','frame_')) and not isinstance(value,bool) else f'{value:g}'
    return str(value)


def category(key):
    if key in DIMENSIONS:return 'dimensions'
    if key in POSITION:return 'position'
    if 'handle' in key or 'profile_snapshot' in key or key=='asset':return 'accessories'
    return 'style'


def merge_template(current,saved,scope):
    allowed={'all':{'dimensions','position','style','accessories'},'dimensions':{'dimensions'},
             'style':{'style'},'accessories':{'accessories'},'appearance':{'style','accessories'}}[scope]
    result=deepcopy(current)
    for key,value in saved.items():
        if category(key) in allowed:result[key]=deepcopy(value)
    return result


def texture_rotated(texture,normal,angle):
    from core.texture import face_uv_axes
    gu,cu,gv,cv=face_uv_axes(texture,normal);a=math.radians(angle);c,s=math.cos(a),math.sin(a)
    u=[gu.x(),gu.y(),gu.z(),cu];v=[gv.x(),gv.y(),gv.z(),cv]
    result=deepcopy(texture);result.pop('rot',None);result['uvw']=[c*x-s*y for x,y in zip(u,v)]+[s*x+c*y for x,y in zip(u,v)]
    return result


def part_role(node,inherited='all'):
    ext=node.ext or {};text=' '.join(str(ext.get(k,'')) for k in ('lime_door_role','lime_decorative_role','lime_window_part','lime_kitchen_part','lime_cabinet_part'))
    if ext.get('lime_handle_id') or ext.get('lime_cabinet_handle_pair') or 'c_handle' in text or node.name.startswith(('把手 ','正面把手','反面把手')):return 'handles'
    if ext.get('lime_cabinet_part')=='body':return 'body'
    if ext.get('lime_cabinet_part')=='front' or 'leaf' in text:return 'fronts'
    if 'frame' in text or ('框' in node.name and inherited=='all'):return 'frames'
    return inherited


def install(ns):
    api=SimpleNamespace(**ns);release=api._release_tools;workflow=api._workflows
    ns['normalize_tool_query']=normalize_tool_query;ns['calculate_length']=calculate_length;release.parse_length=calculate_length
    def text_from_value(self,value):
        shown=float(value)/(10 if getattr(self,'_length_mode',True) else 1)
        text=f'{shown:.3f}'.rstrip('0').rstrip('.')
        return text if text and text!='-0' else '0'
    api.LengthSpinBox.textFromValue=text_from_value

    def validate(kind,p):
        if kind=='cabinet':return ns['cabinet_layout'](p)
        if kind=='door':return api._door_system.door_layout(api._door_api,p)
        if kind=='window':return ns['window_layout'](p)
        if kind=='electrical_panel':return api._electrical_panels.parameters(api._feature_api,p)
        return ns[kind+'_layout'](p)
    def parameters_for(window):
        return deepcopy(window.parameters()) if hasattr(window,'parameters') else deepcopy(getattr(window,'patch',{}))
    def state(window):
        try:return release.state_key(parameters_for(window))
        except Exception:return None
    def window_context_valid(window):
        scene=getattr(window,'scene',None);vp=getattr(window,'vp',None)
        if scene is None or vp is None or vp.scene is not scene or scene.edit_group is not getattr(window,'context',None):return False
        target=getattr(window,'target',None)
        return not getattr(window,'editing',False) or target is not None and target in scene.selection

    def dialog_table(parent,title,headers,rows):
        d=QDialog(parent);d.setWindowTitle(title);d.resize(720,500);d.setStyleSheet(parent.styleSheet());layout=QVBoxLayout(d);tree=QTreeWidget();tree.setHeaderLabels(headers)
        for row in rows:tree.addTopLevelItem(QTreeWidgetItem([str(v) for v in row]))
        for col in range(len(headers)):tree.resizeColumnToContents(col)
        layout.addWidget(tree);close=QDialogButtonBox(QDialogButtonBox.Close);close.rejected.connect(d.reject);layout.addWidget(close);d.tree=tree;d.show();parent._ux_detail_dialog=d;return d

    def template_rows(window,name=None):
        key=name or window.template_combo.currentData();row=window.template_data().get('templates',{}).get(window.kind,{}).get(key)
        if not row:raise ValueError('請先選擇一個範本。')
        return row
    def sync_aspect(window):
        if hasattr(window,'aspect_link') and window.aspect_link.isChecked():
            width,height=window.fields['width'].value(),window.fields['height'].value()
            window._ux_ratio=width/height if height else 1.
    def apply_draft(window,p):
        widgets=workflow.controls(window);blockers=[QSignalBlocker(w) for w in {id(v):v for v in widgets.values()}.values()];loading=getattr(window,'_front_loading',False);window._front_loading=True
        try:
            if hasattr(window,'load_values'):window.load_values(deepcopy(p))
            else:
                for key,field in getattr(window,'fields',{}).items():
                    if key not in p:continue
                    if isinstance(field,QCheckBox):field.setChecked(bool(p[key]))
                    elif isinstance(field,QComboBox):field.setCurrentIndex(field.findData(p[key]))
                    elif isinstance(p[key],(int,float)) and not isinstance(p[key],bool):field.setValue(p[key])
                combos={**getattr(window,'combos',{})}
                for key,value in vars(window).items():
                    if isinstance(value,QComboBox) and key in p:combos[key]=value
                for key,combo in combos.items():
                    if key in p and combo.findData(p[key])>=0:combo.setCurrentIndex(combo.findData(p[key]))
                if hasattr(window,'accessories'):window.accessories.load(p)
                if hasattr(window,'library_handle'):window.library_handle.load(p.get('handle_asset'))
                lists={'heights':'drawer_heights','widths':'leaf_widths' if window.kind=='door' else 'panel_widths','louver_widths':'panel_widths','louver_positions':'middle_positions','middle_positions':'middle_positions'}
                for attr,key in lists.items():
                    widget=getattr(window,attr,None)
                    if widget is not None and isinstance(p.get(key),list):widget.setText(', '.join('auto' if v is None else f'{v if key=="middle_positions" else v/10:g}' for v in p[key]))
            if window.kind=='cabinet':window._fronts=deepcopy(p.get('front_settings',{}));window._columns=deepcopy(p.get('drawer_column_heights',{}))
            window._front_rejected=False
        finally:del blockers;window._front_loading=loading
        sync_aspect(window)
        if hasattr(window,'sync_fields'):window.sync_fields()
        if window.kind=='cabinet' and hasattr(window,'sync_front_visibility'):window.sync_front_visibility();window.select_front(getattr(window,'selected_front',0))
        window._preview_fingerprint=None;window.preview()
    ns['apply_parameter_draft']=apply_draft

    def defaults(window):
        kind=window.kind;p=parameters_for(window)
        if kind=='window':return api._windows.defaults(api._window_api,p.get('preset','W01'))
        if kind=='cabinet':return deepcopy(api.CABINET_DEFAULTS)
        if kind=='door':return {**deepcopy(api.DECORATIVE_DEFAULTS),**deepcopy(api._door_system.DOOR_DEFAULTS)}
        if kind=='decorative':return deepcopy(api.DECORATIVE_DEFAULTS)
        if kind=='glass':return deepcopy(ns['glass_layout']())
        if kind=='louver':return deepcopy(ns['louver_layout']()['params'])
        return deepcopy(api._electrical_panels.DEFAULTS)
    def reset_section(window,which):
        current=parameters_for(window);base=defaults(window);result=deepcopy(current)
        def selected(key):
            return category(key)=='accessories' if which=='accessories' else key.startswith(('frame_','cy_','classical_','glass_frame_')) or key in ('left','right','top','bottom','recess','profile_width','profile_height','rect_width','rect_depth','stile_width','rail_width','mullion_width','cross_width','sash_width','outer_frame_width','side_width','belly_height')
        for key in base:
            if selected(key):result[key]=deepcopy(base[key])
        if window.kind=='door':
            for group,default in [('louver_options',api._door_system.LOUVER_DEFAULTS),('glass_options',ns['glass_layout']())]:
                for key,value in default.items():
                    if selected(key):result.setdefault(group,{})[key]=deepcopy(value)
        if which=='accessories':
            for config in result.get('front_settings',{}).values():
                for key in list(config):
                    if category(key)=='accessories':config.pop(key,None)
        validate(window.kind,result);apply_draft(window,result);return '已恢復'+('配件' if which=='accessories' else '框料')+'預設；尺寸、位置與款式保留，尚未寫入模型。'

    def partial_template(window,name):
        scope=window.template_scope.currentData();saved=template_rows(window,name);current=parameters_for(window);merged=merge_template(current,saved['params'],scope);validate(window.kind,merged)
        try:apply_draft(window,merged)
        except Exception:apply_draft(window,current);raise
        return '範本已帶入指定項目；未勾選範圍保留，預覽後再套用。'

    def inspect_changes(window):
        if hasattr(window,'patch'):
            active=[g for g in window.targets if g.uid not in getattr(window,'_ux_excluded',set())];rows=[]
            for g in active:
                p=ns['effective_parameters'](g)[1]
                rows.extend((g.name,NAMES.get(k,k),value_text(k,p.get(k)),value_text(k,v)) for k,v in window.patch.items())
        else:
            target=getattr(window,'target',None);before=ns['effective_parameters'](target)[1] if target else getattr(window,'_ux_initial_parameters',{})
            after=parameters_for(window);rows=[(getattr(target,'name','新增物件'),NAMES.get(k,k),value_text(k,before.get(k)),value_text(k,v)) for k,v in after.items() if before.get(k)!=v]
        return dialog_table(window,'修改前後',['物件','項目','原設定','修改後'],rows or [('—','尚無修改','—','—')])

    def part_owner(app,entity):
        tree=release.hierarchy(app.scene)
        if id(entity) in tree:group,parents=tree[id(entity)]
        else:
            found=next(((g,parents) for g,parents in tree.values() if entity in g.mesh.faces or entity in g.mesh.edges),None)
            if found is None:return None
            group,parents=found
        return next((g for g in [group,*reversed(parents)] if (g.ext or {}).get(api.PARAM_KEY,{}).get('kind') in release.KINDS),None)
    ns['parameter_owner']=part_owner

    def locate_selection(app):
        selected=list(app.scene.selection);owners=[]
        for entity in selected:
            owner=part_owner(app,entity)
            if owner is not None and owner not in owners:owners.append(owner)
        if not owners:raise ValueError('請選取參數物件或其門片／把手；一般群組請使用量體轉換。')
        uids=[g.uid for g in owners]
        while app.scene.edit_group is not None:
            context=app.scene.edit_group.children or []
            if all(g in context for g in owners):break
            app.scene.end_group_edit();mapping=app.scene.groups_by_uid();owners=[mapping[uid] for uid in uids if uid in mapping]
        app.scene.selection.clear();app.scene.selection.update(owners);app.viewport.notify_scene_changed();return owners
    ns['locate_parameter_selection']=locate_selection

    def highlight_overlay(app,vp,painter):
        window=getattr(app.window,'_lime_ux_multi_window',None)
        if window is None or not getattr(window,'_ux_highlight',False) or not window.isVisible() or vp is not window.vp:return
        if not window.check_selection():return
        if getattr(window,'_ux_highlight_revision',None)!=app.scene.content_version:
            window._ux_highlight_tree=release.hierarchy(app.scene);window._ux_highlight_revision=app.scene.content_version
        painter.save();painter.setPen(QPen(QColor('#e18b25'),2));painter.setBrush(Qt.NoBrush)
        for group in window.targets:
            if group.uid in window._ux_excluded:continue
            lo,hi=api.bounds([group]);corners=api.np.array([[x,y,z] for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])])
            parent=QMatrix4x4()
            for ancestor in window._ux_highlight_tree[id(group)][1]:parent=parent*(ancestor.xform if ancestor.xform is not None else QMatrix4x4())
            matrix=api.np.asarray(parent.data(),float).reshape(4,4).T;corners=corners@matrix[:3,:3].T+matrix[:3,3];xs,ys,front=vp.world_to_pixels(corners)
            for index in range(8):
                for bit in (1,2,4):
                    other=index^bit
                    if index<other and front[index] and front[other]:painter.drawLine(QPointF(float(xs[index]),float(ys[index])),QPointF(float(xs[other]),float(ys[other])))
        painter.restore()
    old_overlay=ns['draw_overlay']
    def overlay(app,vp,painter):old_overlay(app,vp,painter);highlight_overlay(app,vp,painter)
    ns['draw_overlay']=overlay

    def inherited_handle_ui(window):
        if not hasattr(window,'front_mode'):return
        mode=window.front_mode.currentData();p=parameters_for(window);asset=p.get('handle_asset');window.inherited_handle.setVisible(mode=='inherit');window.inherited_handle.setText('沿用整櫃：'+(asset.get('name','未命名') if asset else '不安裝把手'))
        none=mode=='none'
        for button in window.position_buttons.values():button.setVisible(not none)
        for text in window.front_editor.findChildren(QLabel):
            if text.text()=='把手位置':text.setVisible(not none)
        if none:window.position_details.hide();window.edge_row.hide()
        fixed=window.fixed_height.isChecked();window.fixed_badge.setText(f'固定 {window.front_height.value()/10:g} cm' if fixed else '未指定高度，分配剩餘空間')

    def enhance_model(window):
        if getattr(window,'_ux_ready',False):return
        window._ux_ready=True;window._ux_preview_valid=True;window._ux_last_valid_preview=getattr(window,'preview_group',None);window._ux_loading=False
        try:window._ux_initial_parameters=parameters_for(window)
        except Exception:window._ux_initial_parameters={}
        window._ux_clean_state=state(window);kind=getattr(window,'kind','cabinet_many');window._ux_kind=kind
        window.preview_notice=api.label('','muted');window.preview_notice.setWordWrap(True);window.body.addWidget(window.preview_notice);window.preview_notice.hide()
        if hasattr(window,'front_mode'):
            window.inherited_handle=api.label('','muted');window.front_scroll.widget().layout().insertWidget(2,window.inherited_handle);inherited_handle_ui(window)
        if hasattr(window,'fields') and all(k in window.fields for k in ('width','height')) and all(hasattr(window.fields[k],'value') for k in ('width','height')):
            window.aspect_link=QCheckBox('等比例調整寬高');window.body.insertWidget(3,window.aspect_link);window._ux_aspect_guard=False;window._ux_ratio=1.
            def toggle(on):
                w,h=window.fields['width'].value(),window.fields['height'].value()
                if on and min(w,h)<=0:window.aspect_link.setChecked(False);window.message('寬高須大於零才能連動。',True);return
                window._ux_ratio=w/h if h else 1.
            window.aspect_link.toggled.connect(toggle)
            def link(key):
                if window._ux_aspect_guard or window._ux_loading or not window.aspect_link.isChecked():return
                other='height' if key=='width' else 'width';field=window.fields[other];value=window.fields[key].value()/(window._ux_ratio if key=='width' else 1/window._ux_ratio)
                if not field.minimum()<=value<=field.maximum():window.message('等比例尺寸超出可用範圍。',True);return
                window._ux_aspect_guard=True
                try:
                    with QSignalBlocker(field):field.setValue(value)
                    window.preview()
                finally:window._ux_aspect_guard=False
            for key in ('width','height'):window.fields[key].valueChanged.connect(lambda *_,k=key:link(k))
        if hasattr(window,'template_combo'):
            row=QHBoxLayout();window.template_scope=QComboBox()
            for text,value in [('全部設定','all'),('只套尺寸','dimensions'),('只套款式／框料','style'),('只套配件','accessories'),('款式與配件，保留尺寸','appearance')]:window.template_scope.addItem(text,value)
            row.addWidget(window.template_scope,1);row.addWidget(api.button('預覽範本內容',lambda:window.run(lambda:show_template(window))))
            panel=getattr(window,'template_panel',None)
            if panel is not None:panel.layout().addLayout(row)
            else:window.body.insertLayout(3,row)
        if hasattr(window,'parameters') and kind in ('cabinet','door','window','glass','louver','decorative'):
            row=QHBoxLayout();row.addWidget(api.button('查看修改前後',lambda:inspect_changes(window)));row.addWidget(api.button('框料恢復款式預設',lambda:window.run(lambda:reset_section(window,'frames'))));row.addWidget(api.button('配件恢復預設',lambda:window.run(lambda:reset_section(window,'accessories'))))
            layout=getattr(window,'settings_layouts',{}).get('進階設定')
            if layout is not None:layout.insertLayout(0,row)
            else:window.body.addLayout(row)
        if kind=='electrical_panel':
            row=QHBoxLayout();window.placed_count=api.label('本次已放置 0 個','muted');row.addWidget(window.placed_count,1);row.addWidget(api.button('復原最後放置',window.undo_last_placement));window.body.insertLayout(3,row)
        if hasattr(window,'patch'):enhance_multi(window)
        guide={'cabinet':'基準：櫃體局部座標；面板邊距以各片自己的邊界計算。','door':'基準：框內淨開口；門片退入沿正面深度方向。','window':'基準：地板標高與窗台；離牆正值朝外。','electrical_panel':'基準：藍色安裝面；紅箭頭朝外，離牆正值朝外、負值嵌入。'}
        window.context_hint=api.label(guide.get(kind,'預覽不改動模型；建立／更新後可 Ctrl＋Z 復原。'),'muted');window.body.addWidget(window.context_hint)
        window._ux_timer=QTimer(window);window._ux_timer.setInterval(350);window._ux_timer.timeout.connect(lambda:refresh_context(window));window._ux_timer.start();refresh_context(window)
        # Ensure actions remain in the bottom region; PaintWindow is handled separately.
        window.setStyleSheet(window.styleSheet()+' QTreeWidget {alternate-background-color:#edf3f8;}')

    def refresh_context(window):
        if not window._ux_ready:return
        if hasattr(window,'front_mode'):
            try:inherited_handle_ui(window)
            except Exception:pass
        for combo in window.findChildren(QComboBox):
            signature=(combo.count(),combo.currentText())
            if getattr(combo,'_ux_tooltip_signature',None)!=signature:
                for index in range(combo.count()):combo.setItemData(index,combo.itemText(index),Qt.ToolTipRole)
                combo.setToolTip(combo.currentText());combo._ux_tooltip_signature=signature
        if hasattr(window,'placed_count'):window.placed_count.setText(f'本次已放置 {len(getattr(window,"_placement_history",[]))} 個')
        target=getattr(window,'target',None)
        if hasattr(window,'mode_banner') and getattr(window,'active_picker',None) is None:
            if getattr(window,'editing',False) and target is not None:window.mode_banner.setText('目前模式：修改選取｜'+target.name+'｜預覽後按更新')
            elif getattr(window,'source',None) is None and getattr(window,'_conversion_source',None) is None:window.mode_banner.setText('目前模式：新增｜調整設定、預覽後建立')
        accessories=getattr(window,'accessories',None)
        if accessories is not None and hasattr(accessories,'handle'):
            try:enabled=accessories.handle.value() is not None
            except Exception:enabled=False
            for form in window.findChildren(QFormLayout):
                for widget in [getattr(accessories,'position',None),*getattr(accessories,'numbers',{}).values()]:
                    if widget is None:continue
                    target=getattr(widget,'_lock_wrapper',None) or widget
                    if form.indexOf(target)>=0:form.setRowVisible(target,enabled)

    def show_template(window):
        row=template_rows(window);merged=merge_template(parameters_for(window),row['params'],window.template_scope.currentData());rows=[(NAMES.get(k,k),value_text(k,v),'將套用' if parameters_for(window).get(k)!=v else '維持') for k,v in merged.items()]
        dialog_table(window,'範本內容',['項目','內容','結果'],rows);return '範本預覽已開啟，尚未改動設定。'

    def enhance_multi(window):
        window._ux_excluded=set();window._ux_highlight=False;window.app.window._lime_ux_multi_window=window
        row=QHBoxLayout();window.highlight_button=QPushButton('在模型標示修改範圍');window.highlight_button.setCheckable(True);row.addWidget(window.highlight_button);row.addWidget(api.button('查看修改前後',lambda:inspect_changes(window)));window.body.insertLayout(2,row)
        window.highlight_button.toggled.connect(lambda checked:(setattr(window,'_ux_highlight',checked),window.vp.update()))
        window.selection_list.itemChanged.connect(lambda item:exclusion_changed(window,item));fill_exclusions(window)
    def fill_exclusions(window):
        window._ux_exclusion_loading=True
        try:
            for index,group in enumerate(window.targets):
                item=window.selection_list.item(index)
                if item is None:continue
                item.setData(Qt.UserRole,group.uid);item.setFlags(item.flags()|Qt.ItemIsUserCheckable);item.setCheckState(Qt.Unchecked if group.uid in window._ux_excluded else Qt.Checked);item.setToolTip(item.text())
        finally:window._ux_exclusion_loading=False
    def exclusion_changed(window,item):
        if window._ux_exclusion_loading:return
        uid=item.data(Qt.UserRole)
        if uid is None:return
        if item.checkState()==Qt.Checked:window._ux_excluded.discard(uid)
        else:window._ux_excluded.add(uid)
        window._preview_timer.start();window.update_summary();window.vp.update()

    # Instance methods are patched at the outer class; early constructors remain unchanged.
    classes=[]
    for name in ('CabinetWindow','SingleCabinetWindow','MultiCabinetWindow','DoorWindow','ParametricWindow','DecorativeWindow','PanelWindow','WindowWindow'):
        cls=ns[name]
        if cls not in classes:classes.append(cls)
    snapshots={cls:{name:getattr(cls,name,None) for name in ('__init__','preview','apply_model','read_target','select_front','read_selection','update_summary','load_template','closeEvent','done')} for cls in classes}
    for cls in classes:
        saved=snapshots[cls]
        def init(self,*args,_old=saved['__init__'],**kwargs):
            _old(self,*args,**kwargs);enhance_model(self)
        cls.__init__=init
        if saved['preview']:
            def preview(self,*args,_old=saved['preview'],**kwargs):
                if not getattr(self,'_ux_ready',False):return _old(self,*args,**kwargs)
                targets=getattr(self,'targets',None);active=None
                if targets is not None:
                    active=[g for g in targets if g.uid not in self._ux_excluded];self.targets=active
                try:result=_old(self,*args,**kwargs)
                finally:
                    if targets is not None:self.targets=targets
                group=getattr(self,'preview_group',None);valid=group is not None and not getattr(self,'_front_rejected',False) and getattr(self,'_valid',True)
                if valid:self._ux_last_valid_preview=group;self._ux_preview_valid=True;self.preview_notice.hide()
                else:
                    self._ux_preview_valid=False
                    if not window_context_valid(self) and targets is None:self._ux_last_valid_preview=None
                    self.preview_notice.setText('新輸入尚未採用；目前顯示上一個有效預覽。' if self._ux_last_valid_preview else '請修正設定並重新預覽。');self.preview_notice.show()
                    if hasattr(self,'apply_button'):self.apply_button.setEnabled(False)
                refresh_context(self)
                if active:
                    self.preview_caption.setText('代表：'+active[0].name+(f'｜另有 {len(active)-1} 個' if len(active)>1 else ''))
                return result
            cls.preview=preview
        if saved['apply_model']:
            def apply(self,*args,_old=saved['apply_model'],**kwargs):
                if not getattr(self,'_ux_ready',False):return _old(self,*args,**kwargs)
                if hasattr(self,'front_editor_dirty') and self.front_editor_dirty() and hasattr(self,'flush_front_edit'):
                    if not self.flush_front_edit():return
                if not getattr(self,'_ux_preview_valid',True):
                    self.preview()
                    if not self._ux_preview_valid:self.message('新設定尚未通過驗證，未修改模型。',True);return
                before=self.app.scene.content_version;patch=deepcopy(getattr(self,'patch',{}));target=getattr(self,'target',None)
                stored=(getattr(target,'ext',None) or {}).get(api.PARAM_KEY,{})
                same_kind=stored.get('kind')==getattr(self,'kind',None)
                current_geometry=getattr(self,'kind',None)!='door' or stored.get('geometry_revision',0)>=2
                if target is not None and same_kind and current_geometry and getattr(self,'editing',False) and state(self)==self._ux_clean_state and window_context_valid(self):
                    try:
                        scale,_=ns['parametric_transform'](target)
                        if api.np.allclose(scale,1.,atol=1e-6):self.message('設定與原物件相同，未重建，也未新增復原紀錄。');return
                    except Exception:pass
                self.app.window._lime_ux_preparing=self if hasattr(self,'targets') else None
                try:result=_old(self,*args,**kwargs)
                finally:self.app.window._lime_ux_preparing=None
                if self.app.scene.content_version!=before:
                    self._ux_clean_state=state(self)
                    if patch:
                        plan=getattr(self,'_ux_last_plan',{});count=len(plan.get('targets',[]));skips=plan.get('skipped',[])
                        self.message(f'已更新 {count} 個櫃體的'+ '、'.join(NAMES.get(k,k) for k in patch)+'。'+self.preserved.text()+' Ctrl＋Z 可一次復原。'+release.report_text(plan.get('targets',[]),skips,True))
                refresh_context(self);return result
            cls.apply_model=apply
        if saved['read_target']:
            def read(self,*args,_old=saved['read_target'],**kwargs):
                result=_old(self,*args,**kwargs)
                if getattr(self,'_ux_ready',False):sync_aspect(self);self._ux_clean_state=state(self);refresh_context(self)
                return result
            cls.read_target=read
        if saved['select_front']:
            def select(self,*args,_old=saved['select_front'],**kwargs):
                result=_old(self,*args,**kwargs)
                if getattr(self,'_ux_ready',False):inherited_handle_ui(self)
                return result
            cls.select_front=select
        if saved['load_template']:
            def load_template(self,name,_old=saved['load_template']):
                if not getattr(self,'_ux_ready',False) or self.template_scope.currentData()=='all':
                    result=_old(self,name);sync_aspect(self);return result
                return partial_template(self,name)
            cls.load_template=load_template
        for event_name in ('closeEvent','done'):
            old=saved[event_name]
            if old is None:continue
            def finish(self,*args,_old=old,**kwargs):
                result=_old(self,*args,**kwargs)
                if getattr(self,'_ux_ready',False) and not self.isVisible():self._ux_timer.stop();self._ux_highlight=False;self._ux_last_valid_preview=None;self.app.viewport.update()
                return result
            setattr(cls,event_name,finish)
        if saved['read_selection']:
            def read_selection(self,*args,_old=saved['read_selection'],**kwargs):
                excluded=set(getattr(self,'_ux_excluded',set())) if getattr(self,'busy',False) else set();result=_old(self,*args,**kwargs)
                if getattr(self,'_ux_ready',False):self._ux_excluded=excluded;fill_exclusions(self);self.update_summary()
                return result
            cls.read_selection=read_selection
        if saved['update_summary']:
            def update_summary(self,*args,_old=saved['update_summary'],**kwargs):
                result=_old(self,*args,**kwargs)
                if not getattr(self,'_ux_ready',False):return result
                count=len([g for g in self.targets if g.uid not in self._ux_excluded]);self.apply_button.setText(f'套用到 {count} 個櫃體')
                if not count:self.apply_button.setEnabled(False)
                self.details_toggle.setText(f'查看選取清單（可修改 {count}／排除 {len(self._ux_excluded)}／略過 {len(self.skipped)}） ▾')
                self.selection_label.setText(f'選取 {len(self.targets)} 個可編輯櫃體｜本次修改 {count} 個／排除 {len(self._ux_excluded)} 個／略過 {len(self.skipped)} 個')
                self.summary.setText('本次只修改：'+ '、'.join(NAMES.get(k,k) for k in self.patch)+f'｜{count} 個櫃體' if self.patch else '沒有待套用的修改。')
                none=getattr(self,'handle_mode','keep')=='none'
                for b in self.position_buttons.values():b.setVisible(not none)
                for f in (self.offset,self.edge):
                    f.setVisible(not none)
                    for form in self.findChildren(QFormLayout):
                        if form.indexOf(f)>=0:form.setRowVisible(f,not none)
                return result
            cls.update_summary=update_summary
    original_prepare=ns['prepare_batch']
    def prepare(app,*args,**kwargs):
        window=getattr(app.window,'_lime_ux_preparing',None)
        if window is not None:kwargs['targets_override']=[g for g in window.targets if g.uid not in window._ux_excluded]
        plan=original_prepare(app,*args,**kwargs)
        if window is not None:window._ux_last_plan=plan
        return plan
    ns['prepare_batch']=prepare

    # Auxiliary tools keep the final write action outside their scroll panel too.
    for name,titles in {'ClearanceWindow':['套用高度，垂直移動物件'],'ResizeWindow':['套用高度'],
            'CADWindow':['依勾選圖層建立立體'],'FloorWindow':['預覽輪廓','建立地板'],
            'LatheWindow':['建立旋轉模型']}.items():
        cls=ns[name];old_init=cls.__init__
        def aux_init(self,*args,_old=old_init,_titles=titles,**kwargs):
            _old(self,*args,**kwargs);footer=QWidget();layout=QHBoxLayout(footer);layout.setContentsMargins(20,5,20,8)
            for title in _titles:
                button=next((b for b in self.findChildren(QPushButton) if b.text()==title),None)
                if button is not None:
                    for nested in self.findChildren(QVBoxLayout):nested.removeWidget(button)
                    layout.addWidget(button)
            self.outer.insertWidget(self.outer.indexOf(self.status),footer);self.action_footer=footer
        cls.__init__=aux_init
    old_ffd=ns['FFDWindow'].__init__
    def ffd_init(self,*args,**kwargs):
        old_ffd(self,*args,**kwargs)
        button=next(b for b in self.findChildren(QPushButton) if b.text()=='確認變形');footer=button.parentWidget()
        if footer is not self:self.outer.removeWidget(footer);self.outer.insertWidget(self.outer.indexOf(self.status),footer)
    ns['FFDWindow'].__init__=ffd_init
    api.LockedDoubleSpinBox.textFromValue=lambda self,value:format(value,'.3f').rstrip('0').rstrip('.') or '0'

    # Help follows the actual compact UI and explicit template scopes.
    def help_text(window):
        text='1. 上方確認新增／修改模式與物件名稱，底部預覽後建立／更新。\n2. 長度可輸入 120/3 cm；等比例寬高須自行勾選。\n3. 選取門片或把手可由「修改選取物件」找回所屬模型。\n4. 抽屜直接點分格圖，輸入高度即固定；未指定的面板分配剩餘空間。\n5. 多選櫃體只修改調整的欄位；清單取消勾選可排除個別櫃體。\n6. 可查看修改前後、標示修改範圍；套用時才逐一驗證。設定相同略過重建。\n7. 範本先選套用範圍，可預覽內容；框料與配件可各自恢復預設。\n8. 配件庫先搜尋／預覽，再選用；管理來源不會改動既有模型快照。\n9. 面板放置顯示本次數量；Backspace 復原最後放置，Esc 結束操作。\n10. 離牆正值朝外、負值嵌入；面板離牆距離仍依款式個別記憶。\n11. 新輸入錯誤時保留上一個有效示意並停用套用；大型模型可使用手動預覽。\n12. 材質工具可選部位並旋轉貼圖；Ctrl＋Z 復原。問題請匯出診斷包。'
        QMessageBox.information(window,'工具箱操作說明',text)
    for cls in classes:cls.show_help=help_text
    original_message=api.StyledWindow.message
    def message(self,text,error=False):
        if error and getattr(self,'_ux_ready',False):
            target=getattr(self,'target',None);prefix=target.name if target is not None else ('多選櫃體' if hasattr(self,'targets') else '新增設定')
            if hasattr(self,'selected_front') and any(k in str(text) for k in ('把手','面板','抽屜')):prefix+=f'／第 {self.selected_front+1} 片面板'
            if not str(text).startswith(prefix+'：'):text=prefix+'：'+str(text)
        return original_message(self,text,error)
    api.StyledWindow.message=message

    # Esc ends an owned operation before closing a settings window.
    original_key=api.StyledWindow.keyPressEvent
    def key_press(self,event):
        if event.key()==Qt.Key_Escape:
            for attr,method in (('active_picker','stop'),('_front_pick_tool','stop_front_pick'),('_workflow_picker','stop_workflow_picker'),('session','cleanup')):
                owned=getattr(self,attr,None)
                if owned is not None:
                    action=getattr(self,method,None)
                    if action:
                        if method=='stop_front_pick':action(False)
                        else:action()
                        self.message('目前操作已結束；設定視窗保留。');event.accept();return
        return original_key(self,event)
    api.StyledWindow.keyPressEvent=key_press

    # Placement names and common object classes use the same terminology.
    old_dispatch=ns['dispatch']
    def dispatch(app,key):
        if key=='edit_selected':
            if any(part_owner(app,g) is not None for g in app.scene.selection):
                try:locate_selection(app)
                except Exception as exc:app.viewport.flash_status(str(exc),7000);return
        if key in ('center_object','center_face','resize','clearance') and not app.scene.selection:
            app.viewport.flash_status('請先選取要處理的物件，再啟用'+{'center_object':'整體置中','center_face':'面置中','resize':'高度調整','clearance':'離地高度'}[key]+'。',7000);return
        if key in ('center_object','center_face'):app.viewport.flash_status('基準：'+('物件外框中心' if key=='center_object' else '目標面的外框中心')+'；移動方向由目前物件中心指向目標，Esc 結束。',7000)
        return old_dispatch(app,key)
    ns['dispatch']=dispatch
    ns.update(inspect_template=show_template,reset_tool_section=reset_section,inspect_changes=inspect_changes)

    # Optional detail dialogs share the same wording and model APIs.
    shared=SimpleNamespace(part_role=part_role,texture_rotated=texture_rotated,dialog_table=dialog_table)
    for module_name in ('toolbox_materials','library_usability'):
        spec=__import__('importlib.util',fromlist=['spec_from_file_location']).spec_from_file_location('_lime_'+module_name,api.ROOT/'lime_integrated_assets'/(module_name+'.py'))
        module=__import__('importlib.util',fromlist=['module_from_spec']).module_from_spec(spec);spec.loader.exec_module(module);ns['_'+module_name]=module;module.install(ns,shared)
