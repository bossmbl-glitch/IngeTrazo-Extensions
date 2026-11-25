"""Unified door assemblies and split louvres; all stored lengths are mm."""
from copy import deepcopy
import json
import math

import numpy as np
from PySide6.QtGui import QMatrix4x4, QVector3D
from core.group import Group


def sizes(total,count,values):
    if not isinstance(values,list) or len(values)>count:raise ValueError('指定扇寬項目不得超過扇數。')
    result=[float(values[i]) if i<len(values) and values[i] is not None else None for i in range(count)]
    if any(v is not None and (not math.isfinite(v) or v<=0) for v in result):raise ValueError('指定扇寬必須大於零。')
    remaining=total-sum(v for v in result if v is not None);auto=result.count(None)
    if not auto and abs(remaining)>.01:raise ValueError('指定扇寬總和與可用寬度不符。')
    result=[remaining/auto if v is None else v for v in result]
    if min(result)<=10:raise ValueError('每扇淨寬必須大於 1 cm。')
    return result


LOUVER_DEFAULTS={'middle_enabled':False,'middle_width':100.,'middle_position':50.,
    'middle_positions':[],'panel_widths':[],'upper_gap':8.,'lower_gap':8.,
    'outer_frame_style':'original','outer_frame_width':40.,'sash_style':'original',
    'stile_width':50.,'rail_width':140.,'frame_depth':31.,'handle_asset':None,
    'handle_side':'front','handle_edge':'right','handle_margin':25.,'handle_height':950.,'handle_stand':0.,'handle_angle':0.}


