# SPDX-License-Identifier: GPL-3.0-or-later
"""Place independent, linked D5 lights at selected fixture bounds."""
import uuid
import math
from PySide6.QtCore import Qt
from PySide6.QtGui import QMatrix4x4
from PySide6.QtWidgets import QWidget,QFormLayout,QComboBox,QDoubleSpinBox,QPushButton,QLabel,QHBoxLayout
from . import light_proxy
from .light_data import new_light,KINDS

def bounds(scene,target):
    row=next((r for r in light_proxy.placements(scene) if r[0] is target),None)
    if row is None or light_proxy.metadata(target):raise ValueError('請點選燈具模型群組，不是黃色光源定位模型。')
    points=[]
    def collect(group,matrix,seen):
        if id(group) in seen:raise ValueError('模型群組循環')
        if light_proxy.metadata(group):return
        points.extend(light_proxy.values(matrix.map(v.position)) for v in group.mesh.vertices)
        for child in group.children:
            collect(child,matrix*(child.xform if child.xform is not None else QMatrix4x4()),seen|{id(group)})
    collect(target,row[1],set())
    if not points:raise ValueError('這個群組沒有可用的燈具幾何。')
    return ([min(p[i] for p in points) for i in range(3)],
            [max(p[i] for p in points) for i in range(3)])

def placed_light(scene,group,batch):
    lo,hi=bounds(scene,group)
    pos=[(a+b)/2 for a,b in zip(lo,hi)]
    if batch['anchor']=='bottom':pos[2]=lo[2]
    pos[2]-=batch['offset']
    end=None
    if batch['kind']=='strip':
        # The strip is centred under the fixture, along its longest world XY extent.
        axis=0 if hi[0]-lo[0]>=hi[1]-lo[1] else 1
        end=pos.copy();pos[axis]-=batch['length']/2;end[axis]+=batch['length']/2
    light=new_light(batch['kind'],pos,end)
    light.update(group_id=batch['id'],group_name=batch['name'],group_number=batch['number'],
                 fixture_uid=str(group.uid),fixture_offset=batch['offset'],fixture_anchor=batch['anchor'])
    light['name']=batch['name']+' · '+(group.name or KINDS[batch['kind']])
    return light

