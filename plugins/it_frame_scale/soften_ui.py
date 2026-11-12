"""SketchUp-style tray: right click, angle slider, coplanar checkbox."""
from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QDoubleSpinBox, QCheckBox, QMenu
from .soften import SoftenCommand
from .common import get_targets


class SoftenPanel(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self._gesture = None
        self._version = None
        self._pending = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.flush)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 9, 9, 9)
        row = QHBoxLayout()
        row.addWidget(QLabel('法線間的角度：'))
        self.angle = QDoubleSpinBox()
        self.angle.setRange(0, 180)
        self.angle.setDecimals(1)
        self.angle.setSuffix('°')
        self.angle.setKeyboardTracking(False)
        self.angle.setValue(float(QSettings().value('it_frame_scale/soften_angle', 20.)))
        row.addWidget(self.angle)
        layout.addLayout(row)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1800)
        self.slider.setValue(round(self.angle.value()*10))
        layout.addWidget(self.slider)
        ends = QHBoxLayout()
        ends.addWidget(QLabel('0°'))
        ends.addStretch()
        ends.addWidget(QLabel('180°'))
        layout.addLayout(ends)
        self.normals = QCheckBox('平滑法線')
        self.normals.setChecked(True)
        self.normals.setToolTip('在柔化邊線之間連續計算曲面明暗；銳邊保留轉折。')
        layout.addWidget(self.normals)
        self.coplanar = QCheckBox('柔化共平面')
        self.coplanar.setChecked(True)
        layout.addWidget(self.coplanar)
        note = QLabel('拖曳滑桿直接更新曲面；端面與銳邊依角度保留。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel('選取物件後調整；Ctrl+Z 可復原。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        self.slider.sliderPressed.connect(self.begin)
        self.slider.sliderReleased.connect(self.end)
        self.slider.valueChanged.connect(self.from_slider)
        self.angle.valueChanged.connect(self.from_number)
        self.coplanar.toggled.connect(self.from_checkbox)
        self.normals.toggled.connect(self.from_checkbox)
        self.dock = app.add_panel('柔化邊線', self, name='soften', start_hidden=True)

    def begin(self):
        self._gesture = None

    def end(self):
        self.flush()
        self._gesture = None

    def from_slider(self, value):
        self.angle.blockSignals(True)
        self.angle.setValue(value/10.)
        self.angle.blockSignals(False)
        self.queue()

    def from_number(self, value):
        self.slider.blockSignals(True)
        self.slider.setValue(round(value*10))
        self.slider.blockSignals(False)
        self._gesture = None
        self.queue()

    def from_checkbox(self):
        self._gesture = None
        self.queue()

    def queue(self):
        scene = self.app.scene
        self._pending = (frozenset(scene.selection), scene.version, id(scene.mesh))
        if not self.timer.isActive():
            self.timer.start()

    def flush(self):
        self.timer.stop()
        pending, self._pending = self._pending, None
        if pending is None:
            return
        app, scene = self.app, self.app.scene
        if pending != (frozenset(scene.selection), scene.version, id(scene.mesh)):
            self._gesture = None
            self.status.setText('選取或模型已變更；請重新調整角度。')
            return
        try:
            # Cancel an outstanding scale preview before the independent edit.
            tool = app.window._tools.get('it_frame_scale')
            if tool is not None and tool.dragging:
                tool.on_cancel(app.viewport)
            get_targets(scene)
            history = app.viewport.history
            angle, flat = self.angle.value(), self.coplanar.isChecked()
            if (self._gesture is not None and history.undo_stack
                    and history.undo_stack[-1] is self._gesture
                    and scene.version == self._version):
                command = self._gesture
                command.amend(scene, angle, flat, self.normals.isChecked())
            else:
                command = SoftenCommand(scene, angle, flat, self.normals.isChecked())
                history.execute(command)
                if history.last_error or history.last_rejected:
                    raise ValueError(history.last_error or history.last_rejected)
            app.viewport.notify_scene_changed()
            self._gesture = command if self.slider.isSliderDown() else None
            self._version = scene.version
            QSettings().setValue('it_frame_scale/soften_angle', angle)
            self.status.setText(f'已柔化 {command.count[1]} 條邊線。Ctrl+Z 可復原。')
        except ValueError as error:
            self._gesture = None
            self.status.setText(str(error))

    def open(self):
        self._gesture = None
        self.app.show_panel(self.dock)
        self.queue()
        self.flush()

    def context(self, menu, selection):
        if not selection:
            return
        root = menu
        while isinstance(root.parent(), QMenu):
            root = root.parent()
        if any(a.objectName() == 'it_soften_edges' for a in root.actions()):
            return
        action = root.addAction('柔化／平滑邊線…', lambda: QTimer.singleShot(0, self.open))
        action.setObjectName('it_soften_edges')