def louver_layout(api,values=None):
    values=values or {};t=api.stock_data('louver')['template']
    # Preserve the original stock validation and layout on the existing path.
    p={'width':t['width'],'height':t['height'],'panels':3,'angle':t['blade_reference_angle'],
       'direction':'same','target_gap':8.,'panel_gap':2.,**deepcopy(LOUVER_DEFAULTS)}
    p.update({k:v for k,v in values.items() if k in p})
    api.validate_accessory_parameters(p)
    if not isinstance(p['middle_enabled'],bool):raise ValueError('中橫條開關無效。')
    for key in ('width','height','panels','angle','target_gap','panel_gap','middle_width','middle_position',
                'outer_frame_width','stile_width','rail_width','frame_depth','upper_gap','lower_gap'):
        p[key]=float(p[key])
        if not math.isfinite(p[key]):raise ValueError('百葉參數必須為有限數值。')
    if p['panels']!=int(p['panels']) or not 1<=p['panels']<=20:raise ValueError('扇數須為 1～20 的整數。')
    p['panels']=int(p['panels'])
    if not -90<=p['angle']<=90 or p['direction'] not in ('same','opposite'):raise ValueError('葉片角度或旋轉方式無效。')
    if not all(0<p[k]<=20000 for k in ('width','height')):raise ValueError('百葉寬高須大於零且不超過 2000 cm。')
    if not 0<=p['panel_gap']<=20 or min(p['target_gap'],p['upper_gap'],p['lower_gap'])<=0:raise ValueError('葉片淨距須大於零，扇間隙須為 0～2 cm。')
    if p['outer_frame_style'] not in ('original','plain','none') or p['sash_style'] not in ('original','plain'):raise ValueError('百葉框樣式無效。')
    if min(p['outer_frame_width'],p['stile_width'],p['rail_width'],p['frame_depth'])<5:raise ValueError('百葉框料與厚度至少 0.5 cm。')
    if not 0<=p['middle_position']<=100 or p['middle_width']<5:raise ValueError('橫條位置須為 0～100%，橫條高度至少 0.5 cm。')
    if not isinstance(p['middle_positions'],list) or len(p['middle_positions'])>p['panels']:raise ValueError('橫條位置項目不得超過扇數。')
    positions=[float(p['middle_positions'][i]) if i<len(p['middle_positions']) and p['middle_positions'][i] is not None else p['middle_position'] for i in range(p['panels'])]
    if not all(math.isfinite(v) and 0<=v<=100 for v in positions):raise ValueError('各扇橫條位置須為 0～100%。')
    if p['outer_frame_style']=='original':insets=[t['inset_left'],t['inset_right'],t['inset_bottom'],t['inset_top']]
    else:insets=[p['outer_frame_width'] if p['outer_frame_style']=='plain' else 0.]*4
    widths=sizes(p['width']-insets[0]-insets[1]-(p['panels']-1)*p['panel_gap'],p['panels'],p['panel_widths'])
    stile=t['stile'] if p['sash_style']=='original' else p['stile_width']
    rail=t['rail'] if p['sash_style']=='original' else p['rail_width']
    height=p['height']-insets[2]-insets[3];usable=height-2*rail
    if min(widths)<=2*stile+10 or usable<=0:raise ValueError('百葉門扇放不下框料，請增加尺寸或減少扇數。')
    if p['sash_style']=='original' and (min(widths)<=2*t['sash_x_cap']+10 or height<=2*t['sash_z_cap']+10):raise ValueError('原型框料接頭空間不足，請增加尺寸或使用素框。')
    rows=[]
    for i in range(p['panels']):
        angle=p['angle']*(-1 if p['direction']=='opposite' and i%2 else 1);radians=math.radians(angle)
        zz=[y*math.sin(radians)+z*math.cos(radians) for y,z in t['neutral_profile']];projection=max(zz)-min(zz)
        middle=rail+usable*positions[i]/100
        spans=[('lower',rail,middle-p['middle_width']/2,p['lower_gap']),('upper',middle+p['middle_width']/2,height-rail,p['upper_gap'])] if p['middle_enabled'] else [('all',rail,height-rail,p['target_gap'])]
        zones=[]
        for name,lo,hi,target in spans:
            count=int((hi-lo)/(projection+target))
            if count<1:raise ValueError(f'第 {i+1} 扇的'+('上區' if name=='upper' else '下區' if name=='lower' else '葉片區')+'放不下一片葉片，請移動橫條或增加高度。')
            gap=(hi-lo-count*projection)/count
            zones.append({'name':name,'lo':lo,'hi':hi,'count':count,'gap':gap,'margin':gap/2,'pitch':projection+gap})
        rows.append({'angle':angle,'zmin':min(zz),'zmax':max(zz),'projection':projection,'middle':middle,'zones':zones,
                     'gap':zones[0]['gap'],'margin':zones[0]['margin'],'pitch':zones[0]['pitch']})
    if sum(z['count'] for row in rows for z in row['zones'])>3000:raise ValueError('葉片總數超過 3000，請增加淨距或減少扇數。')
    return {'params':p,'widths':widths,'insets':insets,'stile':stile,'rail':rail,'rows':rows,
            'sash_height':height,'bay':widths[0],'blade_length':widths[0]-2*stile,'usable_height':usable,
            'blades':sum(z['count'] for z in rows[0]['zones'])}


