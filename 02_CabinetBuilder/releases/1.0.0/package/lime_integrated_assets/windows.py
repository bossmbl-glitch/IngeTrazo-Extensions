"""Fixed-profile window presets extracted from the supplied IGZ, plus custom grids."""
from copy import deepcopy
from functools import lru_cache
import json,math
import numpy as np
from PySide6.QtGui import QMatrix4x4,QVector3D

CUSTOM={'custom_slide':'自訂｜橫拉窗','custom_fixed':'自訂｜固定玻璃窗','custom_casement':'自訂｜平開窗'}
DEFAULTS={'preset':'W01','width':1680.,'height':1650.,'depth':100.,'left':60.,'right':60.,'top':60.,'bottom':60.,'mullion_width':80.,'cross_width':110.,'cross_position':38.23529411764706,'columns':2,'rows':2,'column_mode':'original','column_widths':[],'sash_width':40.,'glass_thickness':6.,'glass_finish':'original','handle_mode':'original','handle_asset':None,'opening':'opposed','sill':710.,'stand':0.,'angle':0.}

@lru_cache(maxsize=4)
def stock(path):
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema')!=1 or data.get('units')!='mm':raise ValueError('窗型原型資料版本不符。')
    return {row['id']:row for row in data['presets']}

def presets(api):return stock(api.ROOT/'lime_integrated_assets'/'window_stock.json')

def defaults(api,key='W01'):
    p=deepcopy(DEFAULTS);p['preset']=key
    if key in CUSTOM:
        p.update(width=1800.,height=1500.,depth=100.,left=50.,right=50.,top=50.,bottom=50.,sill=900.,columns=1 if key=='custom_fixed' else 2,rows=1,column_mode='equal',cross_width=50.,cross_position=50.)
    else:
        row=presets(api).get(key)
        if row is None:raise ValueError('窗型不存在，請重新選擇。')
        for k in ('width','height','depth','left','right','top','bottom','sill','columns','rows'):p[k]=row[k]
        if row['cross']:
            lo,hi=row['cross'];p.update(cross_width=hi-lo,cross_position=((lo+hi)/2-row['bottom'])/(row['height']-row['bottom']-row['top'])*100)
        if row['bars']:p['mullion_width']=row['bars'][0][1]-row['bars'][0][0]
    return p

def parameters(api,values=None):
    key=(values or {}).get('preset','W01');p={**defaults(api,key),**{k:deepcopy(v) for k,v in (values or {}).items() if k in DEFAULTS}}
    for k,v in DEFAULTS.items():
        if isinstance(v,(int,float)):
            p[k]=float(p[k])
            if not math.isfinite(p[k]):raise ValueError('窗戶尺寸與位置必須為有限數值。')
    for k in ('columns','rows'):
        if p[k]!=int(p[k]) or not 1<=p[k]<=6:raise ValueError('窗戶欄數與層數須為 1～6 的整數。')
        p[k]=int(p[k])
    if not 250<=p['width']<=12000 or not 250<=p['height']<=6000:raise ValueError('窗寬為 25～1200 cm，窗高為 25～600 cm。')
    if not 20<=p['depth']<=600:raise ValueError('框深為 2～60 cm。')
    if any(not 10<=p[k]<=200 for k in ('left','right','top','bottom','mullion_width','cross_width','sash_width')):raise ValueError('框料尺寸為 1～20 cm。')
    if not 5<=p['cross_position']<=95:raise ValueError('中橫料位置為淨開口由下至上 5～95%。')
    if not 1<=p['glass_thickness']<=16:raise ValueError('玻璃厚度為 0.1～1.6 cm。')
    if not -100000<=p['sill']<=100000 or not -500<=p['stand']<=500 or not -360<=p['angle']<=360:raise ValueError('窗台高度為 −10000～10000 cm；離牆 −50～50 cm，旋轉 −360～360 度。')
    if p['column_mode'] not in ('original','equal','custom') or p['glass_finish'] not in ('original','clear','frost') or p['handle_mode'] not in ('original','none','library') or p['opening'] not in ('left','right','opposed'):raise ValueError('窗戶分格、玻璃或配件設定無效。')
    if p['handle_mode']=='library':
        if not p['handle_asset']:raise ValueError('請在玻璃／配件頁選擇資料庫把手。')
        api.handle_bytes(p['handle_asset'])
    if key not in CUSTOM:
        row=presets(api)[key]
        if p['columns']!=row['columns'] or p['rows']!=row['rows']:raise ValueError('原型扇數與層數由窗型決定；需其他配置請選自訂窗型。')
        if p['depth']<row['depth']-.02:raise ValueError('原型框深至少 '+f'{row["depth"]/10:g}'+' cm；較淺窗框請選自訂款。')
    if not isinstance(p['column_widths'],list) or len(p['column_widths'])>p['columns']:raise ValueError('指定欄寬項目不得超過欄數。')
    return p

