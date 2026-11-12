"""Traditional Chinese batch importer. GPL-3.0-or-later."""
from pathlib import Path
import threading
import time
import traceback

from PySide6.QtCore import Qt, QThread, Signal, Slot, QSettings, QTimer, QEventLoop
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QVBoxLayout, QWidget,
)
from .engine import Cancelled, SUPPORTED_SUFFIXES, commit, find_blender, make_command, stage
from .diagnostics import record


class Worker(QThread):
    progressed = Signal(int, str)

    def __init__(self, filename, options, parent):
        super().__init__(parent)
        self.filename, self.options = filename, options
        self.cancelled = threading.Event()
        self.result = self.error = None

    def run(self):
        record("START " + self.filename)
        try:
            self.result = stage(self.filename, self.options, self.cancelled,
                                lambda f, t: self.progressed.emit(int(f * 100), t))
        except Cancelled as exc:
            self.error = str(exc)
        except Exception as exc:
            self.error = str(exc) + "\n" + traceback.format_exc()
            record("FAILED " + self.filename + "\n" + self.error)


class FileList(QListWidget):
    filesDropped = Signal(object)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.filesDropped.emit([url.toLocalFile() for url in event.mimeData().urls()
                                if url.isLocalFile()])
        event.acceptProposedAction()


