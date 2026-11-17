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
import threading
import time

_IO=threading.local()

def background_io(enabled=True):_IO.background=enabled

def io_yield():
    if getattr(_IO,'background',False):time.sleep(.004)

VERSION='0.9.11'
STATE='bridge-state.json'
CHECKPOINT='checkpoint-state.pending.json'
RESTORE='restore-transaction.json'
ROOT='bridge-backups'


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk);io_yield()
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
        for chunk in iter(lambda:src.read(1024*1024),b''):dst.write(chunk);io_yield()
        dst.flush();os.fsync(dst.fileno())
    if digest(source)!=expected or digest(target)!=expected:raise OSError('備份檔案複製驗證失敗')
    return {'sha256':expected,'bytes':target.stat().st_size}


def validate_state(project,state,blend=None,files=True,scope_root=None):
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
        if Path(blend or inside(project,'bridge.blend')).stat().st_size==0 or inside(scope_root or project,relative).stat().st_size==0:
            raise ValueError('確認配對檔案為空')
        if digest(blend or inside(project,'bridge.blend'))!=state['blend_sha256']:raise ValueError('橋接檔與確認紀錄不符')
        if digest(inside(scope_root or project,relative))!=state['scope_sha256']:raise ValueError('確認快照損壞')
    return state


def build_state(project,scope,metadata=None,blend=None):
    project,pid=project_info(project);scope=Path(scope).resolve()
    if not scope.is_relative_to(project):raise ValueError('確認快照不屬於本專案')
    state={'format':1,'project_id':pid,'project_directory':str(project),
           'receipt_confirmed':True,
           'scope_file':scope.relative_to(project).as_posix(),'scope_sha256':digest(scope),
           'blend_sha256':digest(blend or inside(project,'bridge.blend')),
           'confirmed_at':dt.datetime.now(dt.timezone.utc).isoformat(),**(metadata or {})}
    validate_state(project,state,files=False)
    if Path(blend or inside(project,'bridge.blend')).stat().st_size==0 or scope.stat().st_size==0:raise ValueError('確認配對檔案為空')
    return state


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
    return state


def file_stamp(path):
    st=Path(path).stat();return (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)


def finish_checkpoint(project,state,stamp):
    # Worker owns ProjectLease. The just-hashed pending inode was atomically
    # renamed, so do not re-read the same large blend several times.
    if file_stamp(inside(project,'bridge.blend'))!=stamp:return recover_checkpoint(project)
    validate_state(project,state,files=False)
    if read_json(inside(project,CHECKPOINT))!=state:raise ValueError('確認交易紀錄不符')
    atomic_json(inside(project,STATE),state);inside(project,CHECKPOINT).unlink();return True


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
    slots=m.get('protected_slots',[])
    if not isinstance(slots,list) or any(type(slot) is not int or slot<0 for slot in slots):raise ValueError('保護時段格式無效')
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


def create_point(project,reasons=(),pinned=False,now=None,*,snapshot=None,protected_slots=(),force_new=False,request_id=None):
    project,pid=project_info(project)
    source=Path(snapshot) if snapshot else project
    state=validate_state(project,read_json(inside(source,STATE)),blend=inside(source,'bridge.blend'),scope_root=source)
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
        same_request=request_id and r.get('request_id')==request_id
        if same_request or (not force_new and r.get('state_key')==state_key and r['local_day']==day):
            path=Path(r['path'])
            try:m=_manifest(project,path,True)
            except (OSError,ValueError,KeyError,TypeError):continue
            m['reasons']=sorted(set(m['reasons'])|set(reasons));m['pinned']=m['pinned'] or pinned or bool(protected_slots);m['daily']=m['daily'] or daily
            m['protected_slots']=sorted(set(m.get('protected_slots',[]))|set(protected_slots))
            atomic_json(path/'backup.json',m);return path
    root=inside(project,ROOT);root.mkdir(exist_ok=True)
    name='point-'+now.astimezone(dt.timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
    stage=root/(name+'.pending');target=root/name;stage.mkdir()
    try:
        records=[]
        for relative in ('bridge.blend',state['scope_file'],STATE):
            records.append({'path':relative,**store_shared(project,inside(source,relative),inside(stage,relative))})
        m={'format':1,'kind':'confirmed','created_at':now.astimezone(dt.timezone.utc).isoformat(),'local_day':day,
           'state_key':state_key,'state':state,'files':records,'pinned':bool(pinned or protected_slots),'daily':daily,
           'reasons':sorted(set(reasons)),'d5_project_included':False,
           'protected_slots':sorted(set(protected_slots)),'request_id':request_id}
        atomic_json(stage/'backup.json',m);validate_state(project,state,blend=inside(source,'bridge.blend'),scope_root=source)
        os.replace(stage,target);_manifest(project,target,True)
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True);raise
    return target


def set_pinned(project,point,pinned):
    lease=_lease(project)
    try:
        m=_manifest(project,point,True)
        if not pinned and m.get('protected_slots'):raise ValueError('30 分鐘保護不能取消；需在刪除時明確包含保護點')
        m['pinned']=bool(pinned);atomic_json(Path(point)/'backup.json',m)
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
        try:from .backup_service import finalize_jobs
        except ImportError:from backup_service import finalize_jobs
        finalize_jobs(project)
        return create_point(project,['manual'],pinned=True,force_new=True)
    finally:lease.close()


