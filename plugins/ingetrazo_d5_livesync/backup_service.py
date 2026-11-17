# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable immutable captures, background verified backups, no bpy calls."""
import datetime as dt
import os
from pathlib import Path
import queue
import shutil
import threading
import time
import uuid
try:
    from . import bridge_backups as b
    from . import backup_schedule as schedule
except ImportError:
    import bridge_backups as b
    import backup_schedule as schedule

JOBS='backup-jobs'
STATUS='backup-status.json'


def capture(project,folder):
    state=b.read_json(b.inside(project,b.STATE));b.validate_state(project,state,files=False)
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=False)
    try:
        for relative in ('bridge.blend',state['scope_file'],b.STATE):
            source=b.inside(project,relative);target=b.inside(folder,relative);target.parent.mkdir(parents=True,exist_ok=True)
            # Every checkpoint replaces the inode; these links remain immutable.
            try:os.link(source,target)
            except OSError:b.copy_verified(source,target)
        return state
    except BaseException:shutil.rmtree(folder,ignore_errors=True);raise


class BackupService:
    def __init__(self,project,auto_schedule=True):
        self.project=Path(project).resolve();self.root=b.inside(project,JOBS);self.root.mkdir(exist_ok=True)
        self.queue=queue.Queue();self.stop_event=threading.Event();self.last_at=None;self.last_slot=None
        self.pending=False;self.error='';self.retry_after=0.;self.completed={};self.enqueued=set();self.lock=threading.Lock();self.guard=threading.RLock()
        self.requested_at=None;self.requested_slot=None
        for row in b.list_points(project):
            if not row['valid']:continue
            when=dt.datetime.fromisoformat(row['created_at'])
            if self.last_at is None or when>self.last_at:self.last_at=when
            for slot in row.get('protected_slots',[]):self.last_slot=max(slot,self.last_slot or slot)
        self.requested_at=self.last_at;self.requested_slot=self.last_slot
        for folder in sorted(self.root.glob('job-*')):
            if folder.name.endswith('.pending') or not (folder/'request.json').is_file():continue
            self.enqueued.add(folder.name);self.queue.put(folder)
            try:
                request=b.read_json(folder/'request.json');when=dt.datetime.fromisoformat(request['created_at'])
                if self.requested_at is None or when>self.requested_at:self.requested_at=when
                slot=request.get('slot')
                if slot is not None:self.requested_slot=max(slot,self.requested_slot if self.requested_slot is not None else slot)
            except (OSError,ValueError,KeyError,TypeError):pass
        self.thread=threading.Thread(target=self._run,name='D5 bridge backups',daemon=True);self.thread.start()
        self.scheduler=None
        if auto_schedule:
            self.scheduler=threading.Thread(target=self._schedule,name='D5 backup timer',daemon=True);self.scheduler.start()

    def _status(self,**extra):
        b.atomic_json(b.inside(self.project,STATUS),{**(b.read_json(b.inside(self.project,STATUS)) if b.inside(self.project,STATUS).exists() else {}),'running':self.pending,'error':self.error,
            'queued':self.queue.qsize(),'updated_at':time.time(),**extra})

    def submit(self,reasons,pinned=False,now=None,slot=None,wait=False):
        now=now or dt.datetime.now().astimezone();job=self.root/('job-'+uuid.uuid4().hex)
        stage=job.with_name(job.name+'.pending')
        event=threading.Event();result={}
        with self.guard:
            capture(self.project,stage)
            b.atomic_json(stage/'request.json',{'reasons':list(reasons),'pinned':pinned,
                'created_at':now.isoformat(),'slot':slot,'request_id':job.name})
            os.replace(stage,job)
            with self.lock:self.completed[job.name]=(event,result);self.enqueued.add(job.name)
            self.queue.put(job)
        if wait:
            while not event.wait(.2):
                if not self.thread.is_alive():raise OSError('背景備份程序未完成')
            if 'error' in result:raise OSError(result['error'])
            return result['point']
        return str(job)

    def _schedule(self):
        while not self.stop_event.wait(1.):
            self.retry_jobs();self.tick()

    def tick(self,now=None):
        now=now or dt.datetime.now().astimezone()
        if time.monotonic()<self.retry_after:return
        with self.guard:
            if not b.inside(self.project,b.STATE).exists() or b.inside(self.project,b.CHECKPOINT).exists() or b.inside(self.project,b.RESTORE).exists():return
            try:
                minutes=schedule.settings(self.project)['minutes']
                needed,slot=schedule.due(now,self.requested_at,self.requested_slot,minutes)
                if needed:
                    self.submit(['half_hour' if slot is not None else 'interval'],now=now,slot=slot)
                    self.requested_at=now
                    if slot is not None:self.requested_slot=slot
            except (OSError,ValueError,KeyError,TypeError) as exc:
                self.error=str(exc);self.retry_after=time.monotonic()+5
                try:self._status()
                except OSError:pass

    def preserve(self,reasons=()):
        if not b.inside(self.project,b.STATE).exists():return None
        if reasons:return self.submit(reasons,pinned=True,wait=True)
        # Two recent emergency pairs survive checkpoint replacement without
        # copying every blend during every sync. They are not scheduled points.
        root=b.inside(self.project,'checkpoint-safety');root.mkdir(exist_ok=True)
        folder=root/('pair-'+uuid.uuid4().hex);stage=folder.with_name(folder.name+'.pending')
        with self.guard: capture(self.project,stage);os.replace(stage,folder)
        for old in sorted(root.glob('pair-*'),key=lambda p:p.stat().st_mtime,reverse=True)[2:]:
            if old.is_dir() and not old.name.endswith('.pending'):shutil.rmtree(old)
        return str(folder)

    def _run(self):
        b.background_io(True)
        while not self.stop_event.is_set():
            try:job=self.queue.get(timeout=.2)
            except queue.Empty:continue
            self.pending=True;self.error='';result={}
            try:
                self._status();request=b.read_json(job/'request.json')
                when=dt.datetime.fromisoformat(request['created_at']);slot=request.get('slot')
                point=b.create_point(self.project,request['reasons'],pinned=request['pinned'],now=when,
                    snapshot=job,protected_slots=[] if slot is None else [slot],force_new=True,request_id=request['request_id'])
                self.last_at=when
                if slot is not None:self.last_slot=slot
                result['point']=str(point)
                b.prune_points(self.project);b.collect_unreferenced(self.project)
                shutil.rmtree(job)
                self._status(last_point=str(point),last_revision=b.read_json(point/b.STATE).get('revision'))
            except Exception as exc:
                result['error']=str(exc);self.error=str(exc);self.retry_after=time.monotonic()+5
                # Durable job remains, including its original pairing/time.
                try:self._status(failed_job=str(job))
                except OSError:pass
            finally:
                self.pending=False
                try:self._status()
                except OSError:pass
                with self.lock:
                    completion=self.completed.pop(job.name,None);self.enqueued.discard(job.name)
                if completion:
                    event,out=completion;out.update(result);event.set()
                self.queue.task_done()

    def retry_jobs(self):
        if time.monotonic()<self.retry_after:return
        with self.guard,self.lock:
            for folder in sorted(self.root.glob('job-*')):
                if not folder.name.endswith('.pending') and folder.name not in self.enqueued and (folder/'request.json').is_file():
                    self.enqueued.add(folder.name);self.queue.put(folder)

    def close(self):
        # No UI/SDK waits for large copies on shutdown; durable jobs replay on
        # the next worker start if process termination interrupts the thread.
        self.stop_event.set()
        if self.scheduler:self.scheduler.join(timeout=.3)
        self.thread.join(timeout=.3)


def finalize_jobs(project):
    """Caller owns ProjectLease, worker is stopped. Preserve all queued times
    before manual backup/export, so moving PCs cannot silently omit them.
    """
    root=b.inside(project,JOBS);done=[]
    if not root.exists():return done
    for job in sorted(root.glob('job-*')):
        if job.name.endswith('.pending'):continue
        if job.is_symlink() or job.resolve().parent!=root.resolve():raise ValueError('未完成備份路徑無效')
        request=b.read_json(job/'request.json');slot=request.get('slot')
        point=b.create_point(project,request['reasons'],pinned=request['pinned'],
            now=dt.datetime.fromisoformat(request['created_at']),snapshot=job,
            protected_slots=[] if slot is None else [slot],force_new=True,request_id=request['request_id'])
        done.append(str(point));shutil.rmtree(job)
    if done:b.prune_points(project);b.collect_unreferenced(project)
    b.atomic_json(b.inside(project,STATUS),{'running':False,'error':'','queued':0,'updated_at':time.time(),
                                          'last_point':done[-1] if done else None})
    return done
