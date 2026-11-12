# SPDX-License-Identifier: GPL-3.0-or-later
"""D5-only light editing, persisted separately from native Render lights."""
import copy
import math
from PySide6.QtCore import Qt,QPointF,QSize,QTimer
from PySide6.QtGui import QAction,QVector3D,QColor,QPen,QPixmap,QPainter,QIcon
from PySide6.QtWidgets import QWidget,QVBoxLayout,QFormLayout,QLabel,QListWidget,QListWidgetItem,QDoubleSpinBox,QLineEdit,QCheckBox,QToolButton,QStyle
from .light_data import new_light,clean_lights,KINDS
from .lighting import light_icon
from .light_drag import LightDrag
from . import light_proxy

def icon(kind):
    if kind=='fixtures':
        pix=QPixmap(24,24);pix.fill(Qt.GlobalColor.transparent)
        p=QPainter(pix);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.setPen(QPen(QColor('#d89b16'),1.5));p.setBrush(QColor('#ffe298'))
        for x,y in ((5,5),(17,5),(5,16),(17,16)):
            p.drawEllipse(QPointF(x,y),3,3);p.drawLine(x-2,y+4,x+2,y+4)
        p.end();return QIcon(pix)
    if kind=='move':
        pix=QPixmap(24,24);pix.fill(Qt.GlobalColor.transparent)
        p=QPainter(pix);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.setPen(QPen(QColor('#d89b16'),2))
        p.drawLine(12,2,12,22);p.drawLine(2,12,22,12)
        for a,b,c,d in ((12,2,8,6),(12,2,16,6),(12,22,8,18),(12,22,16,18),(2,12,6,8),(2,12,6,16),(22,12,18,8),(22,12,18,16)):p.drawLine(a,b,c,d)
        p.end();return QIcon(pix)
    if kind!='strip':return light_icon('light_'+kind+'.svg')
    pix=QPixmap(24,24);pix.fill(Qt.GlobalColor.transparent)
    p=QPainter(pix);p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor('#d89b16'),1.8));p.setBrush(QColor('#ffe298'))
    p.drawRoundedRect(2,7,20,6,2,2)
    for x in (4,9,15,20):p.drawLine(x,16,x,21)
    p.end();return QIcon(pix)

