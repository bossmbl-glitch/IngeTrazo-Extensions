# SPDX-License-Identifier: GPL-3.0-or-later
"""Observe renderer lifetime outside the potentially blocked SDK worker."""
import os


def renderer_processes(_directory=None):
    """Return matching renderer PIDs, or None when Windows inspection fails.

    The launcher is not the renderer. The launcher's configured directory can
    differ from the running renderer's version directory, so inspect the main
    executable name rather than assuming the launcher's install layout.
    """
    if os.name!='nt':return None
    import ctypes
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_=[('dwSize',wintypes.DWORD),('cntUsage',wintypes.DWORD),('pid',wintypes.DWORD),
                  ('heap',ctypes.c_size_t),('module',wintypes.DWORD),('threads',wintypes.DWORD),
                  ('parent',wintypes.DWORD),('priority',wintypes.LONG),('flags',wintypes.DWORD),
                  ('exe',wintypes.WCHAR*260)]
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD];api.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    api.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Entry)];api.Process32FirstW.restype=wintypes.BOOL
    api.Process32NextW.argtypes=api.Process32FirstW.argtypes;api.Process32NextW.restype=wintypes.BOOL
    api.CloseHandle.argtypes=[wintypes.HANDLE];api.CloseHandle.restype=wintypes.BOOL
    snapshot=api.CreateToolhelp32Snapshot(2,0)
    if snapshot in (None,ctypes.c_void_p(-1).value):return None
    found=set();unknown=False
    try:
        entry=Entry();entry.dwSize=ctypes.sizeof(entry)
        if not api.Process32FirstW(snapshot,ctypes.byref(entry)):return None
        while True:
            if entry.exe.casefold() in {'d5render.exe','d5_render.exe','d5renderue.exe'}:
                found.add(entry.pid)
            if not api.Process32NextW(snapshot,ctypes.byref(entry)):
                if ctypes.get_last_error()!=18:unknown=True  # ERROR_NO_MORE_FILES
                break
    finally:api.CloseHandle(snapshot)
    windows=window_renderer_pids()
    if windows is not None:found.update(windows)
    return None if unknown and not found else found


class RendererWatch:
    def __init__(self,probe=renderer_processes):
        self.probe=probe;self.seen=False;self.missing_since=None;self.next_poll=0.
        self.probe_available=None;self.pids=[];self.sdk_seen=False;self.sdk_state='';self.sdk_missing=None

    def report(self):
        return {'armed':self.seen,'probe_available':self.probe_available,'renderer_pids':self.pids,
                'missing_since':self.missing_since,'expected_executable':'D5 renderer names or D5 Unreal window','sdk_session_seen':self.sdk_seen,'sdk_state':self.sdk_state,'detection_warning':('程序查詢失敗，暫不能確認 D5 是否關閉' if self.probe_available is False else None if self.seen else '尚未辨識 D5 主程序；使用 SDK 狀態輔助監測')}

    def observe_sdk(self,state,now):
        self.sdk_state=str(state or '')
        name=self.sdk_state.rsplit('.',1)[-1]
        if name=='D5RenderInProject':self.sdk_seen=True;self.sdk_missing=None;return False
        if not self.sdk_seen or name not in {'D5RenderNotRunning','D5RenderNotStarted','D5RenderClosed'}:
            self.sdk_missing=None;return False
        if self.sdk_missing is None:self.sdk_missing=now
        return now-self.sdk_missing>=2.

    def closed(self,directory,now):
        if now<self.next_poll:return False
        self.next_poll=now+1.
        try:pids=self.probe(directory)
        except (OSError,ValueError):pids=None
        self.probe_available=pids is not None
        self.pids=sorted(pids) if pids else []
        if pids is None:self.missing_since=None;return False
        if pids:self.seen=True;self.missing_since=None;return False
        if not self.seen:return False
        if self.missing_since is None:self.missing_since=now
        return now-self.missing_since>=2.


def window_renderer_pids():
    """Observe D5's Unreal window when its executable name differs by build."""
    if os.name!='nt':return None
    import ctypes
    from ctypes import wintypes
    user=ctypes.WinDLL('user32',use_last_error=True);found=set()
    callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    user.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    user.GetClassNameW.argtypes=user.GetWindowTextW.argtypes
    user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user.EnumWindows.argtypes=[callback,wintypes.LPARAM];user.EnumWindows.restype=wintypes.BOOL
    def visit(hwnd,_):
        title=ctypes.create_unicode_buffer(1024);kind=ctypes.create_unicode_buffer(256)
        user.GetWindowTextW(hwnd,title,len(title));user.GetClassNameW(hwnd,kind,len(kind))
        if renderer_window(kind.value,title.value):
            pid=wintypes.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            if pid.value:found.add(pid.value)
        return True
    cb=callback(visit)
    return found if user.EnumWindows(cb,0) else None


def renderer_window(kind,title):
    return kind.casefold()=='unrealwindow' and 'd5' in title.casefold()
