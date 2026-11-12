# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicitly saved last-used tool state. Never writes to the profile library."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import tempfile

STATE_VERSION = 1
PARAM_KEYS = ('width_mm', 'height_mm', 'rotate_deg', 'mirror', 'off_u_mm',
              'off_v_mm', 'miter', 'anchor', 'closed_side', 'reverse_depth')
BOOLEANS = {'mirror', 'miter', 'reverse_depth'}
NUMBERS = {'width_mm', 'height_mm', 'rotate_deg', 'off_u_mm', 'off_v_mm'}
STRINGS = {'anchor', 'closed_side'}


def validate(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get('version') != STATE_VERSION:
        raise ValueError('使用狀態版本無效。')
    profile = snapshot.get('profile')
    if not isinstance(profile, str) or not profile.strip() or len(profile) > 90:
        raise ValueError('剖面名稱無效。')
    points = snapshot.get('section')
    if not isinstance(points, list) or not 3 <= len(points) <= 2048:
        raise ValueError('剖面座標數量無效。')
    normalized = []
    for p in points:
        if not isinstance(p, (list, tuple)) or len(p) != 2 or any(type(v) not in (float, int) or not math.isfinite(v) for v in p):
            raise ValueError('剖面座標資料無效。')
        normalized.append([float(p[0]), float(p[1])])
    options = snapshot.get('settings')
    if not isinstance(options, dict) or any(k not in options for k in PARAM_KEYS):
        raise ValueError('操作設定不完整。')
    for key in NUMBERS:
        v = options[key]
        if type(v) not in (int, float) or not math.isfinite(v):
            raise ValueError(f'{key} 數值無效。')
    for key in BOOLEANS:
        if type(options[key]) is not bool:
            raise ValueError(f'{key} 設定無效。')
    for key in STRINGS:
        if not isinstance(options[key], str) or len(options[key]) > 80:
            raise ValueError(f'{key} 設定無效。')
    if not (0 < options['width_mm'] <= 100000 and 0 < options['height_mm'] <= 100000):
        raise ValueError('剖面尺寸超出範圍。')
    for key in ('material', 'tag'):
        if not isinstance(snapshot.get(key), str) or len(snapshot[key]) > 255:
            raise ValueError(f'{key} 設定無效。')
    return {'version': STATE_VERSION, 'profile': profile, 'section': normalized,
            'settings': {k: options[k] for k in PARAM_KEYS},
            'material': snapshot['material'], 'tag': snapshot['tag']}


def store(path, snapshot):
    path = Path(path)
    safe = validate(snapshot)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement, so crashes cannot leave a half-written settings file.
    handle, tmp = tempfile.mkstemp(prefix='.last_used_', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as writer:
            json.dump(safe, writer, ensure_ascii=False, indent=2)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def load(path):
    path = Path(path)
    if not path.exists():
        return None
    return validate(json.loads(path.read_text(encoding='utf-8')))
