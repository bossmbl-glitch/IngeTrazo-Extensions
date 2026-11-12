# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, best-effort local diagnostics. Never changes model or receipts."""
from __future__ import annotations
import json,os,time,zipfile
from pathlib import Path


class PerfRecorder:
    def __init__(self,directory,max_bytes=2*1024*1024,backups=2,journal='sync-events.jsonl'):
        if journal not in ('sync-events.jsonl','source-events.jsonl'):raise ValueError('Unknown journal')
        self.directory=Path(directory);self.max_bytes=max_bytes;self.backups=backups;self.errors=[];self.journal=journal

    def _failure(self,exc):
        message=str(exc)
        if message not in self.errors:self.errors=(self.errors+[message])[-5:]
        return False

    def event(self,event,**data):
        try:
            path=self.directory/self.journal
            line=json.dumps({'time':time.time(),'event':event,**data},ensure_ascii=False)+'\n'
            if path.exists() and path.stat().st_size+len(line.encode('utf-8'))>self.max_bytes:
                path.with_name(path.name+'.'+str(self.backups)).unlink(missing_ok=True)
                for n in range(self.backups-1,0,-1):
                    old=path.with_name(path.name+'.'+str(n))
                    if old.exists():os.replace(old,path.with_name(path.name+'.'+str(n+1)))
                os.replace(path,path.with_name(path.name+'.1'))
            with path.open('a',encoding='utf-8') as f:f.write(line)
            return True
        except Exception as exc:return self._failure(exc)

    def snapshot(self,name,data):
        if name not in ('performance-latest.json','material-audit-latest.json','duplicate-models-latest.json','sync-decision-latest.json'):raise ValueError('Unknown diagnostic file')
        path=self.directory/name;pending=path.with_suffix('.pending')
        try:
            pending.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
            os.replace(pending,path);return True
        except Exception as exc:
            try:pending.unlink(missing_ok=True)
            except OSError:pass
            return self._failure(exc)


def read_json_safe(path):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}


REASONS={'first_full_sync':'本次連線首次完整同步','requested_full_update':'手動要求完整更新',
         'command_history_overflow':'近期命令追蹤超過保留上限，需重新建立完整基準',
         'unsupported_history':'目前主程式命令介面無法安全追蹤','mesh_serial_unavailable':'網格缺少可追蹤的修改序號',
         'host_lights_require_full_update':'場景含橋接自建光源，使用完整同步','host_light_proxy_requires_full_update':'場景含光源代理，使用完整同步',
         'group_geometry_or_metadata_changed':'群組幾何或屬性有變動','scene_or_material_changed':'場景或材質有變動',
         'no_confirmed_placement_command':'沒有可確認的純位置命令','no_content_change':'沒有新的內容版本',
         'unsupported_transform':'變換不屬於可確認的純移動／旋轉','billboard_subtree':'受影響群組含面向相機物件',
         'no_placement_change':'沒有可獨立傳送的位置差異',
         'worker_requested_full':'背景接收端要求完整更新','non_placement_command':'含移動／旋轉以外的操作',
         'unsupported_command_or_external_change':'有未支援的命令或未經命令紀錄的內容變動',
         'group_structure_changed':'新增、刪除或群組階層已變更','group_structure_or_metadata_changed':'群組識別、階層、圖層或屬性已變更',
         'global_settings_changed':'全域設定或圖層模式已變更','loose_geometry_changed':'未分組幾何已變更',
         'loose_material_changed':'材質變動影響未分組幾何','partial_scope_too_broad':'影響群組過多，完整更新較合適',
         'texture_file_changed':'既有貼圖檔案已變更','no_local_visual_change':'沒有可獨立更新的可見群組變動',
         'group_edit_context':'正在編輯群組內部','certified_local_change':'已確認受影響群組，使用局部更新',
         'certified_placement':'已確認只有位置／旋轉變動','disabled_or_no_confirmed_baseline':'局部功能未開啟或尚無本連線確認基準'}

def decision_reason(decision):
    code=decision.get('partial_reason') if decision.get('route')=='full' else decision.get('reason')
    if code in ('disabled_or_no_confirmed_baseline',None,''):code=decision.get('reason','')
    return REASONS.get(code,code or '等待判定')

