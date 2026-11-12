# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded Windows install discovery, including custom-drive registry installs."""
import os
import re
import shutil
from pathlib import Path

def clean_path(value):
    return Path(os.path.expandvars(str(value or '').strip().strip('"')))

def registry_locations():
    if os.name!='nt':return []
    import winreg
    result=[]
    for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive,r'Software\Microsoft\Windows\CurrentVersion\Uninstall',0,winreg.KEY_READ|view) as root:
                    for i in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            with winreg.OpenKey(root,winreg.EnumKey(root,i)) as key:
                                name=str(winreg.QueryValueEx(key,'DisplayName')[0])
                                if not any(x in name.lower() for x in ('blender','d5 render')):continue
                                for field in ('InstallLocation','DisplayIcon'):
                                    try:
                                        value=str(winreg.QueryValueEx(key,field)[0])
                                        value=re.sub(r',-?\d+$','',value).strip('"')
                                        path=clean_path(value)
                                        result.append((name,path.parent if path.suffix.lower()=='.exe' else path))
                                    except OSError:pass
                        except OSError:pass
            except OSError:pass
    return result

def blender_candidates():
    candidates=[]
    for env in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA'):
        root=Path(os.environ.get(env,r'C:\Program Files'))
        candidates.extend((root/'Blender Foundation').glob('Blender */blender.exe'))
        candidates.extend((root/'Programs'/'Blender Foundation').glob('Blender */blender.exe'))
        candidates.append(root/'Steam/steamapps/common/Blender/blender.exe')
    candidates.extend(path/'blender.exe' for name,path in registry_locations() if 'blender' in name.lower())
    found=shutil.which('blender.exe')
    if found:candidates.append(Path(found))
    def score(path):
        numbers=tuple(int(x) for x in re.findall(r'\d+',path.parent.name))
        return (numbers,str(path))
    return sorted({p for p in candidates if p.is_file()},key=score,reverse=True)

def d5_candidates():
    roots=[Path(os.environ.get(key,''))/'D5 Render' for key in ('APPDATA','LOCALAPPDATA','PROGRAMFILES','PROGRAMFILES(X86)')]
    roots.extend(path for name,path in registry_locations() if 'd5 render' in name.lower())
    return sorted({p for p in roots if (p/'d5_launcher.exe').is_file()},key=str)

def choose(saved,candidates,kind):
    path=clean_path(saved)
    if kind=='d5' and path.name.lower()=='d5_launcher.exe':path=path.parent
    valid=(path/'d5_launcher.exe').is_file() if kind=='d5' else path.is_file() and path.name.lower()=='blender.exe'
    return str(path) if valid else str(candidates[0]) if candidates else str(saved or '')
