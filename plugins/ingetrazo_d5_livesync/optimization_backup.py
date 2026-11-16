# SPDX-License-Identifier: GPL-3.0-or-later
"""Preserve our bridge IDs before opt-in many-to-one material migration.
This does not back up a D5 project or D5's unsaved material edits.
"""
from pathlib import Path
import datetime
import hashlib
import json
import os
import shutil
import uuid


def backup_bridge_identity(project,baseline,version='0.9.2',purpose='bridge identity before material compaction'):
    project=Path(project).resolve();baseline=Path(baseline).resolve()
    if not baseline.is_relative_to(project) or not baseline.is_file():
        raise RuntimeError('備份前的橋接基準無效，已停止')
    bridge=project/'bridge.blend'
    if not bridge.is_file():raise RuntimeError('備份前找不到bridge.blend，已停止')
    parent=project/'optimization-backups';parent.mkdir(exist_ok=True)
    name='before-'+version+'-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
    stage=parent/(name+'.pending');target=parent/name;stage.mkdir()
    records=[]
    try:
        for source in (bridge,baseline):
            relative=source.relative_to(project);destination=stage/relative;destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,destination)
            digest=hashlib.sha256(destination.read_bytes()).hexdigest()
            if digest!=hashlib.sha256(source.read_bytes()).hexdigest():raise RuntimeError('橋接備份驗證失敗')
            records.append({'path':relative.as_posix(),'sha256':digest,'bytes':destination.stat().st_size})
        metadata={'version':version,'purpose':purpose,'d5_project_included':False,'files':records}
        with (stage/'backup.json').open('w',encoding='utf-8') as stream:
            json.dump(metadata,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(stage,target)
    except Exception:
        shutil.rmtree(stage,ignore_errors=True);raise
    return str(target)