def prune_points(project,rolling=10,daily_days=7,interval_limit=6):
    if rolling<1 or daily_days<1 or interval_limit<1:raise ValueError('備份保留數至少為 1')
    valid=[r for r in list_points(project) if r['valid']]
    protected=[r for r in valid if r.get('pinned') or r.get('protected_slots')]
    ordinary=[r for r in valid if not r.get('pinned') and not r.get('protected_slots')]
    intervals=[r for r in ordinary if 'interval' in r.get('reasons',[])]
    legacy=[r for r in ordinary if 'interval' not in r.get('reasons',[])]
    keep={r['path'] for r in protected+intervals[:interval_limit]+legacy[:rolling]}
    # Daily labels must not let ordinary timer points bypass their six-point cap.
    days=sorted({r['local_day'] for r in legacy if r.get('daily')},reverse=True)[:daily_days]
    keep.update(r['path'] for r in legacy if r.get('daily') and r['local_day'] in days)
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


def store_shared(project,source,target):
    sha=digest(source);root=inside(project,ROOT)/'objects';root.mkdir(parents=True,exist_ok=True)
    blob=root/sha
    if blob.exists() and digest(blob)!=sha:
        os.replace(blob,root/(sha+'.corrupt-'+uuid.uuid4().hex))
    if not blob.exists():
        temp=root/(sha+'.'+uuid.uuid4().hex+'.pending')
        try:copy_verified(source,temp);os.replace(temp,blob)
        finally:temp.unlink(missing_ok=True)
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    try:os.link(blob,target)
    except OSError:copy_verified(blob,target)
    return {'sha256':sha,'bytes':target.stat().st_size}


def protected(point):return bool(point.get('pinned') or point.get('protected_slots'))


def storage_summary(project):
    rows=list_points(project);days={};seen=set();physical=0
    for row in rows:
        if not row['valid']:continue
        day=row['local_day'];summary=days.setdefault(day,{'count':0,'protected':0,'logical_bytes':0})
        summary['count']+=1;summary['protected']+=int(protected(row))
        for record in row['files']:
            summary['logical_bytes']+=record['bytes']
    root=inside(project,ROOT)
    if root.exists():
        for file in root.rglob('*'):
            if file.is_symlink() or not file.is_file():continue
            st=file.stat();key=(st.st_dev,st.st_ino or str(file))
            if key not in seen:physical+=st.st_size;seen.add(key)
    return {'days':days,'physical_bytes_estimate':physical,'free_bytes':shutil.disk_usage(project).free}


def delete_points(project,points,include_protected=False):
    lease=_lease(project)
    try:
        recover_deletions(project)
        jobs=inside(project,'backup-jobs')
        if jobs.exists() and any((p/'request.json').is_file() for p in jobs.glob('job-*')):raise ValueError('尚有未完成備份，請先按立即備份完成後再清理')
        paths=list(dict.fromkeys(str(Path(p).resolve()) for p in points))
        manifests=[_manifest(project,p,True) for p in paths]
        if not include_protected and any(protected(m) for m in manifests):raise ValueError('含保護備份，需明確確認包含保護點')
        root=inside(project,ROOT);records=[]
        for path in paths:
            p=Path(path);records.append({'original':p.name,'tombstone':'.deleting-'+uuid.uuid4().hex})
        journal=inside(project,'backup-delete.pending.json')
        atomic_json(journal,{'format':1,'project_id':Path(project).name,'records':records})
        moved=[]
        try:
            for r in records:os.replace(root/r['original'],root/r['tombstone']);moved.append(r)
        except BaseException:
            for r in reversed(moved):os.replace(root/r['tombstone'],root/r['original'])
            journal.unlink(missing_ok=True);raise
        recover_deletions(project)
        collect_unreferenced(project)
        return len(paths)
    finally:lease.close()


def recover_deletions(project):
    journal=inside(project,'backup-delete.pending.json')
    if not journal.exists():return False
    data=read_json(journal)
    if data.get('format')!=1 or data.get('project_id')!=Path(project).name:raise ValueError('備份刪除交易不符')
    root=inside(project,ROOT)
    for row in data.get('records',[]):
        if not re.fullmatch(r'point-[A-Za-z0-9_-]+',row.get('original','')) or not re.fullmatch(r'\.deleting-[0-9a-f]{32}',row.get('tombstone','')):raise ValueError('備份刪除路徑無效')
    for row in data['records']:
        original=inside(root,row['original']);tomb=inside(root,row['tombstone'])
        if original.exists() and not tomb.exists():os.replace(original,tomb)
        if tomb.exists():shutil.rmtree(tomb)
    journal.unlink();return True


def collect_unreferenced(project):
    rows=list_points(project);root=inside(project,ROOT)
    # Unknown/corrupt/pending data is evidence. Never guess its dependencies.
    if any(not r['valid'] for r in rows) or any(p.name.endswith('.pending') for p in root.iterdir()):return []
    used={r['sha256'] for point in rows for r in point['files']};removed=[]
    pool=root/'objects'
    if pool.exists():
        for file in pool.iterdir():
            if re.fullmatch('[0-9a-f]{64}',file.name) and file.name not in used and file.is_file() and not file.is_symlink():
                file.unlink();removed.append(file.name)
    return removed
