# SPDX-License-Identifier: GPL-3.0-or-later
"""Check the selected Blender and its own D5 installation, without launching D5."""
import sys,json,traceback,platform
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from transport import write_json

def check(directory):
    report={'ok':False,'python':platform.python_version()}
    try:
        import bpy,addon_utils,importlib.util
        report['blender']=bpy.app.version_string
        report['executable']=bpy.app.binary_path
        report['addon_search_paths']=list(addon_utils.paths())
        if not (4,5)<=bpy.app.version[:2]<=(5,2):
            raise RuntimeError('目前選到 Blender '+bpy.app.version_string+'。本版相容檢查涵蓋 4.5 至 5.2 正式版；請選取此範圍的 Blender。')
        runtime=f'{bpy.app.version[0]}.{bpy.app.version[1]}'
        spec=importlib.util.find_spec('d5_export')
        if spec is None:
            raise RuntimeError(f'此 Blender {runtime} 找不到官方 D5 LiveSync。請用官方最新安裝包為 Blender {runtime} 安裝 Sync，再回來按 D5；安裝在其他 Blender 版本不會生效。')
        report['addon_file']=spec.origin
        report['expected_python_abi']=f'cp{sys.version_info.major}{sys.version_info.minor}'
        report['sdk_files']=[p.name for p in (Path(spec.origin).parent/'tools').glob('d5plugin*.pyd')]
        failures=[]
        addon=addon_utils.enable('d5_export',default_set=True,persistent=False,handle_error=lambda:failures.append(traceback.format_exc()))
        if addon is None:
            report['addon_error']='\n'.join(failures)
            raise RuntimeError(f'官方 D5 Sync 已找到，但無法在 Blender {runtime} 載入。請用官方安裝包安裝此版本適用的 Sync，不要從其他 Blender 資料夾直接複製；可按「複製診斷」查看錯誤。')
        version=tuple(addon.bl_info.get('version',()))
        report['addon_version']=list(version)
        if version!=(1,0,0,28):raise RuntimeError('官方 D5 Sync 版本不相符：'+str(version)+'；本插件支援 1.0.0.0028。')
        from d5_export.tools import d5plugin
        for name in ('GetD5RenderStatus','D5ProcessedSyncConnect','D5AddLight','D5SendLights','D5SetPathElementName','D5DeletePathElementName'):
            if not hasattr(d5plugin,name):raise RuntimeError('官方 Sync 缺少必要介面 '+name+'，請重新安裝。')
        from d5_adapter import D5Adapter
        from transport import read_json
        config=read_json(Path(directory)/'config.json',{})
        if config.get('d5_path'):
            adapter=D5Adapter(config['d5_path'],addon=addon)
            adapter.close()
            report['adapter_initialized']=True
        report.update(ok=True,message=f'Blender {runtime} 與官方 D5 Sync 載入檢查通過。')
    except Exception as error:
        report.update(message=str(error),traceback=traceback.format_exc())
    write_json(Path(directory)/'preflight.json',report)
    print('PREFLIGHT',json.dumps(report,ensure_ascii=False),flush=True)

if __name__=='__main__':check(sys.argv[sys.argv.index('--')+1])