class ImportDialog(QDialog):
    def __init__(self, app):
        super().__init__(app.window)
        self.app = app
        self.worker = None
        self.running = False
        self.auto_close = False
        self.stopping = False
        self.pending = []
        self.success = self.failed = 0
        self.phase = "就緒"
        self.file_started = 0.0
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self.show_elapsed)
        self.settings = QSettings("IngeTrazo", "ModelImport")
        self.setWindowTitle("SKP / MAX / CAD 輕鬆匯入 · 0.5.1")
        # Import is a document operation. Block New/Open and other model edits
        # until the batch finishes, including while a parser runs in a worker.
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.resize(720, 670)
        layout = QVBoxLayout(self)
        intro = QLabel("把 .skp / .max / .dwg / .dxf 拖到清單，或按「加入檔案」。\n"
                       "也能直接拖進模型視窗；MAX 與 DWG 會在背景轉換。\n"
                       "CAD 每個檔案自動包成一個群組，保留內部圖層與圖塊。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.controls = QWidget()
        controls = QVBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        self.files = FileList()
        self.files.filesDropped.connect(self.add_paths)
        controls.addWidget(self.files)
        row = QHBoxLayout()
        for text, handler in (("加入檔案…", self.browse_files),
                              ("移除選取", self.remove_files), ("清空", self.files.clear)):
            button = QPushButton(text)
            button.clicked.connect(handler)
            row.addWidget(button)
        controls.addLayout(row)
        self.container = QCheckBox("每個 SKP 包成一個群組，保留內部元件")
        self.container.setChecked(True)
        self.scenes = QCheckBox("匯入 SKP 場景／相機")
        self.annotations = QCheckBox("匯入 SKP 尺寸與文字標註")
        self.zoom = QCheckBox("完成後縮放至全部模型")
        self.zoom.setChecked(True)
        for control in (self.container, self.scenes, self.annotations, self.zoom):
            controls.addWidget(control)
        form = QFormLayout()
        self.blender = QLineEdit(self.settings.value("blender", "") or find_blender())
        br = QHBoxLayout()
        br.addWidget(self.blender)
        choose = QPushButton("選擇…")
        choose.clicked.connect(self.browse_blender)
        br.addWidget(choose)
        form.addRow("Blender 執行檔（MAX 用）", br)
        self.scale = QDoubleSpinBox()
        self.scale.setDecimals(6)
        self.scale.setRange(0.000001, 1000000)
        self.scale.setValue(1.0)
        self.scale.setToolTip("MAX 解析座標轉公尺的倍率：公尺 1、公分 0.01、毫米 0.001。")
        form.addRow("MAX 座標 → 公尺倍率", self.scale)
        self.cad_units = QComboBox()
        for label, scale in (("依檔案單位（未標示用毫米）", None),
                             ("毫米 mm", .001), ("公分 cm", .01), ("公尺 m", 1.0),
                             ("英吋 in", .0254), ("英呎 ft", .3048)):
            self.cad_units.addItem(label, scale)
        index = int(self.settings.value("cad_unit_index", 0) or 0)
        self.cad_units.setCurrentIndex(index if 0 <= index < self.cad_units.count() else 0)
        form.addRow("CAD 圖面單位", self.cad_units)
        controls.addLayout(form)
        note = QLabel("MAX 單位需自行確認：公尺 1／公分 0.01／毫米 0.001。\n"
                      "CAD 文字、標註、填充及部分實體不支援；SKP 使用主程式解析核心。")
        note.setWordWrap(True)
        controls.addWidget(note)
        layout.addWidget(self.controls)
        self.status = QLabel("就緒")
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setPlaceholderText("進度、結果和錯誤原因會列在這裡。")
        layout.addWidget(self.log)
        actions = QHBoxLayout()
        self.start_button = QPushButton("開始匯入")
        self.start_button.clicked.connect(self.start)
        self.cancel_button = QPushButton("取消剩餘匯入")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setEnabled(False)
        save = QPushButton("儲存紀錄…")
        self.save_button = save
        save.clicked.connect(self.save_log)
        close = QPushButton("關閉")
        close.clicked.connect(self.close)
        for button in (self.start_button, self.cancel_button, save, close):
            actions.addWidget(button)
        layout.addLayout(actions)

    def start_dropped(self, paths):
        if self.running:
            return
        self.files.clear()
        self.add_paths(paths)
        if not self.files.count():
            return
        self.auto_close = True
        self.controls.hide()
        self.log.hide()
        self.start_button.hide()
        self.save_button.hide()
        self.resize(540, 160)
        self.show()
        self.raise_()
        self.start()
        if not self.running:
            self.restore_controls()

    def restore_controls(self):
        self.auto_close = False
        for widget in (self.controls, self.log, self.start_button, self.save_button):
            widget.show()
        self.resize(720, 670)

    @Slot(object)
    def add_paths(self, paths):
        if self.running:
            return
        known = {self.files.item(i).text().casefold() for i in range(self.files.count())}
        for p in paths:
            p = str(Path(p).resolve())
            if Path(p).suffix.lower() not in SUPPORTED_SUFFIXES:
                self.log.appendPlainText("略過非 SKP/MAX/DWG/DXF 檔案：" + p)
            elif p.casefold() not in known:
                self.files.addItem(p)
                known.add(p.casefold())

    def browse_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "選擇模型", "",
                                               "模型與 CAD (*.skp *.max *.dwg *.dxf *.SKP *.MAX *.DWG *.DXF)")
        self.add_paths(paths)

    def remove_files(self):
        for item in self.files.selectedItems():
            self.files.takeItem(self.files.row(item))

    def browse_blender(self):
        p, _ = QFileDialog.getOpenFileName(self, "選擇 Blender 執行檔", "", "執行檔 (*)")
        if p:
            self.blender.setText(p)

    def start(self):
        if self.running or not self.files.count():
            return
        scene = self.app.viewport.scene
        if getattr(scene, "_edit_stack", None):
            QMessageBox.information(self, "請先離開元件編輯", "請先結束群組／元件編輯，再匯入模型。")
            return
        self.options = {"scenes": self.scenes.isChecked(),
                        "annotations": self.annotations.isChecked(),
                        "container": self.container.isChecked(),
                        "blender": self.blender.text().strip(),
                        "max_scale": self.scale.value(),
                        "cad_scale": self.cad_units.currentData()}
        self.settings.setValue("blender", self.options["blender"])
        self.settings.setValue("cad_unit_index", self.cad_units.currentIndex())
        self.pending = [self.files.item(i).text() for i in range(self.files.count())]
        self.success = self.failed = 0
        self.running, self.stopping = True, False
        self.controls.setEnabled(False)
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.next_file()

    def next_file(self):
        if self.stopping or not self.pending:
            self.finish()
            return
        filename = self.pending.pop(0)
        self.file_started = time.monotonic()
        self.phase = "讀取模型…"
        self.elapsed_timer.start()
        self.log.appendPlainText("\n開始：" + filename)
        self.progress.setValue(0)
        self.worker = Worker(filename, self.options, self)
        self.worker.progressed.connect(self.set_progress)
        self.worker.finished.connect(self.parsed)
        self.worker.start()

    @Slot(int, str)
    def set_progress(self, value, message):
        self.progress.setValue(max(0, min(100, value)))
        self.phase = {"Parsing .skp (OpenSKP)…": "正在解析 SKP…",
                      "Building geometry…": "正在整理模型幾何，大型檔案可能需數分鐘…",
                      "Done": "處理完成"}.get(message, message)
        self.show_elapsed()

    @Slot()
    def show_elapsed(self):
        seconds = int(time.monotonic() - self.file_started) if self.file_started else 0
        self.status.setText(f"{self.phase}　｜　已用 {seconds // 60} 分 {seconds % 60} 秒")

    @Slot()
    def parsed(self):
        worker = self.worker
        self.worker = None
        if self.stopping:
            self.log.appendPlainText("已取消；目前檔案未加入場景。")
        elif worker.error:
            self.failed += 1
            self.log.appendPlainText("失敗：" + worker.error)
        else:
            self.cancel_button.setEnabled(False)
            self.phase = "建立模型與復原紀錄…（此階段請稍候）"
            self.show_elapsed()
            try:
                def tick(f, text):
                    self.set_progress(int(f * 100), text)
                    QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
                command = make_command(worker.result, self.options, tick)
                commit(self.app.viewport.history, command)
            except Exception:
                self.failed += 1
                detail = "建立模型失敗：\n" + traceback.format_exc()
                self.log.appendPlainText(detail)
                record(detail)
            else:
                self.success += 1
                record("SUCCESS " + str(worker.result["path"]) + " " + str(worker.result.get("geometry", {})))
                self.log.appendPlainText("完成：" + str(worker.result["path"]) + "（Ctrl+Z 可復原）")
                geometry = worker.result.get("geometry", {})
                if geometry:
                    self.log.appendPlainText(f"模型：{geometry['placements']:,} 個群組配置、"
                                             f"{geometry['faces']:,} 個面、{geometry['edges']:,} 條邊。")
                if worker.result.get("note"):
                    self.log.appendPlainText(worker.result["note"])
                    statusbar = getattr(self.app.window, "statusBar", None)
                    if callable(statusbar):
                        statusbar().showMessage(worker.result["note"], 15000)
                if worker.result.get("stats"):
                    stats = worker.result["stats"]
                    self.log.appendPlainText(f"CAD：1 個檔案群組（內含 {stats.get('groups', 0)} 個子群組）、{stats.get('edges', 0)} 邊；"
                                             f"略過項目：{stats.get('skipped', {})}")
                # Display refresh is outside the transaction: a paint error must
                # never falsely report that a committed model was rolled back.
                try:
                    self.app.viewport.notify_scene_changed()
                    self.app.viewport.update()
                except Exception as exc:
                    self.log.appendPlainText("模型已加入，但畫面更新失敗：" + str(exc))
                report = worker.result.get("report", {})
                if report:
                    self.log.appendPlainText(report["note"])
                    for texture in report.get("missing_textures", []):
                        self.log.appendPlainText("缺少貼圖：" + texture)
            self.cancel_button.setEnabled(True)
        worker.deleteLater()
        QTimer.singleShot(0, self.next_file)

    def cancel(self):
        self.stopping = True
        self.cancel_button.setEnabled(False)
        self.phase = "正在取消，等待解析器安全停止…"
        self.show_elapsed()
        if self.worker:
            self.worker.cancelled.set()

    def finish(self):
        self.elapsed_timer.stop()
        self.running = False
        self.controls.setEnabled(True)
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        text = f"成功 {self.success} 個／失敗 {self.failed} 個"
        if self.stopping:
            text += "；剩餘匯入已取消"
        self.status.setText(text)
        self.log.appendPlainText(text)
        self.progress.setValue(100 if not self.stopping else 0)
        if self.success and self.zoom.isChecked():
            zoom = getattr(self.app.window, "_on_zoom_extents", None)
            if callable(zoom):
                zoom()
        if self.auto_close:
            if not self.failed:
                self.hide()
            self.restore_controls()

    def save_log(self):
        name, _ = QFileDialog.getSaveFileName(self, "儲存匯入紀錄", "匯入紀錄.txt", "文字 (*.txt)")
        if name:
            try:
                Path(name).write_text(self.log.toPlainText(), "utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "無法儲存", str(exc))

    def reject(self):
        if self.running:
            self.cancel()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.running:
            self.cancel()
            event.ignore()
        else:
            event.accept()
