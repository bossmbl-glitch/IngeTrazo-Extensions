"""Read-only catalog analysis and an explicit native model mounting-point picker."""
from copy import deepcopy
import hashlib,json
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QMatrix4x4,QVector3D
from PySide6.QtWidgets import QHBoxLayout,QMessageBox,QProgressDialog,QApplication


def install(ns,ux):
    def references(app,record):
        identity=record['id'];rows=[]
        def contains(value):
            if isinstance(value,dict):return value.get('id')==identity or any(contains(v) for v in value.values())
            if isinstance(value,(list,tuple)):return any(contains(v) for v in value)
            return False
        path=ns['store_for'](app).directory/'toolbox_templates.json'
        if path.exists():
            data=json.loads(path.read_text(encoding='utf-8'))
            for kind,templates in data.get('templates',{}).items():
                for name,row in templates.items():
                    if contains(row):rows.append(('範本',str(name),str(kind),'保留已存快照'))
        def walk(group):
            if contains(group.ext or {}):rows.append(('目前模型',group.name,group.uid,'已嵌入資料保留'))
            for child in group.children or ():walk(child)
        for group in app.scene.groups:walk(group)
        return rows

    def fingerprint(window,record,kind):
        if kind=='profiles':
            value=(ns['section_fingerprint'](record['points_mm']),ns['placement'](record.get('placement')))
            return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
        group=ns['handle_from_snapshot'](window.store.snapshot_handle(record,kind))
        def node(g):return (ns['_handle_sharing'].mesh_key(g.mesh),list(g.xform.data()) if g.xform is not None else None,g.material,[node(c) for c in g.children or ()])
        return hashlib.sha256(json.dumps(node(group),sort_keys=True).encode()).hexdigest()

    base=ns['AccessoryWindow']
    class Library(base):
        def __init__(self,app):
            self._mount_source=None;self._mount_picker=None;self._mount_previous=None
            super().__init__(app)
            self.mount.addItem('模型點選安裝基準','custom')
            row=QHBoxLayout();row.addWidget(ns['button']('在模型點選安裝基準',self.start_mount_pick));row.addWidget(ns['button']('檢查疑似重複',self.find_duplicates));row.addWidget(ns['button']('查看使用位置',self.inspect_references));self.body.insertLayout(3,row)
            self.search.setPlaceholderText('搜尋配件名稱；選取→預覽→選用／插入')
            for button in self.findChildren(ns['QPushButton']):
                if button.text()=='儲存名稱／基準設定':button.setText('管理來源：儲存名稱／基準')
                elif button.text()=='收錄預覽配件':button.setText('管理來源：收錄預覽配件')
            self.rebuild_list()
        def rebuild_list(self,*args):
            super().rebuild_list(*args)
            for i in range(self.list.count()):
                item=self.list.item(i);item.setToolTip(item.text())
        def stage_handle(self,group):
            self.stop_mount_pick();self._mount_source=group if self.app.scene.groups_by_uid().get(group.uid) is group else None
            if self.mount.currentData()=='custom':self.mount.setCurrentIndex(self.mount.findData('center'))
            return super().stage_handle(group)
        def inspect_references(self):
            def run():
                record=self.current();rows=references(self.app,record)
                ux.dialog_table(self,'配件使用位置：'+record['name'],['來源','名稱','類型／識別碼','移除後'],rows or [('—','目前模型與本機範本未找到引用','—','其他未開啟檔案不在檢查範圍')]);return '僅檢查目前模型與本機範本，未修改資料。'
            self.run(run)
        def delete(self):
            try:
                record=self.current();rows=references(self.app,record)
                detail='\n'.join(f'{r[0]}：{r[1]}' for r in rows[:12]) or '目前模型與本機範本未找到引用。'
                if len(rows)>12:detail+=f'\n另有 {len(rows)-12} 處，請由「查看使用位置」檢查。'
                text=f'從來源資料庫移除「{record["name"]}」？\n'+detail+'\n已嵌入模型與範本的快照保留；未開啟檔案不在檢查範圍。'
                if QMessageBox.question(self,'移除來源前確認',text)!=QMessageBox.Yes:return
                self.run(lambda:(self.store.delete(self.kind.currentData(),record['id']),self.refresh(),'已移除來源；既有快照保留。')[2])
            except Exception as exc:self.message(str(exc),True)
        def find_duplicates(self):
            if getattr(self,'_ux_analyzing',False):return
            self._ux_analyzing=True
            records=list(self.rows);kind=self.kind.currentData();progress=QProgressDialog('分析配件（可取消；不修改資料）','取消',0,len(records),self);progress.setMinimumDuration(0);progress.setWindowModality(Qt.WindowModal)
            try:
                buckets={};errors=[]
                for index,record in enumerate(records):
                    progress.setLabelText(f'分析 {index+1}/{len(records)}：'+record['name']);progress.setValue(index);QApplication.processEvents()
                    if progress.wasCanceled():self.message('已取消分析，資料庫未變更。');return
                    try:key=fingerprint(self,record,kind);buckets.setdefault(key,[]).append(record)
                    except Exception as exc:errors.append(('無法分析',record['name'],str(exc)))
                rows=[]
                for group in buckets.values():
                    if len(group)>1:
                        rows.extend(('疑似相同',r['name'],'同幾何、材質、基準；請人工確認') for r in group)
                progress.setValue(len(records));ux.dialog_table(self,'疑似重複配件',['結果','名稱','說明'],rows+errors or [('完成','沒有找到疑似重複','未自動刪除任何資料')]);self.message('分析完成；僅列出建議，沒有刪除資料。')
            except Exception as exc:self.message(str(exc),True)
            finally:progress.close();self._ux_analyzing=False
        def start_mount_pick(self):
            try:
                if self.pending_handle is None:raise ValueError('先載入目前選取的實體配件，再點選安裝基準。')
                source=self._mount_source
                if source is None or self.app.scene.groups_by_uid().get(source.uid) is not source:raise ValueError('請先將來源模型放入場景並選取，再按「載入目前選取」；匯入預覽不能直接在場景點選。')
                self.stop_mount_pick();vp=self.app.viewport;scene=self.app.scene
                def hierarchy(root,matrix):
                    world=matrix*(root.xform if root.xform is not None else QMatrix4x4());yield root,world
                    for child in root.children or ():yield from hierarchy(child,world)
                entries=[entry for root in scene.groups for entry in hierarchy(root,QMatrix4x4())];world=next(m for g,m in entries if g is source);inverse,ok=world.inverted()
                if not ok:raise ValueError('來源座標不可反轉，請檢查模型縮放。')
                allowed={id(face) for node,_ in hierarchy(source,QMatrix4x4()) for face in node.mesh.faces}
                def choose(x,y,commit):
                    if not commit:return False
                    try:
                        if vp.scene is not scene or scene.groups_by_uid().get(source.uid) is not source:raise ValueError('場景或來源已變更，請重新載入。')
                        face,placement=vp.pick_face_placement(x,y)
                        if face is None or id(face) not in allowed:raise ValueError('請點選剛才載入的配件表面。')
                        api=ns['_feature_api'];surface=ns['_electrical_panels'].WallSurface(api,face,placement);origin,direction=vp._pixel_to_ray(x,y)
                        eye,ray=api.xyz(origin),api.xyz(direction);den=float(ray@surface.normal)
                        if abs(den)<1e-10:raise ValueError('視線與表面平行，請轉動視角。')
                        distance=float((surface.start-eye)@surface.normal/den);point=eye+distance*ray
                        if distance<0 or not surface.contains(point):raise ValueError('沒有命中配件表面，請重新點選。')
                        local=inverse.map(QVector3D(*map(float,point)));self.set_mount_point(local);self.stop_mount_pick();self.message('已設定安裝基準；確認紅點與朝外方向後收錄，來源模型保留。');return True
                    except Exception as exc:self.message(str(exc),True);return False
                self._mount_previous=vp.active_tool;self._mount_picker=ns['_ray_picker'].RayPicker(vp,choose,self.stop_mount_pick,'點選配件安裝基準');vp.set_active_tool(self._mount_picker);vp.setFocus();self.message('點選來源配件表面作為安裝基準；Esc 取消。')
            except Exception as exc:self.message(str(exc),True)
        def set_mount_point(self,local):
            if self.pending_handle is None:raise ValueError('沒有待收錄模型。')
            self.pending_handle.ext={**(self.pending_handle.ext or {}),'lime_custom_mount_local_m':list(local.toTuple())};self.mount.setCurrentIndex(self.mount.findData('custom'));self.pending_preview()
        def stop_mount_pick(self):
            tool=self._mount_picker;self._mount_picker=None
            if tool is not None and self.app.viewport.active_tool is tool:
                previous=self._mount_previous;self._mount_previous=None;self.app.viewport.set_active_tool(previous)
        def closeEvent(self,event):self.stop_mount_pick();return super().closeEvent(event)
        def done(self,result):self.stop_mount_pick();return super().done(result)
        def keyPressEvent(self,event):
            if event.key()==Qt.Key_Escape and self._mount_picker is not None:self.stop_mount_pick();event.accept();return
            return super().keyPressEvent(event)
    ns['AccessoryWindow']=Library;ns['asset_references']=references;ns['asset_fingerprint']=fingerprint