def _widths(api,p,total,weights=None):
    if p['column_mode']=='custom':return api._door_system.sizes(total,p['columns'],p['column_widths'])
    weights=np.asarray(weights if p['column_mode']=='original' and weights is not None else [1.]*p['columns'],float)
    return (total*weights/weights.sum()).tolist()

def axis_map(source,target):
    source=np.asarray(source,float);target=np.asarray(target,float)
    order=np.argsort(source);source=source[order];target=target[order]
    if np.any(np.diff(source)<.001) or np.any(np.diff(target)<2.):raise ValueError('目前尺寸放不下固定框料及接頭，請放大窗戶、調整分格或縮小框料。')
    return source,target

def layout(api,values=None):
    p=parameters(api,values);row=presets(api).get(p['preset']);w,h=p['width'],p['height'];l,r,b,t=p['left'],p['right'],p['bottom'],p['top']
    if w-l-r<100 or h-b-t<100:raise ValueError('窗框之間的淨開口不足 10 cm。')
    if row:
        cap=48.;sx=[0,row['left'],row['left']+cap];tx=[0,l,l+cap]
        if row['bars']:
            old=[row['left'],*[v for pair in row['bars'] for v in pair],row['width']-row['right']]
            weights=[old[i+1]-old[i] for i in range(0,len(old)-1,2)];widths=_widths(api,p,w-l-r-len(row['bars'])*p['mullion_width'],weights);cursor=l
            for i,(a,z) in enumerate(row['bars']):
                cursor+=widths[i];sx.extend([a-cap,a,z,z+cap]);tx.extend([cursor-cap,cursor,cursor+p['mullion_width'],cursor+p['mullion_width']+cap]);cursor+=p['mullion_width']
        elif row['columns']>1:
            widths=_widths(api,p,w-l-r);cursor=l
            for i in range(row['columns']-1):
                a=row['left']+(row['width']-row['left']-row['right'])*(i+1)/row['columns'];cursor+=widths[i];sx.extend([a-60,a+60]);tx.extend([cursor-60,cursor+60])
        else:widths=[w-l-r]
        sx.extend([row['width']-row['right']-cap,row['width']-row['right'],row['width']]);tx.extend([w-r-cap,w-r,w]);xmap=axis_map(sx,tx)
        sz=[0,row['bottom'],row['bottom']+cap];tz=[0,b,b+cap]
        if row['cross']:
            a,z=row['cross'];center=b+(h-b-t)*p['cross_position']/100;low,high=center-p['cross_width']/2,center+p['cross_width']/2
            sz.extend([a-cap,a,z,z+cap]);tz.extend([low-cap,low,high,high+cap])
        sz.extend([row['height']-row['top']-cap,row['height']-row['top'],row['height']]);tz.extend([h-t-cap,h-t,h]);zmap=axis_map(sz,tz)
        return {'params':p,'row':row,'xmap':xmap,'zmap':zmap,'widths':widths,'panes':sum(part['role']=='glass' for part in row['parts'])}
    sliding=p['preset']=='custom_slide';casement=p['preset']=='custom_casement'
    if sliding and p['columns']<2:raise ValueError('橫拉窗至少兩扇；單片玻璃請選固定窗。')
    if sliding and p['depth']<p['columns']*30+10:raise ValueError('框深不足以放入目前軌道數；請增加框深或減少扇數。')
    if p['depth']<40:raise ValueError('自訂窗框深度至少 4 cm，才能容納玻璃與窗扇。')
    mw=p['mullion_width'];widths=_widths(api,p,w-l-r-(0 if sliding else (p['columns']-1)*mw))
    if min(widths)<max(100,2*p['sash_width']+30):raise ValueError('每欄淨寬不足以放入窗扇框料。')
    net=h-b-t-(p['rows']-1)*p['cross_width']
    if p['rows']==2:
        center=b+(h-b-t)*p['cross_position']/100;heights=[center-p['cross_width']/2-b,h-t-center-p['cross_width']/2]
    else:heights=[net/p['rows']]*p['rows']
    if min(heights)<max(100,2*p['sash_width']+30):raise ValueError('分格淨高不足，請減少層數或移動橫料。')
    panes=[];z=b
    for j,hh in enumerate(heights):
        x=l
        for i,ww in enumerate(widths):
            overlap=p['sash_width']*.8 if sliding else 0.
            panes.append({'rect':[x-(overlap/2 if i else 0),z,x+ww+(overlap/2 if i<p['columns']-1 else 0),z+hh],'column':i,'row':j});x+=ww+(0 if sliding else mw)
        z+=hh+p['cross_width']
    return {'params':p,'row':None,'widths':widths,'heights':heights,'panes':len(panes),'cells':panes,'sliding':sliding,'casement':casement}

