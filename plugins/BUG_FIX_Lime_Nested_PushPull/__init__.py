# SPDX-License-Identifier: GPL-3.0-or-later
"""IngeTrazo 0.6.0: live rendering of an open, nested mesh.

Install as a user plugin. This wraps the renderer's eligibility predicate;
all geometry operations and history remain the upstream implementation.
"""
from functools import wraps
import inspect

PLUGIN_NAME = 'BUG_FIX｜巢狀群組推拉顯示修正'
__version__ = '0.1.0'
_PATCH_TAG = '_lime_nested_pushpull_fix_version'


def install_patch(viewport_class=None):
    """Install once per process; repeated setup across windows is harmless."""
    if viewport_class is None:
        from views.viewport import Viewport
        viewport_class = Viewport
    original_instance = viewport_class._instance_chunk
    original = viewport_class._instanced_eligible
    if getattr(original, _PATCH_TAG, None) == __version__:
        return False
    if len(inspect.signature(original).parameters) != 2:
        raise RuntimeError('推拉顯示修正：目前主程式的繪圖介面不相容。')

    @wraps(original)
    def eligible(self, group):
        scene = self.scene
        # Scene.begin_group_edit exposes the innermost group's world-space
        # mesh as scene.mesh. A nested placement is a different Group proxy
        # but shares that SAME mesh. Match by mesh identity, never group id.
        # The static GPU prototype cache keys on the placements epoch, which
        # deliberately excludes the open mesh's mutation serial. Route this
        # live mesh through the consolidated renderer (revalidated on each
        # scene version) instead. Other meshes retain original eligibility.
        if (getattr(scene, 'edit_group', None) is not None
                and getattr(group, 'mesh', None) is scene.mesh):
            return False
        return original(self, group)

    @wraps(original_instance)
    def instance_chunk(self, group):
        scene = self.scene
        active = (getattr(scene, 'edit_group', None) is not None
                  and getattr(group, 'mesh', None) is scene.mesh)
        if not active:
            return original_instance(self, group)
        # A pure translation updates base.rev while preserving base.uid.
        # Upstream's instance key omits rev, so it can retain the old arrays
        # even after opting out of static GPU instancing. Rebuild only this
        # active placement when its source mesh/version has changed.
        token = (id(scene.mesh), getattr(scene.mesh, '_mut_serial', None),
                 scene.version)
        cache = getattr(self, '_inst_chunks', None)
        cur = cache.get(id(group)) if cache is not None else None
        if cur is not None and cur.get('_lime_live_token') != token:
            cache.pop(id(group), None)
        result = original_instance(self, group)
        result['_lime_live_token'] = token
        return result

    viewport_class._instance_chunk = instance_chunk
    setattr(eligible, _PATCH_TAG, __version__)
    viewport_class._instanced_eligible = eligible
    return True


def setup(app):
    install_patch()
    # Read-only information entry, no floating panel or extra toolbar.
    def info():
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(
            app.window, PLUGIN_NAME,
            '試用版 v' + __version__ + '\n修正已載入。請直接使用原生推拉工具。\n'
            '目標版本：IngeTrazo 0.6.0\n移除套件後請完整關閉並重新啟動。')
    app.add_menu_action(PLUGIN_NAME + '｜版本資訊', info,
                        tip='查看巢狀群組推拉顯示修正版本')
