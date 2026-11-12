"""Parsing and isolated Blender conversion. GPL-3.0-or-later."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


class Cancelled(Exception):
    pass


SUPPORTED_SUFFIXES = {".skp", ".max", ".dwg", ".dxf"}


def external_env():
    env = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        env.pop(key, None)
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        prefix = os.path.normcase(os.path.abspath(frozen_root))
        env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep)
                                    if not os.path.normcase(os.path.abspath(p)).startswith(prefix))
        if "LD_LIBRARY_PATH_ORIG" in env:
            env["LD_LIBRARY_PATH"] = env["LD_LIBRARY_PATH_ORIG"]
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


def _start_external(args, out, env, flags):
    """Undo PyInstaller's DLL search override only while creating the child."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT,
                                env=env, creationflags=flags)
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    size = kernel.GetDllDirectoryW(0, None)
    previous = ctypes.create_unicode_buffer(size + 1)
    kernel.GetDllDirectoryW(len(previous), previous)
    if not kernel.SetDllDirectoryW(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT,
                                env=env, creationflags=flags)
    finally:
        kernel.SetDllDirectoryW(previous.value or None)


def find_blender():
    found = shutil.which("blender")
    if found:
        return found
    roots = [Path(os.environ.get("ProgramFiles", "C:/Program Files")) /
             "Blender Foundation", Path("/Applications")]
    for root in roots:
        if root.is_dir():
            matches = sorted(root.glob("Blender*/blender.exe"), reverse=True)
            matches += sorted(root.glob("Blender*.app/Contents/MacOS/Blender"))
            if matches:
                return str(matches[0])
    return ""


def validate_file(filename):
    p = Path(filename).resolve()
    if p.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError("接受 SKP、MAX、DWG 與 DXF 檔案。")
    if not p.is_file():
        raise ValueError("找不到檔案：" + str(p))
    with p.open("rb") as stream:
        head = stream.read(64)
    if not head:
        raise ValueError("檔案是空的。")
    if p.suffix.lower() == ".dwg" and not head.startswith(b"AC"):
        raise ValueError("不是可辨識的 DWG 檔案。")
    if p.suffix.lower() == ".max" and not head.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise ValueError("不是可辨識的 MAX 容器，或此 MAX 格式尚未支援。")
    if p.suffix.lower() == ".skp" and not (
        head.startswith(b"PK\x03\x04") or b"S\x00k\x00e\x00t\x00c\x00h" in head
    ):
        raise ValueError("不是可辨識的 SKP 檔案；更改副檔名不會轉換格式。")
    return p


def prepare_skp(p, options, progress):
    try:
        from .skp_compat import parse as parser
    except (ImportError, AttributeError) as exc:
        raise RuntimeError("此版 IngeTrazo 缺少所需 SKP API，請使用 0.5.11 相容版本。") from exc
    payload = parser(p, progress=progress)
    if not payload or payload.get("empty"):
        raise ValueError("檔案中沒有可匯入的模型幾何。")
    # The payload belongs to this job. Do not modify host scene or shared parser state.
    if not options["scenes"]:
        payload.pop("scenes", None)
    if not options["annotations"]:
        payload.pop("dimensions", None)
        payload.pop("texts", None)
    return payload


def convert_max(p, options, cancelled, progress, job_dir):
    blender = Path(options["blender"])
    if not blender.is_file():
        raise RuntimeError("請先選擇 Blender 4.2 以上版本的執行檔。")
    root = Path(__file__).parent
    output = job_dir / "model.glb"
    log = job_dir / "blender.log"
    args = [str(blender), "--background", "--factory-startup", "--disable-autoexec",
            "--python-exit-code", "7", "--python", str(root / "blender_worker.py"),
            "--", str(p), str(output), str(options["max_scale"])]
    progress(0.05, "Blender 背景轉換 MAX…")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    env = external_env()
    with log.open("wb") as out:
        proc = _start_external(args, out, env, flags)
        start = time.monotonic()
        try:
            while proc.poll() is None:
                if cancelled.is_set():
                    raise Cancelled("已取消 MAX 轉換。")
                if time.monotonic() - start > 1800:
                    raise TimeoutError("MAX 轉換超過 30 分鐘，已停止；請嘗試較小的模型。")
                time.sleep(0.1)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
    report_path = job_dir / "report.json"
    report = json.loads(report_path.read_text("utf-8")) if report_path.exists() else {}
    if proc.returncode or not output.is_file() or not report.get("ok"):
        with log.open("rb") as stream:
            stream.seek(max(0, log.stat().st_size - 5000))
            tail = stream.read().decode("utf-8", errors="replace")
        raise RuntimeError(report.get("error", "MAX 轉換失敗，可能是解析器不支援此模型。") + "\n" + tail)
    return output, report


