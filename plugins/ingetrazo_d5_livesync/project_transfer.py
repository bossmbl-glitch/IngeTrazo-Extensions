# SPDX-License-Identifier: GPL-3.0-or-later
"""Portable confirmed bridge pairs; never includes D5 or native IGZ projects."""
import copy
import datetime as dt
import hashlib
import json
import ntpath
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
import os
try:
    from . import bridge_backups as b
except ImportError:
    import bridge_backups as b

MANIFEST='transfer.json'
MAX_FILES=20000
MAX_BYTES=100*1024**3


def model_path(directory):
    return ntpath.join(directory,'model.igz') if '\\' in directory else directory.rstrip('/')+'/model.igz'


def _rebase(state, origin, target):
    if not isinstance(state,dict) or state.get('project_directory')!=origin:
        raise ValueError('移機配對的原始路徑不符')
    state=copy.deepcopy(state)
    owners=state.get('transfer_model_owners',[model_path(origin)])
    if not isinstance(owners,list) or not owners or any(not isinstance(p,str) or not p for p in owners):
        raise ValueError('移機來源模型識別無效')
    state['project_directory']=str(Path(target).resolve())
    state['transfer_model_owners']=owners
    state.setdefault('transferred_from',origin)
    b.validate_state(target,state,files=False)
    return state


def _safe_name(name):
    path=PurePosixPath(name)
    if (not name or '\\' in name or ':' in name or path.is_absolute() or '..' in path.parts
            or path.as_posix()!=name):raise ValueError('移機包含不安全路徑')
    return name


def _json_bytes(value):return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')


def _inspect(z):
    infos=z.infolist()
    if len(infos)>MAX_FILES or sum(i.file_size for i in infos)>MAX_BYTES:raise ValueError('移機包超過檔案或大小限制')
    names=set()
    for info in infos:
        _safe_name(info.filename)
        if info.filename in names or info.is_dir() or stat.S_ISLNK(info.external_attr>>16):raise ValueError('移機包含重複或非一般檔案')
        names.add(info.filename)
    if MANIFEST not in names or z.getinfo(MANIFEST).file_size>8*1024**2:raise ValueError('移機包缺少有效清單')
    m=json.loads(z.read(MANIFEST))
    if (not isinstance(m,dict) or m.get('format')!=1 or m.get('kind')!='ingetrazo-bridge-transfer'
            or not re.fullmatch('[0-9a-f]{32}',m.get('project_id',''))
            or m.get('d5_included') is not False or m.get('native_igz_included') is not False):
        raise ValueError('不支援的移機包')
    origin=m.get('origin_directory')
    if not isinstance(origin,str) or not origin:raise ValueError('移機來源路徑無效')
    records=m.get('files')
    if not isinstance(records,dict) or set(records)!=(names-{MANIFEST}):raise ValueError('移機檔案清單不符')
    points=m.get('points')
    if (not isinstance(points,list) or not points or len(points)!=len(set(points))
            or any(not isinstance(n,str) or not re.fullmatch(r'point-[A-Za-z0-9_-]+',n) for n in points)
            or m.get('current_point') not in points):raise ValueError('移機還原點清單無效')
    for name,record in records.items():
        _safe_name(name)
        parts=PurePosixPath(name).parts
        if len(parts)<3 or parts[0]!=b.ROOT or parts[1] not in points:raise ValueError('移機包含非橋接檔案')
        if (not isinstance(record,dict) or type(record.get('bytes')) is not int or record['bytes']<1
                or record['bytes']!=z.getinfo(name).file_size
                or not re.fullmatch('[0-9a-f]{64}',record.get('sha256',''))):raise ValueError('移機檔案紀錄無效')
    return m


def export_project(project,destination):
    project,pid=b.project_info(project);destination=Path(destination).resolve()
    if destination.is_relative_to(project):raise ValueError('請將移機包存到橋接專案資料夾以外')
    lease=b._lease(project)
    temp=None
    try:
        b.recover_restore(project);b.recover_checkpoint(project)
        try:from .backup_service import finalize_jobs
        except ImportError:from backup_service import finalize_jobs
        finalize_jobs(project)
        current=b.create_point(project,['transfer_export'],pinned=True)
        rows=b.list_points(project,verify=True)
        if any(not r['valid'] for r in rows):raise ValueError('有損壞的還原點，請先保留診斷並處理，尚未匯出')
        files={}
        for row in rows:
            point=Path(row['path'])
            for relative in ['backup.json']+[r['path'] for r in row['files']]:
                source=b.inside(point,relative)
                name=source.relative_to(project).as_posix();files[name]={'bytes':source.stat().st_size,'sha256':b.digest(source)}
        m={'format':1,'kind':'ingetrazo-bridge-transfer','plugin_version':b.VERSION,'project_id':pid,
           'origin_directory':str(project),'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),
           'current_point':current.name,'points':[Path(r['path']).name for r in rows],
           'files':files,'d5_included':False,'native_igz_included':False}
        destination.parent.mkdir(parents=True,exist_ok=True)
        fd,temp=tempfile.mkstemp(prefix=destination.name+'.',suffix='.pending',dir=destination.parent);os.close(fd)
        with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as z:
            z.writestr(MANIFEST,_json_bytes(m))
            for name in files:z.write(b.inside(project,name),name)
        # Verify the completed archive, not just the source hashes.
        with tempfile.TemporaryDirectory(prefix='transfer-check-',dir=destination.parent) as check:
            _unpack(temp,Path(check)/pid)
        with open(temp,'r+b') as stream:os.fsync(stream.fileno())
        os.replace(temp,destination);return m
    finally:
        if temp:Path(temp).unlink(missing_ok=True)
        lease.close()