def decision_text(decision):
    routes={'full':'完整同步','partial':'局部形狀／材質更新','transforms':'快速位置更新'}
    rows=['本輪更新決策','提交版本：'+str(decision.get('revision','—')),
          '更新方式：'+routes.get(decision.get('route'),'等待判定'),'原因：'+decision_reason(decision)]
    if decision.get('receiver_reason'):rows.append('接收端原因：'+str(decision['receiver_reason']))
    for item in decision.get('commands',[]):
        rows.append('操作：'+str(item.get('action',''))+'／'+str(item.get('command','')))
        if item.get('face_count'):rows.append('  操作面數：'+str(item['face_count']))
        if item.get('commands') and item.get('commands')!=[item.get('command')]:rows.append('  子命令：'+'、'.join(item['commands']))
        for target in item.get('targets',[]):rows.append('  目標：'+str(target.get('name',''))+'；識別 '+str(target.get('uid','')))
    for item in decision.get('changed_groups',[]):
        changes=[name for flag,name in [('geometry','幾何'),('material','群組材質'),('placement','位置／旋轉'),('metadata','屬性')] if item.get(flag)]
        rows.append('群組：'+item.get('name','')+'；圖層 '+item.get('layer','')+'；變動 '+'、'.join(changes))
    if decision.get('changed_materials'):rows.append('材質庫變更：'+'、'.join(decision['changed_materials']))
    if decision.get('added_keys'):rows.append('新增識別：'+'、'.join(decision['added_keys']))
    if decision.get('removed_keys'):rows.append('移除識別：'+'、'.join(decision['removed_keys']))
    if decision.get('root_names'):rows.append('局部範圍：'+'、'.join(decision['root_names']))
    if decision.get('inspection_unavailable'):rows.append('變動盤點限制：'+str(decision['inspection_unavailable']))
    rows.append('完整機器可讀資料見sync-decision-latest.json；命令最多保留近期256筆，清單可能截斷。')
    return '\n'.join(rows)+'\n'


def performance_text(performance):
    if not performance:return '尚無官方傳送的分段量測；請先同步一次。\n'
    timing=performance.get('timing',{})
    def seconds(name):
        value=timing.get(name)
        return f'{value:.3f} 秒' if isinstance(value,(int,float)) else '未量測'
    rows=['同步效能摘要','版本：0.9.9',
          '提交版本：'+str(performance.get('revision','—')),
          '本次嘗試：'+str(performance.get('attempt','—')),
          '類型：'+('本次背景程序首次傳送' if performance.get('initial_connection') else '後續更新'),
          '適配器處理：'+('成功' if performance.get('success') else '失敗／尚未成功'),
          '官方回傳碼：'+(str(performance.get('official_return_code')) if performance.get('official_operator_called') else '本輪未呼叫官方模型同步'),
          '橋接保存確認：'+('已完成' if performance.get('revision_confirmed') else '待確認／失敗'),
          '來源資料路徑：'+({'transforms':'快速移動／旋轉更新','partial':'局部形狀／材質更新'}.get(performance.get('update_kind'),'完整模型快照')),
          '來源資料大小：'+str(performance.get('source_payload_bytes','—'))+' bytes',
          '官方同步模式：'+str(performance.get('sync_mode','—'))]
    for label,key in [('同步前準備','prepare_seconds'),('官方同步操作總時間','official_operator_seconds'),
                      ('其中 send_raw 呼叫','official_send_raw_seconds'),
                      ('官方操作中其餘時間','official_operator_outside_send_raw_seconds'),
                      ('橋接光源','lights_seconds'),('材質檢查（前）','audit_before_seconds'),
                      ('材質檢查（後）','audit_after_seconds'),('材質明細寫入','diagnostic_detail_write_seconds'),
                      ('適配器合計','adapter_total_seconds')]:
        rows.append(label+'：'+seconds(key))
    worker=performance.get('worker_timing',{})
    for label,key in [('來源匯出','export_seconds'),('圖層與材質合併','scope_compose_seconds'),
                      ('Blender匯入','blender_import_seconds'),('官方適配器整段','send_seconds'),
                      ('重複模型辨識','duplicate_scan_seconds'),('首次橋接預存','initial_bridge_save_seconds'),
                      ('橋接保存','checkpoint_seconds'),('預先等待D5忙碌','d5_busy_wait_seconds')]:
        value=worker.get(key)
        if isinstance(value,(int,float)):rows.append(label+f'：{value:.3f} 秒')
    if isinstance(worker.get('export_seconds'),(int,float)) and isinstance(worker.get('snapshot_to_send_seconds'),(int,float)):
        rows.append(f"匯出至確認約：{worker['export_seconds']+worker['snapshot_to_send_seconds']:.3f} 秒（含等待與檢查）")
    decision=performance.get('sync_decision',{})
    if decision:
        rows.append('更新決策：'+decision_reason(decision))
        if decision.get('partial_reason'):rows.append('局部路徑退回原因：'+str(decision['partial_reason']))
        rows.append('觸發命令：'+('、'.join(str(c.get('action',''))+':'+str(c.get('command','')) for c in decision.get('commands',[])) or '本輪沒有可追蹤原生命令'))
    audit=performance.get('material_summary',{});observed=performance.get('official_observed',{})
    rows+=['材質庫總數：'+str(audit.get('total_materials','未取得')),
           '原始網格面引用材質數：'+str(audit.get('polygon_referenced_materials','未取得')),
           '官方文字紀錄材質數：'+str(observed.get('materials_logged','未取得')),
           '官方文字紀錄網格數：'+str(observed.get('meshes_logged','未取得')),
           '已送材質但本輪網格紀錄未引用：'+str(observed.get('sent_materials_not_referenced_in_logged_meshes','未取得')),
           'send_raw 內部仍可能包含準備、連線與等待，不能直接當成純網路時間。',
           '原始網格引用統計不是官方求值後的完整依賴判定，不據此刪除材質。',
           '明細：material-audit-latest.json；事件歷史：sync-events.jsonl。']
    if performance.get('error'):rows.append('錯誤：'+str(performance['error']))
    if performance.get('worker_error'):rows.append('背景處理錯誤：'+str(performance['worker_error']))
    if performance.get('material_audit_errors'):rows.append('材質檢查問題：'+str(performance['material_audit_errors']))
    if performance.get('diagnostics_errors'):rows.append('日誌寫入問題：'+str(performance['diagnostics_errors']))
    return '\n'.join(rows)+'\n'


