# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-blocking IngeTrazo control panel for the background Blender worker."""
from __future__ import annotations
import os
import time
import json
from pathlib import Path
from PySide6.QtCore import QTimer, QSettings, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLabel,
    QPushButton, QLineEdit, QFileDialog, QDoubleSpinBox, QApplication, QCheckBox)
from .transport import SnapshotSession, read_json, write_json
from .launcher import launch
from .schedule import SyncSchedule
from .discovery import blender_candidates,d5_candidates,choose,clean_path


def default_blender():
    found=blender_candidates()
    return str(found[0]) if found else ''


class SyncPanel(QWidget):
    state_changed = Signal()

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.process = None
        self.session = None
        self.bound_scene = self.bound_mesh = None
        self.dirty_at = None
        self.last_export = float("-inf")
        self.running = False
        self.exporting = False
        self.last_metrics_revision = 0
        self.log_file = None
        self.checking=False
        self.cancelled=False
        self.last_error=''
        self.source_recorder=None
        self.motion_certificate=None;self.force_full_next=False
        self.renderer_watch=None;self.stop_reason='';self.preview_was_active=False
        self.settings = QSettings("IngeTrazo", "D5LiveSyncBridge")
        self.schedule = SyncSchedule(self.settings.value("paused", False, type=bool))
        self.retry_request = 0
        self.allowed_revision = 0
        self.layer_dialog = None
        self.duplicate_dialog=None
        layout = QVBoxLayout(self)
        intro = QLabel("在 IngeTrazo 編輯，由背景 Blender 與官方 D5 Sync 自動更新 D5。首次連接可能需要在 D5 選擇專案。首次啟動本版後，請儲存一次 IGZ 以記住固定關聯。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.blender = QLineEdit(choose(self.settings.value('blender',''),blender_candidates(),'blender'))
        self.d5 = QLineEdit(choose(self.settings.value('d5',''),d5_candidates(),'d5'))
        form = QFormLayout()
        form.addRow("Blender 執行檔", self.blender)
        browse = QPushButton("選擇 Blender…")
        self.blender_browse=browse
        browse.clicked.connect(self.choose_blender)
        form.addRow("", browse)
        form.addRow("D5 安裝資料夾", self.d5)
        self.d5_browse=QPushButton('選擇 D5 資料夾…');self.d5_browse.clicked.connect(self.choose_d5);form.addRow('',self.d5_browse)
        self.detect_button=QPushButton('重新偵測安裝位置');self.detect_button.clicked.connect(self.detect);form.addRow('',self.detect_button)
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0.5, 30)
        self.delay.setValue(1.0)
        self.delay.setSuffix(" 秒")
        form.addRow("停止編輯後等待", self.delay)
        self.compatibility = QCheckBox("完整傳送（移動未更新時啟用，再停止並重啟同步）")
        self.compatibility.setToolTip("保留網格快取加速，但每輪向 D5 完整傳送，以便比較相容性。")
        self.compatibility.setChecked(True)
        form.addRow("相容模式", self.compatibility)
        self.fast_motion=QCheckBox('快速移動／旋轉更新')
        self.fast_motion.setChecked(self.settings.value('fast_motion',True,type=bool))
        self.fast_motion.toggled.connect(lambda value:self.settings.setValue('fast_motion',value))
        self.fast_motion.setToolTip('先完整同步成功一次；原生元件的純移動／旋轉傳送小型更新，其他操作自動完整同步。')
        form.addRow('更新速度',self.fast_motion)
        self.partial_updates=QCheckBox('局部形狀／材質更新')
        self.partial_updates.setChecked(self.settings.value('partial_updates',True,type=bool))
        self.partial_updates.toggled.connect(lambda value:self.settings.setValue('partial_updates',value))
        self.partial_updates.setToolTip('先完成完整同步；符合條件時只處理受影響的最外層群組，其他操作完整同步。停止後調整再重新啟動。')
        form.addRow('局部處理',self.partial_updates)
        self.compact_scene = self.app.scene
        self.compact_materials = QCheckBox("整理舊版重複材質（先另存 D5 專案）")
        self.compact_materials.setToolTip("預設保留既有材質識別。勾選後合併因UV定位產生的重複材質；D5中各自調整的設定無法全部保留。停止後勾選，再重新啟動。")
        form.addRow("效能", self.compact_materials)
        material_notice = QLabel("材質隨模型完整傳送；D5 修改的繼承由固定關聯處理。")
        material_notice.setWordWrap(True)
        form.addRow("材質", material_notice)
        layout.addLayout(form)
        layer_button = QPushButton("圖層同步範圍…")
        layer_button.clicked.connect(self.open_layers)
        layout.addWidget(layer_button)
        self.start_button = QPushButton("開始自動同步目前模型")
        self.start_button.clicked.connect(self.start)
        layout.addWidget(self.start_button)
        self.pause_button = QPushButton("暫停")
        self.pause_button.setCheckable(True)
        self.pause_button.setStyleSheet("QPushButton:checked { background-color: #eda13b; color: #201500; font-weight: bold; }")
        self.pause_button.clicked.connect(lambda: self.set_paused(True))
        layout.addWidget(self.pause_button)
        self.resume_button = QPushButton("繼續")
        self.resume_button.clicked.connect(lambda: self.set_paused(False))
        layout.addWidget(self.resume_button)
        self.now_button = QPushButton("更新一次（暫停時仍可用）")
        self.now_button.clicked.connect(self.sync_now)
        layout.addWidget(self.now_button)
        self.full_button=QPushButton('完整更新一次');self.full_button.clicked.connect(self.sync_full_now);layout.addWidget(self.full_button)
        self.stop_button = QPushButton("停止背景同步")
        self.stop_button.clicked.connect(lambda: self.stop())
        layout.addWidget(self.stop_button)
        folder = QPushButton("開啟同步紀錄")
        folder.clicked.connect(self.open_folder)
        layout.addWidget(folder)
        diagnostic=QPushButton('複製診斷');diagnostic.clicked.connect(self.copy_diagnostics);layout.addWidget(diagnostic)
        self.auto_status = QCheckBox('同步時自動顯示狀態視窗')
        self.auto_status.setChecked(self.settings.value('status_auto_show',True,type=bool))
        self.auto_status.toggled.connect(lambda value:self.settings.setValue('status_auto_show',value))
        layout.addWidget(self.auto_status)
        self.detailed_log=QCheckBox('詳細逐物件日誌（檔案較大，重啟同步後生效）')
        self.detailed_log.setChecked(self.settings.value('detailed_log',False,type=bool))
        self.detailed_log.toggled.connect(lambda value:self.settings.setValue('detailed_log',value))
        layout.addWidget(self.detailed_log)
        export_button=QPushButton('匯出效能診斷包…');export_button.clicked.connect(self.export_performance_bundle);layout.addWidget(export_button)
        self.duplicate_scan=QCheckBox('辨識重複模型（只讀報告）')
        self.duplicate_scan.setChecked(self.settings.value('duplicate_scan',True,type=bool))
        self.duplicate_scan.toggled.connect(lambda value:self.settings.setValue('duplicate_scan',value))
        layout.addWidget(self.duplicate_scan)
        duplicates=QPushButton('重複模型辨識報告…');duplicates.clicked.connect(self.open_duplicate_report);layout.addWidget(duplicates)
        monitor_button=QPushButton('同步狀態…');monitor_button.clicked.connect(self.open_sync_status);layout.addWidget(monitor_button)
        self.status = QLabel("尚未啟動。需要已安裝官方 D5 LiveSync for Blender 1.0.0.0028。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.metrics = QLabel("同步模型、材質與工具列建立的 D5 光源。不連動建模視角或最終出圖。")
        self.metrics.setWordWrap(True)
        layout.addWidget(self.metrics)
        layout.addStretch()
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.tick)
        app.on_document_changed(self.document_changed)
        if hasattr(app, "on_document_closed"):
            app.on_document_closed(self.document_closed)
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        from .status_ui import SyncStatusMonitor
        self.progress = SyncStatusMonitor(self)
        self.buttons()

    def open_sync_status(self):
        self.progress.show()

    def open_duplicate_report(self):
        from .duplicate_ui import DuplicateReportDialog
        if self.duplicate_dialog is None:self.duplicate_dialog=DuplicateReportDialog(self)
        self.duplicate_dialog.refresh()
        if self.duplicate_dialog.isMinimized():self.duplicate_dialog.showNormal()
        else:self.duplicate_dialog.show()

    def buttons(self):
        active = self.process is not None and self.process.poll() is None
        self.start_button.setEnabled(not active)
        self.now_button.setEnabled(self.running)
        self.pause_button.setEnabled(True)
        self.pause_button.setChecked(self.schedule.paused)
        self.resume_button.setEnabled(self.schedule.paused)
        self.stop_button.setEnabled(active)
        self.compact_materials.setEnabled(not active)
        self.detailed_log.setEnabled(not active)
        self.fast_motion.setEnabled(not active);self.full_button.setEnabled(self.running)
        self.partial_updates.setEnabled(not active)
        self.duplicate_scan.setEnabled(not active)
        self.blender.setEnabled(not active)
        self.d5.setEnabled(not active)
        self.blender_browse.setEnabled(not active)
        self.d5_browse.setEnabled(not active);self.detect_button.setEnabled(not active)
        self.state_changed.emit()

    def open_layers(self):
        from .layer_ui import LayerDialog
        if self.layer_dialog is None:
            self.layer_dialog = LayerDialog(self)
        elif self.layer_dialog.scene is not self.app.scene:
            self.layer_dialog.refresh()
        self.layer_dialog.show();self.layer_dialog.raise_();self.layer_dialog.activateWindow()

    def choose_blender(self):
        filename, _ = QFileDialog.getOpenFileName(self, "選擇 blender.exe", self.blender.text(), "Blender (blender.exe)")
        if filename:
            self.blender.setText(filename)

    def choose_d5(self):
        path=QFileDialog.getExistingDirectory(self,'選擇內含 d5_launcher.exe 的資料夾',self.d5.text())
        if path:self.d5.setText(path)

    def detect(self):
        self.blender.setText(choose(self.blender.text(),blender_candidates(),'blender'))
        self.d5.setText(choose(self.d5.text(),d5_candidates(),'d5'))
        self.status.setText('已重新偵測。按 D5 開始時會實際檢查所選 Blender 與官方 Sync。')

    def log_tail(self):
        if not self.session:return ''
        try:
            with (self.session.directory/'worker.log').open('rb') as stream:
                stream.seek(0,2);stream.seek(max(0,stream.tell()-8192));return stream.read().decode('utf-8',errors='replace')
        except OSError:return ''

    def diagnostics(self):
        from . import __version__
        from .layer_scope import host_policy
        data={'compact_legacy_materials':self.compact_materials.isChecked(),'layer_sync_modes':host_policy(self.app.scene),'bridge':__version__,'blender':self.blender.text(),'d5':self.d5.text(),'error':self.last_error,'status':self.status.text()}
        data['source_diagnostic_errors']=list(self.source_recorder.errors) if self.source_recorder else []
        data['d5_monitor']=self.renderer_watch.report() if self.renderer_watch else {'armed':False}
        if self.session:
            data.update(preflight=read_json(self.session.directory/'preflight.json',{}),worker=read_json(self.session.directory/'status.json',{}),
                        performance=read_json(self.session.directory/'performance-latest.json',{}),log_tail=self.log_tail())
            duplicates=read_json(self.session.directory/'duplicate-models-latest.json',{})
            data['duplicate_models']={k:duplicates.get(k) for k in ('revision','summary','applied','sdk_instancing_verified','errors')}
        return json.dumps(data,ensure_ascii=False,indent=2)

    def export_performance_bundle(self):
        from .perf_diagnostics import export_bundle
        path,_=QFileDialog.getSaveFileName(self,'匯出效能診斷包','IngeTrazo-D5-效能診斷.zip','ZIP (*.zip)')
        if not path:return
        if not path.lower().endswith('.zip'):path+='.zip'
        try:
            export_bundle(self.session.directory if self.session else None,path,self.diagnostics())
            self.status.setText('效能診斷包已匯出：'+path)
        except Exception as exc:self.status.setText('診斷包匯出失敗：'+str(exc))

    def copy_diagnostics(self):
        QApplication.clipboard().setText(self.diagnostics())

    def fail(self,message):
        self.last_error=str(message);self.running=False;self.checking=False
        self.progress.failure(message)
        self.status.setText('無法開始／繼續同步：'+str(message)+'\n可按「複製診斷」取得詳細資料。')
        self.status.setStyleSheet('color: #c33;')
        if self.session:
            try:write_json(self.session.directory/'startup-error.json',{'message':str(message)})
            except OSError:pass

    def matches(self):
        return self.app.scene is self.bound_scene and self.app.scene.loose_mesh is self.bound_mesh

    def start(self):
        if self.process and self.process.poll() is None:
            return
        try:
            self.cancelled=False;self.last_error='';self.status.setStyleSheet('')
            if self.motion_certificate:self.motion_certificate.close();self.motion_certificate=None
            self.session=None;self.process=None;self.source_recorder=None;self.force_full_next=False
            self.progress.begin_start()
            self.detect()
            blender = clean_path(self.blender.text())
            d5 = clean_path(self.d5.text())
            if not blender.is_file() or blender.name.lower() != "blender.exe":
                raise ValueError("請選擇有效的 blender.exe")
            if not (d5 / "d5_launcher.exe").is_file():
                raise ValueError("D5 資料夾內找不到 d5_launcher.exe")
            self.settings.setValue("blender", str(blender))
            self.settings.setValue("d5", str(d5))
            cache = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "IngeTrazoD5Bridge" / "sessions"
            # Persist with the IGZ through the same host history command that
            # the existing light tools use; no geometry is touched.
            import uuid
            from core.history import SetPluginDataCommand
            from .light_data import KEY
            raw=dict((getattr(self.app.scene, 'plugin_data', {}) or {}).get(KEY, {}) or {})
            project_id=raw.get('bridge_project_id')
            new_identity=not isinstance(project_id,str) or len(project_id)!=32
            if new_identity:
                project_id=uuid.uuid4().hex
                raw['bridge_project_id']=project_id
                self.app.viewport.history.execute(SetPluginDataCommand(KEY,raw))
                self.app.viewport.notify_scene_changed()
                self.app.viewport.update()
            from .project_store import project_directory
            project=project_directory(cache.parent,project_id)
            self.session = SnapshotSession(cache)
            self.bound_scene = self.app.scene
            self.bound_mesh = self.app.scene.loose_mesh
            self.last_metrics_revision = 0
            self.schedule = SyncSchedule(self.schedule.paused)
            self.allowed_revision = 0
            self.retry_request = 0
            self.write_control()
            write_json(self.session.directory / "config.json", {"d5_path": str(d5), "incremental_d5": not self.compatibility.isChecked(),
                "project_directory": str(project), "project_id": project_id,
                "detailed_log":self.detailed_log.isChecked(),
                "duplicate_scan":self.duplicate_scan.isChecked(),
                "fast_transforms":self.fast_motion.isChecked(),
                "partial_updates":self.partial_updates.isChecked(),
                "compact_legacy_materials": self.compact_materials.isChecked()})
            self.log_file = (self.session.directory / "worker.log").open("w", encoding="utf-8")
            self.process = launch(blender, Path(__file__).parent/'preflight.py', self.session.directory, self.log_file)
            self.checking=True;self.check_started=time.monotonic();self.running = False
            self.dirty_at = None
            self.status.setText('正在檢查同步環境。首次使用本版後，請儲存 IGZ，以保存固定關聯。')
            self.timer.start()
        except Exception as exc:
            self.fail(exc)
            if self.log_file:
                self.log_file.close()
                self.log_file = None
        self.buttons()

    def start_worker(self):
        if not self.matches():raise RuntimeError('檢查期間已切換模型，請為目前模型重新按 D5。')
        # Start the receiver first. The normal scheduler publishes the initial
        # snapshot so first-export failures use the same retry path as edits.
        if self.log_file:self.log_file.close()
        self.log_file=(self.session.directory/'worker.log').open('a',encoding='utf-8')
        self.process=launch(clean_path(self.blender.text()),Path(__file__).parent/'worker.py',self.session.directory,self.log_file)
        self.running=True;self.checking=False;self.dirty_at=None
        from .renderer_watch import RendererWatch
        self.renderer_watch=RendererWatch();self.stop_reason='';self.preview_was_active=False
        from .host_motion import MotionCertificate
        self.motion_certificate=MotionCertificate(self.app)
        self.session.motion_certificate=self.motion_certificate
        self.session.partial_enabled=self.partial_updates.isChecked()
        self.session.motion_enabled=self.fast_motion.isChecked()
        self.status.setText('檢查通過，正在連接 D5；首次可能需要在 D5 選擇模型關聯。')
        self.buttons()

    def publish(self,replace_pending=False):
        if not self.matches():
            raise RuntimeError("文件已切換，請重新開始同步")
        from formats.igz import save_scene
        from .perf_diagnostics import PerfRecorder
        if self.source_recorder is None or self.source_recorder.directory!=self.session.directory:
            self.source_recorder=PerfRecorder(self.session.directory,journal='source-events.jsonl')
        token = self.schedule.begin()
        self.exporting = True
        publishing_revision=self.session.revision if replace_pending else self.session.revision+1
        trigger=('receiver_need_full' if replace_pending else 'manual_full' if self.force_full_next
                 else 'manual_update' if token[1] else 'initial' if not self.session.revision else 'document_changed')
        self.source_recorder.event('sync_requested',revision=publishing_revision,trigger=trigger,
                                   generation=token[0],published_generation=self.schedule.published_generation,
                                   content_version=getattr(self.app.scene,'content_version',None))
        def begin_export():
            self.progress.begin_revision(publishing_revision)
            self.source_recorder.event('snapshot_begin',revision=publishing_revision,trigger=trigger)
        try:
            result = self.session.publish(self.app.scene, save_scene,force_full=self.force_full_next,replace_pending=replace_pending,on_export=begin_export)
            self.force_full_next=False
            result.setdefault('sync_decision',{})['trigger']=trigger
            if result.get('kind')=='skipped':
                self.source_recorder.event('sync_skipped',revision=result['revision'],**result['sync_decision'])
                self.source_recorder.snapshot('sync-decision-latest.json',{'revision':result['revision'],**result['sync_decision']})
                self.schedule.published(token)
                self.status.setText('內容與已確認版本相同，已略過重複傳送。')
                return result
            self.source_recorder.event('snapshot_published',revision=result['revision'],seconds=result.get('export_seconds'),bytes=result.get('bytes'),kind=result.get('kind'),fallback_reason=result.get('fast_fallback_reason'))
            decision=result.get('sync_decision',{})
            self.source_recorder.snapshot('sync-decision-latest.json',{'revision':result['revision'],**decision})
            self.source_recorder.event('sync_decision',revision=result['revision'],**decision)
            self.last_export = time.monotonic()
            self.schedule.published(token)
            self.allowed_revision = result["revision"]
            self.write_control()
            return result
        except Exception as exc:
            self.source_recorder.event('snapshot_failed',revision=publishing_revision,error=str(exc))
            self.schedule.failed(token, time.monotonic())
            self.progress.failure('快照匯出失敗，保留修改並稍後重試：'+str(exc))
            raise
        finally:
            self.exporting = False

    def document_changed(self):
        if self.motion_certificate:self.motion_certificate.notice()
        if self.app.scene is not self.compact_scene:
            self.compact_materials.setChecked(False)
            self.compact_scene = self.app.scene
        if not (self.running or self.checking):
            return
        if not self.matches():
            self.stop()
            self.status.setText("文件已切換，背景同步已停止；請為目前模型重新開始。")
        else:
            self.schedule.changed(time.monotonic())

    def document_closed(self, scene):
        if scene is self.bound_scene:
            self.stop()

    def write_control(self):
        if self.session:
            write_json(self.session.directory / "control.json", {
                "paused": self.schedule.paused,
                "allowed_revision": self.allowed_revision,
                "retry_request": self.retry_request,
            })

    def toggle_pause(self):
        self.set_paused(not self.schedule.paused)

    def set_paused(self, paused):
        if self.schedule.paused == paused:
            self.buttons()
            return
        self.schedule.paused = paused
        self.settings.setValue("paused", self.schedule.paused)
        self.settings.sync()
        # Cancel an unexported manual request when the user explicitly pauses.
        if self.schedule.paused:
            self.schedule.manual = False
        self.write_control()
        self.status.setText("已暫停：後續修改只累積；已提交的更新會完成。" if self.schedule.paused
                            else "已繼續：將合併傳送最新修改。")
        self.buttons()

    def sync_now(self):
        if self.running:
            if not self.session.pending or self.schedule.dirty:
                self.schedule.request()
            self.retry_request += 1
            self.write_control()

    def sync_full_now(self):
        if self.running:self.force_full_next=True;self.schedule.request()

    def tick(self):
        if not self.process:
            return
        if self.checking:
            code=self.process.poll()
            if code is None:
                if time.monotonic()-self.check_started>120:
                    self.stop(force=True);self.fail('Blender 啟動檢查超過 120 秒。請先確認所選 Blender 能正常開啟。')
                return
            report=read_json(self.session.directory/'preflight.json',{})
            self.checking=False
            if report.get('ok') and code==0:
                try:self.start_worker()
                except Exception as exc:self.fail(exc)
            else:
                self.fail(report.get('message') if report and not report.get('ok') else f'Blender 啟動檢查中斷（代碼 {code}）。請先開啟所選 Blender 確認能正常運作，再按「複製診斷」查看載入錯誤。')
            if not self.running:
                self.timer.stop()
                if self.log_file:self.log_file.close();self.log_file=None
                self.buttons()
            return
        state = read_json(self.session.directory / "status.json", {})
        if self.cancelled:
            if self.process.poll() is not None:
                self.timer.stop()
                if self.log_file:self.log_file.close();self.log_file=None
            self.buttons()
            return
        if self.running and self.renderer_watch and self.renderer_watch.closed(clean_path(self.d5.text()),time.monotonic()):
            if self.source_recorder:
                self.source_recorder.event('d5_closed',revision=self.session.revision,
                                           acknowledged_revision=self.session.acknowledged,pending=self.session.pending)
            if self.session.pending:self.schedule.changed(time.monotonic())
            self.stop(reason='D5 已關閉，同步已停止；尚未確認的修改會在重新連線時重新同步。')
            return
        from .host_activity import preview_active
        editing=preview_active(getattr(self.app,'viewport',None))
        if state:
            try:
                self.session.acknowledge(state)
            except ValueError as exc:
                self.stop()
                self.status.setText(str(exc))
                return
            if (state.get('phase')=='need_full' and state.get('processing_revision')==self.session.revision
                    and read_json(self.session.directory/'latest.json',{}).get('kind') in ('transforms','partial')
                    and not editing and not QApplication.mouseButtons()
                    and QApplication.activeModalWidget() is None
                    and not self.exporting and time.monotonic()>=self.schedule.retry_at):
                try:
                    self.publish(replace_pending=True);self.retry_request+=1;self.write_control()
                except Exception as exc:self.last_error=str(exc)
                return
            message = state.get("message", "")
            scope = state.get("layer_scope")
            if scope:
                message += (f"；圖層：更新{scope.get('normal_root_groups',0)}組、"
                            f"保留{scope.get('held_root_groups',0)}組、排除{scope.get('omitted_root_groups',0)}組")
            if self.schedule.dirty and self.session.pending:
                message += "；已合併新的修改，等待上一版完成。"
            if self.schedule.paused:
                message = "【已暫停自動同步】" + message
                if self.schedule.dirty:
                    message += "；有待更新修改，按「更新」傳送一次或按「繼續」。"
            self.status.setText(message)
            if state.get('phase') in ('error', 'retrying', 'retry_wait'):
                self.last_error=message;self.status.setStyleSheet('color: #c33;')
            else:
                self.status.setStyleSheet('')
            if state.get("phase") in ("sent", "idle") and state.get("revision", 0) > self.last_metrics_revision:
                self.last_metrics_revision = state["revision"]
                from .perf_diagnostics import decision_reason
                self.metrics.setText(
                    ({'transforms':'快速位置更新；','partial':'局部形狀／材質更新；'}.get(state.get('update_kind'),'完整同步；'))+f"第 {state['revision']} 版：來源準備 {state.get('export_seconds', 0):.2f} 秒，"
                    f"Blender 接收 {state['import_seconds']:.2f} 秒，"
                    f"D5 傳送 {state['send_seconds']:.2f} 秒。\n"
                    "官方傳送成功不等於畫面已完成渲染收斂。"+
                    ('\n更新原因：'+decision_reason(state['sync_decision']) if state.get('sync_decision') else ''))
        if self.process.poll() is not None:
            self.running = False
            self.timer.stop()
            if self.cancelled:self.status.setText('背景同步已停止。')
            elif state.get("phase") not in ("error", "stopped"):
                self.fail(f'背景程序已結束（代碼 {self.process.returncode}）；請按「複製診斷」查看啟動錯誤。')
            if self.log_file:
                self.log_file.close()
                self.log_file = None
            self.buttons()
            return
        if not self.running or self.exporting or not self.schedule.ready(
                time.monotonic(), self.delay.value(), self.session.pending):
            return
        if not self.matches():
            self.stop()
            return
        if editing or QApplication.mouseButtons() or QApplication.activeModalWidget() is not None:
            if editing and not self.preview_was_active and self.source_recorder:
                self.source_recorder.event('preview_deferred',tool=type(self.app.viewport.active_tool).__name__)
            self.preview_was_active=editing
            self.schedule.changed_at = time.monotonic()
            return
        self.preview_was_active=False
        now = time.monotonic()
        if now - self.last_export < 1.0:
            return
        try:
            self.publish()
        except Exception as exc:
            self.last_error = str(exc)
            self.status.setText("快照失敗，未傳送；保留修改並稍後重試：" + str(exc))
            self.status.setStyleSheet('color: #c33;')

    def stop(self, force=False,reason=''):
        if self.motion_certificate:self.motion_certificate.close();self.motion_certificate=None
        self.cancelled=True;self.checking=False
        self.stop_reason=reason
        self.progress.stop(reason)
        self.running = False
        self.dirty_at = None
        if self.session:
            try:
                self.session.stop()
            except OSError:
                pass
        process = self.process
        if process and process.poll() is None:
            if force:
                process.terminate()
            else:
                # Terminate only the exact child we own if native D5 connection
                # code is blocked and cannot consume the stop request.
                QTimer.singleShot(3000, lambda p=process: p.terminate() if p.poll() is None else None)
        self.buttons()
        if reason:self.status.setText(reason)

    def shutdown(self):
        self.stop(force=True)

    def open_folder(self):
        path=self.session.directory if self.session else Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'IngeTrazoD5Bridge'
        path.mkdir(parents=True,exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