def stage(filename, options, cancelled, progress):
    p = validate_file(filename)

    def tick(frac, message):
        if cancelled.is_set():
            raise Cancelled("已取消；模型尚未加入場景。")
        progress(frac, message)

    tick(0, "讀取模型…")
    if p.suffix.lower() in {".dwg", ".dxf"}:
        from .cad import prepare_cad
        return prepare_cad(p, options, cancelled, tick)
    if p.suffix.lower() == ".skp":
        payload = prepare_skp(p, options, tick)
        tick(0.75, "SKP 解析完成")
        return {"kind": "skp", "payload": payload, "path": p}
    # GLB images are extracted into IngeTrazo's persistent texture store during
    # read_triangles; the temporary conversion directory can then be removed.
    with tempfile.TemporaryDirectory(prefix="ingetrazo-model-import-") as tmp:
        output, report = convert_max(p, options, cancelled, tick, Path(tmp))
        from formats.glb import read_triangles
        batches, _ = read_triangles(output)
        tick(0.75, "MAX 轉換完成")
        if not batches:
            raise ValueError("轉換後沒有可匯入的面；此 MAX 內容可能不受支援。")
        return {"kind": "max", "batches": batches, "report": report, "path": p}


def make_command(result, options, progress=None):
    """Construct on GUI thread. Preserve import metadata across undo/redo too."""
    from core.history import SnapshotImport
    from core.group import Group

    def mutate(scene):
        before = list(scene.groups)
        if result["kind"] == "skp":
            from formats.skp import apply_payload
            apply_payload(scene, result["payload"], progress=progress)
        elif result["kind"] == "cad":
            from formats.dxf_in import load_dxf
            result["stats"] = load_dxf(scene, result["path"], doc=result["doc"],
                                       scale=result["scale"], name=result["path"].stem,
                                       progress=progress)
        else:
            from formats.glb import build_mesh
            # Explicit unit multiplier was applied by Blender. No size guessing.
            mesh = build_mesh(result["batches"], progress=progress)
            if not mesh.faces:
                raise ValueError("轉換後沒有有效的模型面。")
            scene.groups.append(Group(mesh, name=result["path"].stem))
        added = [g for g in scene.groups if g not in before]
        if not added:
            raise ValueError("解析完成，但沒有產生可用的模型群組。")
        from core.group import iter_placements
        faces = edges = placements = 0
        for top in added:
            for group, _matrix in iter_placements(top):
                faces += len(group.mesh.faces)
                edges += len(group.mesh.edges)
                placements += 1
        if not faces and not edges:
            raise ValueError("解析結果只有空群組，沒有模型面或線段；匯入已取消。")
        result["geometry"] = dict(faces=faces, edges=edges, placements=placements)
        # CAD layers and block placements stay inside one selectable file group.
        # The optional container checkbox applies only to SKP.
        if result["kind"] == "cad" or (options["container"] and result["kind"] == "skp"):
            container = Group(name=result["path"].stem)
            container.adopt(added)
            scene.groups[:] = before + [container]

    class ImportCommand(SnapshotImport):
        def do(self, scene):
            first = self.after is None
            if first:
                self.metadata_before = (dict(scene.materials), scene.back_face_color)
            try:
                super().do(scene)
            except BaseException:
                scene.materials.clear()
                scene.materials.update(self.metadata_before[0])
                scene.back_face_color = self.metadata_before[1]
                raise
            if first:
                self.metadata_after = (dict(scene.materials), scene.back_face_color)
            else:
                scene.materials.clear()
                scene.materials.update(self.metadata_after[0])
                scene.back_face_color = self.metadata_after[1]

        def undo(self, scene):
            super().undo(scene)
            scene.materials.clear()
            scene.materials.update(self.metadata_before[0])
            scene.back_face_color = self.metadata_before[1]

    return ImportCommand(mutate)


def commit(history, command):
    history.execute(command)
    # History deliberately catches command exceptions and exposes them here.
    # A normal return alone therefore does not mean the import succeeded.
    error = getattr(history, "last_error", None) or getattr(history, "last_rejected", None)
    if error:
        raise RuntimeError(error)
