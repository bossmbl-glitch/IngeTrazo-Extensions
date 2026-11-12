import os,json,copy,hashlib
from pathlib import Path

def root():
    custom=os.environ.get('IT_MATERIAL_LIBRARY')
    return Path(custom) if custom else Path(os.environ.get('APPDATA',str(Path.home())))/'IngeTrazo/material_studio'

def read():
    p=root()/'library.json'
    if not p.exists():return []
    data=json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(data,list):raise ValueError('個人材質庫格式無效。')
    return data

def _read_list(name):
    path=root()/name
    if not path.exists():return []
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,list):raise ValueError('材質庫設定格式無效。')
    return value

def _write_list(name,value):
    root().mkdir(parents=True,exist_ok=True)
    temp=root()/(name+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(root()/name)

def remove(name):
    records=read();removed=[r for r in records if r['name']==name]
    if not removed:return False
    # Retain image files: open models and other saved colours may share them.
    _write_list('deleted.json',_read_list('deleted.json')+removed)
    _write_list('library.json',[r for r in records if r['name']!=name]);return True

def restore_last():
    trash=_read_list('deleted.json')
    if not trash:return None
    entry=trash[-1];records=read()
    if any(r['name']==entry['name'] for r in records):raise ValueError('已有同名收藏，未覆蓋；請先處理同名材質。')
    _write_list('library.json',records+[entry]);_write_list('deleted.json',trash[:-1]);return entry

def hidden():return _read_list('hidden.json')

def hide(category,name):
    entries=hidden();key=[category,name]
    if key not in entries:_write_list('hidden.json',entries+[key])

def unhide_all():_write_list('hidden.json',[])

def save(recipe):
    from .engine import validate
    r=validate(recipe)
    folder=root();folder.mkdir(parents=True,exist_ok=True)
    if r['path']:
        source=Path(r['path']);data=source.read_bytes()
        target=folder/'textures'/(hashlib.sha256(data).hexdigest()[:24]+source.suffix.lower())
        target.parent.mkdir(exist_ok=True)
        if not target.exists():target.write_bytes(data)
        r['path']=str(target)
    records=read()
    records=[x for x in records if x['name']!=r['name']]+[r]
    temp=folder/'library.tmp'
    temp.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(folder/'library.json')
    return r
