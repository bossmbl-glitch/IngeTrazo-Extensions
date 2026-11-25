"""Plain glass cabinet fronts and equal clear-height shelves, in millimetres."""
from copy import deepcopy
import math

DEFAULTS={'glass_frame_width':20.,'glass_frame_depth':20.,'glass_thickness':5.,
          'shelf_thickness':20.,'shelf_target':350.,'shelf_count':5,'shelf_mode':'auto'}


def shelves(p):
    thickness=p['shelf_thickness'];available=p['height']-60.
    if p['shelf_mode']=='auto':
        count=max(1,min(100,int(math.floor((available+thickness)/(p['shelf_target']+thickness)+.5))))
    else:count=int(p['shelf_count'])
    clear=(available-(count-1)*thickness)/count
    if clear<10:raise ValueError('分格太多或層板太厚，每格淨高須至少 1 cm。')
    return {'count':count,'clear':clear,'levels':[30.+i*clear+(i-1)*thickness for i in range(1,count)]}


def validate(p):
    for key in DEFAULTS:
        if key=='shelf_mode':continue
        p[key]=float(p[key])
        if not math.isfinite(p[key]):raise ValueError('玻璃門及層板尺寸須為有限數值。')
    if p['shelf_mode'] not in ('auto','count'):raise ValueError('分格方式無效。')
    if not 1<=p['shelf_count']<=100 or p['shelf_count']!=int(p['shelf_count']):raise ValueError('分格數須為 1～100 的整數。')
    if min(p['glass_frame_width'],p['glass_frame_depth'],p['glass_thickness'],p['shelf_thickness'])<2:raise ValueError('框料、玻璃及層板厚度至少 0.2 cm。')
    if p['glass_thickness']>p['glass_frame_depth']:raise ValueError('玻璃厚度不可超過框厚。')
    if p['glass_frame_depth']>30:raise ValueError('素框厚度不可超過櫃身前方預留的 3 cm。')
    if p['shelf_target']<10:raise ValueError('目標格高至少 1 cm。')
    if p['face_mode']!='doors':raise ValueError('素框玻璃門請使用門片配置；抽屜或開放櫃請切換其他樣式。')
    shelves(p)


def front(api,rect,p,name):
    x,z,x2,z2=rect;b=p['glass_frame_width'];t=p['glass_frame_depth']
    if min(x2-x,z2-z)<=2*b+10:raise ValueError('門片放不下素框與玻璃，請增加尺寸或減少框寬。')
    root=api.Group(name='素框玻璃'+name);parts=[]
    for title,r in [('左框',[x,z,x+b,z2]),('右框',[x2-b,z,x2,z2]),('下框',[x+b,z,x2-b,z+b]),('上框',[x+b,z2-b,x2-b,z2])]:
        parts.append(api.recessed_solid(r,[],[],0,0,t,title))
    glass=api.recessed_solid([x+b,z+b,x2-b,z2-b],[],[],0,0,p['glass_thickness'],'中間玻璃')
    glass.xform=api.QMatrix4x4();glass.xform.translate(0,(t-p['glass_thickness'])/2000,0)
    for face in glass.mesh.faces:face.attrs={'color':[.78,.9,.94],'opacity':.35,'back':{'color':[.78,.9,.94],'opacity':.35}}
    root.adopt([*parts,glass]);root.ext={};return root


def install(api):
    original_layout=api.cabinet_layout;original_model=api.cabinet_model
    def layout(values=None):
        p=deepcopy(api.CABINET_DEFAULTS);p.update({k:v for k,v in (values or {}).items() if k in p})
        if p['door_style']!='plain_glass':return original_layout(values)
        validate(p)
        # Use the existing cabinet body and gap rules; glass fields are narrower.
        raw={**p,'door_style':'original','left':p['glass_frame_width'],'right':p['glass_frame_width'],
             'top':p['glass_frame_width'],'bottom':p['glass_frame_width'],'profile_mode':'none'}
        result=original_layout(raw);result['params'].update({k:p[k] for k in DEFAULTS});result['params'].update({k:p[k] for k in ('left','right','top','bottom','profile_mode')});result['params']['door_style']='plain_glass'
        return result
    def model(values=None):
        data=layout(values);p=data['params']
        if p['door_style']!='plain_glass':return original_model(values)
        raw={**p,'door_style':'original','left':p['glass_frame_width'],'right':p['glass_frame_width'],'top':p['glass_frame_width'],'bottom':p['glass_frame_width'],'profile_mode':'none','handle_asset':None}
        root=original_model(raw);children=[g for g in root.children if g.ext.get('lime_cabinet_part')=='body'];body=children[0]
        shelf=shelves(p);boards=list(body.children)
        for index,z in enumerate(shelf['levels'],1):
            board=api.recessed_solid([30,z,p['width']-30,z+p['shelf_thickness']],[],[],0,0,p['depth']-60,f'層板 {index:02d}')
            board.xform=api.QMatrix4x4();board.xform.translate(0,.03,0);board.ext['lime_glass_shelf']=True;boards.append(board)
        body.adopt(boards)
        for index,(rect,_) in enumerate(data['fronts'],1):
            panel=front(api,rect,p,f'門片 {index:02d}');panel.ext['lime_cabinet_part']='front';children.append(panel)
        root.adopt(children);root.ext[api.PARAM_KEY]['params']=p;root.ext.update(front_thickness_mm=p['glass_frame_depth'],lime_glass_shelf_count=shelf['count']-1,lime_glass_clear_height_mm=shelf['clear'])
        api.add_cabinet_handles(root,data)
        return api.bind_face_materials(root)
    return layout,model
