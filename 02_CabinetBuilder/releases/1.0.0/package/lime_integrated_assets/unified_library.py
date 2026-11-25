"""One library UI and backup over the existing catalogs, without migrating assets."""
from copy import deepcopy
from pathlib import Path
import json
import os
import tempfile
from PySide6.QtCore import QLockFile


def atomic_bytes(path,raw):
    path=Path(path)
    if raw is None:path.unlink(missing_ok=True);return
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.lime_library_',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:Path(name).unlink(missing_ok=True)


class UnifiedStore:
    def __init__(self,api,base,on_changed=None):
        self.api=api;self.base=base;self.directory=base.directory;self.path=base.path
        self.panels=api.AccessoryStore(base.directory/'electrical_panels');self.on_changed=on_changed
    def changed(self):
        if self.on_changed is not None:self.on_changed()
    def load(self):
        data=self.base.load();data['panels']=self.panels.load()['handles'];return data
    def add_profile(self,*args,**kwargs):
        result=self.base.add_profile(*args,**kwargs);self.changed();return result
    def raw_store(self,kind):
        if kind not in ('profiles','handles','panels'):raise ValueError('配件分類無效。')
        return self.panels if kind=='panels' else self.base
    def add_handle(self,*args,kind='handles',**kwargs):
        if kind not in ('handles','panels'):raise ValueError('請選擇把手或面板分類。')
        if kind=='panels':
            mount=kwargs.get('mount',args[3] if len(args)>3 else 'center');kwargs['mount']='custom' if mount=='custom' else 'center';args=(*args[:3],) if len(args)>3 else args
        result=self.raw_store(kind).add_handle(*args,**kwargs);self.changed();return result
    def snapshot_handle(self,record,kind='handles'):return self.raw_store(kind).snapshot_handle(record)
    def update(self,kind,*args,**kwargs):
        result=self.raw_store(kind).update('handles' if kind=='panels' else kind,*args,**kwargs);self.changed();return result
    def delete(self,kind,identity):
        self.raw_store(kind).delete('handles' if kind=='panels' else kind,identity);self.changed()
    def export(self,path):
        data=self.base.load();data['handles']=[self.base.snapshot_handle(row) for row in data['handles']]
        data.update(schema=3,format='LimeAccessoryBackup',panels=[self.panels.snapshot_handle(row) for row in self.panels.load()['handles']])
        self.api._atomic_json(Path(path),data)
    def plan(self,store,profiles,handles):
        data,token=store.begin();added=0;files=[]
        for row in profiles:
            self.check_progress()
            record=self.api.profile_snapshot(row);self.api._asset_id(record['id'],'p');fp=self.api.section_fingerprint(record['points_mm'])
            if any(self.api.section_fingerprint(old['points_mm'])==fp and self.api.placement(old.get('placement'))==record['placement'] and old.get('kind')==record.get('kind') for old in data['profiles']):continue
            if any(old['id']==record['id'] or old['name'].casefold()==record['name'].casefold() for old in data['profiles']):raise ValueError('剖面名稱或識別碼衝突，原資料未變更。')
            data['profiles'].append(record);added+=1
        for row in handles:
            self.check_progress()
            raw=self.api.handle_bytes(row);identity=self.api._asset_id(row['id'],'h');name=self.api.asset_name(row['name'])
            if any(old['id']==identity and old['sha256']==row['sha256'] for old in data['handles']):continue
            if any(old['id']==identity or old['name'].casefold()==name.casefold() for old in data['handles']):raise ValueError('實體配件名稱或識別碼衝突，原資料未變更。')
            self.api.handle_from_snapshot(row)
            target=store.directory/(identity+'.igz')
            if target.exists() and target.read_bytes()!=raw:raise ValueError('配件檔案衝突，未覆寫既有資料。')
            record={key:deepcopy(value) for key,value in row.items() if key!='blob_b64'};record['name']=name
            data['handles'].append(record);files.append((target,raw));added+=1
        return store,data,token,files,added
    def check_progress(self):
        callback=getattr(self,'_progress',None);cancel=getattr(self,'_cancel',None)
        if cancel and cancel():raise ValueError('已取消匯入，資料庫與模型未變更。')
        self._progress_index=getattr(self,'_progress_index',0)+1
        if callback:callback(self._progress_index,getattr(self,'_progress_total',1))
        if cancel and cancel():raise ValueError('已取消匯入，資料庫與模型未變更。')
    def import_json(self,path,progress=None,cancel=None):
        self._progress=progress;self._cancel=cancel;self._progress_index=0;self._progress_total=1
        try:return self._import_json(path)
        finally:self._progress=None;self._cancel=None
    def _import_json(self,path):
        path=Path(path)
        if path.stat().st_size>128*1024*1024:raise ValueError('備份檔過大，請分批匯入。')
        incoming=json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(incoming,dict):raise ValueError('配件備份格式無效。')
        if incoming.get('schema') in (1,2):
            result=self.base.import_json(path)
            if result:self.changed()
            return result
        if incoming.get('schema')!=3 or incoming.get('units')!='mm':raise ValueError('不支援的配件備份格式。')
        for key in ('profiles','handles','panels'):
            if not isinstance(incoming.get(key),list):raise ValueError('備份缺少有效的配件分類。')
        self._progress_total=sum(len(incoming[k]) for k in ('profiles','handles','panels'))
        self.check_progress()
        # Parse and check every category before writing any catalog or asset.
        plans=[self.plan(self.base,incoming['profiles'],incoming['handles']),self.plan(self.panels,[],incoming['panels'])]
        added=sum(plan[4] for plan in plans)
        if not added:return 0
        if cancel:=getattr(self,'_cancel',None):
            if cancel():raise ValueError('已取消匯入，資料庫未變更。')
        locks=[];created=[];written=[]
        try:
            for store,_,_,_,_ in sorted(plans,key=lambda p:str(p[0].path)):
                store.directory.mkdir(parents=True,exist_ok=True);lock=QLockFile(str(store.path)+'.lock');lock.setStaleLockTime(30000)
                if not lock.tryLock(1000):raise ValueError('配件資料庫正在使用，請稍後重試。')
                locks.append(lock)
            for store,_,token,_,_ in plans:
                if (store.path.read_bytes() if store.path.exists() else None)!=token:raise ValueError('資料庫已變更，請重新整理後再匯入。')
            for _,_,_,files,_ in plans:
                for target,raw in files:
                    if target.exists():
                        if target.read_bytes()!=raw:raise ValueError('配件檔案衝突，未覆寫既有資料。')
                        continue
                    with target.open('xb') as stream:
                        created.append(target);stream.write(raw)
            for store,data,token,_,count in plans:
                if not count:continue
                for target,raw in ([(store.path.with_suffix('.json.bak'),token)] if token is not None else [])+[(store.path,json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8'))]:
                    before=target.read_bytes() if target.exists() else None;atomic_bytes(target,raw);written.append((target,before))
        except Exception:
            for target,before in reversed(written):atomic_bytes(target,before)
            for target in created:target.unlink(missing_ok=True)
            raise
        finally:
            for lock in reversed(locks):lock.unlock()
        self.changed();return added