def _move(points,lay):
    points=np.array(points,float,copy=True);points[:,0]=np.interp(points[:,0],*lay['xmap']);points[:,2]=np.interp(points[:,2],*lay['zmap']);return points

def _mesh_part(api,data,points):
    group=api.Group();verts=[QVector3D(*map(float,p/1000)) for p in points]
    for f in data['faces']:
        face=group.mesh.add_face([verts[i] for i in f['loop']],[[verts[i] for i in ring] for ring in f['holes']] or None);face.attrs=deepcopy(f['attrs']);face.interior=f['interior']
    for item in data['edges']:
        edge=group.mesh.add_edge(verts[item['a']],verts[item['b']]);edge.hidden=item['hidden'];edge.soft=item['soft'];edge.curve=item['curve'];edge.layer=item['layer']
    return group

def _paint_glass(group,p):
    if p['glass_finish']=='original':return
    for face in group.mesh.faces:
        face.attrs['color']=[.56,.76,.82] if p['glass_finish']=='clear' else [.73,.81,.82];face.attrs['opacity']=.35 if p['glass_finish']=='clear' else .75

def stock_model(api,lay):
    p=lay['params'];row=lay['row'];children=[];anchors=[]
    for i,part in enumerate(row['parts']):
        role=part['role'];old=np.array(part['vertices_mm'],float);bounds=np.array(part['bounds_mm']);center=bounds.mean(0);span=np.ptp(bounds,axis=0)
        moved=_move(old,lay);new_center=_move([center],lay)[0];ratio=p['depth']/row['depth']
        if role in ('handle','hardware'):
            points=old-center;new_center[1]=center[1]
        else:
            points=moved;new_center=None
            if role=='glass':points[:,1]=(old[:,1]-center[1])*p['glass_thickness']/span[1]+center[1]
            elif role=='gasket':points[:,1]=(old[:,1]-center[1])*(p['glass_thickness']+2)/span[1]+center[1]
            elif span[1]>=row['depth']*.8:points[:,1]=old[:,1]*ratio
            else:points[:,1]=old[:,1]
        if role=='handle' and span[2]>100:
            anchor=_move([center],lay)[0];anchor[1]=bounds[1,1];anchors.append(anchor)
        if role=='handle' and p['handle_mode']!='original':continue
        group=_mesh_part(api,part,points);group.name={'frame':'窗框／框料','glass':'玻璃','gasket':'玻璃膠條','handle':'原型把手','hardware':'原型五金'}[role]+f' {i+1:02d}'
        group.ext={'lime_stock_path':f'window/{row["id"]}/{i}','lime_window_part':role,'lime_window_source_faces':[f['source_face'] for f in part['faces']]}
        if new_center is not None:
            group.xform=QMatrix4x4();group.xform.translate(*map(float,new_center/1000))
        if role=='handle':group.ext['lime_handle_id']=f'window/{row["id"]}/handle/{i}'
        if role=='glass':_paint_glass(group,p)
        children.append(group)
    if p['handle_mode']=='library':
        for i,a in enumerate(anchors):children.append(api.placed_handle(p['handle_asset'],*a,name=f'資料庫窗把手 {i+1}'))
    return children

