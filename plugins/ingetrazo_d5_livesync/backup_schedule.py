# SPDX-License-Identifier: GPL-3.0-or-later
"""Persisted minute preferences and half-hour deadlines; no SDK/UI dependencies."""
import datetime as dt
try:from . import bridge_backups as b
except ImportError:import bridge_backups as b
SETTINGS='backup-settings.json'
DEFAULT_MINUTES=10

def settings(project):
    path=b.inside(project,SETTINGS)
    if not path.exists():return {'format':1,'minutes':DEFAULT_MINUTES}
    return clean(b.read_json(path))

def clean(value):
    if not isinstance(value,dict) or type(value.get('minutes')) is not int or not 1<=value['minutes']<=1440:
        raise ValueError('備份間隔需為 1～1440 分鐘')
    return {'format':1,'minutes':value['minutes']}

def save_settings(project,minutes):
    value=clean({'minutes':minutes});b.atomic_json(b.inside(project,SETTINGS),value);return value

def half_hour(now):return int(now.timestamp())//1800

def due(now,last_at,last_slot,minutes):
    slot=half_hour(now)
    protected=last_slot!=slot
    ordinary=last_at is None or (now-last_at).total_seconds()>=minutes*60
    return ordinary or protected,slot if protected else None
