"""Selection inspector with explicit undoable repair controls and transient overlays."""
import json
from datetime import datetime
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QPointF
from PySide6.QtGui import QColor, QPen, QVector3D
from PySide6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFileDialog, QMessageBox, QCheckBox, QProgressBar)
from .analysis import scene_sources, snapshot, scan

LIMITS = ('這是幾何檢查，不判斷設計意圖。正常門窗、薄片、開放屋頂也可能有開放邊界。'
          '同一網格內檢查接合；跨群組只比對相同輪廓的重複面，不檢查部分重疊或所有實體穿插。'
          '無法從單一完整平面推斷原本應有的分割線，也不檢查材質、視圖裁切或顯示卡問題。')


class ScanWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)

    def __init__(self, snapshots, tol, parent):
        super().__init__(parent)
        self.snapshots, self.tol = snapshots, tol

    def run(self):
        try:
            result = scan(self.snapshots, self.tol,
                          cancelled=self.isInterruptionRequested,
                          progress=lambda a, b, c: self.progress.emit(a, b, c))
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(f'{type(exc).__name__}: {exc}')


class InspectorDialog(QDialog):
    def __init__(self, app):
        super().__init__(app.window)
        self.app = app
        self.setWindowTitle('選取模型檢查與修正 1.2.0')
        self.resize(1020, 760)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.result = None
        self.worker = None
        self.token = None
        self.busy = False
        self.cancelled = False
        self.active_issue = None
        self.visible_issues = []
        layout = QVBoxLayout(self)
        intro = QLabel('先在畫面選取模型群組或元件，再按「檢查選取模型」。可多選，只檢查選取模型及其內部零件。')
        intro.setWordWrap(True); layout.addWidget(intro)
        row = QHBoxLayout(); layout.addLayout(row)
        row.addWidget(QLabel('範圍：僅選取的模型'), 1)
        row.addWidget(QLabel('接合／平面容差'))
        self.tol = QDoubleSpinBox(); self.tol.setRange(.1, 10); self.tol.setDecimals(2)
        self.tol.setValue(.5); self.tol.setSuffix(' mm'); row.addWidget(self.tol)
        self.start_button = QPushButton('檢查選取模型'); self.start_button.clicked.connect(self.start_scan)
        row.addWidget(self.start_button)
        self.cancel_button = QPushButton('取消'); self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel); row.addWidget(self.cancel_button)
        row = QHBoxLayout(); layout.addLayout(row)
        row.addWidget(QLabel('自動修正：'))
        self.fix_topology = QCheckBox('補缺線／修正面邊連結'); self.fix_topology.setChecked(True)
        self.fix_duplicates = QCheckBox('移除完全重複面'); self.fix_duplicates.setChecked(True)
        self.fix_sharp = QCheckBox('顯示隱藏折角線'); self.fix_sharp.setChecked(True)
        self.fix_bad_faces = QCheckBox('修正重複頂點／退化面'); self.fix_bad_faces.setChecked(True)
        for option in (self.fix_topology,self.fix_duplicates,self.fix_sharp,self.fix_bad_faces): row.addWidget(option)
        row.addStretch()
        row = QHBoxLayout(); layout.addLayout(row)
        self.fix_holes = QCheckBox('補小型封閉破洞（可能封住刻意開口）')
        row.addWidget(self.fix_holes)
        row.addWidget(QLabel('最大跨距'))
        self.hole_size = QDoubleSpinBox(); self.hole_size.setRange(.1,5000)
        self.hole_size.setValue(50); self.hole_size.setSuffix(' mm'); row.addWidget(self.hole_size)
        row.addStretch()
        self.repair_button = QPushButton('自動修正選取模型')
        self.repair_button.clicked.connect(self.auto_repair); row.addWidget(self.repair_button)
        repair_note = QLabel('修正後會重新檢查；Ctrl+Z 一次復原。共享元件僅修改選取副本。補洞只處理簡單共平面封閉環，略過既有面內孔。')
        repair_note.setWordWrap(True); layout.addWidget(repair_note)
        self.summary = QLabel('尚未檢查。請先點選要檢查的模型；沒有選取模型時不會開始掃描。')
        self.summary.setWordWrap(True); layout.addWidget(self.summary)
        self.progress = QProgressBar(); self.progress.hide(); layout.addWidget(self.progress)
        row = QHBoxLayout(); layout.addLayout(row)
        self.filter = QComboBox(); self.filter.addItem('全部類型')
        self.filter.currentTextChanged.connect(self.populate); row.addWidget(self.filter, 1)
        self.mark = QCheckBox('顯示目前問題標記'); self.mark.setChecked(True)
        self.mark.toggled.connect(lambda _: app.viewport.update()); row.addWidget(self.mark)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['判定', '問題類型', '群組／位置'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 75); self.table.setColumnWidth(1, 190)
        self.table.itemSelectionChanged.connect(self.choose)
        self.table.cellDoubleClicked.connect(lambda *_: self.focus_issue())
        layout.addWidget(self.table, 1)
        self.detail = QLabel(''); self.detail.setWordWrap(True)
        self.detail.setMinimumHeight(58); layout.addWidget(self.detail)
        row = QHBoxLayout(); layout.addLayout(row)
        self.focus_button = QPushButton('放大到問題'); self.focus_button.clicked.connect(self.focus_issue)
        row.addWidget(self.focus_button)
        clear = QPushButton('清除標記'); clear.clicked.connect(self.clear_mark); row.addWidget(clear)
        self.export_button = QPushButton('匯出檢查報告…'); self.export_button.clicked.connect(self.export_report)
        row.addWidget(self.export_button); row.addStretch()
        close = QPushButton('關閉'); close.clicked.connect(self.close); row.addWidget(close)
        note = QLabel(LIMITS); note.setWordWrap(True); note.setStyleSheet('color: #777; font-size: 11px;')
        layout.addWidget(note)
        app.add_overlay(self.draw_overlay)
        self.timer = QTimer(self); self.timer.setInterval(400)
        self.timer.timeout.connect(self.check_stale); self.timer.start()
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        self.set_busy(False)

    def scene_token(self):
        s = self.app.scene
        return (id(s), s.content_version, id(s.edit_group))

    def fresh(self):
        return self.token == self.scene_token()

    def set_busy(self, busy):
        self.busy = busy
        self.start_button.setEnabled(not busy)
        self.repair_button.setEnabled(not busy)
        for option in (self.fix_topology,self.fix_duplicates,self.fix_sharp,self.fix_bad_faces,self.fix_holes,self.hole_size):
            option.setEnabled(not busy)
        self.tol.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.export_button.setEnabled(not busy and self.result is not None and self.fresh())
        self.focus_button.setEnabled(not busy and self.result is not None and self.fresh())
        self.progress.setVisible(busy)

    def start_scan(self, repair_note=''):
        if self.worker is not None or self.busy: return
        self.repair_note = repair_note if isinstance(repair_note,str) else ''
        self.clear_mark()
        self.result = None; self.table.setRowCount(0)
        self.token = self.scene_token(); self.cancelled = False
        self.scope_text = '僅選取的模型群組／元件（含內部零件）'
        self.scan_tolerance = self.tol.value()
        self.sources = iter(scene_sources(self.app.scene, tuple(self.app.scene.selection)))
        self.snapshots = []
        self.summary.setText('正在讀取幾何…')
        self.progress.setRange(0, 0); self.set_busy(True)
        QTimer.singleShot(0, self.capture_next)

    def capture_next(self):
        if self.cancelled or not self.fresh():
            self.fail('已取消或模型已改變；請重新檢查。'); return
        try:
            mesh, matrix, name = next(self.sources)
            self.snapshots.append(snapshot(mesh, matrix, name))
            self.summary.setText(f'正在讀取第 {len(self.snapshots)} 個網格：{name}')
            QTimer.singleShot(0, self.capture_next)
        except StopIteration:
            self.sources = None
            if not self.snapshots:
                self.fail('檢查範圍沒有可掃描的面或邊線。'); return
            self.worker = ScanWorker(self.snapshots, self.scan_tolerance/1000, self)
            self.worker.progress.connect(self.on_progress)
            self.worker.completed.connect(self.scan_finished)
            self.worker.failed.connect(self.fail)
            self.worker.finished.connect(self.worker_stopped)
            self.worker.start()
        except Exception as exc:
            self.fail(str(exc))

    def on_progress(self, value, total, name):
        self.progress.setRange(0, total); self.progress.setValue(value)
        self.summary.setText(f'檢查 {value+1} / {total}：{name}')

    def worker_stopped(self):
        if self.worker:
            self.worker.deleteLater(); self.worker = None
        self.snapshots = []
        self.set_busy(False)

    def scan_finished(self, result):
        if not self.fresh():
            self.fail('掃描期間模型已變更，結果已捨棄；請重新檢查。'); return
        self.result = result
        self.filter.blockSignals(True); self.filter.clear(); self.filter.addItem('全部類型')
        self.filter.addItems(sorted({i.code for i in result.issues})); self.filter.blockSignals(False)
        self.populate()
        count = len(result.issues)
        headline = f'{"檢查完成" if result.complete else "部分檢查"}：{result.meshes} 個網格、{result.faces} 個面、{result.edges} 條邊；{count} 處異常或待確認。'
        if not count: headline += ' 本次規則未發現問題，不代表設計與外觀完全正確。'
        if result.notes: headline += '\n' + '\n'.join(result.notes)
        if self.repair_note: headline = self.repair_note + '\n' + headline
        self.summary.setText(headline)
        self.set_busy(False)

    def auto_repair(self):
        if self.busy or self.worker is not None: return
        from .repair import RepairModelsCommand, RepairOptions
        options = RepairOptions(self.fix_topology.isChecked(), self.fix_duplicates.isChecked(),
                                self.fix_sharp.isChecked(), self.fix_holes.isChecked(), self.hole_size.value(),self.fix_bad_faces.isChecked())
        self.set_busy(True)
        self.summary.setText('正在準備選取模型的修正…')
        self.summary.repaint()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        note = None
        try:
            cmd = RepairModelsCommand(self.app.scene, tuple(self.app.scene.selection), options)
            if not cmd.items:
                self.summary.setText(cmd.summary())
                return
            self.app.viewport.history.execute(cmd)
            error = self.app.viewport.history.last_error or self.app.viewport.history.last_rejected
            if error: raise RuntimeError(error)
            self.app.viewport.notify_scene_changed()
            note = cmd.summary()
        except Exception as exc:
            self.summary.setText('無法自動修正：' + str(exc))
        finally:
            QApplication.restoreOverrideCursor()
            self.set_busy(False)
        if note:
            self.clear_mark()
            self.start_scan(note)

    def fail(self, message):
        self.result = None; self.sources = None; self.snapshots = []
        self.summary.setText(message)
        self.set_busy(False)

    def cancel(self):
        self.cancelled = True
        if self.worker: self.worker.requestInterruption()

    def check_stale(self):
        if self.result is not None and not self.fresh():
            self.clear_mark()
            self.summary.setText('模型或編輯範圍已改變，這份結果已過期。請選取模型，再按「檢查選取模型」。')
            self.focus_button.setEnabled(False); self.export_button.setEnabled(False)

    def populate(self, *_):
        self.clear_mark()
        text = self.filter.currentText()
        self.visible_issues = [i for i in (self.result.issues if self.result else []) if text == '全部類型' or i.code == text]
        self.table.setRowCount(len(self.visible_issues))
        for row, issue in enumerate(self.visible_issues):
            for col, value in enumerate([issue.level, issue.code, issue.name]):
                item = QTableWidgetItem(value)
                item.setToolTip(issue.detail)
                if col == 0: item.setForeground(QColor('#c83b32' if issue.level == '錯誤' else '#a56b12'))
                self.table.setItem(row, col, item)

    def choose(self):
        row = self.table.currentRow()
        self.active_issue = self.visible_issues[row] if 0 <= row < len(self.visible_issues) and self.fresh() else None
        issue = self.active_issue
        description = issue.detail if issue else ''
        if issue and len(issue.segments) > 12000:
            description += ' 此項超過 12,000 條邊，畫面僅標記前 12,000 條；報告保留全部座標。'
        self.detail.setText(description)
        self.mark.setChecked(True)
        self.app.viewport.update()

    def clear_mark(self):
        self.active_issue = None
        self.detail.setText('')
        self.app.viewport.update()

    def focus_issue(self):
        if not self.active_issue: self.choose()
        issue = self.active_issue
        if not issue or not self.fresh() or not issue.points: return
        p = np.asarray(issue.points)
        lo, hi = p.min(axis=0), p.max(axis=0)
        pad = max(float(np.linalg.norm(hi-lo))*.1, .015)
        self.app.viewport.camera.fit_box(QVector3D(*(lo-pad)), QVector3D(*(hi+pad)), margin=1.25)
        self.app.viewport.update()

    def draw_overlay(self, viewport, painter):
        issue = self.active_issue
        if not self.isVisible() or not self.mark.isChecked() or not issue or not self.fresh() or not issue.points:
            return
        # Draw through surfaces deliberately; this is a diagnostic overlay, not scene geometry.
        segs = issue.segments[:12000]
        if segs:
            pts = np.asarray(segs).reshape((-1, 3))
            x, y, front = self.app.world_to_pixels(pts)
            for color, width in [('#ffffff', 5), ('#e53377', 2.5)]:
                pen = QPen(QColor(color)); pen.setWidthF(width); painter.setPen(pen)
                for i in range(0, len(pts), 2):
                    if front[i] and front[i+1]:
                        painter.drawLine(QPointF(float(x[i]), float(y[i])), QPointF(float(x[i+1]), float(y[i+1])))
        center = np.asarray(issue.points).mean(axis=0)
        x, y, front = self.app.world_to_pixels([center])
        if front[0]:
            painter.setPen(QPen(QColor('#e53377'), 2))
            painter.drawEllipse(QPointF(float(x[0]), float(y[0])), 7, 7)
            painter.drawText(QPointF(float(x[0])+12, float(y[0])-8), issue.code)

    def export_report(self):
        if self.result is None or not self.fresh(): return
        path, _ = QFileDialog.getSaveFileName(self, '匯出模型檢查報告', '模型檢查報告.json', 'JSON (*.json)')
        if not path: return
        try:
            if not path.lower().endswith('.json'): path += '.json'
            Path(path).write_text(json.dumps(self.report(), ensure_ascii=False, indent=2), encoding='utf-8')
            self.summary.setText(self.summary.text() + '\n報告已儲存：' + path)
        except Exception as exc:
            QMessageBox.warning(self, '匯出失敗', str(exc))

    def report(self):
        return dict(plugin='it_model_inspector', version='1.2.0',
                    generated_at=datetime.now().astimezone().isoformat(), scope=self.scope_text,
                    tolerance_mm=self.scan_tolerance, complete=self.result.complete,
                    meshes=self.result.meshes, faces=self.result.faces, edges=self.result.edges,
                    limits=LIMITS, notes=self.result.notes, repair_summary=self.repair_note,
                    issues=[i.record() for i in self.result.issues])

    def closeEvent(self, event):
        self.cancel(); self.clear_mark(); super().closeEvent(event)

    def shutdown(self):
        self.cancel()
        if self.worker: self.worker.wait()
