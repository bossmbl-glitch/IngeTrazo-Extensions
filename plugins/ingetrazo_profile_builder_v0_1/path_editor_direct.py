# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct model gestures and compact controls; geometry stays in the session."""
import math
import numpy as np
from PySide6.QtCore import Qt,QPoint,QPointF
from PySide6.QtGui import QColor,QPen
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,QDoubleSpinBox
from .path_editor_enhancements import EnhancedPathEditTool


class DirectPathEditTool(EnhancedPathEditTool):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.toolbar=None;self.length_dialog=None;self.dimension_buttons=[]
        self.direct_mode=True
        self.corner_editor=None;self.corner_button=None

    def _open_dialog(self):
        # Retain the tested secondary controls, hidden until explicitly opened.
        super()._open_dialog()
        self.dialog.hide();self.dialog.setWindowTitle('更多路徑工具')
        self.dialog.rejected.disconnect(self.cancel)
        self.dialog.rejected.connect(self._return_direct)
        self.dialog.finished.connect(lambda *_:self._return_direct())
        for label in self.scroll_body.findChildren(QLabel):
            if '原XYZ絕對' in label.text():label.setText('對齊使用原牆面／踢腳平面；空間路徑使用XY。\nXYZ絕對座標為cm，僅編輯主節點。')
        for spin in self.coordinates:spin.setSuffix(' cm');spin.setDecimals(5)
        self.toolbar=QDialog(self.panel,Qt.Tool|Qt.WindowTitleHint|Qt.WindowCloseButtonHint)
        self.toolbar.setWindowTitle('編輯路徑');self.toolbar.setMinimumWidth(360)
        layout=QVBoxLayout(self.toolbar);header=QHBoxLayout()
        title=QLabel('編輯路徑');self.title_label=title;title.setStyleSheet('font-weight:bold; font-size:15px;')
        header.addWidget(title);header.addStretch()
        more=QPushButton('更多工具 ▾');more.setAutoDefault(False);more.clicked.connect(self.show_more);header.addWidget(more)
        layout.addLayout(header);row=QHBoxLayout()
        for text,callback in [('↶ 復原',self.undo),('↷ 重做',self.redo),('完成',self.finish),('取消',self.cancel)]:
            button=QPushButton(text);button.setAutoDefault(False);button.clicked.connect(callback);row.addWidget(button)
            if text=='完成':button.setStyleSheet('background:#168bd2; color:white; padding:5px 12px;')
        layout.addLayout(row)
        help_label=QLabel('拖節點或線段；點尺寸輸入長度。\nShift 多選 · 空白處拖曳框選 · 雙擊線段插點')
        layout.addWidget(help_label)
        self.direct_status=QLabel('Enter 完成 · Esc 取消 · Delete 刪點');self.direct_status.setWordWrap(True)
        layout.addWidget(self.direct_status)
        self.toolbar.rejected.connect(self.cancel)
        self.toolbar.resize(390,self.toolbar.sizeHint().height())
        origin=self.viewport.mapToGlobal(QPoint(16,16));self.toolbar.move(origin)
        self.toolbar.show();self._sync();self._focus_viewport()
        self.corner_button=QPushButton('倒角／圓角…',self.viewport)
        self.corner_button.setAutoDefault(False);self.corner_button.hide()
        self.corner_button.setStyleSheet('background:#fff3e2; color:#965200; border:1px solid #e2a04d; border-radius:6px; padding:4px 8px;')
        self.corner_button.clicked.connect(self.open_corner)
        corner_more=self._button('選取轉角：倒角／圓角…',self.open_corner)
        self.scroll_body.layout().insertWidget(6,corner_more)

    def show_more(self):
        self._dismiss_length();self._stop_move();self.direct_mode=False
        self.dialog.show();self.dialog.raise_();self.dialog.activateWindow()

    def _return_direct(self):
        if self.done:return
        self._stop_move();self.direct_mode=True
        self.mode_combo.setCurrentIndex(0);self.mode=0;self._sync();self._focus_viewport()

    def _sync(self):
        super()._sync()
        if self.dialog is not None:
            if self.selected is not None:
                point=self.candidate if self.moving and self.candidate is not None else self.session.points[self.selected]
                for spin,value in zip(self.coordinates,point):spin.setValue(value*100.)
        if self.toolbar is not None and hasattr(self,'direct_status'):
            self.direct_status.setText('拖曳中，放開滑鼠定位。' if self.moving else 'Enter 完成 · Esc 取消 · Delete 刪點')

    def _apply_coordinates(self):
        if self.selected is None:self._status('請先點選一個節點。');return
        point=[spin.value()/100. for spin in self.coordinates]
        self._stop_move();self.session.move(self.selected,point);self._sync();self._focus_viewport()

    def _status(self,message=None):
        super()._status(message)
        if self.toolbar is not None and hasattr(self,'direct_status'):
            self.direct_status.setText(message or 'Enter 完成 · Esc 取消 · Delete 刪點')

    def on_click(self,ctx):
        if self.done:return
        if self.corner_editor is not None:return
        self._dismiss_length()
        if not self.direct_mode or self.mode!=0 or self.moving or self.box_start is not None:
            return super().on_click(ctx)
        index=self._node_at(ctx.screen)
        if index is not None:return super().on_click(ctx)
        segment=self._segment_at(ctx.screen)
        if segment is not None:
            previous=self.mode;self.mode=3
            try:super().on_click(ctx)
            finally:self.mode=previous
        else:
            self.box_start=QPointF(ctx.screen);self.box_end=QPointF(ctx.screen)
            self.box_add=bool(getattr(ctx,'modifiers',Qt.NoModifier)&Qt.ShiftModifier)

    def on_double_click(self,ctx):
        if self.done:return
        if self.corner_editor is not None:return
        self._dismiss_length();self._stop_move();segment=self._segment_at(ctx.screen)
        if segment is None:return
        index,t=segment
        points=self.session.points;a=np.array(points[index]);b=np.array(points[(index+1)%len(points)])
        self._safe(lambda:self._insert_direct(index,a+(b-a)*t))

    def _insert_direct(self,index,point):
        self.selected=self.session.insert(index,point);self.selection={self.selected};self.segment=index

    def on_release(self,viewport):
        if self.corner_editor is not None:return
        super().on_release(viewport)
        if self.direct_mode and self.mode==0 and self.moving:
            self._stop_move();self._sync()
        # A blank click simply clears selection. A dragged box is completed above.
        if self.box_start is not None:
            self._finish_box();self._sync()

    def _dismiss_length(self):
        if self.length_dialog is not None:
            self.length_dialog.close();self.length_dialog.deleteLater();self.length_dialog=None

    def undo(self):
        self._dismiss_corner()
        self._dismiss_length();self.error='';super().undo()

    def redo(self):
        self._dismiss_corner()
        self._dismiss_length();self.error='';super().redo()

    def on_key(self,viewport,key,modifiers):
        if self.corner_editor is not None:
            if key==int(Qt.Key_Escape):self._dismiss_corner()
            return True
        before=len(self.session.points)
        if key in (int(Qt.Key_Delete),int(Qt.Key_Backspace)):self._dismiss_length()
        result=super().on_key(viewport,key,modifiers)
        if len(self.session.points)!=before:
            self.selection.clear();self.selected=None;self.segment=None;self._sync()
        return result

    def _open_length(self,index):
        if self.done:return
        if self.corner_editor is not None:return
        self._stop_move();self.segment=index;self.selected=index;self.selection={index};self._sync()
        if self.length_dialog is not None:self.length_dialog.close();self.length_dialog.deleteLater()
        dialog=QDialog(self.toolbar or self.panel,Qt.Tool|Qt.WindowTitleHint|Qt.WindowCloseButtonHint)
        self.length_dialog=dialog;dialog.setWindowTitle('線段長度');layout=QVBoxLayout(dialog)
        row=QHBoxLayout();value=QDoubleSpinBox();value.setDecimals(4);value.setRange(.0001,1e8)
        value.setSuffix(' cm');value.setValue(self.session.segment_length(index)*100.);row.addWidget(value)
        fixed=QComboBox();fixed.addItems(['固定起點','固定終點','固定中心']);row.addWidget(fixed)
        ok=QPushButton('確定');ok.setDefault(True);row.addWidget(ok);layout.addLayout(row)
        error=QLabel();error.setWordWrap(True);layout.addWidget(error)
        def apply():
            try:
                self.session.set_segment_length(index,value.value()/100.,('start','end','center')[fixed.currentIndex()])
                self.error='';self._sync();dialog.accept();self._focus_viewport()
            except (ValueError,IndexError,ArithmeticError) as exc:error.setText(str(exc))
        ok.clicked.connect(apply)
        self.length_input=value;self.length_fixed=fixed;self.length_ok=ok
        dialog.show();dialog.adjustSize()
        button=next((b for b in self.dimension_buttons if b.property('segment')==index),None)
        anchor=button.mapToGlobal(QPoint(0,-dialog.height()-8)) if button is not None else self.viewport.mapToGlobal(QPoint(40,150))
        screen=dialog.screen().availableGeometry()
        dialog.move(max(screen.left(),min(anchor.x(),screen.right()-dialog.width())),max(screen.top(),min(anchor.y(),screen.bottom()-dialog.height())))
        value.setFocus();value.selectAll()

    def _update_dimensions(self):
        if self.done:return
        points=self._display_points();count=len(points) if self.session.closed else len(points)-1
        # Small paths show each length; large paths show only the selected segment.
        indices=list(range(count)) if count<=32 else ([self.segment] if self.segment is not None and self.segment<count else [])
        while len(self.dimension_buttons)<len(indices):
            button=QPushButton(self.viewport);button.setAutoDefault(False)
            button.setStyleSheet('QPushButton {background:#ffffff; color:#165b78; border:1px solid #78b9da; border-radius:9px; padding:3px 7px;} QPushButton:hover {background:#e4f5ff;}')
            button.setToolTip('點擊修改線段長度（cm）')
            button.clicked.connect(lambda checked=False,b=button:self._open_length(b.property('segment')))
            self.dimension_buttons.append(button)
        occupied=[]
        for slot,button in enumerate(self.dimension_buttons):
            if slot>=len(indices):button.hide();continue
            i=indices[slot];a,b=points[i],points[(i+1)%len(points)]
            pixel=self._project((np.array(a)+b)*.5)
            if pixel is None:button.hide();continue
            text=f'{math.dist(a,b)*100.:.2f} cm'
            if button.text()!=text:button.setText(text);button.adjustSize()
            button.setProperty('segment',i)
            x=round(pixel[0]-button.width()*.5);y=round(pixel[1]-button.height()*.5)
            rectangle=button.rect().translated(x,y)
            # Avoid covering node handles or another dimension at distant zoom.
            node_pixels=[self._project(p) for p in points]
            covers_node=any(p is not None and rectangle.adjusted(-10,-10,10,10).contains(QPoint(round(p[0]),round(p[1]))) for p in node_pixels)
            if x<0 or y<0 or x+button.width()>self.viewport.width() or y+button.height()>self.viewport.height() or covers_node or any(rectangle.intersects(other) for other in occupied):
                button.hide();continue
            occupied.append(rectangle);button.move(x,y);button.show();button.raise_()

    def draw_overlay(self,viewport,painter):
        # Lengths are actual clickable controls instead of painted text.
        segment=self.segment;self.segment=None
        selected=self.selected;selection=self.selection
        if self.corner_editor is not None:
            self.selected=None;self.selection=set()
        try:super().draw_overlay(viewport,painter)
        finally:self.segment=segment;self.selected=selected;self.selection=selection
        self._update_dimensions()
        self._draw_corner_preview(painter)
        self._update_corner_button()

    @property
    def overlay_hidden_nodes(self):
        editor=self.corner_editor
        return set(editor.changed[1:-1]) if editor is not None and editor.visible_preview and editor.points is not None else set()

    def on_hover(self,ctx):
        if self.corner_editor is None:super().on_hover(ctx)

    def _display_points(self):
        editor=self.corner_editor
        if editor is not None and editor.visible_preview and editor.points is not None:
            return editor.points
        return super()._display_points()

    def open_corner(self):
        if self.done:return
        if self.corner_editor is not None:
            self.corner_editor.dialog.raise_();return
        if self.selected is None or self.selection!={self.selected}:
            self._status('請先單擊選取一個轉角，再按節點旁的「倒角／圓角」。');return
        if not self.session.closed and self.selected in (0,len(self.session.points)-1):
            self._status('開放路徑端點沒有轉角，請選中間節點。');return
        self._dismiss_length();self._stop_move()
        from .path_corners import CornerEditor
        self.corner_editor=CornerEditor(self,self.selected)
        self.corner_editor.show();self._update_corner_button()

    def _dismiss_corner(self):
        if self.corner_editor is not None:self.corner_editor.dialog.reject()

    def _update_corner_button(self):
        button=self.corner_button
        if button is None:return
        i=self.selected
        valid=(not self.done and self.corner_editor is None and not self.moving and i is not None
               and self.selection=={i} and (self.session.closed or 0<i<len(self.session.points)-1))
        if not valid:button.hide();return
        pixel=self._project(self.session.points[i])
        if pixel is None or not (0<=pixel[0]<=self.viewport.width() and 0<=pixel[1]<=self.viewport.height()):
            button.hide();return
        button.adjustSize()
        x=max(0,min(round(pixel[0]+14),self.viewport.width()-button.width()))
        y=max(0,min(round(pixel[1]+28),self.viewport.height()-button.height()))
        button.move(x,y);button.show();button.raise_()

    def _draw_corner_preview(self,painter):
        editor=self.corner_editor
        if editor is None or not editor.visible_preview or editor.points is None:return
        painter.save()
        try:
            path=editor.points;indices=editor.changed
            # The orange replacement follows the exact path used by the mesh preview.
            painter.setPen(QPen(QColor('#ed8c15'),3))
            for a,b in zip(indices,indices[1:]):
                start,end=self._project(path[a]),self._project(path[b])
                if start is not None and end is not None:painter.drawLine(QPointF(*start),QPointF(*end))
            painter.setBrush(QColor('#ed8c15'));painter.setPen(QPen(QColor('white'),1.5))
            for i in (indices[0],indices[-1]):
                pixel=self._project(path[i])
                if pixel is not None:painter.drawEllipse(QPointF(*pixel),5,5)
            middle=self._project(path[indices[len(indices)//2]])
            if middle is not None:
                painter.setPen(QColor('#965200'))
                label=('R ' if editor.kind.currentIndex()==1 else '兩側各退 ')+f'{editor.amount.value():g} cm'
                painter.drawText(QPointF(*middle)+QPointF(14,-18),label)
            original=editor.source[0];i=editor.index;corner=self._project(original[i])
            painter.setPen(QPen(QColor('#888888'),1.5,Qt.DashLine))
            if corner is not None:
                for end in (self._project(path[indices[0]]),self._project(path[indices[-1]])):
                    if end is not None:painter.drawLine(QPointF(*corner),QPointF(*end))
                painter.setBrush(QColor('white'));painter.drawEllipse(QPointF(*corner),4,4)
        finally:painter.restore()

    def finish(self):
        self._dismiss_corner();super().finish()

    def cancel(self):
        self._dismiss_corner();super().cancel()

    def apply_continue(self):
        self._dismiss_corner();super().apply_continue()

    def on_deactivate(self,viewport):
        self._dismiss_corner()
        if self.corner_button is not None:
            self.corner_button.hide();self.corner_button.deleteLater();self.corner_button=None
        for button in self.dimension_buttons:button.hide();button.deleteLater()
        self.dimension_buttons=[]
        if self.length_dialog is not None:self.length_dialog.close();self.length_dialog.deleteLater();self.length_dialog=None
        if self.toolbar is not None:
            toolbar=self.toolbar;self.toolbar=None
            toolbar.rejected.disconnect(self.cancel);toolbar.close();toolbar.deleteLater()
        super().on_deactivate(viewport)
