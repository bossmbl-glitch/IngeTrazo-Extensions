# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only native-name conflict review; never renames model data silently."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QLabel,QTableWidget,QTableWidgetItem,
    QPushButton,QApplication,QAbstractItemView)
from .transport import read_json


def review_rows(report,conflict):
    data=report.get('materials',{});rows=[]
    name=conflict.get('name') if conflict else None
    if conflict:
        name=name or '未指定材質'
        rows.append((name,str(len(data[name].get('targets',{}))) if name in data else '待核對',
                     '衝突：'+str(conflict.get('reason') or '無法安全確認材質關聯'),True))
    for material,entry in sorted(data.items()):
        if conflict and material==name:continue
        count=len(entry.get('targets',{}));changed=material in report.get('source_changes',[])
        state='保留舊外觀；新呈現請改名' if changed else '保留舊分支，未合併' if count>1 else '沿用既有識別'
        rows.append((material,str(count),state,False))
    return rows


def review_dialog(panel,report,conflict):
    dialog=QDialog(panel);dialog.setWindowTitle('材質名稱與關聯');dialog.resize(760,400)
    layout=QVBoxLayout(dialog)
    layout.addWidget(QLabel('同名材質預設保留原 D5 關聯與外觀。需要不同呈現時，請在 IngeTrazo 建立新名稱。'))
    rows=review_rows(report,conflict);table=QTableWidget(len(rows),3,dialog);table.setObjectName('material_review_table')
    table.setHorizontalHeaderLabels(['來源材質名稱','既有關聯數','本輪預檢狀態'])
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    # Keep conflicts at the top; interactive column sorting could hide them below normal rows.
    table.setSortingEnabled(False)
    for row,values in enumerate(rows):
        for col,value in enumerate(values[:3]):
            item=QTableWidgetItem(value);item.setToolTip(value)
            if values[3]:
                item.setForeground(QColor('#b42318'));item.setBackground(QColor('#fff0f0'))
                font=item.font();font.setBold(True);item.setFont(font)
            table.setItem(row,col,item)
    table.resizeColumnsToContents();table.horizontalHeader().setStretchLastSection(True);layout.addWidget(table)
    hint='此為本輪預檢紀錄，不代表 D5 畫面已驗收。'
    if conflict:hint+='\n'+str(conflict.get('reason',''))+'；建議名稱：'+str(conflict.get('suggestion',''))
    label=QLabel(hint);label.setTextFormat(Qt.TextFormat.PlainText);label.setWordWrap(True)
    if conflict:label.setStyleSheet('color:#b42318; font-weight:bold')
    layout.addWidget(label)
    if conflict:
        button=QPushButton('複製建議名稱');button.clicked.connect(lambda:QApplication.clipboard().setText(str(conflict.get('suggestion',''))));layout.addWidget(button)
    close=QPushButton('關閉');close.clicked.connect(dialog.accept);layout.addWidget(close)
    return dialog


def show_review(panel):
    root=panel.session.directory if panel.session else None
    report=read_json(root/'named-materials-latest.json',{}) if root else {}
    conflict=read_json(root/'material-conflicts.json',{}) if root else {}
    review_dialog(panel,report,conflict).exec()
