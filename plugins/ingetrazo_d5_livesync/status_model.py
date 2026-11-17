# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only presentation state; never acknowledges or schedules a revision."""
PHASES={'starting':'背景 Blender 正在啟動', 'composing':'正在合併圖層與材質',
        'importing':'Blender 正在匯入模型', 'connecting':'官方 Sync 正在處理模型／材質並連接 D5',
        'sending':'官方 Sync 正在處理及傳送模型／材質', 'waiting_d5':'等待 D5 完成目前工作',
        'checkpointing':'正在保存本輪同步識別', 'analyzing':'正在辨識重複模型',
        'transforming':'正在套用快速位置更新','updating_visibility':'正在更新圖層顯示','updating_local':'正在局部更新群組／材質',
        'need_full':'正在改用完整同步', 'idle':'等待背景程序接收本輪快照'}


def duration(seconds):
    value=max(0,int(seconds));hours,rem=divmod(value,3600);minutes,seconds=divmod(rem,60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'


class StatusModel:
    def __init__(self):
        self.target=0;self.started=None;self.finished=None;self.last_activity=None
        self.token=None;self.source_error='';self.explicit_stop=False;self.initializing=False
        self.stop_message=''

    def reset(self,now):
        self.__init__();self.started=now;self.last_activity=now;self.initializing=True

    def begin(self,revision,now):
        self.target=revision;self.started=now;self.finished=None;self.last_activity=now
        self.token=None;self.source_error='';self.explicit_stop=False;self.initializing=False

    def failure(self,message):
        self.source_error=str(message);self.explicit_stop=False

    def stop(self,now=None,message=''):
        self.explicit_stop=True;self.initializing=False
        self.source_error=''
        self.stop_message=message
        if now is not None:self.finished=now

    def view(self,now,*,state=None,session_revision=0,checking=False,exporting=False,
             running=False,alive=False,paused=False,dirty=False,manual=False,
             cancelled=False,log_token=None,stale_after=15):
        state=state or {};phase=state.get('phase','');revision=self.target or session_revision
        # Status-file and log activity are observations, not proof that the
        # native SDK has completed or that the D5 viewport is correct.
        token=(phase,state.get('processing_revision'),state.get('acknowledged_revision'),log_token)
        if token!=self.token:
            self.token=token;self.last_activity=now
        idle_age=max(0,now-(self.last_activity if self.last_activity is not None else now))
        elapsed=max(0,(self.finished if self.finished is not None else now)-(self.started if self.started is not None else now))
        card={'kind':'idle','title':'尚未啟動','detail':'按 D5 開始同步目前模型。','busy':False,
              'revision':revision,'elapsed':duration(elapsed),'note':'','paused':paused,'pending':dirty}
        def result(kind,title,detail,busy=False,note=''):
            card.update(kind=kind,title=title,detail=detail,busy=busy,note=note);return card
        if self.source_error:
            return result('error','同步失敗',self.source_error,note='請查看詳細紀錄；成功回報前不會顯示同步完成。')
        if self.explicit_stop or cancelled:
            return result('stopped','已停止同步',self.stop_message or '背景同步已停止或正在結束；D5 場景保留。')
        if checking:
            self.initializing=False
            return result('active','同步中…','正在檢查 Blender 與官方 D5 Sync',True,
                          '正在確認環境；尚未代表 D5 已連線。')
        if self.initializing and not running and not alive:
            return result('active','準備同步…','正在準備啟動同步與檢查環境',True)
        if not alive and running:
            return result('error','背景程序已結束','尚未取得本輪成功回報，請查看詳細紀錄。')
        if phase in ('error','material_conflict'):
            return result('error','同步失敗',state.get('message') or '背景程序回報錯誤。')
        if exporting:
            return result('active','同步中…','IngeTrazo 正在匯出模型快照',True,
                          '來源匯出期間介面可能短暫停頓，請稍候。')
        ack=state.get('acknowledged_revision',0)
        try:ack=int(ack)
        except (ValueError,TypeError):ack=0
        if ack>max(revision,session_revision):
            return result('error','版本回報不一致','收到超出本機提交範圍的版本，請查看詳細紀錄。')
        complete=(revision>0 and ack>=revision and phase in ('sent','idle')
                  and state.get('official_return_code')==0)
        if complete:
            if self.finished is None:self.finished=now
            card['elapsed']=duration(self.finished-(self.started if self.started is not None else self.finished))
            if state.get('bridge_backup_warning'):
                return result('waiting','同步完成，備份未完成',
                              '本輪已收到成功回報，但新的橋接還原點尚未完成。',
                              note='請檢查磁碟與備份錯誤，未完成工作會保留並重試。'+str(state['bridge_backup_warning']))
            suffix=('背景備份處理中；同步不等待備份複製。' if state.get('backup_in_progress') else '')+('有新的修改待更新。' if dirty else '等待下一次修改。')
            if paused:
                return result('paused','已暫停','本輪同步已完成；後續更新已暫停。',note=suffix+' D5 畫面與材質仍需確認。')
            return result('success','同步完成','已收到第 '+str(revision)+' 版的成功回報。',
                          note=suffix+' 成功回報不代表畫面已渲染收斂或材質已驗收。')
        if revision>0 and ack>=revision and phase in ('sent','idle') and state.get('official_return_code') not in (None,0):
            return result('error','同步失敗','官方 Sync 回傳未成功：'+str(state.get('official_return_code')),
                          note='本輪尚未確認成功，請查看詳細紀錄。')
        pending=(revision>0 and not complete)
        if phase in ('retrying','retry_wait') and pending:
            return result('error','同步失敗／重試中' if phase=='retrying' else '同步失敗',
                          state.get('message','傳送失敗。'),note='本輪尚未確認成功。')
        if pending and (running or alive):
            # Old idle metrics cannot turn a newer queued revision green.
            processing=state.get('processing_revision',0) or 0
            detail=PHASES.get(phase,'等待背景程序回報目前階段')
            if phase=='idle' or (processing and processing!=revision):detail='等待背景程序接收第 '+str(revision)+' 版快照'
            if ack>=revision:detail='等待本輪官方成功回報'
            queued='有新的修改待更新；本輪完成後再處理。' if dirty else '尚未收到本輪成功回報。'
            if paused:queued='後續更新已暫停；已提交的本輪仍會繼續。'
            if idle_age>=stale_after:
                return result('waiting','等待回應…',detail,False,
                              ('背景程序仍存活；' if alive else '')+str(int(idle_age))+' 秒未收到新紀錄。'+queued)
            return result('active','同步中…',detail,True,'最近收到階段或紀錄更新。'+queued)
        if paused:return result('paused','已暫停','按「更新」傳送一次，或按「繼續」恢復自動同步。',note='有修改待更新。' if dirty else '')
        if running and (dirty or manual):return result('active','準備同步…','等待編輯結束並匯出最新快照',True)
        if self.started is not None and not running and not alive:return result('stopped','尚未連線','尚未啟動背景同步；請查看設定或詳細紀錄。')
        if running:return result('idle','等待修改','背景同步待命中。')
        return card
