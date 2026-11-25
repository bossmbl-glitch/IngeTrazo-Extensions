# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct-edit assistance without expanding the default toolbar."""
import copy,math
import numpy as np
from PySide6.QtCore import Qt,QPoint,QPointF,QRect,QTimer,QObject,QEvent
from PySide6.QtGui import QColor,QPen
from PySide6.QtWidgets import (QMenu,QPushButton,QMessageBox,QDialog,QVBoxLayout,QFormLayout,
    QHBoxLayout,QLabel,QDoubleSpinBox,QComboBox,QCheckBox,QInputDialog)
from .path_editor_direct import DirectPathEditTool
from .path_aid_session import point_key


class PanelExitGuard(QObject):
    def __init__(self,tool):
        super().__init__(tool.panel);self.tool=tool
    def eventFilter(self,watched,event):
        tool=self.tool
        if event.type()==QEvent.Close and watched is tool.panel and not tool.done:
            tool.cancel()
            if not tool.done:event.ignore();return True
        return False


class FramePreview:
    def __init__(self,tool):
        self.tool=tool;self.session=tool.session;self.source=copy.deepcopy(self.session.points);self.closed=self.session.closed
        self.points=None;self.finished=False;self.fixed_point=None
        _,_,low,high=self.session.frame_bounds()
        dialog=QDialog(tool.toolbar,Qt.Tool|Qt.WindowTitleHint|Qt.WindowCloseButtonHint)
        self.dialog=dialog;dialog.setWindowTitle('整框尺寸');dialog.setWindowModality(Qt.ApplicationModal)
        layout=QVBoxLayout(dialog);form=QFormLayout();self.fields=[]
        for label,value in zip(('寬度','高度'),(high-low)*100.):
            spin=QDoubleSpinBox();spin.setRange(.0001,1e8);spin.setDecimals(4);spin.setSuffix(' cm');spin.setValue(value)
            form.addRow(label,spin);self.fields.append(spin)
        self.anchor=QComboBox();self.anchor.addItems(['中心','左下角','右下角','左上角','右上角']);form.addRow('固定位置',self.anchor)
        layout.addLayout(form);layout.addWidget(QLabel('尺寸以路徑為準；套用後按完成才重建。'))
        self.message=QLabel();self.message.setWordWrap(True);layout.addWidget(self.message)
        row=QHBoxLayout();self.apply_button=QPushButton('套用');self.apply_button.setDefault(True);row.addWidget(self.apply_button)
        cancel=QPushButton('取消');cancel.setAutoDefault(False);row.addWidget(cancel);layout.addLayout(row)
        self.timer=QTimer(dialog);self.timer.setSingleShot(True);self.timer.timeout.connect(self.refresh)
        for field in self.fields:field.valueChanged.connect(self.schedule)
        self.anchor.currentIndexChanged.connect(self.schedule)
        self.apply_button.clicked.connect(self.apply);cancel.clicked.connect(dialog.reject);dialog.finished.connect(self.dispose)
    def schedule(self,*_):
        if self.finished:return
        self.points=None;self.apply_button.setEnabled(False);self.message.setText("計算預覽中…")
        self.tool.ghost=[];self.tool.viewport.update();self.timer.start(90)
    def refresh(self,*_):
        if self.finished:return
        self.timer.stop();self.points=None
        try:
            if self.tool.session is not self.session or self.source!=self.session.points or self.closed!=self.session.closed:raise ValueError('工作路徑已改變，請重新開啟整框尺寸。')
            self.session.assert_unchanged(self.tool.viewport.scene)
            self.points=self.session.frame_points(*(s.value()/100. for s in self.fields),
                ('center','bottom_left','bottom_right','top_left','top_right')[self.anchor.currentIndex()])
            temp=copy.copy(self.session);temp.points=self.points;temp.replacement()
            u,v,low,high=self.session.frame_bounds()
            fraction=((.5,.5),(0,0),(1,0),(0,1),(1,1))[self.anchor.currentIndex()]
            fixed=low+(high-low)*np.array(fraction);origin=np.array(self.source[0])
            self.fixed_point=point_key(origin+u*(fixed[0]-origin@u)+v*(fixed[1]-origin@v))
            self.message.setText('預覽有效。');self.apply_button.setEnabled(True)
        except (ValueError,RuntimeError,ArithmeticError) as exc:self.points=None;self.message.setText(str(exc));self.apply_button.setEnabled(False)
        self.tool._refresh_analysis()
    def show(self):self.dialog.show();self.refresh()
    def apply(self):
        self.refresh()
        if self.points is None:return
        if any(math.dist(a,b)>1e-9 for a,b in zip(self.source,self.points)):
            self.session._record();self.session.points=copy.deepcopy(self.points)
        self.dialog.accept();self.tool._sync();self.tool._focus_viewport()
    def dispose(self,*_):
        if self.finished:return
        self.finished=True;self.timer.stop();self.points=None
        if self.tool.frame_preview is self:self.tool.frame_preview=None
        if not self.tool.done:self.tool._schedule_analysis();self.tool.viewport.update()
        self.dialog.deleteLater()


