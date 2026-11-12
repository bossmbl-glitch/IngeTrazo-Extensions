# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomic local handoff. Only one unacknowledged revision is allowed."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import time
import uuid
import zipfile
import tempfile
try:
    from .light_data import clean_lights, KEY
except ImportError:
    from light_data import clean_lights, KEY


def windows_file_busy(error):
    """Windows may deny replacing a file held briefly by a reader/scanner.

    Error 5 may also mean permanent permissions: retries are always bounded.
    Other I/O errors must retain their original failure semantics.
    """
    return isinstance(error,OSError) and getattr(error,'winerror',None) in (5,32,33)


def replace_json_file(source,target):
    delays=(.01,.02,.04,.08,.12,.16,.20,.20,.20)
    for attempt in range(len(delays)+1):
        try:
            os.replace(source,target)
            return
        except OSError as exc:
            if not windows_file_busy(exc) or attempt==len(delays):raise
            time.sleep(delays[attempt])


def write_json(path, value):
    path = Path(path)
    # Independent writers cannot truncate each other's temporary payload.
    # Close the completed file before replacing; readers see old or new JSON.
    fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=str(path.parent))
    temp=Path(name)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        replace_json_file(temp,path)
    finally:
        try:temp.unlink(missing_ok=True)
        except OSError:pass


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class SnapshotSession:
    def __init__(self, parent):
        self.directory = Path(parent) / ("session-" + uuid.uuid4().hex)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.revision = 0
        self.acknowledged = 0
        self.motion_certificate=None;self.motion_baseline=None;self.motion_candidate=None
        self.motion_candidate_revision=0;self.motion_base_hash=None;self.motion_protocol=False;self.last_fallback_reason='first_full_sync'
        self.partial_enabled=False;self.partial_protocol=False;self.motion_enabled=True
        write_json(self.directory / "owner.json", {
            "plugin": "ingetrazo_d5_livesync", "version": "0.9.10-test.1",
            "parent_pid": os.getpid(), "created": time.time(),
        })

    @property
    def pending(self):
        return self.revision > self.acknowledged

    def publish(self, scene, exporter,force_full=False,replace_pending=False,on_export=None):
        receiver_reason=''
        if replace_pending:
            state=read_json(self.directory/'status.json',{});latest=read_json(self.directory/'latest.json',{})
            if (not self.pending or latest.get('kind') not in ('transforms','partial') or state.get('phase')!='need_full'
                    or state.get('processing_revision')!=self.revision):raise RuntimeError('只能替換接收端明確拒絕、尚未送出的快速版本')
            self.last_fallback_reason='worker_requested_full'
            receiver_reason=state.get('message','')
        if self.pending and not replace_pending:
            raise RuntimeError("上一版尚未收到 D5 傳送成功回報")
        if not force_full and not replace_pending and self.motion_baseline is not None and self.motion_certificate is not None:
            state=read_json(self.directory/'status.json',{})
            if (self.acknowledged==self.revision and self.revision>0
                    and state.get('phase') in ('sent','idle') and state.get('official_return_code')==0
                    and state.get('acknowledged_revision')==self.revision
                    and state.get('model_sha256')==self.motion_base_hash):
                try:unchanged=self.motion_certificate.unchanged(self.motion_baseline)
                except (ValueError,AttributeError,TypeError):unchanged=False
                if unchanged:
                    return {'revision':self.revision,'kind':'skipped','bytes':0,'export_seconds':0.,
                            'sync_decision':{'route':'skipped','reason':'acknowledged_state_unchanged',
                                             'commands':[],'baseline_revision':self.acknowledged}}
        if on_export is not None:on_export()
        started = time.perf_counter()
        number = self.revision if replace_pending else self.revision + 1
        certificate=self.motion_certificate
        candidate=None
        partial_reason='disabled_or_no_confirmed_baseline'
        if self.motion_enabled and certificate is not None and self.motion_baseline is not None and self.motion_protocol and not force_full and not replace_pending:
            try:
                changes,candidate=certificate.delta(self.motion_baseline)
                from .layer_scope import host_policy
                payload={'protocol':1,'base_revision':self.acknowledged,'base_model_sha256':self.motion_base_hash,
                         'layer_policy':host_policy(scene),'changes':changes}
                target=self.directory/f'transforms-{number:08d}.json';write_json(target,payload)
                manifest={'revision':number,'kind':'transforms','file':target.name,'published_at':time.time(),
                          'export_seconds':time.perf_counter()-started,'bytes':target.stat().st_size,
                          'source_fast_path':True,'changed_groups':len(changes),'snapshot_filter_seconds':0.,
                          'snapshot_serialize_seconds':0.,'snapshot_validate_seconds':0.,
                          'sync_decision':{'route':'transforms','reason':'certified_placement','commands':certificate.history_since(self.motion_baseline)}}
                manifest.update(export_seconds=time.perf_counter()-started,published_at=time.time())
                write_json(self.directory/'latest.json',manifest)
                self.motion_candidate=candidate;self.motion_candidate_revision=number;self.revision=number
                return manifest
            except (ValueError,AttributeError,TypeError) as exc:self.last_fallback_reason=str(exc)
        elif force_full:self.last_fallback_reason='requested_full_update'
        if (self.partial_enabled and self.partial_protocol and certificate is not None and self.motion_baseline is not None
                and not force_full and not replace_pending):
            try:
                roots,current=certificate.partial_plan(self.motion_baseline)
            except (ValueError,AttributeError,TypeError) as exc:partial_reason=str(exc)
            else:
                from .local_update import export_roots
                from .layer_scope import host_policy
                target=self.directory/f'partial-{number:08d}.igz'
                export_roots(scene,roots,current,exporter,target,self.acknowledged,self.motion_base_hash,host_policy(scene))
                candidate=certificate.capture(with_dependencies=True,allow_edit=True)
                if candidate['cv']!=current['cv']:raise ValueError('局部匯出途中內容已變更，保留修改重試')
                manifest={'revision':number,'kind':'partial','file':target.name,'published_at':time.time(),
                          'export_seconds':time.perf_counter()-started,'bytes':target.stat().st_size,'source_fast_path':True,
                          'partial_roots':len(roots),'snapshot_filter_seconds':0.,'snapshot_serialize_seconds':0.,'snapshot_validate_seconds':0.,
                          'sync_decision':{**certificate.describe(self.motion_baseline,'certified_local_change' if roots else 'no_local_visual_change'),'route':'partial','root_keys':roots,
                                           'root_names':[r['name'] for r in current['groups'] if r['key'] in roots]}}
                manifest.update(export_seconds=time.perf_counter()-started,published_at=time.time())
                write_json(self.directory/'latest.json',manifest)
                self.motion_candidate=candidate;self.motion_candidate_revision=number;self.revision=number
                return manifest
        before_cv=getattr(scene,'content_version',None)
        target = self.directory / f"snapshot-{number:08d}.igz"
        # The host temporarily puts the edited group's mesh in scene.mesh.
        # Save the document root without leaving edit mode or mutating the host.
        from .light_proxy import filtered_scene,lights as proxy_lights
        snapshot = filtered_scene(scene)
        light_state = (getattr(scene, "plugin_data", {}) or {}).get(KEY, {}) or {}
        lights = proxy_lights(scene)
        light_ids = sorted({uuid.UUID(str(x)).hex for x in light_state.get("d5_light_ids", [])}
                           | {x["id"] for x in lights})
        filtered_at = time.perf_counter()
        result = exporter(snapshot, target)
        serialized_at = time.perf_counter()
        if result and result.get("missing", 0):
            raise ValueError(f"有 {result['missing']} 張來源貼圖無法讀取；已停止這次同步")
        if not target.exists() or target.stat().st_size == 0:
            raise ValueError("IngeTrazo 沒有產生有效快照")
        if zipfile.is_zipfile(target):
            with zipfile.ZipFile(target) as archive:
                # Do not decompress the full file on the host UI thread and
                # then decompress it again in Blender. The worker checks CRCs
                # of document, geometry and embedded textures before sending.
                if "document.json" not in archive.namelist():
                    raise ValueError("IGZ 快照缺少 document.json")
        else:
            json.loads(target.read_text(encoding="utf-8"))
        from .layer_scope import host_policy, light_layers
        policy = host_policy(scene)
        layers = light_layers(scene) if lights else {}
        if certificate is not None and before_cv==getattr(scene,'content_version',None):
            try:candidate=certificate.capture(with_dependencies=True,allow_edit=self.partial_enabled)
            except (ValueError,AttributeError,TypeError):candidate=None
        manifest = {"revision": number, "layer_policy": policy, "d5_light_layers": layers, "file": target.name,
                    "kind":"full","source_fast_path":False,"fast_fallback_reason":self.last_fallback_reason,
                    "sync_decision":{**(certificate.describe(self.motion_baseline,self.last_fallback_reason) if certificate else {'reason':self.last_fallback_reason}),
                                     'route':'full','partial_reason':partial_reason,'receiver_reason':receiver_reason},
                    "published_at": time.time(),
                    "export_seconds": time.perf_counter() - started,
                    "bytes": target.stat().st_size, "d5_lights": lights, "d5_light_ids": light_ids,
                    "snapshot_filter_seconds": filtered_at-started,
                    "snapshot_serialize_seconds": serialized_at-filtered_at,
                    "snapshot_validate_seconds": time.perf_counter()-serialized_at}
        manifest.update(export_seconds=time.perf_counter()-started,published_at=time.time())
        write_json(self.directory / "latest.json", manifest)
        self.motion_candidate=candidate;self.motion_candidate_revision=number
        self.revision = number
        return manifest

    def acknowledge(self, status):
        number = int(status.get("acknowledged_revision", 0))
        if not 0 <= number <= self.revision:
            raise ValueError("接收端回報的版本號無效")
        self.acknowledged = max(self.acknowledged, number)
        if (number==self.motion_candidate_revision and status.get('phase') in ('sent','idle')
                and status.get('official_return_code')==0 and status.get('fast_transform_protocol')==1
                and isinstance(status.get('model_sha256'),str) and len(status['model_sha256'])==64):
            self.motion_baseline=self.motion_candidate;self.motion_base_hash=status['model_sha256'];self.motion_protocol=True
            self.partial_protocol=status.get('partial_update_protocol')==1
        # Only delete our old immutable snapshots after acknowledgement.
        for old in [*self.directory.glob("snapshot-????????.igz"),*self.directory.glob('transforms-????????.json'),*self.directory.glob('partial-????????.igz')]:
            try:
                revision = int(old.stem.split("-")[-1])
                if revision < self.acknowledged - 1:
                    old.unlink()
            except (OSError, ValueError):
                pass

    def stop(self):
        write_json(self.directory / "control.json", {"stop": True})
