# SPDX-License-Identifier: GPL-3.0-or-later
"""Path editing aids. All coordinates are world metres; new UI defaults to cm."""
import copy
import math
import numpy as np
from PySide6.QtCore import Qt,QPointF,QRectF,QTimer
from PySide6.QtGui import QColor,QPen,QPolygonF,QVector3D
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,
    QPushButton,QCheckBox,QComboBox,QDoubleSpinBox,QSpinBox,QTabWidget,QScrollArea)
from .path_editor import PathEditSession,ProfilePathEditTool,_crossing_segments


class PathValidationError(ValueError):
    def __init__(self,message,nodes=(),segments=()):
        super().__init__(message);self.nodes=set(nodes);self.segments=set(segments)


class EnhancedPathSession(PathEditSession):
    def replace_points(self,replacements):
        clean={int(i):tuple(float(v) for v in point) for i,point in replacements.items()}
        if any(i<0 or i>=len(self.points) or len(point)!=3 or not all(math.isfinite(v) for v in point) for i,point in clean.items()):
            raise ValueError('選取節點或位移數值無效。')
        if not any(math.dist(self.points[i],point)>1e-9 for i,point in clean.items()):return
        self._record()
        for i,point in clean.items():self.points[i]=point

    def move_many(self,indices,delta):
        if not indices:raise ValueError('請先選取節點或線段。')
        vector=np.asarray(delta,dtype=float)
        if vector.shape!=(3,) or not np.isfinite(vector).all():raise ValueError('位移必須是有限的XYZ距離。')
        self.replace_points({i:np.asarray(self.points[i])+vector for i in indices})

    def segment_indices(self,index):
        count=len(self.points) if self.closed else len(self.points)-1
        if not 0<=index<count:raise ValueError('請先選取有效線段。')
        return index,(index+1)%len(self.points)

    def segment_length(self,index):
        a,b=self.segment_indices(index);return math.dist(self.points[a],self.points[b])

    def set_segment_length(self,index,length,fixed='start'):
        a,b=self.segment_indices(index);start=np.asarray(self.points[a]);end=np.asarray(self.points[b])
        old=np.linalg.norm(end-start)
        if not math.isfinite(length) or length<=1e-8 or old<1e-8:raise ValueError('線段長度必須大於零，原線段也不能是零長度。')
        vector=(end-start)/old*length
        if fixed=='start':changes={b:start+vector}
        elif fixed=='end':changes={a:end-vector}
        elif fixed=='center':
            center=(start+end)*.5;changes={a:center-vector*.5,b:center+vector*.5}
        else:raise ValueError('固定端選項無效。')
        self.replace_points(changes)

    def insert_at(self,index,value,kind='distance'):
        a,b=self.segment_indices(index);length=self.segment_length(index)
        if not math.isfinite(value) or length<1e-8:raise ValueError('插點數值或原線段無效。')
        if kind=='ratio':t=value
        elif kind=='distance':t=value/length
        elif kind=='end':t=1.-value/length
        else:raise ValueError('插點方式無效。')
        if not 0<t<1:raise ValueError('插點必須在線段兩端之間；比例須大於0且小於100%。')
        return self.insert(index,np.asarray(self.points[a])*(1-t)+np.asarray(self.points[b])*t)

    def plane_axes(self):
        n=self.world_normal
        if n is None:return np.array((1.,0.,0.)),np.array((0.,1.,0.))
        up=np.array((0.,0.,1.));up=up-n*np.dot(up,n)
        if np.linalg.norm(up)>.2:
            v=up/np.linalg.norm(up);u=np.cross(v,n)
            leading=0 if abs(u[0])>=abs(u[1]) else 1
            if u[leading]<0:u=-u
        else:
            u=np.array((1.,0.,0.));u-=n*np.dot(u,n);u/=np.linalg.norm(u);v=np.cross(n,u)
            if v[1]<0:v=-v
        return u,v

    def align(self,indices,primary,horizontal=True):
        if len(indices)<2 or primary not in indices:raise ValueError('請選至少兩點，以最後選取的節點為對齊基準。')
        u,v=self.plane_axes();axis=v if horizontal else u
        target=np.dot(self.points[primary],axis)
        self.replace_points({i:np.asarray(self.points[i])+axis*(target-np.dot(self.points[i],axis)) for i in indices})

    def reverse_route(self):
        self._record()
        self.points=([self.points[0]]+self.points[:0:-1]) if self.closed else self.points[::-1]

    def angle_at(self,index):
        if index is None or (not self.closed and index in (0,len(self.points)-1)):return None
        a=np.asarray(self.points[index-1])-self.points[index]
        b=np.asarray(self.points[(index+1)%len(self.points)])-self.points[index]
        denominator=np.linalg.norm(a)*np.linalg.norm(b)
        if denominator<1e-12:return None
        return math.degrees(math.acos(float(np.clip(np.dot(a,b)/denominator,-1.,1.))))

    def diagnostics(self):
        try:points=self.to_local(self.points)
        except (ValueError,TypeError):return PathValidationError('路徑座標無效。',range(len(self.points)))
        for i,point in enumerate(points):
            if not all(math.isfinite(c) for c in point):return PathValidationError('節點座標無效。',[i])
            for j,other in enumerate(points[i+1:],i+1):
                if math.dist(point,other)<1e-8:return PathValidationError('路徑有重複節點。',[i,j])
        if self.local_normal is not None:
            n=self.local_normal/np.linalg.norm(self.local_normal);origin=np.asarray(self.recipe['path'][0])
            wrong=[i for i,p in enumerate(points) if abs(np.dot(np.asarray(p)-origin,n))>1e-5]
            if wrong:return PathValidationError('節點離開原踢腳高度或固定牆面。',wrong)
        pair=_crossing_segments(points,self.closed,details=True)
        if pair:
            nodes={pair[0],(pair[0]+1)%len(points),pair[1],(pair[1]+1)%len(points)}
            return PathValidationError('紅色線段有自交或重疊。',nodes,pair)
        for i in range(len(points)):
            if not self.closed and i in (0,len(points)-1):continue
            before=np.asarray(points[i])-points[i-1];after=np.asarray(points[(i+1)%len(points)])-points[i]
            dot=float(np.dot(before,after)/max(np.linalg.norm(before)*np.linalg.norm(after),1e-20))
            if dot<-.97 or (self.recipe['settings'].get('miter',True) and dot<(-7./9.)):
                return PathValidationError('紅色節點轉角過尖，可能無法安全斜接。',[i],[(i-1)%len(points),i])
        return None

    def validate(self):
        problem=self.diagnostics()
        if problem is not None:raise problem
        return super().validate()


