"""One-time B assignment; preserve later user shortcut edits."""
from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction, QKeySequence

def install_bucket_key(window, action):
    from views import shortcuts as sh
    st=QSettings()
    if st.value('it_material_studio/bucket_key_installed',False,type=bool):return
    sh.remember_defaults(window)
    key=QKeySequence('B')
    bottom=window._view_actions['bottom']
    for other in window.findChildren(QAction):
        if other is action or key not in other.shortcuts():continue
        keys=[s for s in other.shortcuts() if s!=key]
        if other is bottom:
            fallback=QKeySequence('Ctrl+7')
            if not any(fallback in a.shortcuts() for a in window.findChildren(QAction) if a is not other):
                keys.append(fallback)
        other.setShortcuts(keys);sh.save_shortcut(other,keys)
    action.setShortcut(key);sh.save_shortcut(action,[key])
    st.setValue('it_material_studio/bucket_key_installed',True);st.sync()
