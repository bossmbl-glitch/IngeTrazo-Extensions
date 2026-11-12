import copy,math
from pathlib import Path
from PySide6.QtCore import Qt,QSize,QPointF,QTimer,Signal
from PySide6.QtGui import QColor,QImage,QPixmap,QPainter,QPen,QBrush,QIcon,QTransform
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QLineEdit,
    QPushButton,QToolButton,QDoubleSpinBox,QCheckBox,QComboBox,QListWidget,QListWidgetItem,
    QAbstractItemView,QFileDialog,QColorDialog,QMenu,QSplitter,QScrollArea,QTabWidget,QSlider,QSpinBox,QMessageBox)
from .engine import default_recipe,from_attrs,texture_for,apply,selected_groups,walk,ColorEditCommand,CreateColorCommand,image_color,NewMaterialCommand,material_for,paint_selection
from .textures import image_for
from .colors import ColorControls
from . import library

class Preview(QWidget):
    changed=Signal(float,float,float,float) # offsets, relative size factor, angle delta
    finished=Signal()
    def __init__(self):
        super().__init__();self.r=default_recipe();self.pic=QPixmap();self.press=None
        self.setMinimumSize(220,150);self.setMouseTracking(True)
        self.setToolTip('拖曳：平移貼圖｜滾輪：縮放｜Shift＋滾輪：旋轉｜右鍵：方向選單')
        self.wheel_timer=QTimer(self);self.wheel_timer.setSingleShot(True)
        self.wheel_timer.timeout.connect(self.finished.emit)
    def set_recipe(self,r):
        self.r=copy.deepcopy(r)
        self.pic=QPixmap.fromImage(image_for(r)) if r['path'] else QPixmap()
        self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(),QColor('#e9edf2'))
        p.save();p.setClipRect(10,10,self.width()-20,self.height()-38)
        p.translate(self.width()/2,(self.height()-26)/2)
        scale=min(self.width()-20,self.height()-40)/100
        p.scale(scale,-scale)
        p.rotate(self.r['angle'])
        p.translate(self.r['offset_u'],self.r['offset_v'])
        if self.pic.isNull():
            p.resetTransform();p.fillRect(10,10,self.width()-20,self.height()-38,QColor.fromRgbF(*self.r['color']))
        else:
            # One source image tile occupies exactly the chosen cm dimensions.
            brush=QBrush(self.pic)
            transform=QTransform()
            transform.scale(self.r['width']/self.pic.width()*(-1 if self.r['flip_u'] else 1),
                            -self.r['height']/self.pic.height()*(-1 if self.r['flip_v'] else 1))
            brush.setTransform(transform)
            p.setPen(Qt.NoPen);p.setBrush(brush)
            p.drawRect(-100000,-100000,200000,200000)
        p.restore();p.setPen(QColor('#384559'))
        p.drawText(0,self.height()-25,self.width(),22,Qt.AlignCenter,'拖曳平移　滾輪縮放　Shift＋滾輪旋轉')
        p.end()
    def mousePressEvent(self,e):
        if e.button()==Qt.LeftButton:self.press=e.position()
    def mouseMoveEvent(self,e):
        if self.press is None:return
        delta=e.position()-self.press;self.press=e.position()
        scale=min(self.width()-20,self.height()-40)/100
        a=math.radians(self.r['angle']);x=delta.x()/scale;y=-delta.y()/scale
        self.changed.emit(x*math.cos(a)+y*math.sin(a),-x*math.sin(a)+y*math.cos(a),1.,0.)
    def mouseReleaseEvent(self,e):
        if e.button()==Qt.LeftButton and self.press is not None:
            self.press=None;self.finished.emit()
    def wheelEvent(self,e):
        steps=e.angleDelta().y()/120
        if e.modifiers()&Qt.ShiftModifier:self.changed.emit(0,0,1,steps*5)
        else:self.changed.emit(0,0,1.1**steps,0)
        self.wheel_timer.start(350);e.accept()