def louver_model(api,values=None):
    layout=louver_layout(api,values);p=layout['params'];t=api.stock_data('louver')['template']
    # Retain every original stock vertex/part on the unchanged default path.
    if not p['middle_enabled'] and not p['panel_widths'] and p['outer_frame_style']=='original' and p['sash_style']=='original' and not p['handle_asset']:
        root=api._legacy_louver_model(p);root.ext[api.PARAM_KEY]['params']=p
        return root
    root=Group(name=f'百葉門 {p["panels"]}扇');children=[]
    def stock_frame(part,mapping):
        sub=Group(name=part['name']);sub.adopt(api.stock_leaf('louver',leaf,*[mapping(v) for v in leaf['bounds_mm']],t['origin_mm']) for leaf in part['leaves']);return sub
    if p['outer_frame_style']=='original':
        for part in t['outer_parts']:
            children.append(stock_frame(part,lambda v:[api.warp_stock(v[0],0,t['width'],0,p['width'],t['outer_cap']),v[1],api.warp_stock(v[2],0,t['height'],0,p['height'],t['outer_cap'])]))
    elif p['outer_frame_style']=='plain':
        b=p['outer_frame_width'];w,h=p['width'],p['height']
        for name,rect in [('外框左',[0,0,b,h]),('外框右',[w-b,0,w,h]),('外框下',[b,0,w-b,b]),('外框上',[b,h-b,w-b,h])]:children.append(api.recessed_solid(rect,[],[],0,0,p['frame_depth'],name))
    source=t['blade'];offset=QMatrix4x4();offset.translate(-t['blade_min_x']/1000,-t['blade_center_y']/1000,-t['blade_center_z']/1000)
    stock=api.stock_matrix(source['transform_mm'],t['origin_mm']);x=layout['insets'][0];z=layout['insets'][2]
    for index,width in enumerate(layout['widths']):
        sash=Group(name=f'窗扇 {index+1}');parts=[];height=layout['sash_height'];stile=layout['stile'];rail=layout['rail']
        if p['sash_style']=='original':
            for part in t['sash_parts']:
                parts.append(stock_frame(part,lambda v:[api.warp_stock(v[0],t['sash_min'][0],t['sash_width'],x,width,t['sash_x_cap']),v[1],api.warp_stock(v[2],t['sash_min'][2],t['sash_height'],z,height,t['sash_z_cap'])]))
        else:
            for name,rect in [('左立料',[x,z,x+stile,z+height]),('右立料',[x+width-stile,z,x+width,z+height]),('下橫料',[x+stile,z,x+width-stile,z+rail]),('上橫料',[x+stile,z+height-rail,x+width-stile,z+height])]:parts.append(api.recessed_solid(rect,[],[],0,0,p['frame_depth'],name))
        row=layout['rows'][index]
        if p['middle_enabled']:
            middle=z+row['middle'];parts.append(api.recessed_solid([x+stile,middle-p['middle_width']/2,x+width-stile,middle+p['middle_width']/2],[],[],0,0,p['frame_depth'],'中間橫條'))
        scale=QMatrix4x4();scale.scale((width-2*stile)/t['blade_length'],1,1)
        rotation=QMatrix4x4();rotation.rotate(row['angle']-t['blade_reference_angle'],1,0,0)
        for zone in row['zones']:
            for i in range(zone['count']):
                center=z+zone['lo']+zone['margin']-row['zmin']+i*zone['pitch']
                move=QMatrix4x4();move.translate((x+stile)/1000,t['blade_center_y']/1000,center/1000)
                blade=Group(api.stock_mesh('louver',source['definition']),name=f'原型葉片 {zone["name"]} {i+1}')
                blade.xform=move*rotation*scale*offset*stock;blade.component=True;parts.append(blade)
        if p['handle_asset']:
            px=x+(p['handle_margin'] if p['handle_edge']=='left' else width-p['handle_margin'])
            if not x<=px<=x+width or not 0<=p['handle_height']<=height:raise ValueError('把手安裝點超出百葉門片。')
            for side in ('front','back'):
                if p['handle_side'] in (side,'both'):
                    py=-p['handle_stand'] if side=='front' else p['frame_depth']+p['handle_stand']
                    parts.append(api.placed_handle(p['handle_asset'],px,py,z+p['handle_height'],p['handle_angle'],side=='back',f'百葉把手 {index+1} {side}'))
        sash.adopt(parts);children.append(sash);x+=width+p['panel_gap']
    root.adopt(children);root.ext={api.PARAM_KEY:{'kind':'louver','params':p,'version':2},'lime_suite_generated':'louver',
        'blades_per_panel':layout['blades'],'actual_gap_mm':layout['rows'][0]['gap'],
        'louver_zone_counts':[[z['count'] for z in row['zones']] for row in layout['rows']]}
    api.detach_stock(root)
    return api.bind_face_materials(root)



def original_louver_frame(api,width,height):
    t=api.stock_data('louver')['template'];frame=Group(name='原型百葉外框');children=[]
    for part in t['outer_parts']:
        sub=Group(name=part['name'])
        def mapping(v):return [api.warp_stock(v[0],0,t['width'],0,width,t['outer_cap']),v[1],api.warp_stock(v[2],0,t['height'],0,height,t['outer_cap'])]
        sub.adopt(api.stock_leaf('louver',leaf,*[mapping(v) for v in leaf['bounds_mm']],t['origin_mm']) for leaf in part['leaves']);children.append(sub)
    frame.adopt(children);frame.ext={'lime_door_role':'frame','lime_original_louver_frame':True};api.detach_stock(frame)
    return frame