class FixtureControls(QWidget):
    def __init__(self,panel):
        super().__init__(panel);self.panel=panel;self.batch=None;self.tool=None
        form=QFormLayout(self)
        self.kind=QComboBox()
        for key in ('spot','point','strip'):self.kind.addItem(KINDS[key],key)
        form.addRow('佈光種類',self.kind)
        self.anchor=QComboBox();self.anchor.addItem('底面中心','bottom');self.anchor.addItem('模型中心','center');form.addRow('位置',self.anchor)
        self.offset=QDoubleSpinBox();self.offset.setRange(0,1000);self.offset.setDecimals(2);self.offset.setValue(.2);self.offset.setSuffix(' cm');form.addRow('向下距離',self.offset)
        self.length=QDoubleSpinBox();self.length.setRange(.001,10000);self.length.setDecimals(3);self.length.setValue(1);self.length.setSuffix(' m');form.addRow('燈條長度',self.length)
        self.kind.currentIndexChanged.connect(lambda:form.setRowVisible(self.length,self.kind.currentData()=='strip'))
        form.setRowVisible(self.length,False)
        row=QHBoxLayout();self.start_button=QPushButton('點選一組');self.selected_button=QPushButton('選取佈光');self.finish_button=QPushButton('完成')
        for b in (self.start_button,self.selected_button,self.finish_button):row.addWidget(b)
        form.addRow(row);self.message=QLabel('每次開始建立新組；Esc 完成目前這組。');self.message.setWordWrap(True);form.addRow(self.message)
        self.start_button.clicked.connect(self.start);self.selected_button.clicked.connect(self.from_selection);self.finish_button.clicked.connect(self.finish)
        self.finish_button.setEnabled(False)

    def show_error(self,error):
        self.message.setText(str(error));self.panel.app.window.statusBar().showMessage(str(error))

    def new_batch(self):
        if self.panel.app.scene.edit_group is not None and light_proxy.metadata(self.panel.app.scene.edit_group):
            raise ValueError('請先退出黃色定位模型的編輯，再點選燈具。')
        number=max((x.get('group_number',0) for x in self.panel.lights()),default=0)+1
        return {'id':uuid.uuid4().hex,'number':number,'name':'燈組 '+str(number),
                'kind':self.kind.currentData(),'anchor':self.anchor.currentData(),
                'offset':self.offset.value()/100.,'length':self.length.value(),'scene':self.panel.app.scene}

    def active(self,value):
        for w in (self.kind,self.anchor,self.offset,self.length):w.setEnabled(not value)
        self.finish_button.setEnabled(value)

    def start(self):
        from tools.select import SelectTool
        self.finish();self.panel.cancel()
        try:batch=self.new_batch()
        except ValueError as error:self.show_error(error);return
        controls=self
        class FixturePick(SelectTool):
            name='燈具佈光';shortcut=None;uses_snap=False;box_select=False
            def on_activate(self,viewport):pass
            def on_click(self,ctx):
                entity=self._pick(ctx.viewport,ctx.screen.x(),ctx.screen.y())
                controls.add([entity])
            def on_double_click(self,ctx):pass
            def on_triple_click(self,ctx):pass
            def on_key(self,viewport,key,modifiers):
                if key in (Qt.Key.Key_Escape,Qt.Key.Key_Return,Qt.Key.Key_Enter):controls.finish();return True
                return False
            def on_deactivate(self,viewport):controls.end_state()
        self.tool=FixturePick();self.panel.app.viewport.set_active_tool(self.tool)
        self.batch=batch;self.active(True)
        self.message.setText(batch['name']+'：連續點燈具；Esc 完成。再次點同一燈具不會重複。')
        self.panel.app.window.statusBar().showMessage(self.message.text());self.panel.app.viewport.setFocus()

    def end_state(self):
        self.batch=None;self.tool=None;self.active(False)
        self.message.setText('已結束這組；再按「點選一組」建立下一組。')

    def finish(self):
        tool=self.tool;self.end_state()
        if tool is not None and self.panel.app.viewport.active_tool is tool:self.panel.app.window._activate_tool('select')

    def add(self,groups):
        from core.group import Group
        if self.batch is None:return
        if self.batch['scene'] is not self.panel.app.scene:self.finish();return
        try:
            if not groups or any(not isinstance(g,Group) or light_proxy.metadata(g) for g in groups):
                raise ValueError('請選取燈具群組／元件；散面請先成組。')
            lights=self.panel.lights();known={x.get('fixture_uid') for x in lights if x.get('group_id')==self.batch['id']}
            added=[]
            template=next((x for x in lights if x.get('group_id')==self.batch['id']),None)
            for group in groups:
                if str(group.uid) in known:continue
                settings=dict(self.batch)
                if template and template['kind']=='strip':settings['length']=math.dist(template['pos'],template['end'])
                light=placed_light(self.panel.app.scene,group,settings)
                if template:
                    for key in ('brightness','kelvin','width','angle','on'):light[key]=template[key]
                added.append(light);known.add(str(group.uid))
            if added:
                self.panel.store(lights+added);self.panel.selected=added[-1]['id'];self.panel.refresh()
            count=sum(x.get('group_id')==self.batch['id'] for x in self.panel.lights())
            self.message.setText(self.batch['name']+'：'+str(count)+' 盞；Esc 完成這組。')
        except (ValueError,TypeError) as error:self.show_error(error)

    def from_selection(self):
        from core.group import Group
        scene=self.panel.app.scene;selected=set(scene.selection)
        groups=[g for g,_,_,_ in light_proxy.placements(scene) if g in selected]
        # Reject overlapping selections rather than placing twice on an assembly.
        def has_selected_child(g):return any(c in selected or has_selected_child(c) for c in g.children)
        if any(not isinstance(x,Group) for x in selected) or any(has_selected_child(g) for g in groups):
            self.show_error('請只選燈具群組，勿同時選父群組和內部子群組。');return
        self.finish();self.panel.cancel()
        try:self.batch=self.new_batch()
        except ValueError as error:self.show_error(error);return
        self.add(groups);message=self.message.text();self.end_state();self.message.setText(message.replace('Esc 完成這組。','已完成。'))
