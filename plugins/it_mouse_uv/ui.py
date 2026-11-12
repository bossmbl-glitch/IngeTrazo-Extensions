"""Traditional Chinese dialogs. All model edits go through the host history."""
from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QVector3D
from PySide6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QPushButton, QDoubleSpinBox, QSpinBox, QComboBox,
    QTabWidget, QFileDialog, QCheckBox)
from .common import execute, get_targets, xyz
from . import uv


def number(value=0., low=-100000., high=100000., suffix="", decimals=3):
    field = QDoubleSpinBox()
    field.setRange(low, high)
    field.setDecimals(decimals)
    field.setValue(value)
    field.setSuffix(suffix)
    field.setKeyboardTracking(False)
    return field


def combo(items):
    field = QComboBox()
    for label, data in items:
        field.addItem(label, data)
    return field


def row(*widgets):
    result = QWidget()
    layout = QHBoxLayout(result)
    layout.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        layout.addWidget(widget)
    return result


def note(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return label


class BaseDialog(QDialog):
    def __init__(self, app, title):
        super().__init__(app.window)
        self.app = app
        self.setWindowTitle(title)
        self.setModal(False)
        self.setMinimumWidth(570)
        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(12)
        self.layout.addWidget(note("先在模型中選取面或群組，再執行工具。每次套用可用 Ctrl+Z 復原。"))
        self.status = note("就緒")

    def finish(self):
        self.layout.addWidget(self.status)
        close = QPushButton("關閉")
        close.clicked.connect(self.close)
        self.layout.addWidget(close)

    def run(self, callback, message="已完成；Ctrl+Z 可復原。"):
        try:
            callback()
            self.status.setText(message)
            self.status.setStyleSheet("")
        except Exception as error:
            self.status.setText("未套用：" + str(error))
            self.status.setStyleSheet("color: #d86055;")

    def button(self, label, callback, layout=None):
        button = QPushButton(label)
        button.clicked.connect(lambda: self.run(callback))
        (layout or self.layout).addWidget(button)
        return button


class UVDialog(BaseDialog):
    def __init__(self, app):
        super().__init__(app, "UV 貼圖工具 · IngeTrazo")
        tabs = QTabWidget()
        self.layout.addWidget(tabs)
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.mode = combo([("平面投影", "planar"), ("依目前視角投影", "view"),
            ("盒狀投影", "box"), ("圓柱投影", "cylinder"),
            ("球面投影", "sphere"), ("四邊面 0–1 貼圖", "quad")])
        self.axis = combo([("X", 0), ("Y", 1), ("Z", 2)])
        self.axis.setCurrentIndex(2)
        self.size = number(100, .001, 100000, " cm")
        self.seam = number(0, -360, 360, "°")
        form.addRow("貼圖方式", self.mode)
        form.addRow("投影軸／圓柱與球面軸", self.axis)
        form.addRow("每張貼圖尺寸（平面／盒狀）", self.size)
        form.addRow("圓柱／球面接縫角度", self.seam)
        layout.addLayout(form)
        self.button("套用投影", self.project, layout)
        self.button("套用檢查格", lambda: execute(app, lambda ts: uv.assign_texture(ts, Path(__file__).parent / "checker.png")), layout)
        self.button("選擇貼圖圖片…", self.image, layout)
        self.button("三角化選取面", lambda: execute(app, uv.triangulate), layout)
        layout.addWidget(note("圓柱／球面／四邊面會自動三角化，UV 範圍約為 0–1（接縫處可超出）。四邊面模式把四角貼到一張完整圖片。只調整正面貼圖。"))
        layout.addStretch()
        tabs.addTab(page, "投影")

        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.du, self.dv = number(), number()
        self.su, self.sv = number(1, .001, 1000), number(1, .001, 1000)
        self.degrees = number(0, -36000, 36000, "°")
        self.pu, self.pv = number(.5), number(.5)
        form.addRow("UV 位移 U / V", row(self.du, self.dv))
        form.addRow("UV 倍率 U / V", row(self.su, self.sv))
        form.addRow("旋轉角度", self.degrees)
        form.addRow("旋轉／縮放中心 U / V", row(self.pu, self.pv))
        layout.addLayout(form)
        self.button("套用 UV 變換", self.adjust, layout)
        self.button("水平鏡射 U", lambda: execute(app, lambda ts: uv.transform(ts, su=-1)), layout)
        self.button("垂直鏡射 V", lambda: execute(app, lambda ts: uv.transform(ts, sv=-1)), layout)
        layout.addWidget(note("UV 倍率 2 代表貼圖重複兩倍、圖案縮小。每次點「套用」都會累加到現有 UV。鏡射以 UV (0.5, 0.5) 為中心。"))
        layout.addStretch()
        tabs.addTab(page, "移動／旋轉／縮放")

        page = QWidget()
        layout = QVBoxLayout(page)
        self.button("儲存 UV 快照…", self.save, layout)
        self.button("載回 UV 快照…", self.load, layout)
        layout.addWidget(note("快照只儲存貼圖座標。載回前必須選取同一批面，並保持幾何與面順序不變。"))
        self.button("匯出 UV 編輯用 OBJ…", self.export, layout)
        self.button("從 OBJ 載回 UV…", self.import_uv, layout)
        layout.addWidget(note("OBJ 以公尺匯出三角面，不輸出材質圖片。外部軟體只能編輯 UV；請保留座標系、單位與三角面。載回時會三角化選取面；配對失敗會整筆取消。"))
        layout.addStretch()
        tabs.addTab(page, "儲存／外部 UV")
        self.layout.addWidget(note("在編輯模式外選取元件，會獨立化並保留子群組。若進入元件內編輯面，則沿用原生的共用定義編輯行為。"))
        self.finish()
        self.resize(610, 650)

    def project(self):
        camera = self.app.viewport.camera
        forward = camera.forward()
        right = QVector3D.crossProduct(forward, camera.up_vector()).normalized()
        up = QVector3D.crossProduct(right, forward).normalized()
        execute(self.app, lambda ts: uv.projection(ts, self.mode.currentData(),
            self.axis.currentData(), self.size.value(), self.seam.value(), (right, up)))

    def adjust(self):
        execute(self.app, lambda ts: uv.transform(ts, self.du.value(), self.dv.value(),
            self.su.value(), self.sv.value(), self.degrees.value(), (self.pu.value(), self.pv.value())))

    def image(self):
        filename, _ = QFileDialog.getOpenFileName(self, "選擇貼圖", "", "圖片 (*.png *.jpg *.jpeg *.webp *.bmp)")
        if filename:
            execute(self.app, lambda ts: uv.assign_texture(ts, filename))

    def save(self):
        filename, _ = QFileDialog.getSaveFileName(self, "儲存 UV", "UV快照.json", "UV 快照 (*.json)")
        if filename:
            uv.save_uv(get_targets(self.app.scene, detached=True), filename)

    def load(self):
        filename, _ = QFileDialog.getOpenFileName(self, "載回 UV", "", "UV 快照 (*.json)")
        if filename:
            execute(self.app, lambda ts: uv.load_uv(ts, filename))

    def export(self):
        filename, _ = QFileDialog.getSaveFileName(self, "匯出 UV OBJ", "UV編輯.obj", "Wavefront OBJ (*.obj)")
        if filename:
            uv.export_obj(get_targets(self.app.scene, detached=True), filename)

    def import_uv(self):
        filename, _ = QFileDialog.getOpenFileName(self, "載回 OBJ UV", "", "Wavefront OBJ (*.obj)")
        if filename:
            execute(self.app, lambda ts: uv.import_obj(ts, filename))


