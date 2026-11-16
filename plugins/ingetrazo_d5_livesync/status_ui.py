# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-modal sync status window; closing it never stops the worker."""
import time
from PySide6.QtCore import QObject,QTimer,Qt
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QProgressBar,QPushButton,QStyle)
from .status_model import StatusModel
from .transport import read_json

COLORS={'active':'#1674d1','success':'#258340','paused':'#be761d','waiting':'#be761d','error':'#c13939','idle':'#586775','stopped':'#586775'}


class SyncStatusDialog(QDialog):
    def __init__(self,panel):
        # An independent normal window can be covered by the host or D5.
        # A parented Tool window stays above its owner even without TopHint.
        super().__init__(None,Qt.WindowType.Window|Qt.WindowType.WindowTitleHint|Qt.WindowType.WindowSystemMenuHint|Qt.WindowType.WindowMinMaxButtonsHint|Qt.WindowType.WindowCloseButtonHint)
        panel.destroyed.connect(self.deleteLater)
        self.panel=panel;self.setWindowTitle('D5 同步狀態');self.setModal(False);self.resize(440,315)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating,True)
        layout=QVBoxLayout(self);layout.setContentsMargins(18,16,18,14);layout.setSpacing(10)
        heading=QHBoxLayout();self.symbol=QLabel();self.symbol.setFixedWidth(38);heading.addWidget(self.symbol)
        self.title=QLabel('尚未啟動');font=self.title.font();font.setPointSize(21);font.setBold(True);self.title.setFont(font);heading.addWidget(self.title,1);layout.addLayout(heading)
        self.detail=QLabel();self.detail.setWordWrap(True);self.detail.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.detail)
        self.bar=QProgressBar();self.bar.setTextVisible(False);self.bar.setFixedHeight(8);layout.addWidget(self.bar)
        self.elapsed=QLabel('已用時間　00:00');self.revision=QLabel('目前版本　—');layout.addWidget(self.elapsed);layout.addWidget(self.revision)
        self.note=QLabel();self.note.setWordWrap(True);self.note.setTextFormat(Qt.TextFormat.PlainText);self.note.setMinimumHeight(38);layout.addWidget(self.note)
        row=QHBoxLayout();layout.addLayout(row)
        self.records=QPushButton('查看詳細紀錄');self.records.clicked.connect(panel.open_folder);row.addWidget(self.records)
        self.pause=QPushButton('暫停後續更新');self.pause.clicked.connect(lambda:panel.set_paused(not panel.schedule.paused));row.addWidget(self.pause)
        self.footer=QLabel('關閉此視窗不會停止同步；暫停不會中斷已提交的本輪。');self.footer.setWordWrap(True);self.footer.setStyleSheet('color:#65717d; font-size:11px');layout.addWidget(self.footer)

    def render(self,card):
        kind=card['kind'];color=COLORS[kind]
        self.title.setText(card['title']);self.title.setStyleSheet('color:'+color)
        pix={'active':QStyle.StandardPixmap.SP_BrowserReload,'success':QStyle.StandardPixmap.SP_DialogApplyButton,
             'paused':QStyle.StandardPixmap.SP_MediaPause,'waiting':QStyle.StandardPixmap.SP_MessageBoxWarning,
             'error':QStyle.StandardPixmap.SP_MessageBoxCritical}.get(kind,QStyle.StandardPixmap.SP_MessageBoxInformation)
        self.symbol.setPixmap(self.style().standardIcon(pix).pixmap(32,32))
        self.detail.setText(card['detail']);self.note.setText(card['note'])
        self.note.setStyleSheet('background:#f1f5fa; color:#33465b; border-radius:4px; padding:8px')
        self.elapsed.setText('已用時間　'+card['elapsed']);self.revision.setText('目前版本　第 '+str(card['revision'])+' 版' if card['revision'] else '目前版本　—')
        if card['busy']:self.bar.setRange(0,0)
        else:self.bar.setRange(0,1);self.bar.setValue(1 if kind=='success' else 0)
        self.bar.setStyleSheet('QProgressBar {border:0;background:#e0e5eb;border-radius:4px;} QProgressBar::chunk {background:'+color+';border-radius:4px;}')
        self.pause.setText('繼續後續更新' if card['paused'] else '暫停後續更新')


class SyncStatusMonitor(QObject):
    def __init__(self,panel):
        super().__init__(panel);self.panel=panel;self.model=StatusModel();self.dialog=SyncStatusDialog(panel)
        self.last_card=None;self.last_session=None;self.last_worker_state={};self.timer=QTimer(self);self.timer.setInterval(500);self.timer.timeout.connect(self.poll);self.timer.start();self.poll()

    def show(self,*,automatic=False):
        self.poll()
        if not automatic and self.dialog.isMinimized():self.dialog.showNormal()
        else:self.dialog.show()  # No activateWindow: keep editing focus.

    def begin_start(self):
        self.model.reset(time.monotonic())
        if self.panel.auto_status.isChecked():self.show(automatic=True)

    def begin_revision(self,revision):
        self.model.begin(revision,time.monotonic())
        if self.panel.auto_status.isChecked():self.show(automatic=True)
        self.poll()
        if self.dialog.isVisible():
            # The host exporter is synchronous. Paint a truthful export label
            # before entering it; do not re-enter the host editing event loop.
            for widget in (self.dialog,self.dialog.title,self.dialog.detail,self.dialog.bar):widget.repaint()

    def failure(self,message):self.model.failure(message);self.poll()
    def stop(self,message=''):self.model.stop(time.monotonic(),message);self.poll()

    def poll(self):
        p=self.panel;session=p.session;state={};log_token=None
        if session:
            if self.last_session!=session.directory:
                self.last_session=session.directory;self.last_worker_state={}
            observed=read_json(session.directory/'status.json',{})
            if isinstance(observed,dict) and observed:self.last_worker_state=observed
            state=self.last_worker_state
            try:
                stat=(session.directory/'worker.log').stat();log_token=(stat.st_size,stat.st_mtime_ns)
            except OSError:pass
        else:
            self.last_session=None;self.last_worker_state={}
        alive=bool(p.process and p.process.poll() is None)
        self.last_card=self.model.view(time.monotonic(),state=state,session_revision=session.revision if session else 0,
            checking=p.checking,exporting=p.exporting,running=p.running,alive=alive,
            paused=p.schedule.paused,dirty=p.schedule.dirty,manual=p.schedule.manual,
            cancelled=p.cancelled,log_token=log_token)
        self.dialog.render(self.last_card)
