# SPDX-License-Identifier: GPL-3.0-or-later
"""Compact only this plugin's toolbar, independent from host toolbar sizing."""
from PySide6.QtCore import QSize,Qt
from PySide6.QtWidgets import QToolButton


def compact_toolbar(toolbar):
    toolbar.setIconSize(QSize(16,16))
    toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    toolbar.setStyleSheet('''
QToolBar#ingetrazo_d5_livesync_toolbar { spacing:1px; padding:1px; }
QToolBar#ingetrazo_d5_livesync_toolbar QToolButton {
    min-width:20px; max-width:20px; min-height:20px; max-height:20px;
    margin:0px; padding:2px; border:1px solid transparent; border-radius:3px;
}
QToolBar#ingetrazo_d5_livesync_toolbar QToolButton:hover { border-color:#a7b2bd; }
QToolBar#ingetrazo_d5_livesync_toolbar QToolButton:checked {
    background-color:#eda13b; color:#201500; border-color:#d78c25;
}
QToolBar#ingetrazo_d5_livesync_toolbar::separator { width:4px; margin:2px; }
''')
    if toolbar.layout():
        toolbar.layout().setContentsMargins(2,1,2,1)
        toolbar.layout().setSpacing(1)
    for action in toolbar.actions():
        button=toolbar.widgetForAction(action)
        if not isinstance(button,QToolButton):continue
        button.setIconSize(QSize(16,16));button.setFixedSize(26,26)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setAccessibleName(action.text())
        # Distinguish startup from the adjacent resume play icon at small size.
        if action.objectName()=='ingetrazo_d5_start':
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            font=button.font();font.setPointSize(9);font.setBold(True);button.setFont(font)
