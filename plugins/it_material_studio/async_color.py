"""Only pixel work runs off the UI thread; scene/history changes stay in Qt."""
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QObject,Signal
from .textures import tint_texture
_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='material-texture')

class FullColorWorker(QObject):
    ready=Signal(object,object)
    def prepare(self,record,paths,recipe):
        def render():
            return {path:tint_texture({'path':path},recipe['color'],recipe.get('tint_mode',0)) for path in paths}
        job=_pool.submit(render)
        def finished(future):
            try:result=future.result()
            except Exception as exc:result=exc
            try:self.ready.emit(record,result)
            except RuntimeError:pass # the document window was closed
        job.add_done_callback(finished)
