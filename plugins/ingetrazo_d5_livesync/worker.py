# SPDX-License-Identifier: GPL-3.0-or-later
"""Run only in a dedicated Blender --background --factory-startup process."""
import sys
import os
import time
import shutil
import traceback
import json
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True, write_through=True)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))
from transport import read_json, write_json, windows_file_busy
from schedule import RetryGate
from layer_scope import compose
from perf_diagnostics import PerfRecorder
from motion_delta import DeltaRejected,PatchCache


def parent_alive(pid):
    if not pid:
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = wintypes.DWORD()
        try:
            return bool(api.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def write_status_file(path,data,recorder):
    try:
        write_json(path,data)
        return True
    except OSError as exc:
        # A heartbeat publishes no new receipt. Keep the last valid status and
        # try again on the next idle heartbeat instead of terminating Blender.
        # All command/ack/checkpoint writes still fail after bounded retries.
        if data.get('phase')!='idle' or not windows_file_busy(exc):raise
        recorder.event('status_write_deferred',phase='idle',
                       acknowledged_revision=data.get('acknowledged_revision'),
                       winerror=getattr(exc,'winerror',None),error=str(exc))
        print('BRIDGE_STATUS_WRITE_DEFERRED '+str(exc),flush=True)
        return False


def main(directory):
    import bpy
    from incremental import IncrementalImporter
    from d5_adapter import D5Adapter
    directory = Path(directory).resolve()
    owner = read_json(directory / "owner.json", {})
    config = read_json(directory / "config.json", {})
    if owner.get("plugin") != "ingetrazo_d5_livesync":
        raise RuntimeError("拒絕開啟不屬於此橋接器的工作目錄")
    importer = None
    lease = None
    project = Path(config.get("project_directory", directory)).resolve()
    project.mkdir(parents=True, exist_ok=True)
    bridge_file = project / "bridge.blend"
    model_file = project / "model.igz"
    ack = 0
    adapter = None
    phase = "starting"
    baseline = None
    material_optimization_backup = None
    last_metrics = {}
    recorder=PerfRecorder(directory)
    def status(state, message, **extra):
        nonlocal phase
        previous_phase=phase
        phase = state
        if extra.get('bridge_backup_warning'):message+='；本輪還原點備份未完成，後續更新會先重試備份'
        data = {"phase": state, "message": message, "updated_at": time.time(),
                "worker_pid": os.getpid(), "acknowledged_revision": ack,
                "scope_baseline_ready": baseline is not None, **extra}
        if not write_status_file(directory / "status.json", data,recorder):return data
        if state!='idle' or previous_phase!=state:
            recorder.event('phase',phase=state,message=message,revision=extra.get('revision',extra.get('processing_revision')),acknowledged_revision=ack)
        # status.json retains metrics; avoid printing them again every 3 sec.
        printable=data if state!='idle' else {k:data[k] for k in ('phase','message','updated_at','worker_pid','acknowledged_revision','scope_baseline_ready')}
        print("BRIDGE_STATUS " + json.dumps(printable, ensure_ascii=False), flush=True)
        return data
    try:
        status("starting", "背景 Blender 正在載入官方 D5 LiveSync")
        from project_store import ProjectLease, checkpoint, material_ids
        lease = ProjectLease(project).acquire()
        from bridge_backups import (bootstrap_state,create_point,prepare_checkpoint,
                                    recover_checkpoint,recover_restore,prune_points,VERSION)
        recover_restore(project)
        recover_checkpoint(project)
        addon = None
        restored = bridge_file.exists()
        if restored:
            # Register SDK RNA properties before loading the saved IDs.
            import addon_utils
            addon = addon_utils.enable("d5_export", default_set=True, persistent=False)
            if addon is None:
                raise RuntimeError("找不到官方 D5 LiveSync for Blender")
            bpy.ops.wm.open_mainfile(filepath=str(bridge_file), load_ui=False)
            expected = config.get("project_id")
            if expected and bpy.context.scene.get("bridge_project_id") != expected:
                raise RuntimeError("橋接檔的專案識別不一致，已停止以保護原關聯")
        adapter = D5Adapter(config["d5_path"], addon=addon)
        adapter.recorder=recorder
        adapter.detailed_log=bool(config.get('detailed_log',False))
        adapter.incremental_enabled = bool(config.get("incremental_d5", False))
        if not restored:
            # Only clear this worker's factory scene for a genuinely new project.
            for obj in list(bpy.data.objects):
                bpy.data.objects.remove(obj, do_unlink=True)
        bpy.context.scene["bridge_project_id"] = config.get("project_id", "legacy")
        baseline_name = bpy.context.scene.get('bridge_scope_baseline', '')
        if baseline_name:
            import re
            if not re.fullmatch(r'scope-[0-9a-f]{64}\.igz', baseline_name):
                raise RuntimeError('圖層基準識別不合法，已停止以保護原同步內容')
            baseline = project / 'scope-cache' / baseline_name
            if not baseline.is_file():
                raise RuntimeError('上次確認的圖層基準遺失；請還原 projects 完整備份，勿直接使用暫時不更新')
            import hashlib
            if hashlib.sha256(baseline.read_bytes()).hexdigest() != baseline_name[6:-4]:
                raise RuntimeError('上次確認的圖層基準損壞，已停止同步')
            if not bpy.context.scene.get('bridge_has_receipt'):
                raise RuntimeError('橋接檔沒有成功確認紀錄，請先保留檔案並選擇有效還原點')
            from layer_scope import Bundle,META
            prior=Bundle(baseline)
            try:prior_policy=prior.data.get(META,{}).get('policy',{})
            finally:prior.close()
            saved=bootstrap_state(project,baseline,{'plugin_version':'legacy','recorded_by':VERSION,
                'blender_version':bpy.app.version_string,'sdk_version':list(adapter.version),
                'revision':None,'layer_policy':prior_policy})
            if Path(saved['scope_file']).name!=baseline_name:raise RuntimeError('橋接檔與確認快照指標不符，請先還原有效配對')
            from project_transfer import rebind_model_owners
            rebound=rebind_model_owners(bpy.data,saved,model_file)
            if rebound:recorder.event('transfer_ownership_rebound',properties=rebound)
        importer = IncrementalImporter()
        last_heartbeat = 0
        initial = True
        retry = RetryGate()
        busy_started=None
        duplicate_cache={}
        patch_cache=PatchCache()
        while parent_alive(owner.get("parent_pid")):
            control = read_json(directory / "control.json", {})
            if control.get("stop"):
                break
            latest = read_json(directory / "latest.json", {})
            revision = int(latest.get("revision", 0))
            if revision <= ack:
                if time.monotonic() - last_heartbeat > 3:
                    status("idle", "等待 IngeTrazo 下一次修改", **last_metrics)
                    last_heartbeat = time.monotonic()
                time.sleep(0.2)
                continue
            if control.get("paused") and revision > int(control.get("allowed_revision", 0)):
                time.sleep(0.2)
                continue
            if not retry.ready(revision, time.monotonic(), control.get("retry_request", 0)):
                time.sleep(0.2)
                continue
            try:
                if recover_checkpoint(project):
                    from bridge_backups import current_state
                    baseline=project/current_state(project)['scope_file']
                    recorder.event('checkpoint_recovered',revision=revision)
                if adapter.busy():
                    if busy_started is None:busy_started=time.perf_counter()
                    if time.monotonic() - last_heartbeat > 3:
                        status("waiting_d5", "D5 正在渲染／更新；完成後自動重試")
                        last_heartbeat = time.monotonic()
                    time.sleep(0.5)
                    continue
                busy_wait_seconds=time.perf_counter()-busy_started if busy_started is not None else 0.
                busy_started=None
                motion=latest.get('kind')=='transforms'
                local=latest.get('kind')=='partial'
                update_kind='transforms' if motion else 'partial' if local else 'full'
                filename = f"transforms-{revision:08d}.json" if motion else f"partial-{revision:08d}.igz" if local else f"snapshot-{revision:08d}.igz"
                if latest.get("file") != filename:
                    raise RuntimeError("快照檔名或版本不一致")
                started = time.perf_counter()
                status("composing", f"正在合併第 {revision} 版圖層與材質", processing_revision=revision)
                # Stable document path preserves importer object keys on reload;
                # the source revision remains immutable until acknowledged.
                pending = project / "model.igz.pending"
                if motion:
                    if not config.get('fast_transforms') or initial or not baseline:raise DeltaRejected('尚無本連線的快速更新基準')
                    from motion_delta import prepare_patch,apply_placements
                    payload=read_json(directory/filename,{})
                    updates,motion_stats=prepare_patch(baseline,payload,ack,pending,cache=patch_cache)
                    scope={**last_metrics.get('layer_scope',{}),'d5_lights':motion_stats['d5_lights'],'d5_light_ids':motion_stats['d5_light_ids']}
                elif local:
                    if not config.get('partial_updates') or initial or not baseline:raise DeltaRejected('尚無局部更新基準')
                    if config.get('compact_legacy_materials'):raise DeltaRejected('材質整理模式需完整同步')
                    from local_update import prepare_local
                    local_keys,local_stats=prepare_local(baseline,directory/filename,ack,pending,patch_cache)
                    scope={**last_metrics.get('layer_scope',{}),**local_stats}
                else:scope = compose(directory / filename, baseline, latest.get('layer_policy', {}), pending,
                                lights=latest.get('d5_lights', []),
                                light_layer_map=latest.get('d5_light_layers', {}),
                                known_light_ids=latest.get('d5_light_ids', []),
                                compact_legacy=bool(config.get('compact_legacy_materials',False)))
                if baseline is not None and bridge_file.exists():
                    reasons=['before_sync']
                    if not motion and not local:
                        if scope.get('visibility_migrated'):reasons.append('visibility_upgrade')
                        if scope.get('material_identity_migrated'):reasons.append('identity_migration')
                        if scope.get('material_policy_changed'):reasons.append('layer_policy_change')
                        if scope.get('legacy_aliases_collapsed',0):reasons.append('material_compaction')
                    point=create_point(project,reasons,pinned=len(reasons)>1)
                    scope['bridge_restore_point']=str(point)
                    recorder.event('bridge_restore_point',revision=revision,path=str(point),reasons=reasons)
                if (scope.get('legacy_aliases_collapsed',0)>0 and material_optimization_backup is None
                        and baseline is not None and bridge_file.exists()):
                    from optimization_backup import backup_bridge_identity
                    material_optimization_backup=backup_bridge_identity(project,baseline)
                composed_at = time.perf_counter()
                status('transforming' if motion else 'updating_local' if local else 'importing',
                       f'Blender 正在套用第 {revision} 版位置更新' if motion else f'Blender 正在局部更新第 {revision} 版群組／材質' if local else f"Blender 正在匯入第 {revision} 版模型",processing_revision=revision)
                # This worker owns its imported collections. Reset visibility so
                # a previously hidden IngeTrazo tag can become visible again.
                if motion:
                    apply_placements(bpy,updates,model_file)
                    os.replace(pending,model_file)
                    stats={**last_metrics.get('stats',{}),'meshes_built':0,'blocks_decoded':0,'blocks_reused':0,
                           'parse_seconds':0.,'build_seconds':0.,'importer_skipped':True,'placement_groups_updated':len(updates)}
                elif local:
                    from local_importer import load_local
                    os.replace(pending,model_file)
                    stats=load_local(importer,bpy.context,str(model_file),local_keys,patch_cache.document)
                else:
                    os.replace(pending,model_file)
                    for collection in bpy.data.collections:
                        if collection.get("ingetrazo_doc") == str(model_file):
                            collection.hide_viewport = False
                            collection.hide_render = False
                    stats = importer.load(bpy.context, str(model_file))
                bpy.context.view_layer.update()
                imported_at = time.perf_counter()
                duplicate_started=time.perf_counter()
                if motion:
                    from motion_delta import refresh_report
                    duplicate_report=refresh_report(read_json(directory/'duplicate-models-latest.json',{}),bpy,revision)
                elif config.get('duplicate_scan',True):
                    status('analyzing','正在辨識重複模型',processing_revision=revision)
                    try:
                        from duplicate_models import scan_duplicates
                        duplicate_report=scan_duplicates(bpy,cache=duplicate_cache)
                    except Exception as exc:
                        duplicate_report={'summary':{'complete':False},'errors':[str(exc)],'applied':False,'sdk_instancing_verified':False}
                else:
                    duplicate_report={'enabled':False,'summary':{'disabled':True},'applied':False,'sdk_instancing_verified':False}
                duplicate_report['revision']=revision
                recorder.snapshot('duplicate-models-latest.json',duplicate_report)
                duplicate_scan_seconds=time.perf_counter()-duplicate_started
                recorder.event('duplicate_scan',revision=revision,summary=duplicate_report.get('summary'),errors=duplicate_report.get('errors',[]))
                initial_save_started=time.perf_counter()
                if initial and not restored:
                    bpy.ops.wm.save_as_mainfile(filepath=str(bridge_file), check_existing=False,
                                               compress=False)
                initial_bridge_save_seconds=time.perf_counter()-initial_save_started
                status("connecting" if initial else "sending",
                       "官方 Sync 正在處理模型／材質並連接 D5；首次請選原專案與材質繼承" if initial
                       else f"官方 D5 Sync 正在傳送第 {revision} 版", processing_revision=revision)
                adapter.perf_context={'revision':revision,'attempt':retry.failures+1,'update_kind':update_kind,
                                      'sync_decision':latest.get('sync_decision',{}),
                                      'source_payload_bytes':latest.get('bytes')}
                send_started=time.perf_counter()
                receipt = adapter.send(initial=initial, lights=scope['d5_lights'],
                                       light_ids=scope['d5_light_ids'])
                sent_at = time.perf_counter()
                status('checkpointing','正在保存本輪同步識別',processing_revision=revision)
                bpy.context.scene['bridge_has_receipt'] = True
                # SDK-generated IDs exist only after send. Persist them now, not
                # just in the pre-send file used by the old bridge.
                # The .blend pointer and immutable complete IGZ are one
                # recoverable checkpoint. Unconfirmed snapshots never become
                # the next hold baseline, including after process restart.
                import hashlib
                scope_directory = project / 'scope-cache'
                scope_directory.mkdir(exist_ok=True)
                confirmed_name = 'scope-' + hashlib.sha256(model_file.read_bytes()).hexdigest() + '.igz'
                confirmed_path = scope_directory / confirmed_name
                if not confirmed_path.exists():
                    confirmed_temp = scope_directory / (confirmed_name + '.pending')
                    shutil.copyfile(model_file, confirmed_temp)
                    os.replace(confirmed_temp, confirmed_path)
                bpy.context.scene['bridge_scope_baseline'] = confirmed_name
                bpy.context.scene['bridge_confirmed_revision']=revision
                checkpoint_metadata={'plugin_version':VERSION,'blender_version':bpy.app.version_string,
                    'sdk_version':list(adapter.version),'revision':revision,'session_id':directory.name,
                    'layer_policy':scope.get('layer_policy',{})}
                checkpoint(bpy,bridge_file,before_install=lambda pending_blend:
                    prepare_checkpoint(project,confirmed_path,checkpoint_metadata,pending_blend))
                recover_checkpoint(project)
                previous_baseline = baseline
                baseline = confirmed_path
                backup_warning='';confirmed_restore_point=None
                try:
                    confirmed_restore_point=str(create_point(project,['confirmed']))
                    recorder.event('bridge_confirmed_restore_point',revision=revision,path=confirmed_restore_point)
                except (OSError,ValueError,KeyError,TypeError) as exc:
                    # The SDK receipt and paired checkpoint are already valid.
                    # Keep them truthful; before the NEXT send, create_point
                    # must succeed, so storage failure cannot skip protection.
                    backup_warning=str(exc)
                    recorder.event('bridge_confirmed_backup_failed',revision=revision,error=backup_warning)
                if not motion and not local and (config.get('fast_transforms') or config.get('partial_updates')):
                    try:patch_cache.prime(baseline,confirmed_name[6:-4])
                    except Exception as exc:
                        patch_cache=PatchCache()
                        recorder.event('motion_cache_unavailable',revision=revision,error=str(exc))
                # Keep current and previous confirmed copies. Orphans from a
                # failed send/checkpoint are never used as a baseline.
                for obsolete in scope_directory.glob('scope-*.igz'):
                    if obsolete not in (baseline, previous_baseline):
                        try:obsolete.unlink()
                        except OSError:pass
                try:
                    removed=prune_points(project) if not backup_warning else []
                    if removed:recorder.event('bridge_backup_pruned',paths=removed)
                except (OSError,ValueError,KeyError,TypeError) as exc:
                    recorder.event('bridge_backup_prune_deferred',error=str(exc))
                checkpoint_seconds = time.perf_counter() - sent_at
                initial = False
                metrics = {"revision": revision, "import_seconds": imported_at - started,
                           "scope_compose_seconds": composed_at - started,
                           "blender_import_seconds": imported_at - composed_at,
                           "send_seconds": sent_at - send_started,
                           "duplicate_scan_seconds":duplicate_scan_seconds,
                           "initial_bridge_save_seconds":initial_bridge_save_seconds,
                           "checkpoint_seconds": checkpoint_seconds,
                           "material_optimization_backup": material_optimization_backup,
                           "bridge_confirmed_restore_point":confirmed_restore_point,
                           "bridge_backup_warning":backup_warning,
                           "restored_bridge": restored,
                           "bridge_file": str(bridge_file),
                           "export_seconds": latest.get("export_seconds"),
                           "snapshot_filter_seconds": latest.get("snapshot_filter_seconds"),
                           "snapshot_serialize_seconds": latest.get("snapshot_serialize_seconds"),
                           "snapshot_validate_seconds": latest.get("snapshot_validate_seconds"),
                           "snapshot_to_send_seconds": time.time() - latest["published_at"],
                           "update_kind":update_kind,"sync_decision":latest.get('sync_decision',{}),
                           "source_fast_path":bool(motion or local),"fast_transform_protocol":1 if (config.get('fast_transforms') or config.get('partial_updates')) else 0,
                           "partial_update_protocol":1 if config.get('partial_updates') else 0,
                           "model_sha256":confirmed_name[6:-4],
                           "fast_fallback_reason":latest.get('fast_fallback_reason',''),
                           "d5_busy_wait_seconds":busy_wait_seconds,
                           "stats": stats, "layer_scope": {k:v for k,v in scope.items()
                           if k not in ('d5_lights','d5_light_ids')}, **receipt}
                with (directory / "timings.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(metrics, ensure_ascii=False) + "\n")
                performance=receipt.get('performance',{})
                if performance:
                    performance['revision_confirmed']=True
                    performance['worker_timing']={key:metrics.get(key) for key in ('export_seconds','scope_compose_seconds','blender_import_seconds','duplicate_scan_seconds','initial_bridge_save_seconds','send_seconds','checkpoint_seconds','snapshot_to_send_seconds','d5_busy_wait_seconds')}
                    performance['duplicate_models']=duplicate_report.get('summary',{})
                    performance['diagnostics_errors']=list(recorder.errors)
                    recorder.snapshot('performance-latest.json',performance)
                recorder.event('revision_confirmed',revision=revision,attempt=retry.failures+1,
                               export_seconds=metrics.get('export_seconds'),send_seconds=metrics['send_seconds'],
                               snapshot_to_send_seconds=metrics['snapshot_to_send_seconds'])
                last_metrics = metrics
                ack = revision
                status("sent", f"第 {revision} 版已由官方 Sync 成功傳送到 D5", **metrics)
                last_heartbeat = time.monotonic()
            except DeltaRejected as exc:
                retry.failures=4
                recorder.event('fast_update_rejected',revision=revision,error=str(exc))
                status('need_full','快速更新條件不符，正在改用完整同步：'+str(exc),processing_revision=revision)
            except Exception as exc:
                traceback.print_exc()
                failed_performance=dict(getattr(adapter,'last_performance',{}))
                if failed_performance.get('revision')!=revision or failed_performance.get('attempt')!=retry.failures+1:
                    failed_performance={'revision':revision,'attempt':retry.failures+1,'success':False}
                failed_performance.update(revision_confirmed=False,worker_error=str(exc),failed_phase=phase)
                recorder.snapshot('performance-latest.json',failed_performance)
                recorder.event('revision_failed',revision=revision,attempt=retry.failures+1,phase=phase,error=str(exc))
                retry.failed(time.monotonic())
                status("retry_wait" if retry.failures >= 4 else "retrying",
                       ("本版傳送失敗，按「更新」重試：" if retry.failures >= 4
                        else "本版傳送失敗，稍後自動重試：") + str(exc),
                       processing_revision=revision, retry_failures=retry.failures)
            time.sleep(0.2)
        status("stopped", "背景同步已停止；D5 場景保留")
    except Exception as exc:
        traceback.print_exc()
        status("error", str(exc), failed_phase=phase)
        raise
    finally:
        if adapter:
            adapter.close()
        if lease:
            lease.close()


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:]
    if len(args) != 1:
        raise SystemExit("Expected one bridge session directory")
    try:
        main(args[0])
    except Exception as error:
        # Include failures before main's imports/status loop is initialized.
        directory=Path(args[0])
        current=read_json(directory/'status.json',{})
        if current.get('phase')!='error':
            write_json(directory/'status.json',{'phase':'error','message':'背景 Blender 啟動失敗：'+str(error),'acknowledged_revision':0,'updated_at':time.time()})
        raise