def _box(api,rect,depth,name,y=0,color=None):
    group=api.recessed_solid(rect,[],[],0,0,depth,name);group.xform=QMatrix4x4();group.xform.translate(0,y/1000,0)
    if color:group.material={'color':color}
    return group

def _ring(api,rect,width,depth,name,y=0):
    x,z,a,b=rect
    return [_box(api,r,depth,name+label,y,[.22,.25,.27]) for label,r in [('下',[x,z,a,z+width]),('上',[x,b-width,a,b]),('左',[x,z+width,x+width,b-width]),('右',[a-width,z+width,a,b-width])]]

def custom_model(api,lay):
    p=lay['params'];w,h,d=p['width'],p['height'],p['depth'];l,r,b,t=p['left'],p['right'],p['bottom'],p['top'];children=[]
    for label,rect in [('下框',[0,0,w,b]),('上框',[0,h-t,w,h]),('左框',[0,b,l,h-t]),('右框',[w-r,b,w,h-t])]:children.append(_box(api,rect,d,label,color=[.22,.25,.27]))
    x=l
    for i,ww in enumerate([] if lay['sliding'] else lay['widths'][:-1]):
        x+=ww;children.append(_box(api,[x,b,x+p['mullion_width'],h-t],d,f'中直料 {i+1}',color=[.22,.25,.27]));x+=p['mullion_width']
    z=b
    for i,hh in enumerate(lay['heights'][:-1]):
        z+=hh;children.append(_box(api,[l,z,w-r,z+p['cross_width']],d,f'中橫料 {i+1}',color=[.22,.25,.27]));z+=p['cross_width']
    for n,cell in enumerate(lay['cells']):
        rect=cell['rect'];x,z,a,zz=rect;sash=p['sash_width'] if lay['sliding'] or lay['casement'] else 12.;y=5+cell['column']*30 if lay['sliding'] else 10.;sd=25. if lay['sliding'] or lay['casement'] else 20.
        children+=_ring(api,rect,sash,sd,f'窗扇 {n+1} ',y)
        glass=_box(api,[x+sash,z+sash,a-sash,zz-sash],p['glass_thickness'],f'玻璃 {n+1}',y+(sd-p['glass_thickness'])/2,[.56,.76,.82]);glass.ext['lime_window_part']='glass';_paint_glass(glass,{**p,'glass_finish':'clear' if p['glass_finish']=='original' else p['glass_finish']});children.append(glass)
        if p['preset']=='custom_fixed':continue
        left=p['opening']=='left' or p['opening']=='opposed' and cell['column']%2==1
        hx=x+sash/2 if left else a-sash/2;hz=(z+zz)/2
        if p['handle_mode']=='library':children.append(api.placed_handle(p['handle_asset'],hx,y,hz,name=f'資料庫窗把手 {n+1}'))
        elif p['handle_mode']=='original':
            handle=_box(api,[hx-8,hz-55,hx+8,hz+55],14,f'把手 {n+1}',y-14,[.11,.13,.14]);handle.ext['lime_handle_id']='window/custom/handle';children.append(handle)
        if lay['casement']:
            hinge_x=a-8 if left else x
            for hinge_index,zhinge in enumerate((z+40,zz-70)):children.append(_box(api,[hinge_x,zhinge,hinge_x+8,zhinge+30],12,f'鉸鏈 {n+1} {hinge_index+1}',y-5,[.11,.13,.14]))
    return children

