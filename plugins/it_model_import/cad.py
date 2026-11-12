"""DXF and local LibreDWG conversion. GPL-3.0-or-later."""
import os
from pathlib import Path
import subprocess
import tempfile
import time

from .engine import Cancelled, _start_external, external_env


def prepare_cad(path, options, cancelled, progress):
    from formats.dxf_in import open_document, read_unit_scale
    if path.suffix.lower() == ".dxf":
        progress(.1, "讀取 DXF 圖面…")
        doc = open_document(path)
    else:
        from formats.dwg_bridge import find_dwg2dxf, _strip_null_handles, _dedupe_handles
        tool = find_dwg2dxf()
        if tool is None:
            raise RuntimeError("此 IngeTrazo 未附帶 DWG 轉換器；請更新主程式或先另存為 DXF。")
        progress(.05, "背景轉換 DWG…")
        with tempfile.TemporaryDirectory(prefix="ingetrazo-cad-import-") as directory:
            output = Path(directory) / "drawing.dxf"
            logfile = Path(directory) / "converter.log"
            args = [str(tool), "-y", "-o", str(output), str(path)]
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            with logfile.open("wb") as log:
                proc = _start_external(args, log, external_env(), flags)
                start = time.monotonic()
                try:
                    while proc.poll() is None:
                        if cancelled.is_set():
                            raise Cancelled("已取消 DWG 轉換。")
                        if time.monotonic() - start > 300:
                            raise TimeoutError("DWG 轉換超過 5 分鐘，已停止。")
                        time.sleep(.1)
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
            if not output.is_file() or not output.stat().st_size:
                with logfile.open("rb") as log:
                    log.seek(max(0, logfile.stat().st_size - 3000))
                    detail = log.read().decode("utf-8", errors="replace")
                raise RuntimeError("DWG 轉換失敗，未產生可用 DXF。\n" + detail)
            _strip_null_handles(output)
            _dedupe_handles(output)
            progress(.5, "讀取轉換後的 CAD 圖面…")
            doc = open_document(output)
    scale = options.get("cad_scale")
    note = ""
    if scale is None:
        scale, _ = read_unit_scale(doc)
        if scale is None:
            scale = .001
            note = "CAD 未標示可辨識的單位，本次依毫米匯入；可在匯入視窗修改 CAD 單位後重試。"
        else:
            note = f"依 CAD 檔頭單位匯入：每圖面單位 = {scale:g} 公尺。"
    else:
        note = f"依指定 CAD 單位匯入：每圖面單位 = {scale:g} 公尺。"
    progress(.75, "CAD 解析完成")
    return {"kind": "cad", "doc": doc, "path": path, "scale": scale, "note": note}
