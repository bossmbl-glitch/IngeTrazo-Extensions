# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-modal per-document layer sync editor with direct check/cross controls."""
from PySide6.QtCore import Qt,Signal,QSize
from PySide6.QtGui import QIcon,QPixmap,QPainter,QPen,QColor
from PySide6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QToolButton,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView)
from .layer_scope import host_inventory,host_policy,POLICY_KEY,clean_policy
from .light_data import KEY

LABELS={'normal':'正常同步','hold':'暫時不更新','exclude':'排除同步'}
COLORS={'normal':'#249c45','hold':'#e98a26','exclude':'#cd4949'}


def state_icon(mode):
    pix=QPixmap(18,18);pix.fill(Qt.GlobalColor.transparent)
    painter=QPainter(pix);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen=QPen(QColor('white'),2.2);pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin);painter.setPen(pen)
    if mode=='normal':
        painter.drawLine(3,9,7,13);painter.drawLine(7,13,15,5)
    elif mode=='hold':
        painter.drawLine(4,4,14,14);painter.drawLine(14,4,4,14)
    else:
        painter.drawEllipse(2,2,14,14);painter.drawLine(4,14,14,4)
    painter.end();return QIcon(pix)


class SyncStateButton(QToolButton):
    mode_changed=Signal(str)

    def __init__(self,name,mode,parent=None):
        super().__init__(parent)
        self.layer_name=name;self.mode=None
        self.setFixedSize(34,28);self.setIconSize(QSize(18,18))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self.toggle_mode)
        self.set_mode(mode)

    def set_mode(self,mode):
        if mode not in LABELS:raise ValueError('未知圖層狀態')
        changed=self.mode!=mode;self.mode=mode
        self.setIcon(state_icon(mode));self.setProperty('syncMode',mode)
        self.setStyleSheet('QToolButton { background:'+COLORS[mode]+'; border:1px solid transparent; border-radius:4px; padding:0px; }'
                          'QToolButton:hover { border:1px solid #253545; }'
                          'QToolButton:focus { border:2px solid #253545; }')
        meanings={'normal':'點一下改為暫時不更新，保留 D5 舊內容',
                  'hold':'點一下恢復正常同步','exclude':'已排除：下次更新移除 D5 舊內容；點一下恢復正常同步'}
        self.setToolTip(self.layer_name+'：'+LABELS[mode]+'\n'+meanings[mode])
        self.setAccessibleName(self.layer_name+'：'+LABELS[mode])
        if changed:self.mode_changed.emit(mode)

    def toggle_mode(self):
        self.set_mode('hold' if self.mode=='normal' else 'normal')


class LayerDialog(QDialog):
    def __init__(self,panel):
        super().__init__(getattr(panel.app,'window',None))
        self.panel=panel;self.app=panel.app;self.scene=None;self.buttons={}
        self.setWindowTitle('D5 圖層同步');self.setModal(False);self.resize(610,450)
        layout=QVBoxLayout(self);layout.setContentsMargins(10,10,10,10);layout.setSpacing(7)
        hint=QLabel('點選勾／叉切換是否同步。以最外層群組圖層控制整組，散線、散面歸 Layer 0。\n'
                    '首次使用圖層保留功能，請先全部正常同步成功一次。')
        hint.setWordWrap(True);layout.addWidget(hint)
        self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels(['圖層','群組數','是否同步'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1,70);self.table.setColumnWidth(2,90)
        layout.addWidget(self.table)
        legend=QLabel('<span style="color:#249c45">✓ 正常同步</span>　'
                      '<span style="color:#c77518">✕ 暫時不更新，保留 D5 舊內容</span><br>'
                      '<span style="color:#b93f3f">⊘ 排除同步</span>：更新時移除先前傳送內容，來源模型保留。')
        legend.setWordWrap(True);layout.addWidget(legend)
        self.notice=QLabel();self.notice.setWordWrap(True);layout.addWidget(self.notice)
        row=QHBoxLayout();row.setSpacing(6);layout.addLayout(row)
        refresh=QPushButton('重新讀取');refresh.clicked.connect(self.refresh);row.addWidget(refresh)
        normal=QPushButton('全部同步');normal.clicked.connect(self.all_normal);row.addWidget(normal)
        self.exclude_button=QPushButton('排除選取圖層…');self.exclude_button.clicked.connect(self.exclude_selected);row.addWidget(self.exclude_button)
        apply=QPushButton('套用設定');apply.clicked.connect(self.apply);row.addWidget(apply)
        close=QPushButton('關閉');close.clicked.connect(self.close);row.addWidget(close)
        self.refresh()

    def refresh(self):
        self.scene=self.app.scene
        policy=host_policy(self.scene);inventory=host_inventory(self.scene)
        self.table.setRowCount(0);self.buttons={}
        for name,count in sorted(inventory.items(),key=lambda x:(x[0]!='Layer 0',x[0])):
            i=self.table.rowCount();self.table.insertRow(i)
            for column,text in ((0,name),(1,str(count))):
                item=QTableWidgetItem(text);item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column==1:item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(i,column,item)
            button=SyncStateButton(name,policy.get(name,'normal'))
            self.buttons[name]=button
            cell=QWidget();center=QHBoxLayout(cell);center.setContentsMargins(0,0,0,0)
            center.setAlignment(Qt.AlignmentFlag.AlignCenter);center.addWidget(button)
            self.table.setCellWidget(i,2,cell)
            button.clicked.connect(lambda checked=False,row=i:self.table.selectRow(row))
            button.mode_changed.connect(lambda mode,row=i:self.staged_change(row,mode))
        self.notice.setText('點選後按「套用設定」。設定隨 IGZ 儲存；全域暫停仍優先生效。')

    def staged_change(self,row,mode):
        self.notice.setText('尚未套用：'+self.table.item(row,0).text()+' → '+LABELS[mode]+'。請按「套用設定」。')

    def all_normal(self):
        for button in self.buttons.values():button.set_mode('normal')
        self.notice.setText('全部已標為正常同步，按「套用設定」才生效。')

    def exclude_selected(self):
        rows=self.table.selectionModel().selectedRows()
        if not rows:
            self.notice.setText('請先點選要排除的圖層列；可用 Ctrl／Shift 多選。');return
        for index in rows:
            name=self.table.item(index.row(),0).text();self.buttons[name].set_mode('exclude')
        self.notice.setText('選取圖層已標為排除；按「套用設定」後於下次更新移除 D5 舊內容。')

    def values(self):
        return clean_policy({name:button.mode for name,button in self.buttons.items()})

    def apply(self):
        if self.app.scene is not self.scene:
            self.notice.setText('文件已切換，請按「重新讀取」後再設定。');return
        try:
            from core.history import SetPluginDataCommand
            raw=dict((getattr(self.scene,'plugin_data',{}) or {}).get(KEY,{}) or {})
            raw[POLICY_KEY]=self.values()
            self.app.viewport.history.execute(SetPluginDataCommand(KEY,raw))
            self.app.viewport.notify_scene_changed();self.app.viewport.update()
            if self.panel.running:self.panel.document_changed()
            self.notice.setText('設定已套用，請儲存 IGZ。全域暫停時按「更新」才會送出；正在傳送的一版沿用原設定。')
        except Exception as exc:
            self.notice.setText('設定失敗：'+str(exc))
