# SPDX-License-Identifier: GPL-3.0-or-later
"""Stopped-session recovery point browser for the current document only."""
import datetime as dt
import os
from pathlib import Path
import re
from PySide6.QtCore import QUrl,QDate
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QSpinBox,QCheckBox,QDateEdit,
    QTableWidget,QTableWidgetItem,QAbstractItemView,QHeaderView,QMessageBox,QFileDialog)
from . import bridge_backups as backups
from .light_data import KEY


def display_time(value):
    try:return dt.datetime.fromisoformat(value).astimezone().strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError,TypeError):return '—'


class BridgeBackupsDialog(QDialog):
    def __init__(self,panel):
        super().__init__(panel);self.panel=panel;self.rows=[];self.directory=None;self.task=None
        self.setWindowTitle('橋接備份與時間點還原');self.resize(900,440)
        layout=QVBoxLayout(self)
        hint=QLabel('還原點保存橋接識別與配對快照，不包含 D5 專案或 IGZ 原始模型。\n'
                    '還原前請停止同步，並準備相同時期的 D5／IGZ 副本。現況會先備份。\n'
                    '一般定時備份只留最新 6 份；第 7 份成功後輪替最舊一份。\n'
                    '每 30 分鐘保護點與手動保留點另計，不占 6 份名額；相同內容共用儲存。')
        hint.setWordWrap(True);layout.addWidget(hint)
        settings_row=QHBoxLayout();layout.addLayout(settings_row)
        settings_row.addWidget(QLabel('自動備份間隔'))
        self.minutes=QSpinBox();self.minutes.setRange(1,1440);self.minutes.setValue(10);self.minutes.setSuffix(' 分鐘');settings_row.addWidget(self.minutes)
        self.save_minutes=QPushButton('套用分鐘');self.save_minutes.clicked.connect(self.apply_minutes);settings_row.addWidget(self.save_minutes)
        settings_row.addWidget(QLabel('30 分鐘保護固定生效；程式關閉時不補造歷史點。'))
        filters=QHBoxLayout();layout.addLayout(filters)
        self.date_filter=QCheckBox('按日期查看／清理');filters.addWidget(self.date_filter)
        self.date_from=QDateEdit(QDate.currentDate());self.date_to=QDateEdit(QDate.currentDate())
        for edit in (self.date_from,self.date_to):edit.setCalendarPopup(True);filters.addWidget(edit);edit.dateChanged.connect(self.refresh)
        self.date_filter.toggled.connect(self.refresh)
        self.table=QTableWidget(0,7);self.table.setHorizontalHeaderLabels(['備份時間','同步版本','外掛版本','圖層模式','用途','保留','檔案狀態'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
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
        self.delete=QPushButton('刪除選取…');self.delete.clicked.connect(self.delete_selected);row.addWidget(self.delete)
        open_folder=QPushButton('開啟資料夾');open_folder.clicked.connect(self.open_folder);row.addWidget(open_folder)
        transfer=QHBoxLayout();layout.addLayout(transfer)
        self.export_transfer=QPushButton('匯出移機包…');self.export_transfer.clicked.connect(self.export_project);transfer.addWidget(self.export_transfer)
        self.import_transfer=QPushButton('匯入移機包…');self.import_transfer.clicked.connect(self.import_project);transfer.addWidget(self.import_transfer)
        transfer.addWidget(QLabel('只搬移橋接資料；D5 使用公用伺服器上的原專案。'))
        clean=QHBoxLayout();layout.addLayout(clean)
        self.include_protected=QCheckBox('手動刪除包含保護點（需再次確認）');clean.addWidget(self.include_protected)
        self.delete_range=QPushButton('刪除目前日期範圍…');self.delete_range.clicked.connect(lambda:self.delete_selected(all_visible=True));clean.addWidget(self.delete_range)
        self.capacity=QLabel();self.capacity.setWordWrap(True);layout.addWidget(self.capacity)
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
        row=self.selected();valid=bool(row and row.get('valid'));stopped=self.stopped() and self.task is None
        self.manual.setEnabled(stopped and self.directory is not None)
        self.restore.setEnabled(stopped and valid);self.pin.setEnabled(stopped and valid and not row.get('protected_slots'))
        self.verify.setEnabled(valid and self.task is None)
        self.delete.setEnabled(stopped and valid)
        self.delete_range.setEnabled(stopped and bool(self.rows) and self.date_filter.isChecked())
        self.export_transfer.setEnabled(stopped and self.directory is not None)
        self.import_transfer.setEnabled(stopped)

    def refresh(self,*_):
        self.rows=[];self.directory=None
        try:self.directory=self.project();self.rows=backups.list_points(self.directory)
        except (OSError,ValueError) as exc:self.notice.setText(str(exc))
        else:self.notice.setText('請停止同步後備份／還原。舊 optimization-backups 保留原處；本列表只顯示新版配對還原點。')
        if self.directory:
            from .backup_schedule import settings
            try:self.minutes.setValue(settings(self.directory)['minutes'])
            except (OSError,ValueError) as exc:self.notice.setText(str(exc))
            if self.date_filter.isChecked():
                start=self.date_from.date().toPython();end=self.date_to.date().toPython()
                self.rows=[r for r in self.rows if r.get('created_at') and start<=dt.datetime.fromisoformat(r['created_at']).astimezone().date()<=end]
            try:
                usage=backups.storage_summary(self.directory)
                items=sorted(usage['days'].items(),reverse=True)
                if self.date_filter.isChecked():items=[(day,info) for day,info in items if start.isoformat()<=day<=end.isoformat()]
                days='；'.join(f"{day}：{info['count']} 份／保護 {info['protected']} 份" for day,info in items[:10])
                if len(items)>10:days+='；其餘日期請篩選查看'
                jobs=self.directory/'backup-jobs'
                if jobs.exists() and any((p/'request.json').is_file() for p in jobs.glob('job-*')):days+='\n尚有未完成備份；停止後按立即備份可補完，再進行清理。'
                self.capacity.setText(f"共用後估計占用 {usage['physical_bytes_estimate']/1024**2:.1f} MiB，剩餘 {usage['free_bytes']/1024**3:.1f} GiB。\n"+days)
            except OSError as exc:self.capacity.setText(str(exc))
        self.table.setRowCount(len(self.rows))
        for index,row in enumerate(self.rows):
            state=row.get('state',{});policy=state.get('layer_policy',{});mode_labels={'normal':'正常','hold':'保留','exclude':'排除'}
            when=display_time(row.get('created_at'))
            reasons={'half_hour':'30 分鐘保護','interval':'定時','confirmed':'同步確認後','before_sync':'同步前','visibility_upgrade':'可見性升級前','identity_migration':'識別升級前','layer_policy_change':'圖層切換前',
                     'transfer_export':'移機匯出前','transfer_import':'移機匯入後','material_compaction':'材質整理前','manual':'手動','before_restore':'還原前現況'}
            purpose='、'.join(reasons.get(r,r) for r in row.get('reasons',[]))
            if row.get('daily'):purpose+='／每日'
            values=[when,str(state.get('revision') if state.get('revision') is not None else '舊基準'),
                    state.get('plugin_version','—'),'、'.join(f'{k}: {mode_labels.get(v,v)}' for k,v in policy.items()) or '正常',purpose,
                    '30分鐘保護' if row.get('protected_slots') else '是' if row.get('pinned') else '否','還原前核對雜湊' if row.get('valid') else row.get('error','無效')]
            for column,value in enumerate(values):
                item=QTableWidgetItem(value);item.setToolTip('備份時間：'+str(row.get('created_at','—'))+'\n資料最後成功確認：'+str(state.get('confirmed_at','—')));self.table.setItem(index,column,item)
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
        self.start_task(backups.manual_point,lambda result:self.notice.setText('已建立並保留還原點：'+Path(result).name))

    def verify_selected(self):
        row=self.selected()
        if not row:return
        self.start_task(lambda p:backups.verify_point(p,row['path']),lambda result:self.notice.setText('配對及 SHA-256 驗證通過。'),require_stopped=False)

    def pin_selected(self):
        row=self.selected()
        if row:self.start_task(lambda p:backups.set_pinned(p,row['path'],not row.get('pinned')))

    def restore_selected(self):
        row=self.selected()
        if not row or not self.stopped():return
        answer=QMessageBox.question(self,'確認還原橋接資料',
            f'時間：{display_time(row.get("created_at"))}\n同步版本：{row.get("state",{}).get("revision","舊基準")}\n'
            '將先備份現況，再還原選取的橋接檔與配對快照。\n'
            '這不會還原 D5 或 IGZ，請先準備相同時期的專案副本。是否繼續？')
        if answer!=QMessageBox.StandardButton.Yes:return
        def completed(result):
            message='橋接資料已還原；請先核對 D5／IGZ 副本，再手動開始同步。'
            self.notice.setText(message);self.panel.status.setText(message);self.panel.progress.stop(message)
        self.start_task(lambda p:backups.restore_point(p,row['path']),completed)

    def start_task(self,action,completed=None,require_stopped=True):
        if self.task is not None or (require_stopped and not self.stopped()):return
        try:
            project=self.project()
            if project!=self.directory:raise ValueError('文件已變更，請重新讀取')
        except (OSError,ValueError) as exc:self.notice.setText(str(exc));return
        from .backup_tasks import FileTask
        self.task=FileTask(lambda:action(project),self)
        scene=self.panel.app.scene;out={}
        if require_stopped:
            self.panel.bridge_file_task=True
            if hasattr(self.panel,'buttons'):self.panel.buttons()
        def finished(result,error):
            task=self.task;self.task=None
            if require_stopped:
                self.panel.bridge_file_task=False
                if hasattr(self.panel,'buttons'):self.panel.buttons()
            if error:QMessageBox.warning(self,'備份操作未完成',error)
            self.refresh()
            if not error and completed:
                if self.panel.app.scene is scene:completed(result)
                else:self.notice.setText('原文件的備份作業已完成，目前文件已切換。')
        self.task.result.connect(lambda result,error:out.update(result=result,error=error))
        self.task.finished.connect(lambda:finished(out.get('result'),out.get('error','')))
        self.task.finished.connect(self.task.deleteLater);self.task.start();self.notice.setText('檔案作業在背景處理中…');self.update_controls()

    def apply_minutes(self):
        try:
            from .backup_schedule import save_settings
            save_settings(self.project(),self.minutes.value())
        except (OSError,ValueError) as exc:self.notice.setText(str(exc));return
        self.notice.setText('分鐘設定已儲存；每 30 分鐘保護點固定生效，不會被一般輪替刪除。')

    def delete_selected(self,all_visible=False):
        if not self.stopped() or self.task is not None:return
        indexes=range(len(self.rows)) if all_visible else [i.row() for i in self.table.selectionModel().selectedRows()]
        rows=[self.rows[i] for i in indexes if self.rows[i].get('valid')]
        include=self.include_protected.isChecked()
        rows=[r for r in rows if include or not backups.protected(r)]
        if not rows:self.notice.setText('沒有可刪除的備份；保護點預設不包含。');return
        size=sum(sum(f['bytes'] for f in row['files']) for row in rows)
        text=f'將刪除 {len(rows)} 份備份，邏輯容量 {size/1024**2:.1f} MiB。共用資料仍有引用時不會釋放。'
        if include:text+='\n包含手動／30 分鐘保護點，刪除後無法從這些點還原。'
        text+='\n只刪除選取的歷史備份，不刪目前 bridge.blend。是否確定？'
        if QMessageBox.question(self,'確認刪除歷史備份',text)!=QMessageBox.StandardButton.Yes:return
        self.start_task(lambda p:backups.delete_points(p,[r['path'] for r in rows],include_protected=include),
                        lambda count:self.notice.setText(f'已刪除 {count} 份歷史備份。'))

    def closeEvent(self,event):
        if self.task is not None:
            self.notice.setText('請等待背景檔案作業完成後關閉。');event.ignore();return
        super().closeEvent(event)

    def export_project(self):
        if not self.stopped():return
        filename,_=QFileDialog.getSaveFileName(self,'匯出橋接移機包','D5-bridge-transfer.zip','橋接移機包 (*.zip)')
        if not filename:return
        if Path(filename).suffix.lower()!='.zip':filename+='.zip'
        from .project_transfer import export_project
        self.start_task(lambda p:export_project(p,filename),lambda result:self.notice.setText('移機包已匯出，含所有已完成保護點；D5／原始 IGZ 不包含。'))

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
