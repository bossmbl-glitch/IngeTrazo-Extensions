# SPDX-License-Identifier: GPL-3.0-or-later
"""Lime rotation 0.3.1, for IngeTrazo 0.6.0."""
from PySide6.QtCore import QObject, QEvent, Qt, QRect
from PySide6.QtGui import QVector3D, QColor
from tools.flip import FlipTool
from core.history import CompoundCommand

# Fixed WORLD axes, independent of the camera and object local axes.
_KEYS = {Qt.Key_Up: 'z_positive', Qt.Key_Down: 'z_negative',
         Qt.Key_Right: 'x_positive', Qt.Key_Left: 'y_positive'}
_ROTATIONS = {'z_positive': ('z', 90), 'z_negative': ('z', -90),
              'x_positive': ('x', 90), 'y_positive': ('y', 90)}



class _KeyFilter(QObject):
    def __init__(self, tool, viewport):
        super().__init__(viewport)
        self.tool, self.viewport = tool, viewport

    def eventFilter(self, watched, event):
        if self.viewport.active_tool is not self.tool:
            return False
        if event.type() not in (QEvent.KeyPress, QEvent.ShortcutOverride):
            return False
        if event.key() not in _KEYS and event.key() != Qt.Key_Escape:
            return False
        if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False
        event.accept()
        if event.type() == QEvent.KeyPress and not event.isAutoRepeat():
            self.tool.on_key(self.viewport, event.key(), event.modifiers())
        return True


class LimeRotation(FlipTool):
    name = '旋轉'
    description = '↑ Z軸+90°　↓ Z軸−90°　→ X軸+90°　← Y軸+90°　Esc 退出'
    shortcut = None
    uses_snap = False

    def __init__(self):
        super().__init__()
        self._filter = None
        self._previous = None
        self._signature = None

    def _say(self, viewport, text, duration=4000):
        viewport.flash_status(text, duration)

    def claims_key(self, key, modifiers):
        return (key in _KEYS or key == Qt.Key_Escape) and not (
            modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))

    def on_activate(self, viewport):
        if viewport.active_tool is not self:
            if not self._targets(viewport)[0]:
                viewport.flash_status('請先選取物件或群組，再啟用旋轉。', 4000)
                return
            self._previous = viewport.active_tool
            viewport.set_active_tool(self)
            viewport.setFocus()
            return
        self._signature = None
        self._capture(viewport)
        self._filter = _KeyFilter(self, viewport)
        viewport.installEventFilter(self._filter)
        self._say(viewport, self.description)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, viewport.setFocus)

    def on_deactivate(self, viewport):
        if self._filter is not None:
            viewport.removeEventFilter(self._filter)
            self._filter.deleteLater()
            self._filter = None
        self._signature = None

    def _capture(self, viewport):
        ok, groups, positions, faces, edges, centre = self._targets(viewport, full=True)
        if not ok:
            return None
        signature = frozenset(id(e) for e in viewport.scene.selection)
        if signature != self._signature:
            self._last_direction = None
            self._centre = QVector3D(centre)
            self._signature = signature
        return groups, positions, faces

    def on_key(self, viewport, key, modifiers):
        if key == Qt.Key_Escape:
            self.on_cancel(viewport)
            return True
        direction = _KEYS.get(key)
        if direction is None:
            return False
        self._perform(viewport, direction)
        return True

    def _perform(self, viewport, direction):
        rotation = _ROTATIONS.get(direction)
        if rotation is not None:
            self._rotate(viewport, *rotation)

    def _rotate(self, viewport, axis_name, degrees):
        from core.history import RotateGroupCommand, RotateVerticesCommand
        try:
            targets = self._capture(viewport)
            if targets is None:
                self._say(viewport, '請先選取物件或群組。')
                return
            groups, positions, faces = targets
            if any(getattr(g, 'locked', False) for g in groups):
                self._say(viewport, '請先解除群組鎖定。')
                return
            movers, seen = [], set()
            def gather(g):
                if id(g) in seen:
                    return
                seen.add(id(g))
                movers.append(g)
                if getattr(g, 'xform', None) is None:
                    for child in getattr(g, 'children', ()):
                        gather(child)
            for g in groups:
                gather(g)
            axis = {'x': QVector3D(1, 0, 0), 'y': QVector3D(0, 1, 0),
                    'z': QVector3D(0, 0, 1)}[axis_name]
            commands = [RotateGroupCommand(g, self._centre, axis, degrees)
                        for g in movers]
            if positions:
                commands.append(RotateVerticesCommand(positions, self._centre,
                                                       axis, degrees,
                                                       carry_textures=True))
            if commands:
                viewport.history.execute(commands[0] if len(commands) == 1
                                         else CompoundCommand(commands))
                self._last_direction = axis_name
                viewport.notify_scene_changed()
                self._say(viewport, f'已繞 {axis_name.upper()} 軸旋轉 {degrees:+d}°｜Ctrl+Z 復原')
        except Exception as error:
            self._say(viewport, '旋轉未完成：' + str(error), 6000)

    def on_key_release(self, viewport, key):
        return False

    def on_cancel(self, viewport):
        previous, self._previous = self._previous, None
        viewport.set_active_tool(previous)
        viewport.flash_status('已退出旋轉。', 3000)

    def on_click(self, ctx):
        pass

    def on_hover(self, ctx):
        pass

    def status_clause(self):
        return self.description

    def draw_overlay(self, viewport, painter):
        from math import hypot
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPen, QPolygonF
        if self._capture(viewport) is None:
            return
        lo, hi = self._bounds(viewport)
        if lo is None or hi is None:
            return
        centre = self._centre
        origin = viewport._world_to_pixel(centre)
        if origin is None:
            return
        # The triad uses model axes and the same pivot as the rotation commands.
        length = max((hi - lo).length() * 0.55, 0.001)
        axes = [('X', QVector3D(1, 0, 0), QColor('#ed4655'), '→ +90°'),
                ('Y', QVector3D(0, 1, 0), QColor('#32bc72'), '← +90°'),
                ('Z', QVector3D(0, 0, 1), QColor('#4b91ff'), '↑ +90° / ↓ −90°')]
        painter.save()
        try:
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
            for label, axis, color, hint in axes:
                tip = viewport._world_to_pixel(centre + axis * length)
                tail = viewport._world_to_pixel(centre - axis * (length * 0.25))
                if tip is None:
                    continue
                ox, oy = origin
                tx, ty = tip
                dx, dy = tx - ox, ty - oy
                distance = hypot(dx, dy)
                painter.setPen(QPen(color, 2.5))
                painter.setBrush(color)
                if distance < 8:
                    # End-on axes need a visible marker rather than a tiny arrow.
                    painter.drawEllipse(QPointF(ox, oy), 5, 5)
                    painter.drawText(QPointF(ox + 10, oy + 22), label + ' ' + hint)
                    continue
                ux, uy = dx / distance, dy / distance
                start = tail if tail is not None else origin
                painter.drawLine(QPointF(*start), QPointF(tx, ty))
                size = min(12.0, distance * 0.3)
                painter.drawPolygon(QPolygonF([
                    QPointF(tx, ty),
                    QPointF(tx - ux * size - uy * size * 0.45,
                            ty - uy * size + ux * size * 0.45),
                    QPointF(tx - ux * size + uy * size * 0.45,
                            ty - uy * size - ux * size * 0.45)]))
                painter.drawText(QPointF(tx + ux * 10 + 4, ty + uy * 10 - 5),
                                 label + ' ' + hint)
            painter.setPen(QPen(QColor('#ffffff'), 1))
            painter.setBrush(QColor('#ffffff'))
            painter.drawEllipse(QPointF(*origin), 3, 3)
            painter.fillRect(QRect(12, 12, 450, 42), QColor(25, 32, 40, 220))
            painter.setPen(QColor('#ffffff'))
            painter.drawText(QRect(22, 17, 430, 32), Qt.AlignLeft | Qt.AlignVCenter,
                             '↑ Z +90°　↓ Z −90°　→ X +90°　← Y +90°｜Esc 退出')
        finally:
            painter.restore()