def model(api,values=None):
    lay=layout(api,values);p=lay['params'];root=api.Group(name='窗組｜'+(lay['row']['name'] if lay['row'] else CUSTOM[p['preset']]))
    children=stock_model(api,lay) if lay['row'] else custom_model(api,lay)
    for i,c in enumerate(children):
        c.ext={**(c.ext or {}),'lime_window_local':list((c.xform if c.xform is not None else QMatrix4x4()).data())}
        if 'lime_stock_path' not in c.ext:c.ext['lime_stock_path']=f'window/{p["preset"]}/{c.name}'
    root.adopt(children);root.ext={api.PARAM_KEY:{'kind':'window','version':1,'params':p},'lime_suite_generated':'window','lime_window_panes':lay['panes']}
    return api.bind_face_materials(root)

def local_basis(root):
    if not root.children:
        return QMatrix4x4(root.axes) if root.mesh.vertices and root.axes is not None else QMatrix4x4()
    child=root.children[0];data=child.ext.get('lime_window_local')
    if not data:raise ValueError('窗組定位資料遺失，請重新讀取完整窗組。')
    saved=QMatrix4x4(*np.array(data).reshape(4,4).T.flatten().tolist());inverse,ok=saved.inverted()
    if not ok:raise ValueError('窗組定位不可逆。')
    return (child.xform if child.xform is not None else QMatrix4x4())*inverse

def recognize(api,group):
    # Only source topology/coordinates identify an original, never its name alone.
    if group.children or not group.mesh.vertices:return None
    pts=np.array([api.xyz(v.position) for v in group.mesh.vertices])*1000;span=np.ptp(pts,axis=0);thin=int(np.argmin(span));turn=QMatrix4x4()
    if thin==0:turn=QMatrix4x4(0,-1,0,0,1,0,0,0,0,0,1,0,0,0,0,1)
    elif thin!=1:return None
    actual=np.array([api.xyz(turn.map(v.position)) for v in group.mesh.vertices])*1000;lo=actual.min(0);actual-=lo
    for key,row in presets(api).items():
        if len(group.mesh.faces)!=sum(len(p['faces']) for p in row['parts']):continue
        expected=np.concatenate([np.array(part['vertices_mm']) for part in row['parts']]);expected-=expected.min(0)
        a=np.unique(np.round(actual,2),axis=0);b=np.unique(np.round(expected,2),axis=0)
        if a.shape!=b.shape or not np.allclose(a,b,atol=.021,rtol=0):continue
        # Locate the original exterior-frame front plane, not protruding handles.
        frame_front=min(part['bounds_mm'][0][1] for part in row['parts'] if part['role']=='frame' and abs((part['bounds_mm'][1][0]-part['bounds_mm'][0][0])-row['width'])<.02 and abs((part['bounds_mm'][1][2]-part['bounds_mm'][0][2])-row['height'])<.02)
        canonical_min=np.concatenate([np.array(part['vertices_mm']) for part in row['parts']]).min(0);lo[1]-=canonical_min[1]
        normalizer=QMatrix4x4();normalizer.translate(*map(float,-lo/1000));normalizer=normalizer*turn;inverse,ok=normalizer.inverted()
        if not ok:return None
        return key,(group.xform if group.xform is not None else QMatrix4x4())*inverse
    return None