class LightPanel(QWidget):
    def __init__(self,app):
        super().__init__()
        self.app=app;self.selected=None;self.pending=None;self.actions={};self.updating=False;self.scene=app.scene
        self.use_proxies=True;self.migrated_scenes=[]
        layout=QVBoxLayout(self)
        from .fixture_lights import FixtureControls
        self.fixtures=FixtureControls(self);layout.addWidget(self.fixtures)
        hint=QLabel('選取燈具定位模型，用一般移動工具定位。定位模型只在 IngeTrazo 顯示，不傳入 D5。');hint.setWordWrap(True);layout.addWidget(hint)
        self.items=QListWidget();self.items.currentItemChanged.connect(self.selection_changed);layout.addWidget(self.items)
        self.editor=QWidget();self.form=QFormLayout(self.editor)
        self.name=QLineEdit();self.name.editingFinished.connect(self.edit);self.form.addRow('名稱',self.name)
        self.fields={}
        for key,title,lo,hi,dec,suffix in (('brightness','亮度',0,1e7,1,''),('kelvin','色溫',1800,10000,0,' K'),('length','長度',.001,10000,3,' m'),('width','寬度',.001,10,3,' m'),('angle','光束角',5,175,0,' °')):
            spin=QDoubleSpinBox();spin.setRange(lo,hi);spin.setDecimals(dec);spin.setSuffix(suffix);spin.setKeyboardTracking(False)
            spin.editingFinished.connect(self.edit);self.fields[key]=spin;self.form.addRow(title,spin)
        self.enabled=QCheckBox('啟用');self.enabled.toggled.connect(self.edit);self.form.addRow(self.enabled)
        self.linked=QCheckBox('連動整組參數');self.linked.setChecked(True);self.form.addRow(self.linked)
        self.group_label=QLabel();self.form.addRow(self.group_label)
        layout.addWidget(self.editor);layout.addStretch()
        app.on_document_changed(self.document_changed);app.add_overlay(self.draw)
        self.drag=LightDrag(self)
        self.selection_signature=None
        self.selection_timer=QTimer(self);self.selection_timer.setInterval(150)
        self.selection_timer.timeout.connect(self.poll_selection);self.selection_timer.start()
        self.refresh()
        QTimer.singleShot(0,self.migrate)

    def lights(self):return light_proxy.lights(self.app.scene)
    def poll_selection(self):
        signature=(id(self.app.scene),frozenset(id(x) for x in self.app.scene.selection))
        if signature==self.selection_signature:return
        self.selection_signature=signature
        self.selected=next((light['id'] for light,group,_,_ in light_proxy.records(self.app.scene) if group in self.app.scene.selection),None)
        self.refresh()
    def migrate(self):
        scene=self.app.scene
        if any(scene is old for old in self.migrated_scenes):return
        self.migrated_scenes.append(scene)
        existing={row[0]['id'] for row in light_proxy.records(scene)}
        if any(light['id'] not in existing for light in self.lights()):self.store(self.lights())
    def store(self,lights):
        identity=self.selected
        light_proxy.apply_lights(self.app,lights)
        if any(x['id']==identity for x in self.lights()):self.select(identity)
    def current(self):return next((x for x in self.lights() if x['id']==self.selected),None)
    def document_changed(self):
        self.drag.cancel()
        if self.app.scene is not self.scene:
            self.fixtures.finish()
            self.scene=self.app.scene;self.selected=None;self.pending=None;QTimer.singleShot(0,self.migrate)
        for light,group,_,_ in light_proxy.records(self.app.scene):
            if group in self.app.scene.selection:self.selected=light['id'];break
        self.refresh()
    def refresh(self):
        self.updating=True;self.items.blockSignals(True);self.items.clear()
        for light in self.lights():
            item=QListWidgetItem(icon(light['kind']),light['name']);item.setData(Qt.ItemDataRole.UserRole,light['id']);self.items.addItem(item)
            if light['id']==self.selected:self.items.setCurrentItem(item)
        self.items.blockSignals(False);light=self.current()
        if light is None:self.selected=None
        self.editor.setEnabled(light is not None)
        if light:
            group_id=light.get('group_id');count=sum(x.get('group_id')==group_id for x in self.lights()) if group_id else 0
            self.linked.setEnabled(bool(group_id));self.group_label.setText((light.get('group_name','')+' · '+str(count)+' 盞') if group_id else '獨立光源')
            self.name.setText(light['name']);self.enabled.setChecked(light['on'])
            for key,spin in self.fields.items():
                spin.setValue(math.dist(light['pos'],light['end']) if key=='length' else light[key])
                self.form.setRowVisible(spin,key not in ('length','width','angle') or (key=='angle' and light['kind']=='spot') or (key in ('length','width') and light['kind']=='strip'))
        for key in ('move','aim','delete'):
            if key in self.actions:self.actions[key].setEnabled(bool(self.lights()) if key=='move' else bool(light and (key!='aim' or light['kind']!='point')))
        self.updating=False;self.app.viewport.update()
    def selection_changed(self,item,previous=None):
        if not self.updating:self.select(item.data(Qt.ItemDataRole.UserRole) if item else None)
    def select(self,identity):
        self.selected=identity
        for light,group,_,_ in light_proxy.records(self.app.scene):
            if light['id']==identity:
                self.app.scene.selection.clear();self.app.scene.selection.add(group);break
        self.refresh()
    @staticmethod
    def translated(light,pos):
        result=copy.deepcopy(light);delta=[b-a for a,b in zip(light['pos'],pos)]
        result['pos']=list(pos);result['end']=[a+d for a,d in zip(light['end'],delta)]
        return result
    def preview(self):
        return self.drag.preview()
    def edit(self,*_args):
        if self.updating:return
        lights=self.lights()
        for light in lights:
            if light['id']!=self.selected:continue
            old=copy.deepcopy(light);light['name']=self.name.text().strip() or 'D5 '+KINDS[light['kind']];light['on']=self.enabled.isChecked()
            for key in ('brightness','kelvin','width','angle'):light[key]=self.fields[key].value()
            if light['kind']=='strip':
                delta=[b-a for a,b in zip(light['pos'],light['end'])];length=math.sqrt(sum(x*x for x in delta))
                if light.get('group_id'):
                    centre=[(a+b)/2 for a,b in zip(light['pos'],light['end'])];half=[d*self.fields['length'].value()/length/2 for d in delta]
                    light['pos']=[a-d for a,d in zip(centre,half)];light['end']=[a+d for a,d in zip(centre,half)]
                else:light['end']=[a+d*self.fields['length'].value()/length for a,d in zip(light['pos'],delta)]
            if old!=light:
                if light.get('group_id') and self.linked.isChecked():
                    shared=[key for key in ('brightness','kelvin','width','angle','on') if old[key]!=light[key]]
                    length_changed=light['kind']=='strip' and abs(math.dist(old['pos'],old['end'])-math.dist(light['pos'],light['end']))>1e-7
                    for peer in lights:
                        if peer['id']==light['id'] or peer.get('group_id')!=light['group_id'] or peer['kind']!=light['kind']:continue
                        for key in shared:peer[key]=light[key]
                        if length_changed:
                            delta=[b-a for a,b in zip(peer['pos'],peer['end'])];length=math.sqrt(sum(x*x for x in delta))
                            centre=[(a+b)/2 for a,b in zip(peer['pos'],peer['end'])]
                            half=[d*self.fields['length'].value()/length/2 for d in delta]
                            peer['pos']=[a-d for a,d in zip(centre,half)];peer['end']=[a+d for a,d in zip(centre,half)]
                self.store(lights)
            break
    def delete(self,identity=None):
        identity=identity or self.selected;lights=self.lights();remaining=[x for x in lights if x['id']!=identity]
        if len(remaining)!=len(lights):self.store(remaining)
    def begin(self,command):
        from tools.base import Tool
        self.drag.cancel()
        if command=='move':
            identity=self.selected;self.cancel();self.select(identity)
            self.app.window._activate_tool('move')
            self.app.window.statusBar().showMessage('用一般移動工具移動燈具定位模型；D5 只接收燈光。')
            return
        panel=self
        if command not in KINDS and command!='move' and self.current() is None:return
        identity=self.selected
        class Pick(Tool):
            name='D5 光源';uses_snap=True;shortcut=None
            def on_activate(self,viewport):pass
            def on_click(self,ctx):
                panel.picked(ctx.world)
            def on_hover(self,ctx):
                if panel.pending:panel.pending['hover']=[ctx.world.x(),ctx.world.y(),ctx.world.z()]
                ctx.viewport.update()
            def on_key(self,viewport,key,modifiers):
                if key==Qt.Key.Key_Escape:panel.cancel();return True
                return False
            def on_deactivate(self,viewport):panel.pending=None
            def rubber_band_lines(self):return []
        self.app.viewport.set_active_tool(Pick())
        self.pending={'command':command,'points':[],'scene':self.app.scene,'id':identity,'hover':None}
        if identity:self.select(identity)
        prompt='點選燈條起點；Esc 取消' if command=='strip' else '點選位置／照射目標；Esc 取消'
        self.app.window.statusBar().showMessage(prompt);self.app.viewport.setFocus()
    def picked(self,point):
        state=self.pending
        if state is None or state['scene'] is not self.app.scene:self.cancel();return
        pos=[point.x(),point.y(),point.z()];command=state['command']
        if command=='strip' and not state['points']:
            state['points'].append(pos);self.app.window.statusBar().showMessage('點選燈條終點；Esc 取消');return
        if command=='strip' and math.dist(state['points'][0],pos)<.001:return
        lights=self.lights();before=copy.deepcopy(lights);identity=state['id']
        if command in KINDS:
            light=new_light(command,state['points'][0] if command=='strip' else pos,pos if command=='strip' else None);lights.append(light);identity=light['id']
        else:
            for light in lights:
                if light['id']!=state['id']:continue
                if command=='move':
                    delta=[b-a for a,b in zip(light['pos'],pos)];light['end']=[a+d for a,d in zip(light['end'],delta)];light['pos']=pos
                elif command=='aim':
                    origin=[(a+b)/2 for a,b in zip(light['pos'],light['end'])] if light['kind']=='strip' else light['pos']
                    direction=[b-a for a,b in zip(origin,pos)]
                    if math.dist(direction,[0,0,0])<1e-6:return
                    light['direction']=direction
        if lights!=before:self.store(lights)
        self.cancel();self.select(identity)
    def cancel(self):
        self.drag.cancel()
        self.pending=None;self.app.window.statusBar().clearMessage();self.app.window._activate_tool('select');self.app.viewport.update()
    def pixel(self,viewport,point):return viewport._world_to_pixel(QVector3D(*point))
    def draw(self,viewport,painter):
        if viewport.scene is not self.app.scene:return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.use_proxies:
            if self.pending and self.pending['points'] and self.pending['hover']:
                a,b=(self.pixel(viewport,p) for p in (self.pending['points'][0],self.pending['hover']))
                if a is not None and b is not None:painter.setPen(QPen(QColor('#ffb82e'),2,Qt.PenStyle.DashLine));painter.drawLine(QPointF(*a),QPointF(*b))
            return
        preview=self.preview()
        for light in self.lights():
            if preview and light['id']==preview['id']:light=preview
            pos=self.pixel(viewport,light['pos'])
            if pos is None:continue
            painter.setPen(QPen(QColor('#ffb82e' if light['on'] else '#8c8c8c'),3 if light['id']==self.selected else 2));painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawEllipse(QPointF(*pos),6,6)
            if light['kind']=='strip':
                end=self.pixel(viewport,light['end'])
                if end is not None:painter.drawLine(QPointF(*pos),QPointF(*end));painter.drawEllipse(QPointF(*end),4,4)
            painter.drawText(QPointF(pos[0]+9,pos[1]+32 if light['id']==self.selected else pos[1]-7),light['name'])
        if preview:
            a=self.pixel(viewport,preview['pos'])
            painter.setPen(QPen(QColor('#47c5ff'),2,Qt.PenStyle.DashLine));painter.setBrush(Qt.BrushStyle.NoBrush)
            if a is not None:
                painter.drawEllipse(QPointF(*a),8,8)
                if preview['kind']=='strip':
                    b=self.pixel(viewport,preview['end'])
                    if b is not None:painter.drawLine(QPointF(*a),QPointF(*b));painter.drawEllipse(QPointF(*b),5,5)
                painter.drawText(QPointF(a[0]+10,a[1]+52),'放開定位；Esc 取消')
        if self.pending and self.pending['points'] and self.pending['hover']:
            a,b=(self.pixel(viewport,p) for p in (self.pending['points'][0],self.pending['hover']))
            if a is not None and b is not None:painter.setPen(QPen(QColor('#ffb82e'),2,Qt.PenStyle.DashLine));painter.drawLine(QPointF(*a),QPointF(*b))
        self.drag.draw(viewport,painter)
    def pick(self,viewport,px,py):
        best,distance=None,12.
        for light in self.lights():
            a=self.pixel(viewport,light['pos'])
            if a is None:continue
            d=math.hypot(px-a[0],py-a[1])
            if light['kind']=='strip':
                b=self.pixel(viewport,light['end'])
                if b is not None:
                    dx,dy=b[0]-a[0],b[1]-a[1];t=max(0,min(1,((px-a[0])*dx+(py-a[1])*dy)/(dx*dx+dy*dy or 1)));d=min(d,math.hypot(px-a[0]-t*dx,py-a[1]-t*dy))
            if d<distance:best,distance=light['id'],d
        return best

