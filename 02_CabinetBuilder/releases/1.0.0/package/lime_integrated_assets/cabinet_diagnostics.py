"""Local cabinet diagnostics; collecting a report never changes the scene."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from functools import wraps
import json
import hashlib
import math
import os
from pathlib import Path
import platform
import tempfile
import time
import traceback
import uuid
import zipfile
import re

KEY = 'lime_suite_parametric'
SESSION = uuid.uuid4().hex
EVENTS = deque(maxlen=500)
LAST = {}
TIMINGS = {}
MAX_LOG_BYTES = 512 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def log_directory():
    from core.paths import user_log_dir
    return user_log_dir()/'lime_toolbox_diagnostics'


def safe(value, depth=0):
    if depth > 8:
        return '<depth limit>'
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        home = str(Path.home())
        value=value.replace(home,'<user>')
        value=re.sub(r'(?i)[a-z]:[\\/](?:[^\s\"<>|]+[\\/]?)+','<private-path>',value)
        value=re.sub(r'/(?:home|Users|workspace|tmp)/[^\s\"<>]+','<private-path>',value)
        value=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '<private-email>',value)
        return value[:16000]
    if isinstance(value, dict):
        return {str(k):('<omitted>' if str(k).lower() in ('blob_b64','password','access_token','token','secret','authorization') else safe(v,depth+1)) for k,v in list(value.items())[:100]}
    if isinstance(value, (list, tuple, set)):
        return [safe(v, depth+1) for v in list(value)[:200]]
    return safe(str(value), depth+1)


def record(operation, phase='info', details=None, error=None, throttle=0):
    """Best effort logging must never prevent editing, including a full disk."""
    try:
        if isinstance(details,dict) and isinstance(details.get('elapsed_ms'),(int,float)) and math.isfinite(details['elapsed_ms']):
            value=max(0.,details['elapsed_ms']);stats=TIMINGS.setdefault(operation,{'count':0,'total_ms':0.,'max_ms':0.,'last_ms':0.})
            stats['count']+=1;stats['total_ms']+=value;stats['max_ms']=max(stats['max_ms'],value);stats['last_ms']=value
        key = (operation, phase)
        tick = time.monotonic()
        if throttle and error is None and tick-LAST.get(key, -1e30) < throttle:
            return
        LAST[key] = tick
        event = {'time_utc':now(), 'session':SESSION, 'operation':operation,
                 'phase':phase, 'details':safe(details)}
        if error is not None:
            event['error'] = {'type':type(error).__name__, 'message':safe(str(error)),
                              'traceback':safe(''.join(traceback.format_exception(type(error), error, error.__traceback__)))}
        EVENTS.append(event)
        line = json.dumps(event, ensure_ascii=False)+'\n'
        if len(line.encode('utf-8')) > min(65536, MAX_LOG_BYTES):
            event['details'] = '<event details omitted: size limit>'
            if 'error' in event:
                event['error']['traceback'] = event['error']['traceback'][-8000:]
            line = json.dumps(event, ensure_ascii=False)+'\n'
        directory = log_directory()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory/'operations.jsonl'
        if path.exists() and path.stat().st_size+len(line.encode('utf-8')) > MAX_LOG_BYTES:
            path.replace(directory/'operations.previous.jsonl')
        with path.open('a', encoding='utf-8') as stream:
            stream.write(line)
    except Exception:
        pass


def group_summary(group):
    data = {'name':getattr(group, 'name', None), 'uid':getattr(group, 'uid', None),
            'layer':getattr(group, 'layer', None), 'hidden':getattr(group, 'hidden', False),
            'locked':getattr(group, 'locked', False), 'component':getattr(group, 'component', False),
            'parameters':(getattr(group, 'ext', None) or {}).get(KEY), 'parameter_length_unit':'mm'}
    matrix = getattr(group, 'xform', None)
    if matrix is not None:
        data['transform'] = list(matrix.data())
    axes = getattr(group, 'axes', None)
    if axes is not None:
        data['axes'] = list(axes.data())
    mesh = getattr(group, 'mesh', None)
    if mesh is not None:
        data['mesh_counts'] = {k:len(getattr(mesh, k, [])) for k in ('vertices', 'edges', 'faces')}
    return safe(data)


def snapshot(viewport=None, draft=None):
    result = {'draft':safe(draft), 'selected':[]}
    scene = getattr(viewport, 'scene', None)
    if scene is None:
        result['scene_available'] = False
        return result
    result.update(scene_available=True, scene_version=getattr(scene, 'version', None),
                  root_group_count=len(scene.groups), selection_count=len(scene.selection),
                  in_group_edit=getattr(scene, 'edit_group', None) is not None)
    active = getattr(viewport, 'active_tool', None)
    result['active_tool'] = type(active).__name__ if active is not None else None
    # Traverse only selected groups; cap both depth and count for malformed trees.
    pending = [(g, 0) for g in list(scene.selection)[:100]]
    seen = set()
    while pending and len(result['selected']) < 200:
        group, depth = pending.pop(0)
        if id(group) in seen:
            continue
        seen.add(id(group))
        summary = group_summary(group)
        summary['depth'] = depth
        result['selected'].append(summary)
        if depth < 8:
            pending.extend((child, depth+1) for child in (getattr(group, 'children', None) or [])[:100])
    history = getattr(viewport, 'history', None)
    result['history'] = {'undo_count':len(getattr(history, 'undo_stack', [])),
                         'redo_count':len(getattr(history, 'redo_stack', [])),
                         'last_error':getattr(history, 'last_error', None),
                         'last_rejected':getattr(history, 'last_rejected', None)}
    return safe(result)


def export_bundle(destination, version, viewport=None, draft=None):
    """Atomic local ZIP; no network, full scene, textures or screenshot capture."""
    from PySide6.QtCore import qVersion
    try:
        from core.version import __version__ as host_version
    except ImportError:
        host_version = 'unknown'
    record('diagnostics.export', 'start')
    state = snapshot(viewport, draft)
    environment = {'schema_version':2, 'model_schema_version':2, 'build_channel':'release-candidate', 'created_utc':now(), 'session':SESSION,
                   'timings':safe(TIMINGS),'plugin_version':version, 'ingetrazo_version':host_version,
                   'python':platform.python_version(), 'qt':qVersion(),
                   'system':platform.system(), 'system_release':platform.release(),
                   'machine':platform.machine()}
    entry = Path(__file__).resolve().parent.parent/'lime_integrated_tools_ingetrazo.py'
    manifest=entry.parent/'MANIFEST.json'
    if manifest.is_file():environment['package_manifest_sha256']=hashlib.sha256(manifest.read_bytes()).hexdigest()
    if entry.is_file():
        environment['plugin_sha256'] = hashlib.sha256(entry.read_bytes()).hexdigest()
    logs = []
    try:
        directory = log_directory()
        for name in ('operations.previous.jsonl', 'operations.jsonl'):
            path = directory/name
            if path.is_file():
                with path.open('rb') as stream:
                    stream.seek(max(0, path.stat().st_size-MAX_LOG_BYTES))
                    chunk = stream.read(MAX_LOG_BYTES)
                if chunk and not chunk.startswith(b'{'):
                    chunk = chunk.partition(b'\n')[2]
                logs.append(chunk.decode('utf-8', errors='replace'))
    except OSError as exc:
        environment['log_read_error'] = safe(str(exc))
    if not logs:
        logs = [''.join(json.dumps(e, ensure_ascii=False)+'\n' for e in EVENTS)]
    destination = Path(destination)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix='.cabinet-diagnostics-', suffix='.zip', dir=destination.parent)
        os.close(fd)
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, value in (('environment.json', environment), ('current_state.json', state)):
                archive.writestr(name, json.dumps(value, ensure_ascii=False, indent=2))
            sanitized=[]
            for line in ''.join(logs).splitlines():
                try:sanitized.append(json.dumps(safe(json.loads(line)),ensure_ascii=False))
                except ValueError:continue
            archive.writestr('operations.jsonl', '\n'.join(sanitized)+'\n')
            archive.writestr('README.txt', '萊姆整合工具箱診斷包\n包含版本、當下參數與選取摘要、近期操作及錯誤。\n時間為 UTC；沒有填寫欄位。將整個 ZIP 傳回開發對話即可。\n紀錄從安裝此版後開始，涵蓋工具箱的操作。\n不包含完整模型、貼圖或畫面；若是造型問題，可能還需要另附模型。\n')
        os.replace(temporary, destination)
    except Exception as exc:
        record('diagnostics.export', 'error', error=exc)
        raise
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    record('diagnostics.export', 'success')
    return destination


def export_dialog(parent, version, viewport=None, draft=None):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    name = '參數櫃體_診斷包_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.zip'
    path, _ = QFileDialog.getSaveFileName(parent, '匯出診斷包（版本、參數、操作與錯誤；遮蔽私人路徑，不含模型）', name, 'ZIP 診斷包 (*.zip)')
    if not path:
        return
    if not path.lower().endswith('.zip'):
        path += '.zip'
    try:
        export_bundle(path, version, viewport, draft() if callable(draft) else draft)
    except Exception as exc:
        record('diagnostics.export_dialog', 'error', error=exc)
        QMessageBox.warning(parent, '無法匯出診斷包', '請換一個可寫入的位置後重試。\n'+str(exc))
        return
    QMessageBox.information(parent, '診斷包已匯出', '將這個 ZIP 傳回開發對話即可：\n'+path)


def observe(operation, context=None, throttle=0):
    def decorate(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            details = None
            try:
                details = context(*args, **kwargs) if context else None
            except Exception:
                pass
            started=time.perf_counter()
            record(operation, 'start', details, throttle=throttle)
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                record(operation, 'error', {'context':details,'elapsed_ms':(time.perf_counter()-started)*1000}, error=exc)
                raise
            record(operation, 'success', {'context':details,'elapsed_ms':(time.perf_counter()-started)*1000}, throttle=throttle)
            return result
        return wrapped
    return decorate