DOOR_DEFAULTS={'leaf_type':'decorative','leaf_count':1,'leaf_widths':[],'leaf_gap':3.,
    'frame_style':'rebate','leaf_thickness':40.,'glass_thickness':10.,'glass_design':'plain',
    'glass_options':{},'louver_options':{},'frame_profile_snapshot':None,'leaf_depth_mode':'center','frame_bottom':0.}


def door_layout(api,values=None):
    p={**deepcopy(api.DECORATIVE_DEFAULTS),**deepcopy(DOOR_DEFAULTS)}
    p.update({k:v for k,v in (values or {}).items() if k in p})
    if p['leaf_type']=='louver' and 'frame_style' not in (values or {}):p['frame_style']='louver_original'
    if p['frame_style']=='louver_original':
        t=api.stock_data('louver')['template'];p.update(frame_left=t['inset_left'],frame_right=t['inset_right'],frame_top=t['inset_top'],frame_bottom=t['inset_bottom'],frame_depth=max(leaf['bounds_mm'][1][1] for part in t['outer_parts'] for leaf in part['leaves']))
    for key in ('width','height','frame_bottom','frame_depth','frame_left','frame_right','frame_top','gap_left','gap_right','gap_top','gap_bottom','leaf_gap','leaf_count','leaf_thickness','glass_thickness','front_setback'):
        p[key]=float(p[key])
        if not math.isfinite(p[key]):raise ValueError('門組尺寸須為有限數值。')
    if p['leaf_type'] not in ('none','decorative','glass','louver') or p['frame_style'] not in ('none','plain','rebate','profile','louver_original'):raise ValueError('門框或門片類型無效。')
    if p['leaf_count']!=int(p['leaf_count']) or not 1<=p['leaf_count']<=20:raise ValueError('門扇數須為 1～20。')
    api.validate_accessory_parameters(p)
    p['leaf_count']=int(p['leaf_count']);p['with_frame']=p['frame_style']!='none'
    if not all(20<=p[key]<=20000 for key in ('width','height')):raise ValueError('門組淨開口寬高須介於 2～2000 cm。')
    if min(p['frame_depth'],p['leaf_thickness'],p['glass_thickness'])<2:raise ValueError('框深與門片厚度至少 0.2 cm。')
    if min(p['frame_left'],p['frame_right'],p['frame_top'])<5 or min(p[k] for k in ('gap_left','gap_right','gap_top','gap_bottom','leaf_gap','front_setback'))<0:raise ValueError('門框料至少 0.5 cm，門縫與退入不得為負。')
    thickness={'glass':p['glass_thickness'],'louver':float(p['louver_options'].get('frame_depth',31.)),'none':0.}.get(p['leaf_type'],p['leaf_thickness'])
    if p['leaf_depth_mode'] not in ('center','manual'):raise ValueError('門片前後定位模式無效。')
    if p['frame_bottom']<0:raise ValueError('下外框寬度不得為負。')
    if p['leaf_depth_mode']=='center':
        stock_pose=p['frame_style']=='louver_original' and p['leaf_type']=='louver' and p['louver_options'].get('sash_style','original')=='original'
        p['front_setback']=0. if stock_pose else (p['frame_depth']-thickness)/2 if p['with_frame'] else 0.
    if p['front_setback']<0:raise ValueError('門片厚度超過框深，無法置中。')
    if p['front_setback']+thickness>p['frame_depth'] and p['with_frame']:raise ValueError('門片厚度與退入超過門框深度。')
    if p['frame_style']=='profile' and not p['frame_profile_snapshot']:raise ValueError('請選擇門框線板剖面，或改用素框／企口框。')
    if p['frame_profile_snapshot']:api.section_points(p['frame_profile_snapshot'])
    # Width/height describe the clear frame opening, including perimeter gaps.
    net_width=p['width']-p['gap_left']-p['gap_right']-(p['leaf_count']-1)*p['leaf_gap']
    net_height=p['height']-p['gap_top']-p['gap_bottom']
    widths=sizes(net_width,p['leaf_count'],p['leaf_widths'])
    if net_height<=10:raise ValueError('扣除門縫後門片高度不足。')
    ox=p['frame_left'] if p['with_frame'] else 0.;oz=0.
    return {'params':p,'widths':widths,'leaf_height':net_height,'leaf_origin':[ox+p['gap_left'],p['front_setback'],(p['frame_bottom'] if p['with_frame'] else 0)+p['gap_bottom']],
            'outer_width':p['width']+(p['frame_left']+p['frame_right'] if p['with_frame'] else 0),
            'outer_height':p['height']+(p['frame_top']+p['frame_bottom'] if p['with_frame'] else 0)}


