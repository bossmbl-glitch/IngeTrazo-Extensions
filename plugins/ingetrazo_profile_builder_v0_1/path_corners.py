# SPDX-License-Identifier: GPL-3.0-or-later
"""World-space chamfers / sampled tangent fillets and a transactional preview."""
import copy
import math
import numpy as np
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QComboBox,
    QDoubleSpinBox,QSpinBox,QLabel,QPushButton,QCheckBox)


def corner_geometry(points,index,closed,kind,amount,segments=8):
    """Return a fresh path and the replacement range; never mutate the input.

    Chamfer amount is the equal setback along both sides. Fillet amount is
    the WORLD radius. Sampling follows traversal, also at the closed seam.
    """
    source=np.asarray(points,dtype=float)
    if source.ndim!=2 or source.shape[1]!=3 or not np.isfinite(source).all():
        raise ValueError('路徑座標無效。')
    count=len(source)
    if count<3 or not isinstance(index,(int,np.integer)) or not 0<=index<count:
        raise ValueError('請選取一個有效的路徑轉角。')
    if not closed and index in (0,count-1):raise ValueError('開放路徑的端點沒有兩側，不能倒角或圓角。')
    if kind not in ('chamfer','fillet'):raise ValueError('轉角處理類型無效。')
    if not math.isfinite(amount) or amount<=1e-8:raise ValueError('倒角距離／圓角半徑必須大於零。')
    c=source[index];a=source[(index-1)%count]-c;b=source[(index+1)%count]-c
    la=float(np.linalg.norm(a));lb=float(np.linalg.norm(b))
    if min(la,lb)<=1e-8:raise ValueError('轉角相鄰線段不能是零長度。')
    u=a/la;v=b/lb;theta=math.acos(float(np.clip(np.dot(u,v),-1.,1.)))
    if theta<1e-5 or math.pi-theta<1e-5:raise ValueError('兩側接近重疊或一直線，沒有可處理的轉角。')
    setback=amount if kind=='chamfer' else amount/math.tan(theta*.5)
    if setback>=min(la,lb)-1e-8:
        limit=min(la,lb)*(1. if kind=='chamfer' else math.tan(theta*.5))*100.
        raise ValueError(f'尺寸超過相鄰線段；此轉角須小於 {limit:.4f} cm，不能吃掉鄰點。')
    start=c+u*setback;end=c+v*setback
    if kind=='chamfer':replacement=[start,end]
    else:
        if not isinstance(segments,(int,np.integer)) or not 2<=segments<=64:
            raise ValueError('圓角平滑度須為2至64段。')
        center=c+(u+v)/np.linalg.norm(u+v)*(amount/math.sin(theta*.5))
        radial=(start-center)/amount;last=(end-center)/amount;angle=math.pi-theta
        perpendicular=(last-radial*math.cos(angle))/math.sin(angle)
        replacement=[center+amount*(radial*math.cos(t*angle)+perpendicular*math.sin(t*angle))
                     for t in np.linspace(0.,1.,segments+1)]
        replacement[0]=start;replacement[-1]=end
    if count-1+len(replacement)>2048:raise ValueError('處理後超過路徑2048點上限，請降低圓角段數。')
    result=[tuple(float(x) for x in row) for row in source[:index]]
    result.extend(tuple(float(x) for x in row) for row in replacement)
    result.extend(tuple(float(x) for x in row) for row in source[index+1:])
    return result,range(index,index+len(replacement))


