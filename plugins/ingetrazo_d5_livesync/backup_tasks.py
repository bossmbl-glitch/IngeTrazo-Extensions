# SPDX-License-Identifier: GPL-3.0-or-later
"""Qt task runner; never calls host/Blender APIs from a thread."""
from PySide6.QtCore import QThread,Signal

class FileTask(QThread):
    result=Signal(object,str)
    def __init__(self,action,parent=None):super().__init__(parent);self.action=action
    def run(self):
        from .bridge_backups import background_io
        background_io(True)
        try:value=self.action()
        except Exception as exc:self.result.emit(None,str(exc))
        else:self.result.emit(value,'')
        finally:background_io(False)