def frame_signature(api,values):
    layout=door_layout(api,values);p=layout['params']
    return json.dumps([p['frame_style'],layout['outer_width'],layout['outer_height'],*[p[k] for k in ('frame_depth','frame_left','frame_right','frame_top','frame_bottom','frame_rebate_front_width','frame_rebate_back_width','frame_rebate_front_depth','frame_rebate_back_depth','frame_profile_snapshot','frame_profile_side')]],sort_keys=True)


def leaf_signature(api,values):
    p=door_layout(api,values)['params'];ignore={'frame_style','frame_profile_snapshot','frame_profile_side','individual_frame','individual_rebate'}
    return json.dumps({k:v for k,v in p.items() if k not in ignore},sort_keys=True)


def frame_model(api,layout):
    p=layout['params'];w,h=layout['outer_width'],layout['outer_height']
    if p['frame_style']=='louver_original':return original_louver_frame(api,w,h)
    frame=Group(name='門框');parts=[]
    specs=[('左門框',[0,0,p['frame_left'],h-p['frame_top']]),('右門框',[w-p['frame_right'],0,w,h-p['frame_top']]),('上門框',[0,h-p['frame_top'],w,h])]
    if p['frame_bottom']>0:specs.append(('下門框',[p['frame_left'],0,w-p['frame_right'],p['frame_bottom']]))
    for index,(name,rect) in enumerate(specs):
        fields=[];depths=[]
        for side in ('front','back'):
            rw=float(p['frame_rebate_'+side+'_width']) if p['frame_style'] in ('rebate','profile') else 0.
            rd=float(p['frame_rebate_'+side+'_depth']) if rw else 0.
            if not math.isfinite(rw+rd) or rw<0 or rw>=min(p['frame_left'],p['frame_right'],p['frame_top']) or rd<0:raise ValueError('門框企口寬深無效。')
            field=[rect[2]-rw,rect[1],rect[2],rect[3]] if index==0 else [rect[0],rect[1],rect[0]+rw,rect[3]] if index==1 else [p['frame_left']-rw,rect[1],w-p['frame_right']+rw,rect[1]+rw]
            if index==3:field=[rect[0],rect[3]-rw,rect[2],rect[3]]
            fields.append([field] if rw else []);depths.append(rd)
        if sum(depths)>=p['frame_depth']-2:raise ValueError('門框企口過深，須保留框體。')
        parts.append(api.recessed_solid(rect,fields[0],fields[1],depths[0],depths[1],p['frame_depth'],name))
    frame.adopt(parts)
    if p['frame_style']=='profile':
        section=api.section_points(p['frame_profile_snapshot']);section[:,0]=section[:,0].min()+section[:,0].max()-section[:,0]
        rect=[p['frame_left'],p['frame_bottom'],w-p['frame_right'],h-p['frame_top']]
        for side in ('front','back'):
            if p['frame_profile_side'] in (side,'both'):
                rebate=p['frame_rebate_'+side+'_depth'] if p['frame_rebate_'+side+'_width']>0 else 0.
                depth=rebate if side=='front' else p['frame_depth']-rebate
                frame.adopt([*frame.children,api.sweep_section(rect,section,depth,'門框裝飾｜'+side,side=='back',True)])
    frame.ext={'lime_door_role':'frame'}
    return frame