class CornerEditor:
    """The dialog's preview owns no scene data and writes one local undo on Apply."""
    def __init__(self,tool,index):
        self.tool=tool;self.session=tool.session;self.index=index
        self.source=(copy.deepcopy(self.session.points),self.session.closed)
        self.points=None;self.changed=();self.visible_preview=True;self.closed=False
        dialog=QDialog(tool.toolbar or tool.panel,Qt.Tool|Qt.WindowTitleHint|Qt.WindowCloseButtonHint)
        self.dialog=dialog;dialog.setWindowTitle('轉角處理');dialog.setWindowModality(Qt.ApplicationModal)
        dialog.setMinimumWidth(330);dialog.setMaximumWidth(460)
        layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel(f'節點 {index+1}｜先預覽，套用後才加入工作路徑'))
        self.kind=QComboBox();self.kind.addItems(['直線倒角','圓角']);layout.addWidget(self.kind)
        form=QFormLayout();self.amount_label=QLabel('退縮距離')
        self.amount=QDoubleSpinBox();self.amount.setDecimals(4);self.amount.setRange(.0001,1e8)
        self.amount.setSuffix(' cm');self.amount.setValue(5.)
        form.addRow(self.amount_label,self.amount)
        self.steps=QSpinBox();self.steps.setRange(2,64);self.steps.setValue(8);self.steps.setSuffix(' 段')
        self.steps_label=QLabel('平滑度');form.addRow(self.steps_label,self.steps);layout.addLayout(form)
        self.explanation=QLabel();self.explanation.setWordWrap(True);layout.addWidget(self.explanation)
        self.preview=QCheckBox('即時預覽');self.preview.setChecked(True);layout.addWidget(self.preview)
        self.message=QLabel();self.message.setWordWrap(True);layout.addWidget(self.message)
        row=QHBoxLayout();self.apply_button=QPushButton('套用');self.apply_button.setDefault(True)
        self.apply_button.setStyleSheet('background:#168bd2; color:white; padding:5px 14px;')
        self.cancel_button=QPushButton('取消');self.cancel_button.setAutoDefault(False)
        row.addWidget(self.apply_button);row.addWidget(self.cancel_button);layout.addLayout(row)
        self.timer=QTimer(dialog);self.timer.setSingleShot(True);self.timer.timeout.connect(self.refresh)
        self.kind.currentIndexChanged.connect(self.schedule)
        self.amount.valueChanged.connect(self.schedule);self.steps.valueChanged.connect(self.schedule)
        self.preview.toggled.connect(self.toggle_preview)
        self.apply_button.clicked.connect(self.apply);self.cancel_button.clicked.connect(dialog.reject)
        dialog.finished.connect(self.dispose)
        self.update_fields()

    def update_fields(self):
        fillet=self.kind.currentIndex()==1
        self.amount_label.setText('半徑' if fillet else '退縮距離')
        self.steps.setVisible(fillet);self.steps_label.setVisible(fillet)
        self.explanation.setText('以折線近似相切圓弧；半徑為路徑世界尺寸。' if fillet else '沿相鄰兩側各退相同距離，再以斜線連接。')

    def schedule(self,*_):
        if self.closed:return
        self.update_fields();self.points=None;self.changed=();self.apply_button.setEnabled(False)
        self.message.setText('計算預覽中…');self.timer.start(90);self.tool._schedule_analysis()

    def toggle_preview(self,value):
        self.visible_preview=bool(value);self.tool._refresh_analysis()

    def assert_current(self):
        if self.tool.session is not self.session or self.session.closed!=self.source[1] or self.session.points!=self.source[0]:
            raise ValueError('工作路徑已變更，請取消後重新選取轉角。')
        self.session.assert_unchanged(self.tool.viewport.scene)

    def refresh(self):
        if self.closed or self.tool.done:return
        self.timer.stop();self.points=None;self.changed=()
        try:
            self.assert_current()
            points,changed=corner_geometry(self.source[0],self.index,self.source[1],
                ('chamfer','fillet')[self.kind.currentIndex()],self.amount.value()/100.,self.steps.value())
            candidate=copy.copy(self.session);candidate.points=points
            candidate.replacement()  # Validate constraints AND the actual production mesh.
            self.points=points;self.changed=tuple(changed);self.message.setText('預覽有效；套用後加入工作路徑，按完成重建。')
            self.apply_button.setEnabled(True)
        except (ValueError,RuntimeError,ArithmeticError,IndexError) as exc:
            self.message.setText(str(exc));self.apply_button.setEnabled(False)
        self.dialog.adjustSize();self.tool._refresh_analysis()

    def show(self):
        self.dialog.show();self.refresh();self.dialog.adjustSize()
        self.amount.setFocus();self.amount.selectAll()

    def apply(self):
        if self.closed:return
        self.refresh()  # Includes pending field edits and stale-source checks.
        if self.points is None:return
        self.session._record();self.session.points=copy.deepcopy(self.points)
        self.tool.selected=self.index;self.tool.selection={self.index};self.tool.segment=self.index
        self.tool.error='';self.dialog.accept()
        self.tool._sync();self.tool._focus_viewport()
        self.tool._status('轉角已加入工作路徑，可復原；按完成才重建線板。')

    def dispose(self,*_):
        if self.closed:return
        self.closed=True;self.timer.stop();self.points=None;self.changed=()
        if self.tool.corner_editor is self:self.tool.corner_editor=None
        if not self.tool.done:self.tool._schedule_analysis();self.tool.viewport.update()
        self.dialog.deleteLater()