def _unpack(archive,target):
    """Verify ALL bytes in a staging directory; no target project writes."""
    target=Path(target).resolve();target.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        m=_inspect(z)
        if target.name!=m['project_id']:raise ValueError('移機包與目前 IGZ 專案識別不同；請先開啟原專案的 IGZ')
        for name,record in m['files'].items():
            path=b.inside(target,name);path.parent.mkdir(parents=True,exist_ok=True)
            h=hashlib.sha256()
            with z.open(name) as src,path.open('wb') as dst:
                for chunk in iter(lambda:src.read(1024*1024),b''):h.update(chunk);dst.write(chunk)
            if h.hexdigest()!=record['sha256']:raise ValueError('移機包檔案雜湊驗證失敗')
    # Source metadata is verified before changing any path or hash record.
    for name in m['points']:
        point=target/b.ROOT/name;manifest=b.read_json(point/'backup.json')
        state=b.read_json(point/b.STATE)
        if not isinstance(manifest,dict) or manifest.get('state')!=state:raise ValueError('移機配對紀錄不符')
        state=_rebase(state,m['origin_directory'],target)
        expected={'backup.json','bridge.blend',b.STATE,state['scope_file']}
        archived={PurePosixPath(n).relative_to(PurePosixPath(b.ROOT)/name).as_posix() for n in m['files'] if PurePosixPath(n).parts[1]==name}
        if archived!=expected:raise ValueError('移機還原點含多餘或缺少檔案')
        # Validate original state record hashes as well as outer archive hashes.
        original_records=manifest.get('files',[])
        if not isinstance(original_records,list):raise ValueError('移機配對清單無效')
        for r in original_records:
            f=b.inside(point,r['path'])
            if b.digest(f)!=r['sha256'] or f.stat().st_size!=r['bytes']:raise ValueError('移機內層雜湊不符')
        manifest['state']=state;b.atomic_json(point/b.STATE,state)
        for record in original_records:
            if record['path']==b.STATE:record.update(sha256=b.digest(point/b.STATE),bytes=(point/b.STATE).stat().st_size)
        b.atomic_json(point/'backup.json',manifest)
        b.verify_point(target,point)
    return m


def import_project(project,archive):
    project,pid=b.project_info(project)
    # Verify first, including identity, before even creating a new project.
    project.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='bridge-transfer-',dir=project.parent) as temp:
        stage=Path(temp)/pid;m=_unpack(archive,stage)
        # Rebase once more from staging to the actual destination. Owner paths
        # stay those physically saved inside the untouched .blend.
        for name in m['points']:
            point=stage/b.ROOT/name;manifest=b.read_json(point/'backup.json')
            state=_rebase(manifest['state'],str(stage.resolve()),project)
            manifest['state']=state
            if name==m['current_point']:
                manifest['pinned']=True
                manifest['reasons']=sorted(set(manifest['reasons'])|{'transfer_import'})
            b.atomic_json(point/b.STATE,state)
            for r in manifest['files']:
                if r['path']==b.STATE:r.update(sha256=b.digest(point/b.STATE),bytes=(point/b.STATE).stat().st_size)
            b.atomic_json(point/'backup.json',manifest)
        project.mkdir(exist_ok=True);lease=b._lease(project)
        try:
            b.recover_restore(project)
            # Existing recovery points are never overwritten or pruned.
            root=b.inside(project,b.ROOT);root.mkdir(exist_ok=True)
            mapping={}
            for name in m['points']:
                new=root/('point-transfer-'+uuid.uuid4().hex)
                pending=new.with_name(new.name+'.pending')
                try:
                    shutil.copytree(stage/b.ROOT/name,pending)
                    os.replace(pending,new);b.verify_point(project,new)
                finally:
                    if pending.exists():shutil.rmtree(pending)
                mapping[name]=new
            result=b._restore_point(project,mapping[m['current_point']])
            return {**result,'points_imported':len(mapping),'project_id':pid}
        finally:lease.close()


def rebind_model_owners(data,state,model):
    """Worker-only: migrate our ownership tags, never names, geometry or SDK IDs."""
    owners=state.get('transfer_model_owners')
    if not owners:return 0
    target=str(Path(model).resolve());allowed=set(owners)|{target};updates=[]
    for collection in ('objects','collections','materials','meshes','images','worlds','scenes'):
        for block in getattr(data,collection,[]):
            for key in ('ingetrazo_doc','ingetrazo_scene_doc','ingetrazo_ambience_doc'):
                value=block.get(key)
                if not value:continue
                if value not in allowed:raise ValueError('橋接內含未知來源識別，已停止移機重綁')
                if value!=target:updates.append((block,key))
    for block,key in updates:block[key]=target
    return len(updates)