def door_model(api,values=None):
    layout=door_layout(api,values);p=layout['params'];root=Group(name='門組｜'+{'none':'只有門框','decorative':'造型門','glass':'玻璃門','louver':'百葉門'}[p['leaf_type']]);children=[]
    if p['with_frame']:children.append(frame_model(api,layout))
    if p['leaf_type']=='none' and not p['with_frame']:raise ValueError('門框與門片不能同時關閉。')
    x,y,z=layout['leaf_origin']
    if p['leaf_type']!='none':
        for index,width in enumerate(layout['widths']):
            height=layout['leaf_height']
            if p['leaf_type']=='decorative':
                local=deepcopy(p);local.update(width=width,height=height,with_frame=False,frame_depth=p['leaf_thickness'],front_setback=0.,back_setback=0.,frame_profile_snapshot=None,individual_leaf=True,frame_rebate_front_width=0.,frame_rebate_back_width=0.)
                leaf=api._legacy_decorative_model(local)
            elif p['leaf_type']=='glass' and p['glass_design']=='stock':
                local={**p['glass_options'],'width':width,'height':height,'depth':p['glass_thickness'],'door_mode':'single','handle_type':'none','handle_asset':None}
                leaf=api.glass_model(local)
            elif p['leaf_type']=='glass':
                if p['glass_design']!='plain':raise ValueError('玻璃門片款式無效。')
                leaf=api.recessed_solid([0,0,width,height],[],[],0,0,p['glass_thickness'],'玻璃門片')
                for face in leaf.mesh.faces:face.attrs={'color':[.78,.9,.94],'opacity':.35,'back':{'color':[.78,.9,.94],'opacity':.35}}
            else:
                local={**p['louver_options'],'width':width,'height':height,'panels':1,'panel_widths':[],'outer_frame_style':'none','handle_asset':None}
                positions=local.get('middle_positions',[])
                local['middle_positions']=[positions[index]] if index<len(positions) else []
                if local.get('direction')=='opposite' and index%2:local['angle']=-local.get('angle',api.stock_data('louver')['template']['blade_reference_angle'])
                local['direction']='same'
                leaf=louver_model(api,local)
            leaf.ext.pop(api.PARAM_KEY,None);leaf.name=f'門片 {index+1:02d}'
            leaf.xform=QMatrix4x4();leaf.xform.translate(x/1000,y/1000,z/1000)
            leaf.ext.update(lime_door_role='leaf',lime_door_leaf_type=p['leaf_type'],lime_door_local_placement=list(leaf.xform.data()))
            # Decorative stock already includes its own library handles.
            if p['leaf_type'] in ('glass','louver') and p.get('handle_asset'):
                px=p['handle_margin'] if p['handle_edge']=='left' else width-p['handle_margin'] if p['handle_edge']=='right' else width/2
                pz=p['handle_height'];thickness=p['glass_thickness'] if p['leaf_type']=='glass' else float(p['louver_options'].get('frame_depth',31.))
                if not 0<=px<=width or not 0<=pz<=height:raise ValueError('把手安裝點超出門片，請調整高度或邊距。')
                handles=[]
                for side in ('front','back'):
                    if p['handle_side'] in (side,'both'):
                        py=-p['handle_stand'] if side=='front' else thickness+p['handle_stand']
                        handles.append(api.placed_handle(p['handle_asset'],px,py,pz,p['handle_angle'],side=='back','正面把手' if side=='front' else '反面把手'))
                leaf.adopt([*(leaf.children or []),*handles])
            children.append(leaf);x+=width+p['leaf_gap']
    root.adopt(children);root.ext={api.PARAM_KEY:{'kind':'door','params':p,'version':2,'geometry_revision':2},'lime_suite_generated':'door'}
    return api.bind_face_materials(root)


