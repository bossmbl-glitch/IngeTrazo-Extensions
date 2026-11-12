from pathlib import Path
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QPixmap, QVector3D
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,
    QListWidget,QListWidgetItem,QFormLayout,QDoubleSpinBox,QSpinBox,QComboBox,
    QCheckBox,QPushButton,QGroupBox,QMessageBox,QApplication)
from .engine import ROOT, catalog, build, LightingPlaceTool


class LightingDialog(QDialog):
    def __init__(self, app):
        super().__init__(app.window)
        self.app = app
        self.items = catalog()
        self.setWindowTitle('燈具生成器 · 21 款模型')
        self.resize(940,720)
        self.setMinimumSize(840,650)
        outer = QVBoxLayout(self)
        title = QLabel('燈具生成器')
        title.setStyleSheet('font-size:24px;font-weight:600;padding:4px 0;')
        outer.addWidget(title)
        subtitle = QLabel('選擇原模型燈具，設定排列，然後在場景中點選位置。')
        outer.addWidget(subtitle)
        body = QHBoxLayout(); outer.addLayout(body,1)
        left = QVBoxLayout(); body.addLayout(left,3)
        self.search = QLineEdit();self.search.setPlaceholderText('搜尋型號，例如 QC1、QA1、M8')
        left.addWidget(self.search)
        self.list = QListWidget();self.list.setIconSize(QSize(100,82))
        left.addWidget(self.list,1)
        for entry in self.items:
            item = QListWidgetItem(QIcon(str(ROOT/entry['preview'])),entry['name'])
            item.setData(Qt.ItemDataRole.UserRole,entry)
            item.setSizeHint(QSize(220,88)); self.list.addItem(item)
        right = QVBoxLayout();body.addLayout(right,2)
        self.preview = QLabel();self.preview.setFixedHeight(180)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet('background:#eef1f5;border-radius:8px;')
        right.addWidget(self.preview)
        self.info = QLabel();self.info.setWordWrap(True);right.addWidget(self.info)
        box = QGroupBox('放置設定');form=QFormLayout(box);right.addWidget(box)
        self.anchor=QComboBox()
        for label,key in [('原定位面中心','mount'),('模型底面中心','bottom'),('模型頂面中心','top')]:
            self.anchor.addItem(label,key)
        form.addRow('安裝錨點',self.anchor)
        self.angle=self.spin(-360,360,0,' °');form.addRow('平面旋轉',self.angle)
        self.scale=self.spin(1,10000,100,' %');form.addRow('整體比例',self.scale)
        self.fixed=QCheckBox('固定安裝標高（世界 Z）');self.fixed.setChecked(True)
        form.addRow(self.fixed)
        self.height=self.spin(-1000000,1000000,2800,' mm');form.addRow('安裝標高',self.height)
        self.fixed.toggled.connect(self.height.setEnabled)
        array=QGroupBox('排列 · 沿旋轉後的 X / Y 軸');f=QFormLayout(array);right.addWidget(array)
        self.cols=QSpinBox();self.cols.setRange(1,100)
        self.rows=QSpinBox();self.rows.setRange(1,100)
        f.addRow('X 方向顆數',self.cols);f.addRow('Y 方向顆數',self.rows)
        self.sx=self.spin(1,1000000,1200,' mm');self.sy=self.spin(1,1000000,1200,' mm')
        f.addRow('X 中心間距',self.sx);f.addRow('Y 中心間距',self.sy)
        self.repeat=QCheckBox('連續放置（Esc 結束）');right.addWidget(self.repeat)
        self.count=QLabel();right.addWidget(self.count)
        self.cols.valueChanged.connect(self.update_count);self.rows.valueChanged.connect(self.update_count)
        self.search.textChanged.connect(self.filter)
        self.list.currentItemChanged.connect(self.selected)
        note=QLabel('錨點是第一顆燈的中心。未勾選固定標高時，使用滑鼠捕捉點的 Z。\n每顆燈具含定位線框與中心十字線；不設定渲染燈光或自動開孔。')
        note.setWordWrap(True);outer.addWidget(note)
        buttons=QHBoxLayout();outer.addLayout(buttons)
        close=QPushButton('關閉');close.clicked.connect(self.close);buttons.addWidget(close)
        buttons.addStretch()
        self.place=QPushButton('開始放置');self.place.setMinimumHeight(38)
        self.place.setStyleSheet('background:#1768ac;color:white;padding:8px 28px;border-radius:5px;')
        self.place.clicked.connect(self.start);buttons.addWidget(self.place)
        self.list.setCurrentRow(0);self.update_count()

    @staticmethod
    def spin(low,high,value,suffix):
        w=QDoubleSpinBox();w.setRange(low,high);w.setDecimals(2)
        w.setValue(value);w.setSuffix(suffix);w.setKeyboardTracking(False)
        return w

    def selected(self,*_):
        item=self.list.currentItem()
        if item is None:
            self.preview.clear();self.info.clear();return
        entry=item.data(Qt.ItemDataRole.UserRole)
        pm=QPixmap(str(ROOT/entry['preview']))
        self.preview.setPixmap(pm.scaled(240,176,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        dims=' × '.join(f'{x:g}' for x in entry.get('body_dimensions_mm',entry['dimensions_mm']))
        self.info.setText(f'{entry["name"]}   |   {dims} mm\n原始尺寸（X × Y × Z）')

    def filter(self,text):
        visible=[]
        for i in range(self.list.count()):
            item=self.list.item(i);hidden=text.lower() not in item.text().lower()
            item.setHidden(hidden)
            if not hidden:visible.append(item)
        if visible and (self.list.currentItem() is None or self.list.currentItem().isHidden()):
            self.list.setCurrentItem(visible[0])
        self.place.setEnabled(bool(visible) and self.cols.value()*self.rows.value()<=400)

    def update_count(self,*_):
        n=self.cols.value()*self.rows.value()
        self.count.setText(f'每次放置 {n} 顆 · 一次復原整組' if n<=400 else '每次最多 400 顆，請減少排列數量。')
        self.place.setEnabled(n<=400 and any(not self.list.item(i).isHidden() for i in range(self.list.count())))

    def params(self):
        return dict(rows=self.rows.value(),cols=self.cols.value(),
            spacing_x=self.sx.value()/1000,spacing_y=self.sy.value()/1000,
            angle=self.angle.value(),scale=self.scale.value()/100,
            height=self.height.value()/1000,fixed_height=self.fixed.isChecked(),
            anchor=self.anchor.currentData(),repeat=self.repeat.isChecked())

    def start(self):
        item=self.list.currentItem()
        if item is None or item.isHidden():return
        entry=item.data(Qt.ItemDataRole.UserRole);p=self.params()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            group,materials=build(entry['id'],p)
            vp=self.app.viewport
            vp.end_group_edit()
            tool=LightingPlaceTool(group,materials,p)
            vp.set_active_tool(tool)
            vp.flash_status('點選第一顆燈的安裝位置；Esc 取消。',6000)
            vp.setFocus();vp.update();self.hide()
        except Exception as exc:
            QMessageBox.critical(self,'無法放置燈具',str(exc))
        finally:
            QApplication.restoreOverrideCursor()
