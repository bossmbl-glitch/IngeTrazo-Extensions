"""Traditional Chinese dialogs. All model edits go through the host history."""
from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QVector3D
from PySide6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QPushButton, QDoubleSpinBox, QSpinBox, QComboBox,
    QTabWidget, QFileDialog, QCheckBox)
from .common import execute, get_targets, xyz
from . import deform


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


class WirePreview(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(210)
        self.lines = []

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#17232e"))
        painter.setPen(QColor("#dbe6ee"))
        painter.drawText(12, 22, "線框預覽：灰色原形／青色結果（不修改模型）")
        if not self.lines:
            painter.drawText(12, 55, "選取模型後，按「更新預覽」。")
            return

        def iso(p):
            x, y, z = xyz(p)
            return (x * .8 - y * .6, x * .25 + y * .33 - z * .9)

        projected = [(iso(a), iso(b), changed) for a, b, changed in self.lines]
        pts = [p for a, b, _ in projected for p in (a, b)]
        lo = [min(p[i] for p in pts) for i in range(2)]
        hi = [max(p[i] for p in pts) for i in range(2)]
        scale = min((self.width() - 36) / max(hi[0] - lo[0], 1e-5),
                    (self.height() - 65) / max(hi[1] - lo[1], 1e-5))

        def screen(p):
            return QPointF((p[0] - (lo[0] + hi[0]) / 2) * scale + self.width() / 2,
                           (p[1] - (lo[1] + hi[1]) / 2) * scale + (self.height() + 30) / 2)

        for a, b, changed in projected:
            painter.setPen(QPen(QColor("#55ddd0" if changed else "#697582"), 1.3 if changed else 1))
            painter.drawLine(screen(a), screen(b))


class DeformDialog(BaseDialog):
    def __init__(self, app):
        super().__init__(app, "縮放與變形工具 · IngeTrazo")
        form = QFormLayout()
        self.mode = combo([("縮放", "scale"), ("旋轉", "rotate"), ("區段拉伸", "stretch"),
            ("平面剪切", "shear"), ("錐化", "taper"), ("扭轉", "twist"), ("徑向彎曲", "bend"),
            ("固定邊框縮放（保留厚度）", "fixed_border")])
        self.axis = combo([("X", 0), ("Y", 1), ("Z", 2)])
        self.axis.setCurrentIndex(2)
        self.center = combo([("包圍盒中心", True), ("包圍盒最小角", False)])
        self.orientation = [number(0, -360, 360, "°") for _ in range(3)]
        form.addRow("操作", self.mode)
        form.addRow("變形軸", self.axis)
        self.axis_label = form.labelForField(self.axis)
        form.addRow("尺寸／旋轉／剪切支點", self.center)
        form.addRow("座標框旋轉 X / Y / Z", row(*self.orientation))
        self.layout.addLayout(form)
        self.params_widget = QWidget()
        form = QFormLayout(self.params_widget)
        form.setContentsMargins(0, 0, 0, 0)
        self.factors = [number(1, .001, 1000) for _ in range(3)]
        self.absolute = QCheckBox("改用目標尺寸（cm）")
        self.dimensions = [number(100, .001, 1000000, " cm") for _ in range(3)]
        self.fixed_axes = [QCheckBox(f"調整 {name}") for name in "XYZ"]
        for i, field in enumerate(self.fixed_axes):
            field.setChecked(i != 1)
        self.auto_border = QCheckBox("依模型自動判斷")
        self.auto_border.setChecked(True)
        self.borders = [number(0, 0, 100000, " cm") for _ in range(3)]
        self.midbands = [number(0, 0, 100000, " cm") for _ in range(3)]
        self.read_dimensions = QPushButton("讀取選取物件目前尺寸")
        self.read_dimensions.clicked.connect(lambda: self.run(self.read_current_dimensions, "已讀取目前尺寸；修改目標尺寸後再預覽或套用。"))
        self.angle = number(45, -350, 350, "°")
        self.distance = number(20, -100000, 100000, " cm")
        self.lower, self.upper = number(25, 0, 99, "%"), number(75, 1, 100, "%")
        self.tu, self.tv = number(.5, .001, 100), number(.5, .001, 100)
        self.subdivision = QSpinBox()
        self.subdivision.setRange(0, 4)
        self.subdivision.setValue(2)
        self.fields = []

        def add(label, widget, modes):
            form.addRow(label, widget)
            self.fields.append((form.labelForField(widget), widget, modes))

        add("倍率 X / Y / Z", row(*self.factors), {"scale"})
        add("尺寸模式", self.absolute, {"scale"})
        add("調整方向（未勾選保持原尺寸）", row(*self.fixed_axes), {"fixed_border"})
        add("目標 X / Y / Z", row(*self.dimensions), {"scale", "fixed_border"})
        add("保留邊框", self.auto_border, {"fixed_border"})
        add("手動兩端各保留 X / Y / Z", row(*self.borders), {"fixed_border"})
        add("中央保留 X / Y / Z（0＝關閉）", row(*self.midbands), {"fixed_border"})
        add("目前尺寸", self.read_dimensions, {"fixed_border"})
        add("角度", self.angle, {"rotate", "shear", "twist", "bend"})
        add("拉伸量（負數為壓縮）", self.distance, {"stretch"})
        add("拉伸區間起點／終點", row(self.lower, self.upper), {"stretch"})
        add("末端橫向倍率 U / V", row(self.tu, self.tv), {"taper"})
        add("細分層級（每級面數 ×4）", self.subdivision, {"stretch", "taper", "twist", "bend"})
        self.layout.addWidget(self.params_widget)
        self.help = note("")
        self.layout.addWidget(self.help)
        self.preview = WirePreview()
        self.layout.addWidget(self.preview)
        actions = QHBoxLayout()
        self.button("更新預覽", self.update_preview, actions)
        self.button("套用到選取物件", self.apply, actions)
        self.layout.addLayout(actions)
        self.layout.addWidget(note("裸面／邊會擴展至整個相連物件。元件會獨立化，子群組保留。非線性變形會三角化並細分；預覽只顯示外框取樣。"))
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.absolute.toggled.connect(self.mode_changed)
        self.auto_border.toggled.connect(self.mode_changed)
        for field in self.fixed_axes:
            field.toggled.connect(self.mode_changed)
        for field in [self.axis, self.center, *self.orientation, *self.factors, *self.dimensions,
                      self.angle, self.distance, self.lower, self.upper, self.tu, self.tv, self.subdivision,
                      *self.borders, *self.midbands]:
            signal = field.currentIndexChanged if isinstance(field, QComboBox) else field.valueChanged
            signal.connect(self.invalidate_preview)
        self.mode_changed()
        self.finish()
        self.resize(650, 810)

    def invalidate_preview(self, *unused):
        self.preview.lines = []
        self.preview.update()

    def mode_changed(self, *unused):
        mode = self.mode.currentData()
        entering = mode != getattr(self, '_last_mode', None)
        self._last_mode = mode
        fixed = mode == "fixed_border"
        self.axis.setVisible(not fixed)
        self.axis_label.setVisible(not fixed)
        self.preview.setMinimumHeight(160 if fixed else 210)
        for label, widget, modes in self.fields:
            label.setVisible(mode in modes)
            widget.setVisible(mode in modes)
        for f in self.factors:
            f.setEnabled(not self.absolute.isChecked())
        for i,f in enumerate(self.dimensions):
            f.setEnabled(self.fixed_axes[i].isChecked() if fixed else self.absolute.isChecked())
            self.borders[i].setEnabled(self.fixed_axes[i].isChecked() and not self.auto_border.isChecked())
            self.midbands[i].setEnabled(self.fixed_axes[i].isChecked())
        self.help.setText({
            "scale": "倍率 1 不變、2 放大兩倍；可直接輸入目標公分尺寸。",
            "rotate": "繞選定軸旋轉，正角度遵循右手定則。",
            "stretch": "起點以下固定，終點以上平移，中間區段均勻拉伸。細分越高，分界近似越細。",
            "shear": "沿軸高度增加橫向偏移。X 軸推向 Y；Y 軸推向 Z；Z 軸推向 X。",
            "taper": "軸底端保持原尺寸，頂端依 U／V 倍率收縮或放大。",
            "twist": "軸底端不轉，頂端旋轉至指定角度；建議細分 2–3。",
            "bend": "沿軸彎成圓弧。X 軸彎向 Y；Y 軸彎向 Z；Z 軸彎向 X。建議細分 2–3。",
            "fixed_border": "依模型最外側的內部分界保留邊框，中間空間伸縮；沒有內部分界的方向採一般縮放。特殊造型可手動設定保留範圍。",
        }[mode])
        if fixed and entering:
            try:
                self.read_current_dimensions()
            except ValueError:
                self.status.setText("請先選取完整外框，再按「讀取選取物件目前尺寸」。")
        self.invalidate_preview()

    def read_current_dimensions(self):
        targets = get_targets(self.app.scene, connected=True, detached=True)
        sizes = deform.dimensions_cm([v.position for t in targets for v in t.vertices],
                                     tuple(f.value() for f in self.orientation))
        for field,size in zip(self.dimensions,sizes):
            field.setValue(size)

    def parameters(self):
        return deform.Parameters(mode=self.mode.currentData(), axis=self.axis.currentData(),
            factors=tuple(f.value() for f in self.factors), angle=self.angle.value(),
            distance_cm=self.distance.value(), lower=self.lower.value() / 100., upper=self.upper.value() / 100.,
            taper_u=self.tu.value(), taper_v=self.tv.value(), center=self.center.currentData(),
            orientation=tuple(f.value() for f in self.orientation), subdivisions=self.subdivision.value(),
            target_cm=tuple(f.value() for f in self.dimensions) if (self.absolute.isChecked() and self.mode.currentData() == "scale") or self.mode.currentData() == "fixed_border" else None,
            fixed_axes=tuple(f.isChecked() for f in self.fixed_axes),
            border_cm=None if self.auto_border.isChecked() else tuple(f.value() for f in self.borders),
            center_band_cm=tuple(f.value() for f in self.midbands))

    def update_preview(self):
        targets = get_targets(self.app.scene, connected=True, detached=True)
        mapper = deform.mapper_for_targets(targets, self.parameters())
        edges = [e for t in targets for e in t.mesh.edges if e.v0 in t.vertices and e.v1 in t.vertices]
        edges = edges[::max(1, (len(edges) + 1199) // 1200)]
        lines = []
        for edge in edges:
            lines.append((edge.a, edge.b, False))
            if hasattr(mapper, 'cut_planes'):
                lines.extend((a,b,True) for a,b in deform._cut_edge(edge.a,edge.b,mapper))
            else:
                points = [mapper(edge.a + (edge.b - edge.a) * (i / 8)) for i in range(9)]
                lines.extend((a, b, True) for a, b in zip(points, points[1:]))
        self.preview.lines = lines
        self.preview.update()

    def apply(self):
        params = self.parameters()
        execute(self.app, lambda ts: deform.apply(ts, params), connected=True)
        self.invalidate_preview()
