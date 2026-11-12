"""Runs only in an isolated Blender process. GPL-3.0-or-later."""
import importlib.util
import json
from pathlib import Path
import sys
import traceback


def main():
    import bpy
    from mathutils import Matrix
    source, dest, scale = sys.argv[sys.argv.index("--") + 1:]
    destination = Path(dest)
    report_file = destination.with_name("report.json")
    report = {"ok": False}
    try:
        if bpy.app.version < (4, 2, 0):
            raise RuntimeError("需要 Blender 4.2 以上版本。")
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        module_path = Path(__file__).parent / "vendor" / "import_max.py"
        spec = importlib.util.spec_from_file_location("it_max_reader", module_path)
        reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reader)

        class Reporter:
            def report(self, level, message):
                print(level, message)

        reader.load(Reporter(), bpy.context, filepath=source,
                    scale_objects=float(scale), use_image_search=True,
                    object_filter={"MATERIAL", "UV", "PRIMITIVE", "EMPTY"},
                    use_apply_matrix=True, global_matrix=Matrix.Identity(4))
        meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
        if not meshes or not any(len(obj.data.polygons) for obj in meshes):
            raise RuntimeError("MAX 解析器沒有讀到可用網格，可能是不支援的版本或物件類型。")
        missing = []
        for img in bpy.data.images:
            if img.source == "FILE" and not img.packed_file and img.filepath:
                if not Path(bpy.path.abspath(img.filepath)).is_file():
                    missing.append(img.filepath)
        bpy.ops.export_scene.gltf(filepath=str(destination), export_format="GLB",
                                  export_apply=True, export_animations=False,
                                  export_cameras=False, export_lights=False)
        report.update(ok=True, objects=len(meshes), missing_textures=missing,
                      note="MAX 經社群解析器轉換；修改器、程序材質、動畫與燈光不保證保留。")
    except Exception as exc:
        report["error"] = str(exc)
        traceback.print_exc()
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    if not report["ok"]:
        raise RuntimeError(report["error"])


if __name__ == "__main__":
    main()
