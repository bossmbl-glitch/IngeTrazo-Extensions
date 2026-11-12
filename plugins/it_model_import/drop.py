"""Direct viewport drop. GPL-3.0-or-later."""
from pathlib import Path

from PySide6.QtCore import QObject, QEvent, Qt, QTimer
from .engine import SUPPORTED_SUFFIXES


class ViewportDrop(QObject):
    def __init__(self, app, get_dialog):
        super().__init__(app.window)
        self.app = app
        self.get_dialog = get_dialog
        self.queued = False
        for target in (app.window, app.viewport):
            target.setAcceptDrops(True)
            target.installEventFilter(self)

    @staticmethod
    def paths(mime):
        if not mime.hasUrls():
            return []
        urls = mime.urls()
        paths = []
        for url in urls:
            if not url.isLocalFile():
                return []
            p = Path(url.toLocalFile())
            if p.suffix.lower() not in SUPPORTED_SUFFIXES or not p.is_file():
                return []
            paths.append(str(p))
        return list(dict.fromkeys(paths))

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind not in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            return False
        paths = self.paths(event.mimeData())
        if not paths:
            return False  # Preserve native image/model and dock-panel drags.
        dialogs = getattr(self.app.window, "_it_model_import_dialogs", [])
        if self.queued or (dialogs and dialogs[0].running):
            event.ignore()
            return True
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        if kind == QEvent.Type.Drop:
            self.queued = True
            # Return to Qt/OLE before opening dialogs or touching the document.
            QTimer.singleShot(0, lambda: self.load(paths))
        return True  # Never also invoke the host's SKP open/replace handler.

    def load(self, paths):
        self.queued = False
        self.get_dialog().start_dropped(paths)
