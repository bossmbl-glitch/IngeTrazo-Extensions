# SPDX-License-Identifier: GPL-3.0-or-later
"""Stopped-session recovery point browser for the current document only."""
import datetime as dt
import os
from pathlib import Path
import re
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QTableWidget,QTableWidgetItem,QAbstractItemView,QHeaderView,QMessageBox,QFileDialog)
from . import bridge_backups as backups
from .light_data import KEY


class BridgeBackupsDialog(QDialog):
    def __init__(self,panel):
        super().__init__(panel);self.panel=panel;self.rows=[];self.directory=None
        self.setWindowTitle('橋接備份與還原');self.resize(900,440)
        layout=QVBoxLayout(self)
        hint=QLabel('還原點保存橋接識別與配對快照，不包含 D5 專案或 IGZ 原始模型。\n'
                    '還原前請停止同步，並準備相同時期的 D5／IGZ 副本。現況會先備份。\n'
                    '保留最近 10 個還原點及 7 個每日備份；高風險與手動保留點不會自動清理。')
        hint.setWordWrap(True);layout.addWidget(hint)
        self.table=QTableWidget(0,7);self.table.setHorizontalHeaderLabels(['備份時間','同步版本','外掛版本','圖層模式','用途','保留','檔案狀態'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self.update_controls);layout.addWidget(self.table)
        self.notice=QLabel();self.notice.setWordWrap(True);layout.addWidget(self.notice)
        row=QHBoxLayout();layout.addLayout(row)
        refresh=QPushButton('重新讀取');refresh.clicked.connect(self.refresh);row.addWidget(refresh)
        self.manual=QPushButton('立即備份');self.manual.clicked.connect(self.make_backup);row.addWidget(self.manual)
        self.verify=QPushButton('驗證選取');self.verify.clicked.connect(self.verify_selected);row.addWidget(self.verify)
        self.pin=QPushButton('保留／取消保留');self.pin.clicked.connect(self.pin_selected);row.addWidget(self.pin)
        self.restore=QPushButton('還原選取…');self.restore.clicked.connect(self.restore_selected);row.addWidget(self.restore)
        open_folder=QPushButton('開啟資料夾');open_folder.clicked.connect(self.open_folder);row.addWidget(open_folder)
        transfer=QHBoxLayout();layout.addLayout(transfer)
        self.export_transfer=QPushButton('匯出移機包…');self.export_transfer.clicked.connect(self.export_project);transfer.addWidget(self.export_transfer)
        self.import_transfer=QPushButton('匯入移機包…');self.import_transfer.clicked.connect(self.import_project);transfer.addWidget(self.import_transfer)
        transfer.addWidget(QLabel('只搬移橋接資料；D5 使用公用伺服器上的原專案。'))
        panel.state_changed.connect(self.update_controls);self.refresh()

    def project(self,require_exists=True):
        raw=(getattr(self.panel.app.scene,'plugin_data',{}) or {}).get(KEY,{}) or {}
        pid=raw.get('bridge_project_id','')
        if not isinstance(pid,str) or not re.fullmatch('[0-9a-f]{32}',pid):raise ValueError('目前文件尚無橋接專案識別')
        root=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'IngeTrazoD5Bridge'/'projects'/pid
        if require_exists and not root.is_dir():raise ValueError('找不到目前文件的橋接專案資料夾')
        return root.resolve()

    def stopped(self):
        p=self.panel
        return not (p.running or p.checking or p.exporting or (p.process and p.process.poll() is None))

    def selected(self):
        row=self.table.currentRow()
        return self.rows[row] if 0<=row<len(self.rows) else None

    def update_controls(self):
        row=self.selected();valid=bool(row and row.get('valid'));stopped=self.stopped()
        self.manual.setEnabled(stopped and self.directory is not None)
        self.restore.setEnabled(stopped and valid);self.pin.setEnabled(stopped and valid)
        self.verify.setEnabled(valid)
        self.export_transfer.setEnabled(stopped and self.directory is not None)
        self.import_transfer.setEnabled(stopped)

    def refresh(self):
        self.rows=[];self.directory=None
        try:self.directory=self.project();self.rows=backups.list_points(self.directory)
        except (OSError,ValueError) as exc:self.notice.setText(str(exc))
        else:self.notice.setText('請停止同步後備份／還原。舊 optimization-backups 保留原處；本列表只顯示新版配對還原點。')
        self.table.setRowCount(len(self.rows))
        for index,row in enumerate(self.rows):
            state=row.get('state',{});policy=state.get('layer_policy',{})
            when=dt.datetime.fromisoformat(row['created_at']).astimezone().strftime('%Y-%m-%d %H:%M:%S') if row.get('created_at') else '—'
            reasons={'confirmed':'同步確認後','before_sync':'同步前','visibility_upgrade':'可見性升級前','identity_migration':'識別升級前','layer_policy_change':'圖層切換前',
                     'transfer_export':'移機匯出前','transfer_import':'移機匯入後','material_compaction':'材質整理前','manual':'手動','before_restore':'還原前現況'}
            purpose='、'.join(reasons.get(r,r) for r in row.get('reasons',[]))
            if row.get('daily'):purpose+='／每日'
            values=[when,str(state.get('revision') if state.get('revision') is not None else '舊基準'),
                    state.get('plugin_version','—'),'、'.join(f'{k}: {v}' for k,v in policy.items()) or '正常',purpose,
                    '是' if row.get('pinned') else '否','還原前核對雜湊' if row.get('valid') else row.get('error','無效')]
            for column,value in enumerate(values):self.table.setItem(index,column,QTableWidgetItem(value))
        self.update_controls()

    def run_stopped(self,action):
        if not self.stopped():self.notice.setText('請先停止同步並等待背景 Blender 結束。');return None
        try:
            project=self.project()
            if project!=self.directory:raise ValueError('文件已切換，請重新讀取備份清單')
            result=action(project)
        except Exception as exc:
            QMessageBox.warning(self,'橋接備份／還原未完成',str(exc));return None
        self.refresh();return result

    def make_backup(self):
        result=self.run_stopped(backups.manual_point)
        if result:self.notice.setText('已建立並保留還原點：'+Path(result).name)

    def verify_selected(self):
        row=self.selected()
        if not row:return
        try:backups.verify_point(self.project(),row['path'])
        except Exception as exc:self.notice.setText('驗證失敗：'+str(exc))
        else:self.notice.setText('配對檔案與 SHA-256 驗證通過；D5 專案仍需使用相同時期副本。')

    def pin_selected(self):
        row=self.selected()
        if row:self.run_stopped(lambda p:backups.set_pinned(p,row['path'],not row.get('pinned')))

    def restore_selected(self):
        row=self.selected()
        if not row or not self.stopped():return
        answer=QMessageBox.question(self,'確認還原橋接資料',
            '將先備份現況，再還原選取的橋接檔與配對快照。\n'
            '這不會還原 D5 或 IGZ，請先準備相同時期的專案副本。是否繼續？')
        if answer!=QMessageBox.StandardButton.Yes:return
        result=self.run_stopped(lambda p:backups.restore_point(p,row['path']))
        if result:
            message='橋接資料已還原；請先核對 D5／IGZ 副本，再手動開始同步。'
            self.notice.setText(message);self.panel.status.setText(message);self.panel.progress.stop(message)

    def export_project(self):
        if not self.stopped():return
        filename,_=QFileDialog.getSaveFileName(self,'匯出橋接移機包','D5-bridge-transfer.zip','橋接移機包 (*.zip)')
        if not filename:return
        if Path(filename).suffix.lower()!='.zip':filename+='.zip'
        from .project_transfer import export_project
        result=self.run_stopped(lambda p:export_project(p,filename))
        if result:self.notice.setText('移機包已匯出。不含 D5／原始 IGZ；請另帶同一份 IGZ，新電腦開啟伺服器上的同一個 D5 專案。')

    def import_project(self):
        if not self.stopped():return
        try:project=self.project(require_exists=False)
        except (OSError,ValueError) as exc:self.notice.setText(str(exc)+'；請先開啟原專案的 IGZ。');return
        filename,_=QFileDialog.getOpenFileName(self,'匯入橋接移機包','','橋接移機包 (*.zip)')
        if not filename:return
        answer=QMessageBox.question(self,'確認匯入橋接資料',
            '將驗證移機包，保全本機現況，再安裝原橋接識別及備份紀錄。\n'
            'D5 專案與資產不包含在內；請使用伺服器上的同一個專案，避免兩台同時同步。\n'
            '完成後不會自動同步。是否繼續？')
        if answer!=QMessageBox.StandardButton.Yes:return
        try:
            if not self.stopped() or project!=self.project(require_exists=False):raise ValueError('同步狀態或文件已變更，請重新操作')
            from .project_transfer import import_project
            result=import_project(project,filename)
        except Exception as exc:QMessageBox.warning(self,'移機匯入未完成',str(exc));return
        self.refresh()
        message=f'已匯入 {result["points_imported"]} 個還原點；請設定本機 Blender／D5 路徑並核對伺服器專案，再手動開始同步。'
        self.notice.setText(message);self.panel.status.setText(message);self.panel.progress.stop(message)

    def open_folder(self):
        try:project=self.project()
        except (OSError,ValueError) as exc:self.notice.setText(str(exc));return
        path=project/backups.ROOT
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path if path.is_dir() else project)))
