# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent project identity, independent from disposable transport sessions."""
import json
import os
import re
from pathlib import Path


def project_directory(parent, project_id):
    if not isinstance(project_id, str) or not re.fullmatch(r'[0-9a-f]{32}', project_id):
        raise ValueError('同步專案識別無效，請重新建立關聯')
    directory=Path(parent)/'projects'/project_id
    directory.mkdir(parents=True,exist_ok=True)
    return directory


class ProjectLease:
    """OS lock releases after a crash. Never allow two workers to save one blend."""
    def __init__(self,directory):
        self.path=Path(directory)/'worker.lock';self.stream=None
    def acquire(self):
        self.stream=self.path.open('a+b');self.stream.seek(0)
        if self.path.stat().st_size==0:self.stream.write(b'0');self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.stream.close();self.stream=None
            raise RuntimeError('同一個專案正在另一個同步程序使用，請先停止那一個同步') from None
        return self
    def close(self):
        if self.stream:
            self.stream.close();self.stream=None


def checkpoint(bpy, path):
    """Atomic snapshot AFTER Sync assigns IDs; keep the D5 source path unchanged."""
    path=Path(path);pending=path.with_name('bridge.checkpoint.blend')
    pending.unlink(missing_ok=True)
    result = bpy.ops.wm.save_as_mainfile(filepath=str(pending),copy=True,check_existing=False,
                                        compress=False,relative_remap=False)
    if result != {'FINISHED'} or not pending.is_file():
        raise RuntimeError('同步已傳送，但橋接識別資料未能保存；原橋接檔保留')
    os.replace(pending,path)
    # Blender backup files, if any, belong solely to this private checkpoint.
    pending.with_suffix('.blend1').unlink(missing_ok=True)


def material_ids(bpy):
    """Our own membership keys survive .blend reload; SDK IDs stay in .d5."""
    import uuid
    ids=set()
    for material in bpy.data.materials:
        if not material.get('ingetrazo_doc'):continue
        key=material.get('bridge_material_id')
        if not key:
            key=uuid.uuid4().hex;material['bridge_material_id']=key
        ids.add(key)
    return ids


def read_material_ids(scene):
    try:return set(json.loads(scene.get('bridge_sent_material_ids','[]')))
    except (TypeError,ValueError):return set()