def export_bundle(directory,target,diagnostics):
    """Allowlisted reports only. Never traverses project/model/texture folders."""
    directory=Path(directory) if directory else None;target=Path(target)
    pending=target.with_name(target.name+'.pending');entries={};skipped=[]
    if directory:
        for name in ('owner.json','preflight.json','status.json','startup-error.json','performance-latest.json',
                     'material-audit-latest.json','duplicate-models-latest.json','sync-events.jsonl','sync-events.jsonl.1','sync-events.jsonl.2',
                     'source-events.jsonl','source-events.jsonl.1','source-events.jsonl.2','sync-decision-latest.json'):
            path=directory/name
            if path.is_file():
                if path.stat().st_size<=8*1024*1024:entries[name]=path.read_bytes()
                else:skipped.append(name+'：超過8MiB，略過')
        for name in ('worker.log','timings.jsonl'):
            path=directory/name
            if path.is_file():
                with path.open('rb') as f:
                    size=f.seek(0,2);start=max(0,size-2*1024*1024);f.seek(start);data=f.read()
                if start:data=data.split(b'\n',1)[-1]
                entries['worker-tail.log' if name=='worker.log' else 'timings-tail.jsonl']=data
    entries['diagnostics.json']=diagnostics.encode('utf-8')
    entries['效能摘要.txt']=performance_text(read_json_safe(directory/'performance-latest.json') if directory else {}).encode('utf-8-sig')
    decision=read_json_safe(directory/'sync-decision-latest.json') if directory else {}
    entries['更新原因.txt']=decision_text(decision).encode('utf-8-sig')
    if __package__:from .duplicate_models import duplicate_text
    else:from duplicate_models import duplicate_text
    entries['重複模型報告.txt']=duplicate_text(read_json_safe(directory/'duplicate-models-latest.json') if directory else {}).encode('utf-8-sig')
    entries['README.txt']=('本包僅包含診斷與日誌，不含IGZ、BLEND或貼圖。\n名稱及本機路徑可能出現在紀錄中。\n'
                           'worker-tail.log與timings-tail.jsonl最多各保留末端2MiB。\n'+'\n'.join(skipped)).encode('utf-8-sig')
    try:
        with zipfile.ZipFile(pending,'w',zipfile.ZIP_DEFLATED) as z:
            for name,data in entries.items():z.writestr(name,data)
        os.replace(pending,target)
    except Exception:
        pending.unlink(missing_ok=True);raise
    return str(target)