class MaterialPanel(QWidget):
    def __init__(self,app):
        super().__init__();self.app=app;self.busy=False;self.r=default_recipe();self.catalog=[]
        self._ratio=1.;self._loaded_name=None;self._sample_once=False;self._material_source='model'
        self._color_command=None;self._color_version=None;self._pending_color=False;self._opacity_dragging=False
        self.color_timer=QTimer(self);self.color_timer.setSingleShot(True);self.color_timer.timeout.connect(self.flush_color)
        self.preview_timer=QTimer(self);self.preview_timer.setSingleShot(True);self.preview_timer.timeout.connect(self.refresh_preview)
        self.opacity_timer=QTimer(self);self.opacity_timer.setSingleShot(True);self.opacity_timer.timeout.connect(self.end_color)
        from .async_color import FullColorWorker
        self.worker=FullColorWorker(self);self.worker.ready.connect(self.full_color_ready)
        self.pending_jobs=0
        outer=QVBoxLayout(self);outer.setContentsMargins(6,6,6,6);outer.setSpacing(5)
        header=QWidget();header.setFixedHeight(78);top=QHBoxLayout(header);top.setContentsMargins(0,0,0,0)
        self.thumbnail=QToolButton();self.thumbnail.setFixedSize(74,74);self.thumbnail.setIconSize(QSize(68,68))
        self.thumbnail.setToolTip('目前材質；調色時同步更新。點一下啟用油漆桶。');self.thumbnail.clicked.connect(self.activate_brush)
        self.thumbnail.setContextMenuPolicy(Qt.CustomContextMenu);self.thumbnail.customContextMenuRequested.connect(self.thumbnail_menu)
        top.addWidget(self.thumbnail);side=QVBoxLayout();name_row=QHBoxLayout()
        self.name=QLineEdit(self.r['name']);self.name.setPlaceholderText('材質名稱');name_row.addWidget(self.name,1)
        self.add_button=QToolButton();self.add_button.setText('＋');self.add_button.setToolTip('新增材質');self.add_button.setFixedWidth(28)
        self.add_button.clicked.connect(self.new_material);name_row.addWidget(self.add_button)
        self.delete_button=QToolButton();self.delete_button.setText('−');self.delete_button.setFixedWidth(28)
        self.delete_button.setToolTip('刪除目前材質');self.delete_button.clicked.connect(self.delete_current);name_row.addWidget(self.delete_button);side.addLayout(name_row)
        tools=QHBoxLayout();brush=QPushButton('油漆桶');brush.clicked.connect(self.activate_brush);tools.addWidget(brush)
        sample=QPushButton('吸取');sample.clicked.connect(self.activate_sampler);tools.addWidget(sample)
        side.addLayout(tools);side.addStretch();top.addLayout(side,1);outer.addWidget(header)
        self.batch_button=QPushButton('套用到全部選取面／物件')
        self.batch_button.clicked.connect(self.apply_selection);outer.addWidget(self.batch_button)
        self.tabs=QTabWidget();editor=QWidget();library_page=QWidget()
        self.tabs.addTab(library_page,'選取');self.tabs.addTab(editor,'編輯');outer.addWidget(self.tabs)
        library_layout=QVBoxLayout(library_page);library_layout.setContentsMargins(5,5,5,5)
        self.category=QComboBox();self.category.addItem('我的材質庫','personal');self.category.addItem('目前模型','model')
        try:
            from views.outliner import library_catalog
            self.catalog=library_catalog()
            for i,(label,entries) in enumerate(self.catalog):self.category.addItem(label,i)
        except (ImportError,AttributeError):pass
        library_layout.addWidget(self.category)
        self.search=QLineEdit();self.search.setPlaceholderText('搜尋材質…');library_layout.addWidget(self.search)
        self.gallery=QListWidget();self.gallery.setViewMode(QListWidget.IconMode);self.gallery.setResizeMode(QListWidget.Adjust)
        self.gallery.setIconSize(QSize(52,52));self.gallery.setGridSize(QSize(76,78));self.gallery.setMovement(QListWidget.Static)
        self.gallery.setMinimumHeight(200);self.gallery.setWordWrap(True);library_layout.addWidget(self.gallery,1)
        refresh=QPushButton('重新整理');refresh.clicked.connect(self.refresh_library);library_layout.addWidget(refresh)
        self.category.currentIndexChanged.connect(self.refresh_library);self.search.textChanged.connect(self.filter_library)
        self.gallery.itemClicked.connect(self.pick_gallery)
        self.gallery.setContextMenuPolicy(Qt.CustomContextMenu);self.gallery.customContextMenuRequested.connect(self.gallery_menu)
        self.manage_button=QPushButton('整理材質 ▾');self.manage_button.clicked.connect(self.manage_menu);library_layout.addWidget(self.manage_button)
        layout=QVBoxLayout(editor);layout.setContentsMargins(6,5,6,5);layout.setSpacing(5)
        def section(text):
            label=QLabel(text);label.setStyleSheet('font-weight:600;border-bottom:1px solid #888;padding-bottom:2px;');layout.addWidget(label)
        section('顏色');self.color_controls=ColorControls();layout.addWidget(self.color_controls)
        self.color_controls.started.connect(self.begin_color);self.color_controls.changed.connect(self.change_color)
        self.color_controls.finished.connect(self.end_color);self.color_controls.resetRequested.connect(self.reset_color)
        section('紋理');self.use_texture=QCheckBox('使用紋理圖像');self.use_texture.toggled.connect(self.toggle_texture);layout.addWidget(self.use_texture)
        row=QHBoxLayout();self.path=QLineEdit();self.path.setReadOnly(True);self.path.setPlaceholderText('選擇材質圖片');row.addWidget(self.path,1)
        browse=QToolButton();browse.setText('…');browse.setToolTip('選擇紋理圖片');browse.clicked.connect(self.choose_image);row.addWidget(browse);layout.addLayout(row)
        self.tint=QCheckBox();self.tint.hide();self.tint.toggled.connect(self.finish_change)
        self.colorize=QCheckBox('著色');self.colorize.setToolTip('統一色相，保留紋理明暗');self.colorize.toggled.connect(self.change_color_mode)
        grid=QGridLayout();self.fields={}
        for row,(key,label) in enumerate([('width','寬'),('height','高')]):
            box=QDoubleSpinBox();box.setDecimals(2);box.setKeyboardTracking(False);box.setRange(.01,100000);box.setSuffix(' cm');box.setMaximumWidth(106)
            grid.addWidget(QLabel(label),row,0);grid.addWidget(box,row,1);self.fields[key]=box
            box.valueChanged.connect(lambda value,k=key:self.field_changed(k,value));box.editingFinished.connect(self.finish_change)
        self.lock=QToolButton();self.lock.setText('鎖');self.lock.setCheckable(True);self.lock.setChecked(True);self.lock.setFixedSize(26,40)
        self.lock.setToolTip('鎖定／解除寬高比例');self.lock.toggled.connect(lambda _:self.update_ratio());grid.addWidget(self.lock,0,2,2,1)
        grid.addWidget(self.colorize,0,3);grid.addWidget(self.color_controls.reset,1,3);self.color_controls.reset.show();layout.addLayout(grid)
        section('不透明度');alpha=QHBoxLayout();self.opacity=QSlider(Qt.Horizontal);self.opacity.setRange(0,100);self.opacity.setValue(100)
        self.opacity_number=QSpinBox();self.opacity_number.setRange(0,100);self.opacity_number.setValue(100);self.opacity_number.setSuffix('%');self.opacity_number.setKeyboardTracking(False);self.opacity_number.setFixedWidth(65)
        alpha.addWidget(self.opacity,1);alpha.addWidget(self.opacity_number);layout.addLayout(alpha)
        self.opacity.sliderPressed.connect(self.begin_opacity);self.opacity.sliderReleased.connect(self.end_opacity)
        self.opacity.valueChanged.connect(self.opacity_changed);self.opacity_number.valueChanged.connect(self.opacity_changed);self.opacity_number.editingFinished.connect(self.end_opacity)
        hint=QLabel('模型上按右鍵 → 紋理 → 位置，可直接拖曳貼圖。');hint.setWordWrap(True);layout.addWidget(hint)
        self.advanced_button=QToolButton();self.advanced_button.setText('其他材質設定');self.advanced_button.setCheckable(True)
        self.advanced_button.setArrowType(Qt.RightArrow);self.advanced_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon);layout.addWidget(self.advanced_button)
        self.advanced_button.toggled.connect(lambda checked:self.advanced_button.setArrowType(Qt.DownArrow if checked else Qt.RightArrow))
        self.advanced=QWidget();advanced=QVBoxLayout(self.advanced);advanced.setContentsMargins(0,0,0,0);layout.addWidget(self.advanced);self.advanced.hide()
        self.advanced_button.toggled.connect(self.advanced.setVisible)
        self.preview=Preview();self.preview.setMinimumHeight(110);self.preview.setMaximumHeight(135);advanced.addWidget(self.preview)
        self.preview.hide()
        self.preview.changed.connect(self.preview_change);self.preview.finished.connect(self.finish_change)
        self.preview.setContextMenuPolicy(Qt.CustomContextMenu);self.preview.customContextMenuRequested.connect(self.preview_menu)
        position_fields=QWidget();positions=QGridLayout(position_fields);position_fields.hide()
        for row,(key,label) in enumerate([('angle','旋轉'),('offset_u','水平位移'),('offset_v','垂直位移')]):
            box=QDoubleSpinBox();box.setDecimals(2);box.setKeyboardTracking(False);box.setRange(-100000,100000);box.setSuffix('°' if key=='angle' else ' cm')
            positions.addWidget(QLabel(label),row,0);positions.addWidget(box,row,1);self.fields[key]=box
            box.valueChanged.connect(lambda value,k=key:self.field_changed(k,value));box.editingFinished.connect(self.finish_change)
        advanced.addWidget(position_fields)
        flips=QHBoxLayout();self.flip_u=QCheckBox('水平翻轉');self.flip_v=QCheckBox('垂直翻轉')
        for box in (self.flip_u,self.flip_v):flips.addWidget(box);box.toggled.connect(self.finish_change)
        self.flip_u.hide();self.flip_v.hide()
        advanced.addLayout(flips);self.projection=QComboBox()
        for label,key in [('依各面方向','face'),('正面 XZ（整框一致）','xz'),('平面 XY','xy'),('側面 YZ','yz')]:self.projection.addItem(label,key)
        self.projection.currentIndexChanged.connect(self.finish_change);advanced.addWidget(self.projection)
        self.both=QCheckBox('正反面一起套用');self.both.setChecked(True);advanced.addWidget(self.both)
        self.auto=QCheckBox('即時更新模型材質');self.auto.setChecked(True);advanced.addWidget(self.auto)
        self.update_button=QPushButton('更新同名材質');self.update_button.clicked.connect(self.update_matching);advanced.addWidget(self.update_button)
        buttons=QHBoxLayout();save=QPushButton('儲存材質');save.clicked.connect(self.save_library);buttons.addWidget(save)
        self.apply_button=QPushButton('套用選取');self.apply_button.clicked.connect(self.apply_selection);buttons.addWidget(self.apply_button);layout.addLayout(buttons)
        layout.addStretch()
        self.status=QLabel('先選取面／物件，再選材質或調色，可一次全部上色。B 油漆桶；Alt＋點擊吸取。');self.status.setWordWrap(True);self.status.setStyleSheet('font-size:11px;');outer.addWidget(self.status)
        self.set_recipe(default_recipe());self.category.setCurrentIndex(2 if self.category.count()>2 else 0);self.refresh_library()
        self.app.viewport.sceneVersionChanged.connect(self.sync_model_color)
        self.app.viewport.sceneVersionChanged.connect(self.refresh_deleted_materials)

    def message(self,text,error=False):
        dark=self.palette().window().color().lightness()<128
        color=('#ffb4ab' if dark else '#b53636') if error else ('#a6ddcb' if dark else '#285c50')
        self.status.setText(text);self.status.setStyleSheet('color:'+color)
    def recipe(self):
        r=copy.deepcopy(self.r);r['name']=self.name.text().strip()
        for key,box in self.fields.items():r[key]=box.value()
        r.update(tint=self.tint.isChecked(),tint_mode=1 if self.colorize.isChecked() else 0,flip_u=self.flip_u.isChecked(),flip_v=self.flip_v.isChecked(),projection=self.projection.currentData())
        r['opacity']=self.opacity.value()/100
        if not self.use_texture.isChecked():r['path']=''
        return r
    def set_recipe(self,r):
        self.end_color()
        self.busy=True;self.r=copy.deepcopy(r);self.name.setText(r['name'])
        for key,box in self.fields.items():box.setValue(r[key])
        self.tint.setChecked(r['tint']);self.flip_u.setChecked(r['flip_u']);self.flip_v.setChecked(r['flip_v'])
        self.colorize.setChecked(r.get('tint_mode',0)==1)
        self.color_controls.set_color(QColor.fromRgbF(*r['color']))
        self.use_texture.setChecked(bool(r['path']));self.opacity.setValue(round(r.get('opacity',1)*100));self.opacity_number.setValue(self.opacity.value())
        self._original_color=copy.deepcopy(r['color'])
        self.projection.setCurrentIndex(self.projection.findData(r['projection']))
        self.path.setText(Path(r['path']).name if r['path'] else '純色材質')
        self.path.setToolTip(r['path']);self._loaded_name=r['name']
        self.update_ratio();self.busy=False;self.refresh_preview()
    def update_ratio(self):
        self._ratio=self.fields['width'].value()/max(self.fields['height'].value(),.01)
    def field_changed(self,key,value):
        if self.busy:return
        if self.lock.isChecked() and key in ('width','height'):
            other='height' if key=='width' else 'width'
            self.busy=True;self.fields[other].setValue(value/self._ratio if key=='width' else value*self._ratio);self.busy=False
        else:self.update_ratio()
        self.refresh_preview()
    def refresh_preview(self):
        if self.busy:return
        self.preview_timer.stop()
        r=self.recipe();self.preview.set_recipe(r)
        tile=QPixmap(68,68);tile.fill(QColor('#e7e7e7'));p=QPainter(tile)
        for y in range(0,68,8):
            for x in range(0,68,8):
                if (x//8+y//8)%2:p.fillRect(x,y,8,8,QColor('#fafafa'))
        p.setOpacity(r.get('opacity',1))
        if r['path']:
            pic=self.preview.pic.scaled(64,64,Qt.KeepAspectRatio,Qt.SmoothTransformation)
            p.drawPixmap((68-pic.width())//2,(68-pic.height())//2,pic)
        else:p.fillRect(2,2,64,64,QColor.fromRgbF(*r['color']))
        p.end();self.thumbnail.setIcon(QIcon(tile))
    def finish_change(self,*args):
        if self.busy:return
        self.end_color()
        self.refresh_preview()
        if self.auto.isChecked():
            if paint_selection(self.app.scene):self.apply_selection()
            elif self._loaded_name in self.app.scene.materials:self.update_matching()
    def preview_change(self,du,dv,factor,angle):
        self.busy=True
        for key,value in [('offset_u',self.fields['offset_u'].value()+du),('offset_v',self.fields['offset_v'].value()+dv),('width',self.fields['width'].value()*factor),('height',self.fields['height'].value()*factor),('angle',self.fields['angle'].value()+angle)]:self.fields[key].setValue(value)
        self.busy=False;self.refresh_preview()
    def rotate(self,angle):
        self.fields['angle'].setValue(self.fields['angle'].value()+angle);self.finish_change()
    def reset_position(self):
        self.busy=True
        for key in ('angle','offset_u','offset_v'):self.fields[key].setValue(0)
        self.flip_u.setChecked(False);self.flip_v.setChecked(False);self.busy=False;self.finish_change()
    def preview_menu(self,pos):
        menu=QMenu(self)
        menu.addAction('順時針 90°',lambda:self.rotate(-90));menu.addAction('逆時針 90°',lambda:self.rotate(90))
        menu.addAction('水平翻轉',self.flip_u.toggle);menu.addAction('垂直翻轉',self.flip_v.toggle)
        menu.addAction('重設位置與方向',self.reset_position);menu.exec(self.preview.mapToGlobal(pos))
    def choose_image(self):
        path,_=QFileDialog.getOpenFileName(self,'選擇材質圖片','','圖片 (*.png *.jpg *.jpeg *.bmp *.webp)')
        if path:self.load_image(path)
    def load_image(self,path):
        image=QImage(path)
        if image.isNull():self.message('圖片無法讀取。',True);return
        r=self.recipe();r['path']=str(path)
        if r['name'] not in self.app.scene.materials:r['name']=Path(path).stem
        r['height']=r['width']*image.height()/image.width();r['tint']=False;r['color']=image_color(path)
        self.set_recipe(r);self.tabs.setCurrentIndex(1);self.finish_change()
    def solid(self):
        r=self.recipe();r['path']='';r['tint']=False;self.set_recipe(r);self.finish_change()
    def choose_color(self):
        self.tabs.setCurrentIndex(1);self.color_controls.setFocus()
    def begin_color(self):
        self._color_command=None;self._color_version=None
        self._color_selection=frozenset(self.app.scene.selection)
    def change_color(self,color):
        if self.busy:return
        self.r['color']=[color.redF(),color.greenF(),color.blueF()]
        self.busy=True
        if self.r['path']:self.tint.setChecked(True)
        self.busy=False;self._pending_color=True
        if not self.preview_timer.isActive():self.preview_timer.start(20)
        if not self.color_timer.isActive():self.color_timer.start(75)
    def flush_color(self):
        self.color_timer.stop()
        if not self._pending_color:return
        self._pending_color=False;self.refresh_preview()
        if not self.auto.isChecked():return
        scene=self.app.scene;history=self.app.viewport.history
        if getattr(self,'_color_selection',frozenset(scene.selection))!=frozenset(scene.selection):
            self.message('選取範圍已改變，請重新調色。');return
        if self._loaded_name not in scene.materials and not scene.selection:
            self.message('顏色已選好；按油漆桶點模型上色。');return
        try:
            cmd=self._color_command
            if cmd is not None and history.undo_stack and history.undo_stack[-1] is cmd and scene.version==self._color_version:
                cmd.recolor(self.recipe(),preview=True);cmd.do(scene)
            else:
                cmd=(CreateColorCommand(scene,self.recipe(),self.both.isChecked()) if paint_selection(scene)
                     else ColorEditCommand(scene,self._loaded_name,self.recipe(),preview=True))
                history.execute(cmd)
                if history.last_error:raise ValueError(history.last_error)
                self._color_command=cmd
                self._loaded_name=cmd.name;self.r['name']=cmd.name;self.name.setText(cmd.name)
            self._color_version=scene.version;self.app.viewport.notify_scene_changed()
            self.message((f'已一次更新全部選取，共 {cmd.paint.count} 個面。' if isinstance(cmd,CreateColorCommand)
                          else '已更新所有同名材質。')+'Ctrl+Z 可復原。')
        except (ValueError,OSError) as exc:self.message(str(exc),True)
    def end_color(self):
        self.opacity_timer.stop();self.flush_color()
        owner=self._color_command;cmd=owner.color if isinstance(owner,CreateColorCommand) else owner
        if cmd is not None and cmd.preview:
            record=(self.app.scene,self.app.viewport.history,owner,cmd,cmd.revision)
            self.pending_jobs+=1;self.worker.prepare(record,cmd.full_sources(),copy.deepcopy(cmd.recipe))
        self._color_command=None;self._color_version=None
        self.__dict__.pop('_color_selection',None)
    def full_color_ready(self,record,result):
        self.pending_jobs=max(0,self.pending_jobs-1)
        scene,history,owner,cmd,revision=record
        if scene is not self.app.scene or cmd.revision!=revision:return
        if owner not in history.undo_stack and owner not in history.redo_stack:return
        if isinstance(result,Exception):self.message('原尺寸貼圖尚未完成：'+str(result),True);return
        if cmd.install_full(scene,result):self.app.viewport.notify_scene_changed()
    def new_material(self):
        self._material_source='model'
        self.end_color();r=default_recipe();i=1
        while '材質'+str(i) in self.app.scene.materials:i+=1
        r['name']='材質'+str(i)
        self.app.viewport.history.execute(NewMaterialCommand(material_for(r)))
        self.set_recipe(r);self.tabs.setCurrentIndex(1);self.category.setCurrentIndex(1);self.refresh_library()
        self.app.viewport.notify_scene_changed();self.name.setFocus();self.name.selectAll()
        self.message('已新增 '+r['name']+'；可直接調色或選紋理圖片，再點模型上色。')
    def activate_sampler(self):
        self.activate_brush();self._sample_once=True;self.message('點模型吸取材質。')
    def toggle_texture(self,checked):
        if self.busy:return
        if checked and not self.r['path']:
            self.choose_image()
            if not self.r['path']:
                self.busy=True;self.use_texture.setChecked(False);self.busy=False
            return
        self.finish_change()
    def begin_opacity(self):
        self._opacity_dragging=True;self.begin_color()
    def end_opacity(self):
        self._opacity_dragging=False;self.end_color()
    def opacity_changed(self,value):
        if self.busy:return
        self.busy=True;self.opacity.setValue(value);self.opacity_number.setValue(value);self.busy=False
        self._pending_color=True
        if not self.preview_timer.isActive():self.preview_timer.start(20)
        if not self.color_timer.isActive():self.color_timer.start(75)
        if not self._opacity_dragging:self.opacity_timer.start(350)
    def thumbnail_menu(self,pos):
        self.preview_menu(self.preview.mapFromGlobal(self.thumbnail.mapToGlobal(pos)))
    def change_color_mode(self,*args):
        if self.busy:return
        self.begin_color();self.change_color(QColor.fromRgbF(*self.r['color']));self.end_color()
    def reset_color(self):
        self.end_color();self.begin_color()
        self.r['color']=image_color(self.r['path']) if self.r['path'] else copy.deepcopy(self._original_color)
        self.busy=True;self.tint.setChecked(False);self.busy=False
        self.color_controls.set_color(QColor.fromRgbF(*self.r['color']))
        self._pending_color=True;self.end_color()
    def sync_model_color(self,*args):
        if self.busy or self._pending_color or self.color_controls.active or self._opacity_dragging:return
        history=self.app.viewport.history
        tips=[stack[-1] for stack in (history.undo_stack,history.redo_stack) if stack]
        if not any(isinstance(cmd,(ColorEditCommand,CreateColorCommand)) and cmd.name==self._loaded_name for cmd in tips):return
        material=self.app.scene.materials.get(self._loaded_name)
        if material is None:return
        r=from_attrs(material.face_attrs());self.busy=True
        self.r['color']=r['color'];self.tint.setChecked(r['tint']);self.colorize.setChecked(r.get('tint_mode',0)==1)
        self.opacity.setValue(round(r.get('opacity',1)*100));self.opacity_number.setValue(self.opacity.value())
        self.color_controls.set_color(QColor.fromRgbF(*r['color']));self.busy=False;self.refresh_preview()
    def apply_selection(self,*args):
        self.end_color()
        try:
            count=apply(self.app,self.recipe(),both=self.both.isChecked())
            self._loaded_name=self.recipe()['name']
            self.message(f'已套用 {count} 個面。Ctrl+Z 可復原。')
        except (ValueError,OSError) as exc:self.message(str(exc),True)
    def update_matching(self):
        if not self._loaded_name:self.message('請先從模型讀取材質。',True);return
        try:
            from .engine import material_used
            if not material_used(self.app.scene,self._loaded_name):
                self.app.viewport.history.execute(NewMaterialCommand(material_for(self.recipe())))
                self.app.viewport.notify_scene_changed();self.message('材質已更新，點模型即可上色。');return
            count=apply(self.app,self.recipe(),matching=self._loaded_name,both=self.both.isChecked())
            self._loaded_name=self.name.text().strip();self.message(f'已更新同名材質，共 {count} 個面。')
        except (ValueError,OSError) as exc:self.message(str(exc),True)
    def activate_brush(self):
        self._sample_once=False
        self.app.window._activate_tool('it_material_studio_paint')
        self.message('B 油漆桶；點選取範圍一次全部上色；Alt＋點擊吸取，放開恢復油漆桶。')
    def read_selection(self):
        self._material_source='model'
        self.tabs.setCurrentIndex(1)
        from core.mesh import Face
        selection=list(self.app.scene.selection)
        faces=[f for f in selection if isinstance(f,Face)]
        if not faces:
            for root in selected_groups(self.app.scene,selection):
                for g,_,inherited in walk(root):
                    if g.mesh.faces:
                        f=g.mesh.faces[0]
                        from core.materials import effective_attrs
                        self.set_recipe(from_attrs(effective_attrs(f.attrs,inherited),f.normal()))
                        self.message('已讀取物件第一個面的材質；不同面可用 Alt＋點擊吸取。');return
        if faces:self.set_recipe(from_attrs(faces[0].attrs,faces[0].normal()));self.message('已讀取選取面的材質。')
        else:self.message('請先選取面或物件。',True)
    def save_library(self):
        try:
            saved=library.save(self.recipe());self.set_recipe(saved)
            self._material_source='personal'
            self.category.setCurrentIndex(0);self.refresh_library();self.message('已存入我的材質庫，貼圖圖片也已另存。')
        except (ValueError,OSError) as exc:self.message(str(exc),True)
    def refresh_library(self,*args):
        self.gallery.clear();key=self.category.currentData()
        try:
            if key=='personal':entries=library.read()
            elif key=='model':entries=[from_attrs(m.face_attrs()) for m in self.app.scene.materials.values()]
            else:
                omitted=library.hidden();category=self.catalog[int(key)][0]
                entries=[from_attrs(paint) for _,paint,_ in self.catalog[int(key)][1] if [category,paint.get('mat') or '自訂材質'] not in omitted]
            for r in entries:
                item=QListWidgetItem(r['name']);item.setData(Qt.UserRole,r)
                icon=QPixmap(r['path']) if r['path'] else QPixmap(60,60)
                if not r['path']:icon.fill(QColor.fromRgbF(*r['color']))
                item.setIcon(QIcon(icon.scaled(60,60,Qt.KeepAspectRatio,Qt.SmoothTransformation)))
                item.setToolTip(r['name']);self.gallery.addItem(item)
            if not entries and key=='personal':self.message('先選圖片或顏色，再按「存入我的材質庫」。')
            self.filter_library()
        except (ValueError,OSError) as exc:self.message(str(exc),True)
    def filter_library(self,*args):
        text=self.search.text().lower()
        for i in range(self.gallery.count()):
            item=self.gallery.item(i);item.setHidden(text not in item.text().lower())
    def pick_gallery(self,item):
        self._material_source=self.category.currentData()
        self.set_recipe(item.data(Qt.UserRole))
        if paint_selection(self.app.scene):self.apply_selection()
        else:self.activate_brush()
    def gallery_menu(self,pos):
        item=self.gallery.itemAt(pos)
        if item is None:return
        name=item.data(Qt.UserRole)['name'];source=self.category.currentData();menu=QMenu(self)
        label='刪除材質' if source in ('personal','model') else '隱藏這個內建材質'
        menu.addAction(label,lambda:self.delete_material(name,source));menu.exec(self.gallery.mapToGlobal(pos))
    def delete_current(self):
        self.delete_material(self._loaded_name,self._material_source)
    def confirm_material_delete(self,name,faces,groups):
        answer=QMessageBox.question(self,'刪除模型材質',f'「{name}」用於 {faces} 個面、{groups} 個群組的材質設定。\n刪除後會改用預設或群組繼承材質，模型形狀不變。\n可用 Ctrl+Z 復原。要刪除嗎？',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        return answer==QMessageBox.Yes
    def delete_material(self,name,source):
        if not name:return False
        self.end_color()
        try:
            if source=='personal':
                if not library.remove(name):self.message('此收藏已不存在。');return False
                message='已刪除收藏；模型保留。可在「整理材質」還原上次刪除。'
            elif source=='model':
                from .deletion import DeleteMaterialsCommand
                if name not in self.app.scene.materials:self.message('此材質尚未存入模型。');return False
                cmd=DeleteMaterialsCommand(self.app.scene,[name])
                if (cmd.face_count or cmd.group_count) and not self.confirm_material_delete(name,cmd.face_count,cmd.group_count):return False
                self.app.viewport.history.execute(cmd)
                if self.app.viewport.history.last_error:raise ValueError(self.app.viewport.history.last_error)
                self.app.viewport.notify_scene_changed();message='已刪除模型材質。Ctrl+Z 可復原。'
            else:
                library.hide(self.catalog[int(source)][0],name);message='已隱藏這個內建材質；可在「整理材質」還原。'
            if name==self._loaded_name:self.set_recipe(default_recipe());self._material_source='model'
            self.refresh_library();self.message(message);return True
        except (ValueError,OSError) as exc:self.message(str(exc),True);return False
    def purge_unused(self):
        self.end_color()
        from .deletion import usage,DeleteMaterialsCommand
        used=usage(self.app.scene);names=[name for name in self.app.scene.materials if name not in used]
        if not names:self.message('沒有未使用的模型材質。');return
        self.app.viewport.history.execute(DeleteMaterialsCommand(self.app.scene,names))
        self.app.viewport.notify_scene_changed()
        if self._loaded_name in names:self.set_recipe(default_recipe())
        self.refresh_library();self.message(f'已清除 {len(names)} 個未使用材質。Ctrl+Z 可復原。')
    def restore_deleted(self):
        try:
            entry=library.restore_last();self.refresh_library()
            self.message('已還原收藏「'+entry['name']+'」。' if entry else '沒有可還原的收藏。')
        except (ValueError,OSError) as exc:self.message(str(exc),True)
    def restore_hidden(self):
        try:library.unhide_all();self.refresh_library();self.message('已還原隱藏的內建材質。')
        except (ValueError,OSError) as exc:self.message(str(exc),True)
    def manage_menu(self):
        menu=QMenu(self)
        menu.addAction('清除模型未使用材質',self.purge_unused)
        menu.addAction('還原上次刪除的收藏',self.restore_deleted)
        menu.addAction('還原隱藏的內建材質',self.restore_hidden)
        menu.exec(self.manage_button.mapToGlobal(self.manage_button.rect().bottomLeft()))
    def refresh_deleted_materials(self,*args):
        from .deletion import DeleteMaterialsCommand
        history=self.app.viewport.history
        if self.category.currentData()=='model' and any(stack and isinstance(stack[-1],DeleteMaterialsCommand) for stack in (history.undo_stack,history.redo_stack)):
            self.refresh_library()