class ProductivePathEditTool(DirectPathEditTool):
    vcb_label='移動距離（未標單位預設cm）'
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.frame_preview=None;self.hover_node=None;self.hover_segment=None
        self.guide_lines=[];self.guide_text='';self.right_snap=True;self.align_snap=True
        self.dimension_leaders=[];self.move_before=None;self.last_move=None;self.hover_point=None
        self.lock_button=None;self.error_button=None;self._discarding=False
        self.exit_decision=self._ask_exit_choice;self.panel_exit_guard=None
    def _open_dialog(self):
        super()._open_dialog()
        self.panel_exit_guard=PanelExitGuard(self);self.panel.installEventFilter(self.panel_exit_guard)
        more=next(b for b in self.toolbar.findChildren(QPushButton) if b.text()=='更多工具 ▾')
        more.clicked.disconnect(self.show_more);more.clicked.connect(self.show_actions)
        self.lock_button=QPushButton(self.viewport);self.lock_button.setAutoDefault(False)
        self.lock_button.clicked.connect(self.toggle_selected_locks);self.lock_button.hide()
        self.error_button=QPushButton('復原這一步',self.viewport);self.error_button.setAutoDefault(False)
        self.error_button.setStyleSheet('color:#c02020; background:#fff0f0; border:1px solid #d74646; padding:4px;')
        self.error_button.clicked.connect(self.undo_problem);self.error_button.hide()
    def show_actions(self):
        menu=QMenu(self.toolbar)
        for text,callback in [('進階工具…',self.show_more),('整框寬高…',self.open_frame),('選取轉角：倒角／圓角…',self.open_corner),
            ('鎖定／解鎖選取節點',self.toggle_selected_locks),('等距分點…',self.open_divide),
            ('封閉框斷開選取線段',self.cut_selected),('接合開放路徑首尾',self.join_selected),('取消複製模式' if self.session.copy_mode else '複製並編輯',self.toggle_copy_mode)]:
            menu.addAction(text,callback)
        menu.addSeparator()
        for text,name in [('直角吸附','right_snap'),('節點對齊輔助線','align_snap')]:
            action=menu.addAction(text);action.setCheckable(True);action.setChecked(getattr(self,name))
            action.toggled.connect(lambda value,n=name:setattr(self,n,value))
        menu.aboutToHide.connect(menu.deleteLater)
        button=self.toolbar.findChildren(QPushButton)[0]
        menu.popup(button.mapToGlobal(QPoint(0,button.height())))
        self.actions_menu=menu
    def _display_points(self):
        if self.frame_preview is not None and self.frame_preview.points is not None:return self.frame_preview.points
        return super()._display_points()
    def _dismiss_frame(self):
        if self.frame_preview is not None:self.frame_preview.dialog.reject()
    def open_frame(self):
        if self.done:return
        self._dismiss_corner();self._dismiss_length();self._stop_move()
        if self.frame_preview is not None:self.frame_preview.dialog.raise_();return
        try:self.frame_preview=FramePreview(self);self.frame_preview.show()
        except (ValueError,RuntimeError,ArithmeticError) as exc:self._status(str(exc))
    def open_divide(self):
        try:index=self._segment()
        except ValueError as exc:self._status(str(exc));return
        parts,ok=QInputDialog.getInt(self.toolbar,'等距分點','將選取線段平均分成幾份？',2,2,128)
        if ok:self._safe(lambda:self.session.divide_segment(index,parts))
    def cut_selected(self):
        self._stop_move();self._safe(lambda:self.session.cut_segment(self._segment()))
        self.selection.clear();self.selected=None;self.segment=None;self._sync()
    def join_selected(self):self._stop_move();self._safe(self.session.join_ends)
    def arm_copy(self):
        self._stop_move();self.session.arm_copy();self._sync();self._status('複製並編輯：完成時新增獨立副本，原線板保留；副本預設仍在原位。')
    def toggle_copy_mode(self):
        if self.session.copy_mode:
            self.session._record();self.session.copy_mode=False;self._sync();self._status('已改回編輯原線板；尚未提交修改。')
        else:self.arm_copy()
    def _sync(self):
        super()._sync()
        if self.toolbar is not None and hasattr(self,'title_label'):
            title='編輯副本（原件保留）' if self.session.copy_mode else '編輯路徑'
            self.title_label.setText(title);self.toolbar.setWindowTitle(title)
    def toggle_selected_locks(self):
        self._stop_move()
        locked=bool(self.selection) and all(self.session.locked(i) for i in self.selection)
        self._safe(lambda:self.session.lock_nodes(self.selection,not locked))
    def _begin_move(self,indices,origin,screen):
        self.last_move=None
        movable={i for i in indices if not self.session.locked(i)}
        if self.mode==3 and movable!=set(indices):
            self.selection=set(indices);self._status('線段有鎖定端點，不能整段平移；請先解鎖。');return
        if not movable:
            self.selection=set(indices);self.selected=min(indices);self._status('節點已鎖定；按節點旁「解鎖」後再移動。');self._sync();return
        if self.selected not in movable:
            self.selection=set(indices);self._status('主節點已鎖定，請拖曳其他未鎖定的節點。');return
        self.move_before=copy.deepcopy(self.session.points)
        super()._begin_move(movable,origin,screen)
    def _point(self,ctx):
        raw=np.array(super()._point(ctx));self.guide_lines=[];self.guide_text=''
        if not self.moving or not self.drag_sources or self.axis is not None:return point_key(raw)
        if getattr(getattr(ctx,'snap',None),'kind',None) in ('endpoint','midpoint','intersection'):return point_key(raw)
        try:u,v=self.session.frame_axes()
        except ValueError:return point_key(raw)
        primary=self.drag_sources[self.selected];point=primary+(raw-self.drag_origin)
        origin=np.array(self.session.points[0]);normal=np.cross(u,v)
        if abs((point-origin)@normal)>1e-5:return point_key(raw)
        pixel=self._project(point)
        if pixel is None:return point_key(raw)
        if self.align_snap:
            for axis,direction,label in ((v,u,'與節點同高'),(u,v,'與節點同直線')):
                choices=[]
                for i,fixed in enumerate(self.session.points):
                    if i in self.drag_sources:continue
                    q=point+axis*((np.array(fixed)-point)@axis);p=self._project(q)
                    if p is not None:choices.append((math.dist(pixel,p),q,np.array(fixed)))
                if choices:
                    distance,q,fixed=min(choices,key=lambda a:a[0])
                    if distance<=7:
                        point=q;self.guide_lines.append((fixed,point));self.guide_text=label
        if self.right_snap and len(self.drag_sources)==1:
            i=self.selected;n=len(self.session.points)
            if self.session.closed or 0<i<n-1:
                a=np.array(self.session.points[i-1]);b=np.array(self.session.points[(i+1)%n]);center=(a+b)*.5
                radius=np.linalg.norm(a-b)*.5;vector=point-center;length=np.linalg.norm(vector)
                if radius>1e-8 and length>1e-8:
                    q=center+vector/length*radius;p=self._project(q)
                    va=a-point;vb=b-point;den=np.linalg.norm(va)*np.linalg.norm(vb)
                    if p is not None and den>1e-12 and abs(float(va@vb)/den)<math.sin(math.radians(3)) and math.dist(pixel,p)<=8:
                        point=q;self.guide_text='90° 直角吸附';self.guide_lines=[(a,point),(b,point)]
        return point_key(raw+point-(primary+(raw-self.drag_origin)))
    def on_click(self,ctx):
        if self.frame_preview is None:
            if not self.moving:self.last_move=None
            super().on_click(ctx)
    def on_double_click(self,ctx):
        if self.frame_preview is None:super().on_double_click(ctx)
    def on_hover(self,ctx):
        if self.frame_preview is not None:return
        self.hover_point=ctx.screen
        if not self.moving and self.corner_editor is None:
            self.hover_node=self._node_at(ctx.screen)
            hit=self._segment_at(ctx.screen) if self.hover_node is None else None;self.hover_segment=hit[0] if hit else None
        else:self.hover_node=None;self.hover_segment=None
        super().on_hover(ctx)
    def on_release(self,viewport):
        if self.frame_preview is None:super().on_release(viewport)
    def _stop_move(self):
        self.guide_lines=[];self.guide_text=""
        super()._stop_move()
    def _drop(self):
        if self.drag_sources and self.candidate is not None:
            before=copy.deepcopy(self.session.points);delta=np.array(self.candidate)-self.drag_sources[self.selected]
            indices=set(self.drag_sources)
            super()._drop()
            if np.linalg.norm(delta)>1e-9:
                self.last_move=(before,copy.deepcopy(self.session.points),indices,delta,len(self.session.undo_stack))
        else:super()._drop()
        self.guide_lines=[];self.guide_text=''
    def value_is_unitless(self):
        # The native measurement box shows raw numbers; our caption declares cm.
        # Explicit units are still parsed by the core.
        return True
    def on_value(self,viewport,value):
        if self.corner_editor is not None or self.frame_preview is not None:return False
        try:
            raw=str(getattr(viewport,'_value_buffer','')).strip()
            if raw:
                from core.units import parse
                parsed=parse(raw,unit='cm')
                if not parsed.ok:raise ValueError('距離格式無效，請輸入數值或cm／mm／m。')
                distance=parsed.value
            else:distance=float(value)
            if not math.isfinite(distance) or abs(distance)<1e-9:raise ValueError('請輸入非零移動距離，未標單位預設cm。')
            if self.moving and self.drag_sources:
                delta=np.array(self.candidate)-self.drag_sources[self.selected];norm=np.linalg.norm(delta)
                if norm<1e-9:raise ValueError('請先拖出移動方向，再輸入距離。')
                direction=delta/norm
                self.candidate=point_key(self.drag_sources[self.selected]+direction*distance);self._drop()
                if self.last_move is not None:
                    before,after,indices,_,count=self.last_move;self.last_move=(before,after,indices,direction,count)
            elif self.last_move is not None:
                before,after,indices,delta,undo_count=self.last_move
                if self.session.points!=after or len(self.session.undo_stack)!=undo_count:raise ValueError('請先拖曳節點或線段，再輸入距離。')
                self.session.undo();self.session.move_many(indices,delta/np.linalg.norm(delta)*distance)
                self.last_move=(before,copy.deepcopy(self.session.points),indices,delta,len(self.session.undo_stack))
            else:raise ValueError('請先拖出移動方向，再輸入距離。')
            self._sync();self._status(f'已精確移動 {distance*100.:g} cm；尚未提交到模型。')
        except (ValueError,ArithmeticError,RuntimeError) as exc:self._status(str(exc))
        return True
    def _refresh_analysis(self):
        if self.moving and len(self.session.points)>80:
            self.ghost=[];self.problem=None;self.viewport.update();return
        super()._refresh_analysis()
    def undo_problem(self):
        if self.moving:self._stop_move();self._sync();self._refresh_analysis()
        else:self.undo()
    def _update_dimensions(self):
        super()._update_dimensions();self.dimension_leaders=[]
        points=self._display_points();count=len(points) if self.session.closed else len(points)-1
        node_rects=[]
        for point in points:
            p=self._project(point)
            if p is not None:node_rects.append(QRect(round(p[0])-10,round(p[1])-10,20,20))
        self._update_corner_button();self._update_lock_button()
        occupied=list(node_rects)
        for helper in (self.corner_button,self.lock_button,self.error_button):
            if helper is not None and helper.isVisible():occupied.append(helper.geometry().adjusted(-4,-4,4,4))
        if self.toolbar is not None and self.toolbar.isVisible():
            top=self.viewport.mapFromGlobal(self.toolbar.pos());occupied.append(QRect(top,self.toolbar.size()))
        slots=list(enumerate(self.dimension_buttons))
        slots.sort(key=lambda item:item[1].property('segment')!=self.segment)
        for slot,button in slots:
            index=button.property('segment')
            if index is None or index>=count:button.hide();continue
            if count>32 and index!=self.segment:button.hide();continue
            p=self._project((np.array(points[index])+points[(index+1)%len(points)])*.5)
            if p is None:button.hide();continue
            w=button.width();h=button.height();placed=False
            for dx,dy in ((-w/2,-h/2),(-w/2,-h-12),(-w/2,12),(-w-16,-h/2),(16,-h/2),(-w/2,-h-38),(-w/2,38)):
                x=round(p[0]+dx);y=round(p[1]+dy);rect=QRect(x,y,w,h)
                if not self.viewport.rect().contains(rect) or any(rect.adjusted(-3,-3,3,3).intersects(other) for other in occupied):continue
                button.move(x,y);button.show();occupied.append(rect);placed=True
                if abs(dx+w/2)>2 or abs(dy+h/2)>2:self.dimension_leaders.append((p,(x+w*.5,y+h*.5)))
                break
            if not placed:button.hide()
    def _ask_exit_choice(self):
        box=QMessageBox(self.toolbar or self.panel);box.setWindowTitle('尚有未完成的路徑修改')
        box.setText('要完成重建、放棄修改，還是繼續編輯？')
        finish=box.addButton('完成並退出',QMessageBox.AcceptRole)
        discard=box.addButton('放棄修改',QMessageBox.DestructiveRole)
        stay=box.addButton('繼續編輯',QMessageBox.RejectRole);box.setDefaultButton(stay);box.exec()
        return 'finish' if box.clickedButton() is finish else ('discard' if box.clickedButton() is discard else 'stay')
    def cancel(self):
        if self.done:return
        pending=bool(self.moving and self.drag_sources and self.candidate is not None and np.linalg.norm(np.array(self.candidate)-self.drag_sources[self.selected])>1e-9)
        if not self._discarding and (self.session.dirty() or pending):
            choice=self.exit_decision()
            if choice=='finish':
                self.finish()
                if not self.done and self.toolbar is not None:self.toolbar.show()
                return
            if choice!='discard':
                if self.toolbar is not None:self.toolbar.show()
                self._focus_viewport();return
        self.discard()
    def discard(self):
        self._discarding=True;self._dismiss_frame();super().cancel()
    def finish(self):self._dismiss_frame();super().finish()
    def apply_continue(self):self._dismiss_frame();super().apply_continue()
    def undo(self):self._dismiss_frame();super().undo()
    def redo(self):self._dismiss_frame();super().redo()
    def on_key(self,viewport,key,modifiers):
        if self.frame_preview is not None:
            if key==int(Qt.Key_Escape):self._dismiss_frame()
            return True
        if key in (int(Qt.Key_Delete),int(Qt.Key_Backspace)) and self.selection:
            unlocked={i for i in self.selection if not self.session.locked(i)}
            if not unlocked:self._status('鎖定節點不可刪除。');return True
            if len(self.session.points)-len(unlocked)<(3 if self.session.closed else 2):self._status('刪除後節點數不足。');return True
            self._stop_move();self.session._record();self.session.points=[p for i,p in enumerate(self.session.points) if i not in unlocked]
            self.selection.clear();self.selected=None;self.segment=None;self._sync();return True
        return super().on_key(viewport,key,modifiers)
    def draw_overlay(self,viewport,painter):
        selected,selection=self.selected,self.selection
        if self.frame_preview is not None:self.selected=None;self.selection=set()
        try:super().draw_overlay(viewport,painter)
        finally:self.selected=selected;self.selection=selection
        painter.save()
        try:
            points=self._display_points()
            if self.frame_preview is not None and self.frame_preview.points is not None and self.frame_preview.fixed_point is not None:
                fixed=self._project(self.frame_preview.fixed_point)
                if fixed is not None:
                    painter.setPen(QPen(QColor('white'),1.5));painter.setBrush(QColor('#ed8c15'));painter.drawEllipse(QPointF(*fixed),7,7)
                    painter.setPen(QColor('#965200'));painter.drawText(QPointF(*fixed)+QPointF(13,19),'固定點')
            if not self.moving and self.corner_editor is None and self.frame_preview is None:
                if self.hover_segment is not None and self.hover_segment<(len(points) if self.session.closed else len(points)-1):
                    a,b=self._project(points[self.hover_segment]),self._project(points[(self.hover_segment+1)%len(points)])
                    if a is not None and b is not None:painter.setPen(QPen(QColor('#ffad32'),5));painter.drawLine(QPointF(*a),QPointF(*b))
                if self.hover_node is not None and self.hover_node<len(points):
                    p=self._project(points[self.hover_node])
                    if p is not None:painter.setPen(QPen(QColor('#ffad32'),3));painter.setBrush(Qt.NoBrush);painter.drawEllipse(QPointF(*p),9,9)
            painter.setPen(QPen(QColor('#999999'),1))
            for a,b in self.dimension_leaders:painter.drawLine(QPointF(*a),QPointF(*b))
            if self.moving and self.move_before is not None:
                painter.setPen(QPen(QColor('#888888'),2,Qt.DashLine))
                for i in range(len(self.move_before) if self.session.closed else len(self.move_before)-1):
                    a,b=self._project(self.move_before[i]),self._project(self.move_before[(i+1)%len(self.move_before)])
                    if a is not None and b is not None:painter.drawLine(QPointF(*a),QPointF(*b))
                if self.drag_sources:
                    distance=np.linalg.norm(np.array(self.candidate)-self.drag_sources[self.selected])*100.
                    p=QPointF(*(self.hover_screen or self.press_screen))
                    text=f'移動 {distance:.3f} cm｜輸入數值可精確定位'
                    x=max(8,min(p.x()+18,self.viewport.width()-painter.fontMetrics().horizontalAdvance(text)-8))
                    y=max(20,min(p.y()-25,self.viewport.height()-24))
                    painter.setPen(QColor('#165b78'));painter.drawText(QPointF(x,y),text)
                    if len(points)>80:painter.drawText(p+QPointF(18,20),'簡化路徑預覽，放開後完整計算')
            painter.setPen(QPen(QColor('#168349' if '90°' in self.guide_text else '#c32d9b'),1.5,Qt.DashLine))
            for a,b in self.guide_lines:
                a,b=self._project(a),self._project(b)
                if a is not None and b is not None:painter.drawLine(QPointF(*a),QPointF(*b))
            if self.guide_text and self.hover_point is not None:
                painter.drawText(self.hover_point+QPointF(18,36),self.guide_text)
                if '90°' in self.guide_text and self.moving and self.selected is not None:
                    i=self.selected;c=self._project(points[i]);a=self._project(points[i-1]);b=self._project(points[(i+1)%len(points)])
                    if all(p is not None for p in (a,b,c)):
                        av=np.array(a)-c;bv=np.array(b)-c
                        if min(np.linalg.norm(av),np.linalg.norm(bv))>1e-5:
                            av=av/np.linalg.norm(av)*11;bv=bv/np.linalg.norm(bv)*11;c=np.array(c)
                            painter.setPen(QPen(QColor('#168349'),2))
                            painter.drawLine(QPointF(*(c+av)),QPointF(*(c+av+bv)));painter.drawLine(QPointF(*(c+av+bv)),QPointF(*(c+bv)))
            for i,p in enumerate(points):
                if point_key(p) in self.session.locks:
                    pixel=self._project(p)
                    if pixel is not None:
                        painter.setPen(QColor('#663399'));painter.drawText(QPointF(*pixel)+QPointF(10,-16),'鎖')
            self._draw_problem(painter,points)
        finally:painter.restore()
        self._update_lock_button()
    def _draw_problem(self,painter,points):
        if self.error_button is None:return
        if self.problem is None or self.corner_editor is not None or self.frame_preview is not None:self.error_button.hide();return
        indices=sorted(i for i in self.problem.nodes if i<len(points))
        pixel=self._project(points[indices[0]]) if indices else None
        if pixel is None:self.error_button.hide();return
        x=max(5,min(round(pixel[0]+16),self.viewport.width()-230));y=max(25,min(round(pixel[1]+40),self.viewport.height()-65))
        painter.setPen(QColor('#ba2020'));painter.drawText(QPointF(x,y),str(self.problem)[:22])
        self.error_button.adjustSize();self.error_button.move(x,y+8);self.error_button.setEnabled(self.moving or bool(self.session.undo_stack));self.error_button.show()
    def _update_corner_button(self):
        if self.frame_preview is not None:
            if self.corner_button is not None:self.corner_button.hide()
            return
        super()._update_corner_button()
    def _update_lock_button(self):
        if self.lock_button is None:return
        if self.done or not self.selection or self.moving or self.corner_editor is not None or self.frame_preview is not None:self.lock_button.hide();return
        i=self.selected
        if i is None or i>=len(self.session.points):self.lock_button.hide();return
        p=self._project(self.session.points[i])
        if p is None:self.lock_button.hide();return
        locked=all(self.session.locked(i) for i in self.selection)
        self.lock_button.setText('解除鎖定' if locked else '鎖定選點');self.lock_button.adjustSize()
        x=max(0,min(round(p[0]+14),self.viewport.width()-self.lock_button.width()))
        y=max(0,min(round(p[1]+60),self.viewport.height()-self.lock_button.height()))
        self.lock_button.move(x,y);self.lock_button.show();self.lock_button.raise_()
    def on_deactivate(self,viewport):
        if self.panel_exit_guard is not None:
            self.panel.removeEventFilter(self.panel_exit_guard);self.panel_exit_guard.deleteLater();self.panel_exit_guard=None
        self._dismiss_frame()
        for button in (self.lock_button,self.error_button):
            if button is not None:button.hide();button.deleteLater()
        self.lock_button=None;self.error_button=None
        super().on_deactivate(viewport)
