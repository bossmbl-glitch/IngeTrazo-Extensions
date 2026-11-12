# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-destructive on-disk storage for Quick Profile user-created profiles."""
from __future__ import annotations
import json
import os
import re
import tempfile
from pathlib import Path
from datetime import datetime, timezone

FORMAT = 'ingetrazo.quick_profile.v1'


def filename_for(name):
    name = str(name).strip()
    if not name or name in ('.', '..') or len(name) > 90:
        raise ValueError('剖面名稱須為 1～90 字。')
    if any(ch in name for ch in '/\\<>:"|?*\0') or name.endswith(('.', ' ')):
        raise ValueError('剖面名稱含 Windows 檔名不允許的字元。')
    if re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', name):
        raise ValueError('剖面名稱是 Windows 保留檔名。')
    return name + '.profile.json'


def _write_atomic(path, data):
    path=Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp=tempfile.mkstemp(prefix='.quick_profile_', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(data,f,ensure_ascii=False,indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def read_owned(path):
    path=Path(path)
    try:
        payload=json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(payload,dict) or payload.get('format')!=FORMAT:
        return None
    if not isinstance(payload.get('name'),str) or not isinstance(payload.get('points'),list):
        return None
    return payload


def find_owned(directory, name):
    directory=Path(directory)
    if not directory.is_dir(): return None
    for path in directory.glob('*.profile.json'):
        data=read_owned(path)
        if data and data['name']==name:
            return path
    return None


def save_profile(directory,name,points, overwrite=False):
    directory=Path(directory)
    if not directory.is_dir():
        raise ValueError('指定資料夾不存在，請先建立資料夾。')
    target=directory / filename_for(name)
    previous=find_owned(directory,name)
    if previous and previous != target:
        target=previous
    if target.exists():
        payload=read_owned(target)
        if payload is None:
            raise FileExistsError('同名檔案已存在但不是本外掛的剖面檔，為避免覆寫已停止。')
        if not overwrite:
            raise FileExistsError('已有相同名稱的剖面，請確認是否覆寫。')
    payload={'format':FORMAT,'name':name,'unit':'mm','points':[[float(x),float(y)] for x,y in points]}
    if len(payload['points'])<3 or len(payload['points'])>2048:
        raise ValueError('剖面至少需要 3 點，最多 2048 點。')
    _write_atomic(target,payload)
    if not target.is_file() or read_owned(target) is None:
        raise OSError('檔案儲存後驗證失敗。')
    return target


def archive_owned(path):
    """Remove a managed file from the browser, keeping a recoverable backup."""
    path=Path(path)
    if read_owned(path) is None:
        raise ValueError('不是本外掛建立的剖面檔，禁止刪除來源。')
    archive=path.parent/'.quick_profile_removed'
    archive.mkdir(exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target=archive/(path.name+'.'+stamp+'.bak')
    path.rename(target)
    return target


def rename_owned(path,new_name):
    path=Path(path)
    old=read_owned(path)
    if old is None:
        raise ValueError('只能重新命名本外掛建立的剖面檔。')
    if old['name']==new_name: return path
    target=save_profile(path.parent,new_name,old['points'],overwrite=False)
    try:
        archive_owned(path)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target