class EnhancedPathEditTool(ProfilePathEditTool):
    def __init__(self,panel,session,show_dialog=True):
        super().__init__(panel,session,show_dialog)
        self.selection=set();self.segment=None;self.drag_sources={};self.drag_origin=None
        self.box_start=None;self.box_end=None;self.box_add=False
        self.snap_text='吸附：尚未取得';self.snap_pixel=None
        self.problem=None;self.ghost=[];self.unit_factor=.01;self.unit_name='cm'
        self.analysis_timer=QTimer(panel);self.analysis_timer.setSingleShot(True)
        self.analysis_timer.timeout.connect(self._refresh_analysis)
        self.preview_enabled=True

    def _open_dialog(self):
        super()._open_dialog()
        dialog=self.dialog;dialog.setWindowTitle('進階編輯路徑｜預覽・量測・多選')
        root=dialog.layout();body=QWidget();layout=QVBoxLayout(body)
        while root.count():
            item=root.takeAt(0)
            if item.widget() is not None:
                widget=item.widget();widget.setParent(body);layout.addWidget(widget);widget.show()
            elif item.layout() is not None:
                sub=item.layout();sub.setParent(None);layout.addLayout(sub)
            else:layout.addItem(item)
        self.scroll_body=body
        tabs=QTabWidget();move_page=QWidget();move_layout=QFormLayout(move_page)
        self.mode_combo.addItems(['移動整段','框選節點'])
        label=body.findChildren(QLabel)[0]
        label.setText('點節點移動；Shift點選可多選，或使用框選模式。\n整段模式點線段後移動；半透明為待重建線板。\n原路徑為灰虛線，紅色標記表示待修正位置。')
        self.preview_box=QCheckBox('顯示半透明重建預覽');self.preview_box.setChecked(True)
        self.preview_box.toggled.connect(self._toggle_preview);layout.insertWidget(2,self.preview_box)
        self.snap_label=QLabel(self.snap_text);layout.insertWidget(3,self.snap_label)
        self.measure_label=QLabel();self.measure_label.setWordWrap(True);layout.insertWidget(4,self.measure_label)
        self.units=QComboBox();self.units.addItems(['cm','mm','m']);self.units.currentTextChanged.connect(self._unit_changed)
        move_layout.addRow('新增距離欄位單位',self.units)
        self.deltas=[self._spin(0) for _ in range(3)]
        for axis,control in zip('XYZ',self.deltas):control.setSuffix(' cm');move_layout.addRow('相對 Δ'+axis,control)
        move_layout.addRow(self._button('移動選取節點',self._relative_move))
        row=QHBoxLayout();row.addWidget(self._button('水平對齊',lambda:self._align(True)));row.addWidget(self._button('垂直對齊',lambda:self._align(False)))
        move_layout.addRow(row)
        explanation=QLabel('對齊使用原牆面／踢腳平面；空間路徑使用XY。\n原XYZ絕對座標欄位仍為mm，僅編輯主節點。');explanation.setWordWrap(True);move_layout.addRow(explanation)
        move_layout.addRow(self._button('視角置中到主節點',self.focus_node))
        tabs.addTab(move_page,'選取／移動')
        measure_page=QWidget();measure_layout=QFormLayout(measure_page)
        self.segment_spin=QSpinBox();self.segment_spin.setMinimum(1);self.segment_spin.valueChanged.connect(self._choose_segment)
        measure_layout.addRow('線段編號',self.segment_spin)
        self.target_length=self._spin(10);self.target_length.setSuffix(' cm');measure_layout.addRow('目標長度',self.target_length)
        self.fixed_end=QComboBox();self.fixed_end.addItems(['固定起點','固定終點','固定中心']);measure_layout.addRow('長度調整',self.fixed_end)
        measure_layout.addRow(self._button('修改線段長度',self._set_length))
        self.insert_kind=QComboBox();self.insert_kind.addItems(['距起點','距終點','比例（%）']);self.insert_kind.currentIndexChanged.connect(self._insert_kind_changed)
        measure_layout.addRow('指定位置插點',self.insert_kind)
        self.insert_value=self._spin(5);self.insert_value.setSuffix(' cm');measure_layout.addRow('距離／比例',self.insert_value)
        measure_layout.addRow(self._button('插入節點',self._insert_at))
        measure_layout.addRow(self._button('反轉路徑（先預覽）',self._reverse))
        tabs.addTab(measure_page,'尺寸／插點');layout.insertWidget(5,tabs)
        layout.insertWidget(layout.count()-1,self._button('套用並繼續編輯',self.apply_continue))
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(body)
        self.scroll_area=scroll
        root.setContentsMargins(4,4,4,4);root.addWidget(scroll)
        available=dialog.screen().availableGeometry().height() if dialog.screen() else 900
        dialog.setMinimumHeight(350);dialog.resize(460,min(780,max(350,available-80)))
        self.segment=0
        self._sync();self._focus_viewport()

    def _spin(self,value):
        spin=QDoubleSpinBox();spin.setDecimals(4);spin.setRange(-1e8,1e8);spin.setValue(value);return spin

    def _button(self,text,callback):
        button=QPushButton(text);button.setAutoDefault(False);button.clicked.connect(callback);return button

    def _safe(self,action):
        try:action();self._sync();self._focus_viewport()
        except (ValueError,IndexError,ArithmeticError) as exc:self._status(str(exc))

    def _toggle_preview(self,checked):
        self.preview_enabled=checked;self._schedule_analysis()

    def _unit_changed(self,name):
        factor={'cm':.01,'mm':.001,'m':1.}[name];ratio=self.unit_factor/factor
        controls=self.deltas+[self.target_length]
        if self.insert_kind.currentIndex()!=2:controls.append(self.insert_value)
        for control in controls:
            control.setValue(control.value()*ratio);control.setSuffix(' '+name)
        self.unit_factor=factor;self.unit_name=name;self._sync()

    def _insert_kind_changed(self,value):
        self.insert_value.setSuffix(' %' if value==2 else ' '+self.unit_name)

    def _choose_segment(self,value):
        self.segment=value-1;self._sync()

    def _relative_move(self):
        self._stop_move()
        self._safe(lambda:self.session.move_many(self.selection,[s.value()*self.unit_factor for s in self.deltas]))

    def _align(self,horizontal):
        self._stop_move();self._safe(lambda:self.session.align(self.selection,self.selected,horizontal))

    def _set_length(self):
        self._stop_move()
        self._safe(lambda:self.session.set_segment_length(self._segment(),self.target_length.value()*self.unit_factor,('start','end','center')[self.fixed_end.currentIndex()]))

    def _insert_at(self):
        def insert():
            kind=('distance','end','ratio')[self.insert_kind.currentIndex()]
            value=self.insert_value.value()*(.01 if kind=='ratio' else self.unit_factor)
            self.selected=self.session.insert_at(self._segment(),value,kind);self.selection={self.selected}
        self._stop_move();self._safe(insert)

    def _reverse(self):
        self._stop_move();self.session.reverse_route();self.selection.clear();self.selected=None;self.segment=None
        self._sync();self._status('路徑已反轉，請先查看半透明預覽；尚未修改原線板。')

    def _segment(self):
        if self.segment is None:raise ValueError('請先點選線段，或指定線段編號。')
        return self.segment

    def focus_node(self):
        if self.selected is None:self._status('請先選取一個主節點。');return
        camera=getattr(self.viewport,'camera',None)
        if camera is None or not hasattr(camera,'target'):self._status('目前視窗沒有可用的視角定位介面。');return
        camera.target=QVector3D(*self.session.points[self.selected]);self.viewport.update();self._focus_viewport()

    def _sync(self):
        self.selection={i for i in self.selection if i<len(self.session.points)}
        if self.selected is not None and self.selected<len(self.session.points) and self.selected not in self.selection:
            self.selection={self.selected}
        super()._sync()
        if self.dialog is not None and hasattr(self,'measure_label'):
            count=len(self.session.points) if self.session.closed else len(self.session.points)-1
            if self.segment is not None and self.segment>=count:self.segment=None
            self.segment_spin.blockSignals(True);self.segment_spin.setMaximum(count)
            if self.segment is not None:self.segment_spin.setValue(self.segment+1)
            self.segment_spin.blockSignals(False)
            info=f'已選 {len(self.selection)} 點'
            if self.segment is not None:info+=f'｜線段{self.segment+1}：{self.session.segment_length(self.segment)/self.unit_factor:.4f} {self.unit_name}'
            angle=self.session.angle_at(self.selected)
            if angle is not None:info+=f'｜主節點內夾角：{angle:.2f}°'
            self.measure_label.setText(info);self.snap_label.setText(self.snap_text)
        self._schedule_analysis()

    def _schedule_analysis(self):
        if not self.done and not self.analysis_timer.isActive():self.analysis_timer.start(90)

    def _refresh_analysis(self):
        if self.done:return
        temp=copy.copy(self.session);temp.points=self._display_points()
        self.problem=temp.diagnostics();self.ghost=[]
        if self.problem is None and self.preview_enabled:
            try:
                group=temp.replacement();matrix=group.xform
                for face in group.mesh.faces:
                    points=[matrix.map(v.position) if matrix is not None else v.position for v in face.loop]
                    self.ghost.append([v.toTuple() for v in points])
            except (ValueError,ArithmeticError,RuntimeError) as exc:
                self.problem=PathValidationError(str(exc),range(len(temp.points)),range(len(temp.points)))
        if self.problem is not None:self._status(str(self.problem))
        self.viewport.update()

    def _stop_move(self):
        super()._stop_move();self.drag_sources={};self.drag_origin=None
        self.box_start=None;self.box_end=None

    def _begin_move(self,indices,origin,screen):
        self.selection=set(indices);self.drag_sources={i:np.array(self.session.points[i]) for i in indices}
        self.drag_origin=np.asarray(origin,dtype=float);self.moving=True
        self.candidate=self.session.points[self.selected];self.press_screen=(screen.x(),screen.y());self.hover_screen=self.press_screen
        self._sync()

    def _point(self,ctx):
        point=np.array(ctx.world.toTuple(),dtype=float)
        if self.session.world_normal is not None:
            n=self.session.world_normal;origin=np.asarray(self.session.original[0][0]);point-=n*np.dot(point-origin,n)
        if self.axis is not None:
            base=np.array(self.drag_origin if self.drag_origin is not None else self.session.points[self.selected or 0])
            base['xyz'.index(self.axis)]=point['xyz'.index(self.axis)];point=base
        return tuple(point)

    def _snap(self,ctx):
        snap=getattr(ctx,'snap',None);kind=getattr(snap,'kind',None)
        names={'endpoint':'端點','midpoint':'中點','on_edge':'邊線','on_face':'面','axis':'軸向','axis_inference':'軸向推論','intersection':'交點','center':'中心','vertex':'頂點','none':'無','reference':'參考邊','close':'閉合點','origin':'原點','aligned':'對齊','intersection_edge':'邊線交點','intersection_face':'面交點'}
        self.snap_text='吸附：'+names.get(kind,str(kind) if kind else '無')
        if self.axis:self.snap_text+='｜'+self.axis.upper()+'軸鎖定'
        if self.session.world_normal is not None and math.dist(ctx.world.toTuple(),self._point(ctx))>1e-6:self.snap_text+='（原平面／軸向投影）'
        self.snap_pixel=self._project(self._point(ctx))
        if self.dialog is not None and hasattr(self,'snap_label'):self.snap_label.setText(self.snap_text)

    def on_click(self,ctx):
        if self.done:return
        self._snap(ctx)
        if self.moving:
            delta=np.asarray(self._point(ctx))-self.drag_origin
            self.candidate=tuple(self.drag_sources[self.selected]+delta)
            self._drop();return
        index=self._node_at(ctx.screen)
        shift=bool(getattr(ctx,'modifiers',Qt.NoModifier)&Qt.ShiftModifier)
        if self.mode==4:
            if self.box_start is not None:self.box_end=QPointF(ctx.screen);self._finish_box();return
            self.box_start=QPointF(ctx.screen);self.box_end=QPointF(ctx.screen);self.box_add=shift;return
        if self.mode==3:
            segment=self._segment_at(ctx.screen)
            if segment is None:self._status('請點選線段中間，再指定移動位置。');return
            self.segment=segment[0];a,b=self.session.segment_indices(self.segment);self.selected=a
            self._begin_move({a,b},self._point(ctx),ctx.screen);return
        if self.mode==0:
            if shift and index is not None:
                if index in self.selection:self.selection.remove(index)
                else:self.selection.add(index)
                self.selected=index if index in self.selection else next(iter(sorted(self.selection)),None)
                self._sync();return
            if index is not None:
                self.selected=index
                self.segment=min(index,len(self.session.points)-2) if not self.session.closed else index
                indices=self.selection if index in self.selection else {index}
                self._begin_move(indices,self.session.points[index],ctx.screen);return
            segment=self._segment_at(ctx.screen)
            if segment is not None:
                self.segment=segment[0];a,b=self.session.segment_indices(self.segment);self.selection={a,b};self.selected=a
            else:self.selection.clear();self.selected=None;self.segment=None
            self._sync();return
        super().on_click(ctx)
        if self.mode==1 and self.moving:
            self._begin_move({self.selected},self.session.points[self.selected],ctx.screen)
        elif self.mode==2:self.selection.clear();self.segment=None;self._sync()

    def on_hover(self,ctx):
        self._snap(ctx)
        if self.box_start is not None:self.box_end=QPointF(ctx.screen);self.viewport.update();return
        if self.moving:
            delta=np.asarray(self._point(ctx))-self.drag_origin
            self.candidate=tuple(self.drag_sources[self.selected]+delta)
            self.hover_screen=(ctx.screen.x(),ctx.screen.y());self._sync()
        else:self.viewport.update()

    def _drop(self):
        if self.drag_sources:
            delta=np.asarray(self.candidate)-self.drag_sources[self.selected]
            self.session.replace_points({i:p+delta for i,p in self.drag_sources.items()})
        self._stop_move();self._sync()

    def on_release(self,viewport):
        if self.box_start is not None and self.box_end is not None and (self.box_start-self.box_end).manhattanLength()>4:self._finish_box();return
        if self.moving and self.press_screen and self.hover_screen and math.dist(self.press_screen,self.hover_screen)>4:self._drop()

    def _finish_box(self):
        rect=QRectF(self.box_start,self.box_end).normalized();chosen=set()
        for i,point in enumerate(self.session.points):
            pixel=self._project(point)
            if pixel is not None and rect.contains(QPointF(*pixel)):chosen.add(i)
        self.selection=(self.selection|chosen) if self.box_add else chosen
        self.selected=max(self.selection) if self.selection else None;self.box_start=None;self.box_end=None;self._sync()

    def _display_points(self):
        points=list(self.session.points)
        if self.moving and self.drag_sources and self.candidate is not None:
            delta=np.asarray(self.candidate)-self.drag_sources[self.selected]
            for i,p in self.drag_sources.items():points[i]=tuple(p+delta)
        elif self.moving and self.selected is not None and self.candidate is not None:points[self.selected]=self.candidate
        return points

    def draw_overlay(self,viewport,painter):
        painter.save()
        try:
            if self.preview_enabled:
                painter.setPen(Qt.NoPen);painter.setBrush(QColor(30,157,218,35))
                for face in self.ghost:
                    pixels=[self._project(point) for point in face]
                    if all(pixel is not None for pixel in pixels):painter.drawPolygon(QPolygonF([QPointF(*pixel) for pixel in pixels]))
            original,closed=self.session.original;painter.setPen(QPen(QColor('#888888'),1.5,Qt.DashLine))
            for i in range(len(original) if closed else len(original)-1):
                a,b=self._project(original[i]),self._project(original[(i+1)%len(original)])
                if a is not None and b is not None:painter.drawLine(QPointF(*a),QPointF(*b))
            if self.box_start is not None:
                painter.setBrush(QColor(30,120,220,20));painter.setPen(QPen(QColor('#187ecb'),1,Qt.DashLine))
                painter.drawRect(QRectF(self.box_start,self.box_end).normalized())
        finally:painter.restore()
        super().draw_overlay(viewport,painter)
        painter.save()
        try:
            points=self._display_points();bad_nodes=self.problem.nodes if self.problem else set();bad_segments=self.problem.segments if self.problem else set()
            for i in bad_segments:
                if i>=len(points) or (not self.session.closed and i==len(points)-1):continue
                a,b=self._project(points[i]),self._project(points[(i+1)%len(points)])
                if a is not None and b is not None:
                    painter.setPen(QPen(QColor('#e02d35'),4));painter.drawLine(QPointF(*a),QPointF(*b))
            for i in self.selection|bad_nodes:
                if i>=len(points):continue
                pixel=self._project(points[i])
                if pixel is not None:
                    painter.setPen(QPen(QColor('white'),1.5));painter.setBrush(QColor('#e02d35' if i in bad_nodes else '#f09920'));painter.drawEllipse(QPointF(*pixel),6,6)
            if self.segment is not None and self.segment<(len(points) if self.session.closed else len(points)-1):
                a,b=self.session.segment_indices(self.segment);middle=(np.array(points[a])+points[b])*.5;pixel=self._project(middle)
                if pixel is not None:
                    length=math.dist(points[a],points[b])/self.unit_factor
                    painter.setPen(QColor('#165b78'));painter.drawText(QPointF(*pixel)+QPointF(8,16),f'{length:.3f} {self.unit_name}')
            if self.selected is not None:
                pixel=self._project(points[self.selected]);temp=copy.copy(self.session);temp.points=points;angle=temp.angle_at(self.selected)
                if pixel is not None and angle is not None:
                    painter.setPen(QColor('#815b16'));painter.drawText(QPointF(*pixel)+QPointF(10,20),f'{angle:.1f}°')
        finally:painter.restore()

    def undo(self):
        self.selection.clear();self.segment=None;super().undo()

    def redo(self):
        self.selection.clear();self.segment=None;super().redo()

    def on_key(self,viewport,key,modifiers):
        if key in (int(Qt.Key_Delete),int(Qt.Key_Backspace)) and len(self.selection)>1:
            if len(self.session.points)-len(self.selection)<(3 if self.session.closed else 2):self._status('刪除後節點數不足。');return True
            self._stop_move();self.session._record();self.session.points=[p for i,p in enumerate(self.session.points) if i not in self.selection]
            self.selection.clear();self.selected=None;self.segment=None;self._sync();return True
        return super().on_key(viewport,key,modifiers)

    def apply_continue(self):
        if self.done:return
        try:
            if self.moving:self._drop()
            result=self.session.commit(self.viewport)
            self.session=type(self.session)(self.viewport.scene,result)
            self.selected=None;self.selection.clear();self.segment=None;self.problem=None;self.ghost=[]
            self._sync();self._status('已套用；可繼續編輯。每次套用各自可復原；取消只放棄本次尚未套用內容。')
        except (ValueError,RuntimeError,ArithmeticError) as exc:
            self.error=str(exc);self._refresh_analysis();self._status(self.error)

    def finish(self):
        if self.moving:self._drop()
        self._refresh_analysis();super().finish()

    def on_deactivate(self,viewport):
        if self.analysis_timer is not None:
            self.analysis_timer.stop();self.analysis_timer.deleteLater();self.analysis_timer=None
        self.ghost=[];super().on_deactivate(viewport)