def migrate(api,group):
    info=api.parametric_info(group);old=deepcopy(info['params']);kind=info['kind']
    if kind=='door':return door_layout(api,old)['params']
    p={**deepcopy(api.DECORATIVE_DEFAULTS),**deepcopy(DOOR_DEFAULTS)}
    if kind=='decorative':
        p.update(old);p['leaf_type']='decorative';p['frame_style']=('profile' if old.get('frame_profile_snapshot') else 'rebate') if old['with_frame'] else 'none'
        p['width']=old['width']+old['gap_left']+old['gap_right'];p['height']=old['height']+old['gap_top']+old['gap_bottom']
        layout=api.decorative_layout(old);p['leaf_thickness']=layout['leaf_thickness'];p['front_setback']=layout['leaf_origin'][1];p['leaf_depth_mode']='manual'
    elif kind in ('glass','louver'):
        p.update(leaf_depth_mode='manual',width=old['width'],height=old['height'],frame_style='none',gap_left=0.,gap_right=0.,gap_top=0.,gap_bottom=0.,front_setback=0.,leaf_type=kind)
        if kind=='glass':
            p.update(glass_options=old,glass_design='stock',glass_thickness=old['depth'],leaf_count=2 if old.get('door_mode')=='double' else 1,leaf_gap=old.get('center_gap',3.))
        else:
            original=louver_layout(api,old);old=original['params'];left,right,bottom,top=original['insets']
            p.update(width=old['width']-left-right,height=old['height']-bottom-top,leaf_count=old['panels'],leaf_gap=old['panel_gap'],leaf_widths=deepcopy(old['panel_widths']),louver_options=old)
            if old['outer_frame_style']=='original':p['frame_style']='louver_original'
            elif old['outer_frame_style']=='plain':p.update(frame_style='plain',frame_left=left,frame_right=right,frame_top=top,frame_bottom=bottom,frame_depth=old['frame_depth'])
            else:p['frame_style']='none'
            p['leaf_thickness']=old['frame_depth'];p.update({key:deepcopy(old[key]) for key in ('handle_asset','handle_side','handle_edge','handle_margin','handle_height','handle_stand','handle_angle')})
    else:raise ValueError('請選取門框、造型門、玻璃門或百葉門組。')
    return door_layout(api,p)['params']


def local_basis(group):
    for child in group.children or ():
        if (child.ext or {}).get('lime_door_role')=='frame':
            frame=child.xform if child.xform is not None else QMatrix4x4()
            solids=list(child.children or []) if (child.ext or {}).get('lime_original_louver_frame') else [n for n in child.children or [] if n.name in ('左門框','右門框','上門框')]
            if not solids:return frame
            basis=solids[0].xform if solids[0].xform is not None else QMatrix4x4()
            if any(not np.allclose((n.xform if n.xform is not None else QMatrix4x4()).data(),basis.data(),atol=1e-5) for n in solids):raise ValueError('門框零件定位已分開變更，請先復原零件移動。')
            return frame*basis
    for child in group.children or ():
        if (child.ext or {}).get('lime_door_role')=='leaf':
            data=child.ext.get('lime_door_local_placement');local=QMatrix4x4(*np.asarray(data).reshape(4,4).T.flatten().tolist()) if data else QMatrix4x4()
            inverse,ok=local.inverted()
            if not ok:raise ValueError('門片定位不可逆。')
            return (child.xform if child.xform is not None else QMatrix4x4())*inverse
    return QMatrix4x4()


def carry_louver_paint(api,source,replacement):
    """Map the old stock hierarchy into the door frame/leaf wrappers."""
    by_name={child.name:child for child in source.children or []}
    frame=next((child for child in replacement.children or [] if child.ext.get('lime_door_role')=='frame'),None)
    if frame is not None:
        for part in frame.children or []:
            old_name={'左門框':'外框左','右門框':'外框右','上門框':'外框上','下門框':'外框下'}.get(part.name,part.name)
            if old_name in by_name:api.carry_parametric_paint(by_name[old_name],part)
    leaves=[child for child in replacement.children or [] if child.ext.get('lime_door_role')=='leaf']
    for index,leaf in enumerate(leaves,1):
        old=by_name.get(f'窗扇 {index}')
        if old is None:continue
        sash=next((child for child in leaf.children or [] if child.name.startswith('窗扇 ')),None)
        if sash is None:continue
        api.carry_parametric_paint(old,sash)
        for previous,current in zip([n for n in old.children or [] if n.name.startswith('原型葉片')],[n for n in sash.children or [] if n.name.startswith('原型葉片')]):api.carry_parametric_paint(previous,current)
        for previous,current in zip([n for n in old.children or [] if (n.ext or {}).get('lime_handle_id')],[n for n in leaf.children or [] if (n.ext or {}).get('lime_handle_id')]):api.carry_parametric_paint(previous,current)
