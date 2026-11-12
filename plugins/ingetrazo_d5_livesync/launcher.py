# SPDX-License-Identifier: GPL-3.0-or-later
"""Launch external Blender safely from IngeTrazo's frozen PyInstaller runtime."""
import os
import subprocess
import sys
from pathlib import Path


def launch(blender, worker, directory, log_file):
    environment = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        environment.pop(key, None)
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        root = os.path.normcase(os.path.abspath(bundle))
        environment["PATH"] = os.pathsep.join(
            p for p in environment.get("PATH", "").split(os.pathsep)
            if not os.path.normcase(os.path.abspath(p)).startswith(root + os.sep)
            and os.path.normcase(os.path.abspath(p)) != root)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    api = None
    if os.name == "nt" and bundle:
        import ctypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        api.SetDllDirectoryW.restype = ctypes.c_int
        if not api.SetDllDirectoryW(None):
            raise OSError("無法重設子程序 DLL 搜尋路徑")
    try:
        return subprocess.Popen([
            str(blender), "--background", "--factory-startup", "--python-exit-code", "2",
            "--python", str(worker), "--", str(directory)],
            stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT,
            env=environment, creationflags=flags)
    finally:
        if api:
            api.SetDllDirectoryW(str(bundle))
