# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapter to the independently installed official D5 Blender add-on.

The SDK and official add-on are not redistributed. All changes below are
in-memory settings of this dedicated worker, not edits to D5's installation.
"""
import json
import sys
import time
import bpy
import addon_utils


class D5Adapter:
    def __init__(self, render_path, addon=None):
        if addon is None:addon = addon_utils.enable("d5_export", default_set=True, persistent=False)
        if addon is None:
            raise RuntimeError("找不到官方 D5 LiveSync for Blender；請先安裝")
        self.version = tuple(addon.bl_info["version"])
        if self.version != (1, 0, 0, 28):
            raise RuntimeError(f"此橋接版只驗證 D5 Sync 1.0.0.0028，目前是 {self.version}")
        from d5_export.tools import d5plugin
        from d5_export.ops.d5_converter import D5ConverterOperator
        from d5_export.ops.d5_sync import D5SyncOperator
        self.sdk = d5plugin
        self.converter = D5ConverterOperator.get()
        self.sent_state = None
        self.incremental_enabled = False
        self.connected = False
        self.last_error = ""
        self.last_return_code = None
        self.send_calls = 0
        D5SyncOperator.use_progress = False
        D5SyncOperator.use_progress_window = False
        # Native modal error dialogs would deadlock a hidden worker. Surface
        # them in the IngeTrazo panel instead; do not silently swallow errors.
        for name, module in list(sys.modules.items()):
            if name.startswith("d5_export.") and hasattr(module, "ErrorDialog"):
                module.ErrorDialog = self.report_error
        bpy.context.preferences.addons["d5_export"].preferences.d5_render_path = render_path
        self._original_send = self.converter.send_raw
        self.converter.send_raw = self._send_with_receipt
        from d5_lights import LightSender
        self.light_sender = LightSender(self.sdk, self.converter)

    def report_error(self, message, *args):
        self.last_error = str(message)
        print("[IngeTrazo-D5]", self.last_error, flush=True)

    def _send_with_receipt(self):
        started=time.perf_counter();self._event('send_raw_begin')
        try:
            result = self._original_send()
            self.last_return_code = int(result)
            self.send_calls += 1
            return result
        finally:
            elapsed=time.perf_counter()-started
            self._raw_seconds=getattr(self,'_raw_seconds',0)+elapsed
            self._raw_measured_calls=getattr(self,'_raw_measured_calls',0)+1
            self._event('send_raw_end',seconds=elapsed,return_code=self.last_return_code)

    def _event(self,event,**data):
        recorder=getattr(self,'recorder',None)
        if recorder is not None:recorder.event(event,**{**getattr(self,'perf_context',{}),**data})

    def state(self):
        return self.sdk.GetD5RenderStatus(False)

    def busy(self):
        return self.state() in (self.sdk.D5RenderNotReadyRendering,
                                self.sdk.D5RenderNotReadyUpdating,
                                self.sdk.D5RenderHardwareChecking)

    def send(self, initial=False, lights=None, light_ids=None):
        from material_audit import audit_materials,compare_audits
        started=time.perf_counter();recorder=getattr(self,'recorder',None)
        self._raw_seconds=0.;self._raw_measured_calls=0;self._operator_seconds=0.;self._prepare_seconds=0.;self._lights_seconds=0.
        self._official_observed={};self._operator_ran=False;receipt={};error=''
        context={**getattr(self,'perf_context',{}),'initial_connection':bool(initial),
                 'incremental_enabled':bool(self.incremental_enabled),'connected_before':bool(self.connected)}
        self._event('adapter_begin',initial_connection=bool(initial))
        def audit():
            if recorder is None:return {}
            try:return audit_materials(bpy)
            except Exception as exc:return {'error':str(exc),'summary':{},'materials':[]}
        audit_started=time.perf_counter();before=audit();before_seconds=time.perf_counter()-audit_started
        try:
            receipt=self._send_impl(initial=initial,lights=lights,light_ids=light_ids)
            return receipt
        except Exception as exc:
            error=str(exc);raise
        finally:
            audit_started=time.perf_counter();after=audit();after_seconds=time.perf_counter()-audit_started
            observed=dict(self._official_observed)
            observed.pop('unreferenced_sent_materials',None)
            timing={'prepare_seconds':self._prepare_seconds,'official_operator_seconds':self._operator_seconds,
                    'official_send_raw_seconds':self._raw_seconds if self._raw_measured_calls else None,
                    'official_operator_outside_send_raw_seconds':max(0,self._operator_seconds-self._raw_seconds) if self._raw_measured_calls else None,
                    'send_raw_calls_measured':self._raw_measured_calls,'lights_seconds':self._lights_seconds,
                    'audit_before_seconds':before_seconds,'audit_after_seconds':after_seconds,
                    'adapter_total_seconds':time.perf_counter()-started}
            report={**context,'success':not bool(error),'error':error,'sync_mode':receipt.get('sync_mode'),
                    'official_operator_called':self._operator_ran,
                    'official_return_code':self.last_return_code if self._operator_ran else None,
                    'timing':timing,'material_summary':after.get('summary',{}),'official_observed':observed,
                    'material_audit_errors':[a['error'] for a in (before,after) if a.get('error')]}
            if recorder is not None:
                write_started=time.perf_counter()
                recorder.snapshot('material-audit-latest.json',{
                    **context,'before':before,'after':after,'change':compare_audits(before,after),
                    'official_observed':self._official_observed})
                timing['diagnostic_detail_write_seconds']=time.perf_counter()-write_started
                timing['adapter_total_seconds']=time.perf_counter()-started
                report['diagnostics_errors']=list(recorder.errors)
                recorder.snapshot('performance-latest.json',report)
                self._event('adapter_end',**report)
            self.last_performance=report;receipt['performance']=report

    def _send_impl(self, initial=False, lights=None, light_ids=None):
        impl_started=time.perf_counter()
        from project_store import material_ids
        current_material_ids = material_ids(bpy)
        # A saved .blend receipt does not mean a NEW official converter has
        # material definitions in its native session/cache. Never suppress the
        # official material payload to attempt to preserve D5 overrides.
        # Persistent identity + D5's own inheritance must handle overrides.
        export_materials = True
        from sync_plan import capture, plan
        objects = list(bpy.context.view_layer.objects)
        current = capture(objects)
        mode, changed = plan(self.sent_state, current)
        if initial or not self.incremental_enabled or not self.connected or self.state() != self.sdk.D5RenderInProject:
            mode = 'full'
        if mode == 'unchanged':
            self._prepare_seconds=time.perf_counter()-impl_started;lights_started=time.perf_counter()
            light_receipt = self.light_sender.send(lights or [], bpy.data.filepath, light_ids)
            self._lights_seconds=time.perf_counter()-lights_started
            self.sent_state = current
            return {'official_return_code': 0, 'render_state': str(self.state()),
                    'send_calls': self.send_calls, 'sync_mode': 'lights_or_unchanged',
                    **light_receipt}
        from d5_export.handlers import D5Handler
        from d5_export.handlers.update import _frame_geometry_changed
        from d5_export.data.sync import live_sync_data
        # Our file loop batches updates explicitly; do not run a second,
        # asynchronous scene-handler sync alongside it.
        D5Handler.unregister(D5Handler.ON_UPDATE_POST)
        live_sync_data["suppress_update"] = True
        sync = bpy.context.window_manager.d5.sync
        sync.use_live_sync = False
        sync.use_live_view = False
        live_sync_data["suppress_update"] = False
        sync.enabled = True
        sync.in_process = False
        updates = bpy.context.scene.d5.sync.updates
        updates.clear()
        if not initial and mode == 'transforms':
            # Retain official mesh cache and send only placements. Geometry,
            # material, deletion and tag changes all use the full fallback.
            for obj in objects:
                if obj.as_pointer() in changed:
                    obj.d5.set_dirty(False)
                    updates.add(obj, "object")
        elif not initial:
            # Rebuilds may replace mesh data while preserving object identity.
            # Mark geometry explicitly, rather than trusting depsgraph timers
            # that do not run inside a --background polling loop.
            updates.delete = True
            updates.collections_changed = True
            self.converter.clear_mesh_cache()
            for obj in bpy.context.view_layer.objects:
                if obj.type in {"MESH", "EMPTY"}:
                    obj.d5.set_dirty(False)
                    updates.add(obj, "object")
                if obj.type == "MESH":
                    _frame_geometry_changed.add(obj.name)
                    obj.data.d5.set_dirty(False)
                    updates.add(obj.data, "mesh")
            if export_materials:
                for material in bpy.data.materials:
                    material.d5.set_dirty(False)
                    updates.add(material, "material")
        self.last_error = ""
        self.last_return_code = None
        calls_before = self.send_calls
        self._prepare_seconds=time.perf_counter()-impl_started
        from official_log import OfficialLogCapture
        capture_log=OfficialLogCapture(sys.stdout,detailed=getattr(self,'detailed_log',False))
        original_stdout=sys.stdout;operator_started=time.perf_counter();self._event('official_operator_begin')
        self._operator_ran=True
        try:
            sys.stdout=capture_log
            result = bpy.ops.d5.sync(
                "EXEC_DEFAULT", mode="SYNC", use_sync_updates=not initial,
                use_d5a_updates=False, use_bake=False, use_selection=False,
                use_export_scenes=False, use_export_lights=False,
                use_export_animation=False, export_materials=export_materials)
        finally:
            sys.stdout=original_stdout
            capture_log.finish();self._official_observed=capture_log.summary()
            self._operator_seconds=time.perf_counter()-operator_started
            self._event('official_operator_end',seconds=self._operator_seconds)
            _frame_geometry_changed.clear()
        if (result != {"FINISHED"} or self.send_calls == calls_before
                or self.last_return_code != 0):
            self.connected = False
            raise RuntimeError(self.last_error or f"D5 同步未成功：{result}，回報碼 {self.last_return_code}")
        self.connected = True
        lights_started=time.perf_counter()
        light_receipt = self.light_sender.send(lights or [], bpy.data.filepath, light_ids)
        self._lights_seconds=time.perf_counter()-lights_started
        bpy.context.scene['bridge_sent_material_ids'] = json.dumps(sorted(current_material_ids))
        self.sent_state = current
        return {"source_materials_sent": export_materials, "sync_mode": mode, "transform_objects": len(changed) if mode == "transforms" else 0,
                "official_return_code": self.last_return_code,
                "render_state": str(self.state()), "send_calls": self.send_calls, **light_receipt}

    def close(self):
        self.converter.destroy_session()
