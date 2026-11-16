# SPDX-License-Identifier: GPL-3.0-or-later
"""Verified, project-scoped recovery points. Call mutations under ProjectLease.

No D5 project is included. Only a confirmed bridge/scope pair is restorable.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

VERSION='0.9.10'
STATE='bridge-state.json'
CHECKPOINT='checkpoint-state.pending.json'
RESTORE='restore-transaction.json'
ROOT='bridge-backups'


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def inside(root,relative):
    root=Path(root).resolve();relative=Path(relative)
    if relative.is_absolute() or '..' in relative.parts:raise ValueError('備份路徑無效')
    path=(root/relative).resolve()
    if not path.is_relative_to(root) or path==root:raise ValueError('備份路徑超出專案')
    return path


def project_info(project):
    project=Path(project).resolve()
    if not re.fullmatch('[0-9a-f]{32}',project.name):raise ValueError('橋接專案識別無效')
    return project,project.name


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.pending',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:Path(name).unlink(missing_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def copy_verified(source,target):
    source=Path(source);target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    expected=digest(source)
    with source.open('rb') as src,target.open('wb') as dst:
        shutil.copyfileobj(src,dst,1024*1024);dst.flush();os.fsync(dst.fileno())
    if digest(source)!=expected or digest(target)!=expected:raise OSError('備份檔案複製驗證失敗')
    return {'sha256':expected,'bytes':target.stat().st_size}


def validate_state(project,state,blend=None,files=True):
    project,pid=project_info(project)
    if not isinstance(state,dict):raise ValueError('確認紀錄格式無效')
    if (state.get('format')!=1 or state.get('receipt_confirmed') is not True or state.get('project_id')!=pid
            or os.path.normcase(state.get('project_directory',''))!=os.path.normcase(str(project))):
        raise ValueError('還原點不屬於目前專案或原始路徑')
    relative=state.get('scope_file','')
    if not re.fullmatch(r'scope-cache/scope-[0-9a-f]{64}\.igz',relative):raise ValueError('確認快照路徑無效')
    if Path(relative).stem[6:]!=state.get('scope_sha256'):raise ValueError('確認快照識別與雜湊不符')
    for key in ('blend_sha256','scope_sha256'):
        if not re.fullmatch('[0-9a-f]{64}',state.get(key,'')):raise ValueError('確認雜湊格式無效')
    if files:
        if Path(blend or inside(project,'bridge.blend')).stat().st_size==0 or inside(project,relative).stat().st_size==0:
            raise ValueError('確認配對檔案為空')
        if digest(blend or inside(project,'bridge.blend'))!=state['blend_sha256']:raise ValueError('橋接檔與確認紀錄不符')
        if digest(inside(project,relative))!=state['scope_sha256']:raise ValueError('確認快照損壞')
    return state


def build_state(project,scope,metadata=None,blend=None):
    project,pid=project_info(project);scope=Path(scope).resolve()
    if not scope.is_relative_to(project):raise ValueError('確認快照不屬於本專案')
    state={'format':1,'project_id':pid,'project_directory':str(project),
           'receipt_confirmed':True,
           'scope_file':scope.relative_to(project).as_posix(),'scope_sha256':digest(scope),
           'blend_sha256':digest(blend or inside(project,'bridge.blend')),
           'confirmed_at':dt.datetime.now(dt.timezone.utc).isoformat(),**(metadata or {})}
    return validate_state(project,state,blend=blend)


def current_state(project):return validate_state(project,read_json(inside(project,STATE)))


def bootstrap_state(project,scope,metadata):
    # Worker only: it has already opened the blend and checked project identity,
    # receipt, scope pointer and hash. Never silently bless a mismatched marker.
    if inside(project,STATE).exists():return current_state(project)
    state=build_state(project,scope,{**metadata,'legacy_import':True})
    atomic_json(inside(project,STATE),state);return state


def prepare_checkpoint(project,scope,metadata,pending_blend):
    state=build_state(project,scope,metadata,blend=pending_blend)
    atomic_json(inside(project,CHECKPOINT),state)


def recover_checkpoint(project):
    journal=inside(project,CHECKPOINT)
    if not journal.exists():return False
    state=read_json(journal);validate_state(project,state,files=False)
    bridge=inside(project,'bridge.blend');pending=inside(project,'bridge.checkpoint.blend')
    if not bridge.is_file() or digest(bridge)!=state['blend_sha256']:
        validate_state(project,state,blend=pending)
        os.replace(pending,bridge)
    validate_state(project,state)
    atomic_json(inside(project,STATE),state);journal.unlink();return True


def _manifest(project,path,verify):
    project,pid=project_info(project);path=Path(path)
    if path.is_symlink():raise ValueError('還原點不能是符號連結')
    path=path.resolve()
    if path.parent!=inside(project,ROOT) or not path.name.startswith('point-'):raise ValueError('還原點路徑無效')
    m=read_json(path/'backup.json')
    if not isinstance(m,dict):raise ValueError('還原點紀錄格式無效')
    if m.get('format')!=1 or m.get('kind')!='confirmed':raise ValueError('不是已確認還原點')
    state=m['state'];validate_state(project,state,files=False)
    expected={'bridge.blend':state['blend_sha256'],state['scope_file']:state['scope_sha256'],STATE:None}
    records=m.get('files',[])
    if (not isinstance(records,list) or len(records)!=3 or any(not isinstance(r,dict) for r in records)
            or {r.get('path') for r in records}!=set(expected)):raise ValueError('還原點缺少配對檔案')
    if (type(m.get('pinned')) is not bool or type(m.get('daily')) is not bool
            or not isinstance(m.get('reasons'),list) or any(not isinstance(r,str) for r in m['reasons'])):
        raise ValueError('還原點保留規則格式無效')
    key=hashlib.sha256((state['blend_sha256']+state['scope_sha256']).encode()).hexdigest()
    if m.get('state_key')!=key:raise ValueError('還原點配對識別不符')
    for r in records:
        f=inside(path,r['path'])
        if not f.is_file() or f.stat().st_size!=r['bytes']:raise ValueError('還原點檔案遺失或大小不符')
        if expected[r['path']] is not None and r['sha256']!=expected[r['path']]:raise ValueError('還原點雜湊不符')
        if verify and digest(f)!=r['sha256']:raise ValueError('還原點完整性驗證失敗')
    if read_json(path/STATE)!=state:raise ValueError('還原點配對紀錄不符')
    if not re.fullmatch(r'\d{4}-\d\d-\d\d',m.get('local_day','')):raise ValueError('還原點日期無效')
    dt.datetime.fromisoformat(m['created_at'])
    return m


def list_points(project,verify=False):
    root=inside(project,ROOT)
    if not root.exists():return []
    rows=[]
    for path in root.glob('point-*'):
        if not path.is_dir() or path.name.endswith('.pending'):continue
        try:m=_manifest(project,path,verify);rows.append({**m,'path':str(path),'valid':True,'verified':verify})
        except (OSError,ValueError,KeyError,TypeError) as exc:rows.append({'path':str(path),'valid':False,'error':str(exc)})
    return sorted(rows,key=lambda r:r.get('created_at',''),reverse=True)


def verify_point(project,point):return _manifest(project,point,True)


def create_point(project,reasons=(),pinned=False,now=None):
    project,pid=project_info(project);state=current_state(project)
    now=now or dt.datetime.now().astimezone();day=now.date().isoformat()
    rows=list_points(project);valid=[r for r in rows if r['valid']]
    state_key=hashlib.sha256((state['blend_sha256']+state['scope_sha256']).encode()).hexdigest()
    daily=True
    for r in valid:
        if not r.get('daily') or r['local_day']!=day:continue
        try:_manifest(project,r['path'],True)
        except (OSError,ValueError,KeyError,TypeError):continue
        daily=False;break
    for r in valid:
        if r.get('state_key')==state_key and r['local_day']==day:
            path=Path(r['path'])
            try:m=_manifest(project,path,True)
            except (OSError,ValueError,KeyError,TypeError):continue
            m['reasons']=sorted(set(m['reasons'])|set(reasons));m['pinned']=m['pinned'] or pinned;m['daily']=m['daily'] or daily
            atomic_json(path/'backup.json',m);return path
    root=inside(project,ROOT);root.mkdir(exist_ok=True)
    name='point-'+now.astimezone(dt.timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
    stage=root/(name+'.pending');target=root/name;stage.mkdir()
    try:
        records=[]
        for relative in ('bridge.blend',state['scope_file'],STATE):
            records.append({'path':relative,**copy_verified(inside(project,relative),inside(stage,relative))})
        m={'format':1,'kind':'confirmed','created_at':now.astimezone(dt.timezone.utc).isoformat(),'local_day':day,
           'state_key':state_key,'state':state,'files':records,'pinned':bool(pinned),'daily':daily,
           'reasons':sorted(set(reasons)),'d5_project_included':False}
        atomic_json(stage/'backup.json',m);validate_state(project,state)
        os.replace(stage,target);_manifest(project,target,True)
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True);raise
    return target


def set_pinned(project,point,pinned):
    lease=_lease(project)
    try:
        m=_manifest(project,point,True);m['pinned']=bool(pinned);atomic_json(Path(point)/'backup.json',m)
    finally:lease.close()


def _lease(project):
    try:from .project_store import ProjectLease
    except ImportError:from project_store import ProjectLease
    project_info(project)
    return ProjectLease(project).acquire()


def manual_point(project):
    lease=_lease(project)
    try:
        recover_restore(project);recover_checkpoint(project)
        return create_point(project,['manual'],pinned=True)
    finally:lease.close()


def prune_points(project,rolling=10,daily_days=7):
    if rolling<1 or daily_days<1:raise ValueError('備份保留數至少為 1')
    valid=[r for r in list_points(project) if r['valid']]
    keep={r['path'] for r in valid[:rolling]}
    days=sorted({r['local_day'] for r in valid if r.get('daily')},reverse=True)[:daily_days]
    keep.update(r['path'] for r in valid if r.get('pinned') or (r.get('daily') and r['local_day'] in days))
    removed=[]
    for r in valid:
        if r['path'] in keep:continue
        # Corrupt/unrecognised files are evidence, not cleanup candidates.
        try:_manifest(project,r['path'],True)
        except (OSError,ValueError,KeyError,TypeError):continue
        shutil.rmtree(r['path']);removed.append(r['path'])
    return removed


def _replace_file(source,target):
    temp=target.with_name(target.name+'.restore-'+uuid.uuid4().hex+'.pending')
    try:copy_verified(source,temp);os.replace(temp,target)
    finally:temp.unlink(missing_ok=True)


def recover_restore(project):
    journal=inside(project,RESTORE)
    if not journal.exists():return False
    data=read_json(journal);project,pid=project_info(project)
    rescue=inside(project,data['rescue'])
    if rescue.parent!=inside(project,ROOT) or not rescue.name.startswith('rescue-'):raise ValueError('還原救援路徑無效')
    metadata=read_json(rescue/'backup.json')
    if metadata.get('project_id')!=pid or metadata.get('kind')!='rescue':raise ValueError('還原交易專案不符')
    scope=data.get('scope_file','')
    if metadata.get('scope_file')!=scope or not re.fullmatch(r'scope-cache/scope-[0-9a-f]{64}\.igz',scope):raise ValueError('還原交易快照不符')
    allowed={'bridge.blend',STATE,CHECKPOINT,'bridge.checkpoint.blend',scope}
    if len(metadata.get('originals',[]))!=len(allowed) or {r['path'] for r in metadata['originals']}!=allowed:raise ValueError('還原交易檔案清單不符')
    for record in metadata['originals']:
        rel=record['path']
        if rel not in allowed or type(record.get('existed')) is not bool:raise ValueError('還原交易檔案無效')
        if record['existed'] and digest(inside(rescue,rel))!=record['sha256']:raise ValueError('還原現況備份已損壞')
    for record in metadata['originals']:
        target=inside(project,record['path'])
        if record['existed']:_replace_file(inside(rescue,record['path']),target)
        else:target.unlink(missing_ok=True)
    journal.unlink();return True


def restore_point(project,point):
    lease=_lease(project)
    try:return _restore_point(project,point)
    finally:lease.close()


def _restore_point(project,point):
    project,pid=project_info(project);recover_restore(project)
    m=_manifest(project,point,True);state=m['state']
    # A coherent current pair is also a normal, pinned recovery point.
    try:current_state(project)
    except (OSError,ValueError,KeyError,TypeError):pass
    else:create_point(project,['before_restore'],pinned=True)
    root=inside(project,ROOT);rescue=root/('rescue-'+uuid.uuid4().hex);rescue.mkdir()
    originals=[]
    for rel in ('bridge.blend',STATE,CHECKPOINT,'bridge.checkpoint.blend',state['scope_file']):
        f=inside(project,rel);record={'path':rel,'existed':f.exists()}
        if f.exists():record.update(copy_verified(f,inside(rescue,rel)))
        originals.append(record)
    atomic_json(rescue/'backup.json',{'format':1,'kind':'rescue','project_id':pid,'scope_file':state['scope_file'],
                                    'originals':originals,'d5_project_included':False})
    atomic_json(inside(project,RESTORE),{'format':1,'rescue':rescue.relative_to(project).as_posix(),'scope_file':state['scope_file']})
    try:
        _replace_file(inside(point,state['scope_file']),inside(project,state['scope_file']))
        _replace_file(inside(point,'bridge.blend'),inside(project,'bridge.blend'))
        _replace_file(inside(point,STATE),inside(project,STATE))
        # Do not promote a pre-restore SDK checkpoint on next startup.
        inside(project,CHECKPOINT).unlink(missing_ok=True)
        inside(project,'bridge.checkpoint.blend').unlink(missing_ok=True)
        current_state(project);inside(project,RESTORE).unlink()
    except BaseException:
        recover_restore(project);raise
    return {'restored':str(point),'rescue':str(rescue),'state':state}
