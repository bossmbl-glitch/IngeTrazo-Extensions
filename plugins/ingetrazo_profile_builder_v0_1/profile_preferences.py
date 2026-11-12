# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-source, explicitly saved profile settings; never edits library geometry."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
from .last_used_state import validate

VERSION = 2


def identity(source, name, path=None, subname=None):
    """Stable for identical names in different source folders."""
    if source in ('external', 'skp') and path:
        return json.dumps([source, str(Path(path).expanduser().resolve()).casefold(), subname or name], ensure_ascii=False)
    return json.dumps(['saved', name], ensure_ascii=False)


def read(path):
    path = Path(path)
    if not path.exists():
        return {'version': VERSION, 'last_key': None, 'profiles': {}}
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('version') != VERSION or not isinstance(value.get('profiles'), dict):
        raise ValueError('個別剖面設定格式錯誤。')
    profiles = {}
    for key, saved in value['profiles'].items():
        if isinstance(key, str):
            try:
                profiles[key] = validate(saved)
            except (ValueError, KeyError, TypeError):
                continue
    last_key = value.get('last_key')
    return {'version': VERSION, 'last_key': last_key if isinstance(last_key, str) else None, 'profiles': profiles}


def write(path, state):
    path = Path(path)
    clean = {'version': VERSION, 'last_key': state.get('last_key'),
             'profiles': {str(k): validate(v) for k, v in state.get('profiles', {}).items()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.profile_preferences_', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def move(state, old_key, new_key, new_name=None):
    if old_key == new_key:
        return False
    if old_key not in state['profiles']:
        return False
    if new_key in state['profiles']:
        raise FileExistsError('新名稱已經有獨立保存的參數，為避免覆蓋已保留原設定。')
    snap = dict(state['profiles'].pop(old_key))
    if new_name:
        snap['profile'] = new_name
    state['profiles'][new_key] = snap
    if state.get('last_key') == old_key:
        state['last_key'] = new_key
    return True


def forget(state, key):
    state['profiles'].pop(key, None)
    if state.get('last_key') == key:
        state['last_key'] = None
