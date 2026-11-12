# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only report viewer; independent window, no model mutation actions."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QPlainTextEdit,QPushButton
from .transport import read_json
from .duplicate_models import duplicate_text


class DuplicateReportDialog(QDialog):
    def __init__(self,panel):
        super().__init__(None,Qt.WindowType.Window|Qt.WindowType.WindowTitleHint|Qt.WindowType.WindowSystemMenuHint|Qt.WindowType.WindowMinMaxButtonsHint|Qt.WindowType.WindowCloseButtonHint)
        panel.destroyed.connect(self.deleteLater)
        self.panel=panel;self.setWindowTitle('重複模型辨識');self.resize(720,520);self.setModal(False)
        layout=QVBoxLayout(self);self.text=QPlainTextEdit();self.text.setReadOnly(True);layout.addWidget(self.text)
        row=QHBoxLayout();layout.addLayout(row)
        refresh=QPushButton('重新讀取');refresh.clicked.connect(self.refresh);row.addWidget(refresh)
        export=QPushButton('匯出效能診斷包…');export.clicked.connect(panel.export_performance_bundle);row.addWidget(export)
        close=QPushButton('關閉');close.clicked.connect(self.close);row.addWidget(close)
        self.refresh()

    def refresh(self):
        session=self.panel.session
        report=read_json(session.directory/'duplicate-models-latest.json',{}) if session else {}
        self.text.setPlainText(duplicate_text(report))