def setup(app):
    """One visible toolbar button; read the active project on every click."""
    from PySide6.QtCore import QSize, QPointF
    from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap, QPen, QPolygonF
    from PySide6.QtWidgets import QToolBar

    window = app.window
    toolbar = QToolBar('旋轉', window)
    toolbar.setObjectName('lime_directional_mirror_toolbar')
    toolbar.setIconSize(QSize(32, 32))
    toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
    toolbar.setMovable(True)
    toolbar.setFloatable(True)

    pixmap = QPixmap(40, 40)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor('#249ee8'), 4))
    painter.drawArc(7, 7, 26, 26, 40 * 16, 280 * 16)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor('#f4a544'))
    painter.drawPolygon(QPolygonF([QPointF(34, 5), QPointF(34, 18), QPointF(23, 10)]))
    painter.end()

    action = QAction(QIcon(pixmap), '旋轉', toolbar)
    action.setObjectName('lime_directional_mirror_activate')
    action.setToolTip('旋轉\n先選物件，再點此按鈕\n↑ Z+90°　↓ Z−90°　→ X+90°　← Y+90°\nEsc 退出')
    action.setStatusTip(LimeRotation.description)


    def activate(checked=False):
        viewport = app.viewport
        # One session per viewport avoids retaining another tab's selection.
        tool = getattr(viewport, '_lime_directional_mirror_tool', None)
        if tool is None:
            tool = LimeRotation()
            viewport._lime_directional_mirror_tool = tool
        if viewport.active_tool is tool:
            viewport.flash_status(tool.description, 4000)
            viewport.setFocus()
        else:
            tool.on_activate(viewport)

    action.triggered.connect(activate)
    toolbar.addAction(action)
    window.addToolBar(Qt.TopToolBarArea, toolbar)
    toolbar.show()
    # Parent ownership and this reference retain the toolbar and callback.
    window._lime_directional_mirror_toolbar = toolbar