def setup_lighting(app,log,toolbar):
    panel=LightPanel(app);panel.dock=app.add_panel('D5 光源',panel,name='lights',start_hidden=True)
    toolbar.addSeparator();win=app.window
    def invoke(key):
        if key in ('settings','fixtures'):app.show_panel(panel.dock)
        elif key=='delete':panel.delete()
        else:panel.begin(key)
    for key,title,image in (('fixtures','燈具佈光／連動燈組',icon('fixtures')),('point','D5 點光源',icon('point')),('spot','D5 聚光燈',icon('spot')),('strip','D5 燈條',icon('strip')),('move','移動燈具定位模型',icon('move')),('aim','瞄準目標',win.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown)),('delete','刪除光源',win.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)),('settings','光源設定',win.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))):
        action=QAction(image,title,win);action.setObjectName('ingetrazo_light_'+key);action.setToolTip(title+('：點選起點、終點' if key=='strip' else ''))
        action.triggered.connect(lambda checked=False,k=key:invoke(k))
        button=QToolButton(toolbar);button.setDefaultAction(action);button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly);button.setIconSize(QSize(22,22));toolbar.addWidget(button);panel.actions[key]=action
    win._ingetrazo_d5_lights=panel;win._ingetrazo_lighting_actions=panel.actions;panel.refresh()
    log.info('D5 light icons attached to sync toolbar')
