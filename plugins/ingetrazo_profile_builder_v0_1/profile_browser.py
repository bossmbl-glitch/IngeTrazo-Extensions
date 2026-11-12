# SPDX-License-Identifier: GPL-3.0-or-later
"""Original picture-based profile browser for Quick Profile v1.5.

Only reads user-supplied JSON profiles and Quick Profile's own library.
No commercial profile artwork or executable code is included.
"""
import json
import os
from pathlib import Path

from PySide6.QtCore import Qt, QSize, Signal, QTimer, QRect, QPoint
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QFont, QFontMetrics
from .skp_profiles import load_skp_profile, ProfileImportError
from .profile_storage import read_owned

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLineEdit, QToolButton, QPushButton, QLabel, QFileDialog, QScrollArea,
    QWidget, QFrame, QMessageBox, QSizePolicy, QInputDialog, QMenu, QStyle, QAbstractButton)


def load_profile_json(path):
    """Read a single-polygon or name-to-polygon JSON file without modifying it."""
    if path.stat().st_size > 4_000_000:
        raise ValueError('單一 JSON 檔案不可大於 4 MB')
    content=json.loads(path.read_text(encoding='utf-8-sig'))
    def valid(value):
        return (isinstance(value,list) and 3 <= len(value) <= 2048
                and all(isinstance(v,(list,tuple)) and len(v)==2
                        and all(isinstance(x,(int,float)) and abs(x)<1e8 for x in v)
                        for v in value))
    if valid(content):
        return [(path.stem,content)]
    if isinstance(content,dict):
        if valid(content.get('points')):
            return [(str(content.get('name') or path.stem), content['points'])]
        result=[(str(k),v) for k,v in content.items() if valid(v)]
        return result[:1000]
    return []


def thumb_icon(points, size=116):
    pm=QPixmap(size,size)
    pm.fill(QColor('#ffffff'))
    if not points: return QIcon(pm)
    painter=QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    xs=[v[0] for v in points]; ys=[v[1] for v in points]
    w=max(max(xs)-min(xs),1e-5); h=max(max(ys)-min(ys),1e-5)
    scale=min((size-22)/w,(size-22)/h)
    x0=(size-(max(xs)-min(xs))*scale)/2
    y0=(size-(max(ys)-min(ys))*scale)/2
    path=QPainterPath()
    for i,(x,y) in enumerate(points):
        px=x0+(x-min(xs))*scale
        py=size-(y0+(y-min(ys))*scale)
        if i==0: path.moveTo(px,py)
        else: path.lineTo(px,py)
    path.closeSubpath()
    painter.setPen(QPen(QColor('#505963'),1.3))
    painter.setBrush(QColor('#fbfbfb'))
    painter.drawPath(path)
    painter.end()
    return QIcon(pm)


class ProfileCard(QAbstractButton):
    chosen=Signal(object)
    activated=Signal(object)
    context_requested=Signal(object, object)

    def __init__(self,item,parent=None):
        super().__init__(parent)
        self.item=item
        self.setIconSize(QSize(108,108))
        font = QFont(self.font())
        font.setPointSize(max(11, font.pointSize()))
        font.setWeight(QFont.DemiBold)
        self.setFont(font)
        self._update_card_size()
        self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
        self.setCheckable(True)
        self.setText(item['name'])
        self.setToolTip(item['name'] + ('\n' + item['hint'] if item.get('hint') and item['hint'] != item['name'] else ''))
        if item['type']=='folder':
            self.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
        elif item.get('points'):
            self.setIcon(thumb_icon(item['points']))
        else:
            self.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        self._sync_status()
        self.clicked.connect(lambda: self.chosen.emit(self.item))

    def _update_card_size(self):
        fm = QFontMetrics(self.font())
        self._title_height = 2 * fm.lineSpacing() + 20
        self._image_height = max(128, fm.lineSpacing() * 5)
        height = self._image_height + self._title_height + 8
        self.setMinimumSize(180, height)
        self.setMaximumWidth(220)
        self.setFixedHeight(height)
        self.updateGeometry()

    def sizeHint(self):
        return QSize(196, self.minimumHeight())

    def changeEvent(self, event):
        super().changeEvent(event)
        # FontChange = 97; use enum to support Windows DPI/font changes.
        from PySide6.QtCore import QEvent
        if event.type() in (QEvent.FontChange, QEvent.ApplicationFontChange):
            self._update_card_size()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bounds = self.rect().adjusted(2, 2, -2, -2)
        selected = self.isChecked()
        painter.setPen(QPen(QColor('#3989d5') if selected else QColor('#cbd5e1'), 2 if selected else 1))
        painter.setBrush(QColor('#e7f2ff') if selected else QColor('#ffffff'))
        painter.drawRoundedRect(bounds, 6, 6)
        icon_size = min(116, self._image_height-16, self.width()-24)
        icon_rect = QRect((self.width()-icon_size)//2, (self._image_height-icon_size)//2, icon_size, icon_size)
        self.icon().paint(painter, icon_rect, Qt.AlignCenter)
        painter.setPen(QPen(QColor('#d7dfe7'),1))
        painter.drawLine(4,self._image_height,self.width()-4,self._image_height)
        painter.setFont(self.font())
        painter.setPen(QColor('#9a3737') if self.item.get('error') else QColor('#202b36'))
        fm = QFontMetrics(self.font())
        maxw = max(1,self.width()-24)
        label = self.text()
        lines=[]
        while label and len(lines)<2:
            if fm.horizontalAdvance(label)<=maxw:
                lines.append(label);label='';break
            pos=1
            while pos<len(label) and fm.horizontalAdvance(label[:pos+1])<=maxw:
                pos+=1
            lines.append(label[:pos]);label=label[pos:]
        if label and lines:
            lines[-1]=fm.elidedText(lines[-1]+label,Qt.ElideRight,maxw)
        top=self._image_height+(self._title_height-len(lines)*fm.lineSpacing())//2
        for i,line in enumerate(lines):
            painter.drawText(QRect(12,top+i*fm.lineSpacing(),maxw,fm.lineSpacing()),Qt.AlignCenter,line)
        painter.end()

    def _sync_status(self):
        if self.item.get('source') != 'skp':
            return
        error = self.item.get('error')
        if error:
            self.setText(self.item['name'] + '  ⚠')
            self.setToolTip(self.item['name'] + '\n' + (self.item.get('hint') or str(error)))
            self.setStyleSheet('QToolButton { color: #9a3737; }')
            self.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning))
        elif self.item.get('points'):
            self.setText(self.item['name'])
            self.setToolTip(self.item['name'] + ('\n' + self.item['hint'] if self.item.get('hint') and self.item['hint'] != self.item['name'] else '') + '\n可使用此 SKP 剖面')
            self.setStyleSheet('')
            self.setIcon(thumb_icon(self.item['points']))
        else:
            self.setText(self.item['name'] + '  ⋯')
            self.setToolTip(self.item['name'] + ('\n' + self.item['hint'] if self.item.get('hint') and self.item['hint'] != self.item['name'] else '') + '\n點選並按「選取」時解析剖面')
            self.setStyleSheet('')
            self.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))

    def contextMenuEvent(self,event):
        self.context_requested.emit(self.item, self.mapToGlobal(event.pos()))
        event.accept()

    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.LeftButton:
            self.activated.emit(self.item)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ProfileBrowser(QDialog):
    """Parented tool-window, with recent profiles, folders and thumbnail grid."""
    def __init__(self, owner):
        super().__init__(owner, Qt.Tool | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.owner=owner
        self.setWindowTitle('選擇輪廓圖形')
        self.setAttribute(Qt.WA_QuitOnClose,False)
        self.resize(940,630)
        self.setMinimumSize(620,460)
        self.state_file=owner.file.parent/'browser_state.json'
        self.library=owner.file.parent/'library'
        self.library.mkdir(parents=True,exist_ok=True)
        self.state=self._load_state()
        saved=Path(self.state.get('directory',str(self.library)))
        self.directory=saved if saved.is_dir() else self.library
        self.selected=None
        self.cards=[]
        self._cache={}
        self._skp_results={}
        self._skp_queue=[]
        self._scan_error=None
        self._skp_timer=QTimer(self)
        self._skp_timer.setSingleShot(True)
        self._skp_timer.timeout.connect(self._load_next_skp)
        self._layout_timer=QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self.render_items)
        lay=QVBoxLayout(self)
        lay.setContentsMargins(9,9,9,9)
        lay.setSpacing(8)
        bar=QHBoxLayout()
        bar.setSpacing(5)
        for text,tip,handler in [('↑','上一層資料夾',self.go_up),('📂','選擇資料夾',self.choose_folder),
                                 ('⌂','返回剖面資料庫首頁',self.go_home),('↻','重新整理',self.reload_items)]:
            b=QToolButton(self); b.setText(text); b.setToolTip(tip)
            b.setFixedSize(31,29); b.clicked.connect(handler); bar.addWidget(b)
        self.path_edit=QLineEdit(str(self.directory))
        self.path_edit.setToolTip('可輸入資料夾路徑後按 Enter')
        self.path_edit.returnPressed.connect(self.go_path)
        self.path_edit.editingFinished.connect(self._path_edit_finished)
        bar.insertWidget(1,self.path_edit,1)
        goto=QToolButton(self)
        goto.setText('前往')
        goto.setToolTip('讀取上方輸入的資料夾路徑')
        goto.setFixedSize(48,29)
        goto.clicked.connect(self.go_path)
        bar.insertWidget(2,goto)
        sync=QToolButton(self)
        sync.setText('同步舊剖面')
        sync.setToolTip('將舊版儲存在外掛內部的自訂線板複製到目前資料夾；不刪除來源')
        sync.clicked.connect(owner.sync_legacy_profiles)
        bar.insertWidget(3,sync)
        show_hidden=QToolButton(self)
        show_hidden.setText('顯示隱藏')
        show_hidden.setToolTip('切換顯示已隱藏的內建剖面，可再從右鍵選單復原')
        show_hidden.setCheckable(True)
        show_hidden.setChecked(bool(self.state.get('show_hidden_builtins',False)))
        show_hidden.clicked.connect(self._toggle_hidden)
        bar.insertWidget(4,show_hidden)
        self.search=QLineEdit()
        self.search.setPlaceholderText('搜尋該目錄')
        self.search.setFixedWidth(190)
        self.search.textChanged.connect(self.render_items)
        bar.addWidget(self.search)
        lay.addLayout(bar)
        self.scan_status=QLabel('')
        self.scan_status.setWordWrap(True)
        self.scan_status.setStyleSheet('color: #555; padding: 2px 3px;')
        lay.addWidget(self.scan_status)
        self.scroll=QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        lay.addWidget(self.scroll,1)
        buttons=QHBoxLayout()
        buttons.addStretch(1)
        select=QPushButton('選取')
        select.setDefault(True)
        select.clicked.connect(self.apply_selection)
        self.setFocusPolicy(Qt.StrongFocus)
        cancel=QPushButton('取消'); cancel.clicked.connect(self.close)
        buttons.addWidget(select); buttons.addWidget(cancel)
        lay.addLayout(buttons)
        # Do not scan or parse user files during the constructor. A large SKP
        # directory can otherwise make the browser appear not to open.
        self._initial_scan_pending = True
        self.scan_status.setText('資料庫已開啟，正在準備檔案清單…')

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self, '_layout_timer') and hasattr(self, 'scroll'):
            self._layout_timer.start(130)

    def showEvent(self, event):
        super().showEvent(event)
        if self._initial_scan_pending:
            self._initial_scan_pending = False
            QTimer.singleShot(120, self._safe_reload)

    def _safe_reload(self):
        try:
            self.reload_items()
        except Exception as exc:
            self._scan_error = f'無法讀取剖面清單：{type(exc).__name__}: {exc}'
            self.scan_status.setText(self._scan_error)
            QMessageBox.warning(self, '剖面資料庫錯誤', self._scan_error)


    def _load_state(self):
        try:
            data=json.loads(self.state_file.read_text(encoding='utf-8'))
            return data if isinstance(data,dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _save_state(self):
        self.state['directory']=str(self.directory)
        self.state_file.parent.mkdir(parents=True,exist_ok=True)
        self.state_file.write_text(json.dumps(self.state,ensure_ascii=False,indent=2),encoding='utf-8')

    def _change_dir(self,path):
        path=Path(path).expanduser()
        if not path.is_dir():
            QMessageBox.warning(self,'找不到資料夾','請輸入有效的資料夾路徑。')
            return
        self.directory=path.resolve()
        self._save_state()
        self.reload_items()

    def _path_edit_finished(self):
        path=self.path_edit.text().strip().strip('"')
        if path and path != str(self.directory):
            self._change_dir(path)

    def go_path(self): self._change_dir(self.path_edit.text().strip().strip('"'))
    def go_up(self): self._change_dir(self.directory.parent)
    def go_home(self): self._change_dir(self.library)
    def choose_folder(self):
        folder=QFileDialog.getExistingDirectory(self,'選擇剖面資料夾',str(self.directory))
        if folder: self._change_dir(folder)

    def reload_items(self):
        """Always list source files first, then parse geometry separately.

        Invalid SKPs remain visible with a diagnostic instead of silently
        disappearing from the browser. No source file is ever modified.
        """
        self._skp_timer.stop()
        self.scan_status.setText('正在讀取檔案名稱…')
        self.path_edit.setText(str(self.directory))
        self._cache={}
        self._skp_queue=[]
        self._scan_error=None
        skp_count=0
        json_count=0
        try:
            entries=sorted(self.directory.iterdir(),key=lambda f:f.name.casefold())
        except OSError as exc:
            entries=[]
            self._scan_error=f'無法掃描資料夾：{exc}'
        for file in entries:
            if not file.is_file():
                continue
            if file.suffix.casefold()=='.json':
                json_count+=1
                try:
                    for name,points in load_profile_json(file):
                        key=('external',str(file),name)
                        self._cache[key]={'type':'profile','key':key,'name':name,'points':points,
                                          'hint':str(file),'source':'external'}
                except (OSError,ValueError,TypeError,UnicodeError) as exc:
                    key=('json-error',str(file))
                    self._cache[key]={'type':'profile','key':key,'name':file.stem,
                            'points':None,'hint':f'{file}\nJSON 解析失敗：{exc}',
                            'source':'invalid-json','error':str(exc)}
            elif file.suffix.casefold()=='.skp':
                skp_count+=1
                key=('skp',str(file))
                record={'type':'profile','key':key,'name':file.stem,
                        'points':None,'hint':str(file)+'\nSKP 剖面：待解析',
                        'source':'skp','path':file}
                self._cache[key]=record
                self._skp_queue.append(record)
        self._scan_counts=(skp_count,json_count)
        self._update_scan_status()
        self.render_items()
        # SKP parsing is intentionally ON DEMAND. Automatically parsing every
        # file blocks Qt's GUI thread for large/old SketchUp directories.

    def _update_scan_status(self):
        if self._scan_error:
            self.scan_status.setText(self._scan_error)
            return
        skp,json_files=self._scan_counts
        waiting=sum(item.get('source')=='skp' and item.get('points') is None
                    and not item.get('error') for item in self._cache.values())
        failed=sum(item.get('source')=='skp' and bool(item.get('error'))
                   for item in self._cache.values())
        ready=skp-waiting-failed
        self.scan_status.setText(
            f'目前目錄：找到 {skp} 個 SKP、{json_files} 個 JSON ｜'
            f' SKP 可用 {ready}、待解析 {waiting}、無法解析 {failed}'
            + (' ｜ 點選 SKP 再按「選取」以解析' if skp else ' ｜ 提示：請按「前往」套用新路徑'))

    def _load_skp_item(self,item):
        if item.get('points') is not None or item.get('error'): return
        file=item['path']
        try:
            stat=file.stat()
            sig=(str(file),stat.st_size,stat.st_mtime_ns)
            if sig not in self._skp_results:
                try: self._skp_results[sig]=(load_skp_profile(file),None)
                except (ProfileImportError, OSError, ValueError) as exc:
                    self._skp_results[sig]=(None,str(exc))
            item['points'],item['error']=self._skp_results[sig]
            item['hint']=str(file) if item['points'] else str(file)+'\n'+item['error']
        except OSError as exc:
            item['error']=str(exc)

    def _load_next_skp(self):
        # Kept for backwards-compatible references; automatic parsing is off.
        return

    def _profile_items(self):
        current=[]
        for name,points in sorted(self.owner.library_profiles().items(),key=lambda v:v[0].casefold()):
            key=('saved',name)
            current.append({'type':'profile','key':key,'name':name,'points':points,'source':'saved'})
        current.extend(self._cache.values())
        # A saved legacy copy and its new on-disk .profile.json are the same
        # logical section; display the disk-backed card, not two copies.
        disk_names={v['name'] for v in self._cache.values()
                    if v.get('source')=='external' and
                    str(v['key'][1]).lower().endswith('.profile.json') and
                    read_owned(v['key'][1]) is not None}
        current=[v for v in current if not (v.get('source')=='saved' and
                                             v['name'] in disk_names and
                                             v['name'] not in self.owner.protected_profile_names())]
        return current

    def render_items(self):
        phrase=self.search.text().strip().casefold()
        root=QWidget()
        layout=QVBoxLayout(root)
        layout.setContentsMargins(8,8,8,8)
        layout.setSpacing(10)
        self.cards=[]
        previous_key=tuple(self.selected['key']) if self.selected else None
        self.selected=None
        data=self._profile_items()
        by_key={tuple(p['key']):p for p in data}
        recent=[]
        for raw in self.state.get('recent',[])[:12]:
            if not isinstance(raw, (list, tuple)) or not all(isinstance(k,str) for k in raw):
                continue
            item=by_key.get(tuple(raw))
            if item and (not phrase or phrase in item['name'].casefold()): recent.append(item)
        if not self.state.get('show_hidden_builtins',False):
            hidden=set(self.state.get('hidden_builtins',[]))
            recent=[item for item in recent if item['name'] not in hidden]
        if recent: self._section(layout,'最近使用',recent)
        # Folder navigation is exclusively in the upper toolbar.
        # The thumbnail area contains profiles only.
        profiles=[item for item in data if not phrase or phrase in item['name'].casefold()]
        hidden=set(self.state.get('hidden_builtins',[]))
        if not self.state.get('show_hidden_builtins',False):
            profiles=[item for item in profiles if not (item['name'] in hidden and item.get('source')=='saved')]
        self._section(layout,f'剖面圖庫（{len(profiles)} 項）',profiles)
        layout.addStretch(1)
        self.scroll.takeWidget()
        self.scroll.setWidget(root)
        if previous_key:
            matches=[c for c in self.cards if tuple(c.item['key'])==previous_key]
            if matches:
                self._select(matches[0].item)

    def _section(self,outer,title,items):
        name=QLabel(title)
        name.setStyleSheet('font-weight: 600; padding-top: 5px;')
        outer.addWidget(name)
        line=QFrame();line.setFrameShape(QFrame.HLine);line.setFrameShadow(QFrame.Plain)
        outer.addWidget(line)
        if not items:
            outer.addWidget(QLabel('沒有符合條件的剖面'))
            return
        grid=QGridLayout();grid.setHorizontalSpacing(12);grid.setVerticalSpacing(16)
        available=max(self.scroll.viewport().width()-22,390)
        cols=max(2,min(7,available//200))
        for i,item in enumerate(items):
            card=ProfileCard(item, self)
            card.chosen.connect(self._select)
            card.activated.connect(self._activate)
            card.context_requested.connect(self._context_menu)
            grid.addWidget(card,i//cols,i%cols)
            self.cards.append(card)
        outer.addLayout(grid)
        outer.addSpacing(14)

    def _context_menu(self,item,global_pos):
        self._select(item)
        menu=QMenu(self)
        source=item.get('source')
        if item.get('type')=='folder':
            action=menu.addAction('開啟資料夾')
            action.triggered.connect(lambda: self._change_dir(item['path']))
        elif source=='skp':
            action=menu.addAction('移除最近使用紀錄（不刪除 SKP）')
            action.triggered.connect(lambda: self._forget_recent(item))
        else:
            rename=menu.addAction('重新命名線板')
            rename.setEnabled(item['name'] not in self.owner.protected_profile_names() and source!='invalid-json')
            rename.triggered.connect(lambda: self._rename_item(item))
            is_builtin=item['name'] in self.owner.protected_profile_names()
            hidden=set(self.state.get('hidden_builtins',[]))
            if is_builtin and item['name'] in hidden:
                unhide=menu.addAction('取消隱藏內建線板')
                unhide.triggered.connect(lambda: self._unhide_item(item))
            else:
                delete=menu.addAction('隱藏內建線板' if is_builtin else '刪除線板')
                delete.triggered.connect(lambda: self._delete_item(item))
        if self.state.get('hidden_builtins'):
            menu.addSeparator()
            show=menu.addAction('顯示隱藏的內建線板')
            show.setCheckable(True)
            show.setChecked(self.state.get('show_hidden_builtins',False))
            show.triggered.connect(self._toggle_hidden)
        menu.exec(global_pos)

    def _toggle_hidden(self):
        self.state['show_hidden_builtins']=not self.state.get('show_hidden_builtins',False)
        for button in self.findChildren(QToolButton):
            if button.text()=='顯示隱藏':
                button.setChecked(self.state['show_hidden_builtins'])
        self._save_state()
        self.render_items()

    def _unhide_item(self,item):
        hidden=set(self.state.get('hidden_builtins',[]))
        hidden.discard(item['name'])
        self.state['hidden_builtins']=sorted(hidden)
        self._save_state()
        self.render_items()

    def _forget_recent(self,item):
        key=list(item['key'])
        self.state['recent']=[r for r in self.state.get('recent',[]) if r!=key]
        self._save_state()
        self.render_items()

    def _rename_item(self,item):
        name,ok=QInputDialog.getText(self,'重新命名線板','新的線板名稱：',text=item['name'])
        if not ok or not name.strip() or name.strip()==item['name']: return
        try:
            new_key=self.owner.rename_browser_profile(item,name.strip())
            old_key=list(item['key'])
            self.state['recent']=[list(new_key) if r==old_key else r for r in self.state.get('recent',[])]
            # Existing legacy saved-card references become disk-backed after rename.
            self.state['recent']=list(dict.fromkeys(tuple(r) for r in self.state['recent']))
            self.state['recent']=[list(r) for r in self.state['recent']]
            self._save_state()
            self.reload_items()
        except Exception as exc:
            QMessageBox.warning(self,'重新命名失敗',str(exc))

    def _delete_item(self,item):
        name=item['name']
        is_builtin=name in self.owner.protected_profile_names()
        message=(f'從資料庫隱藏內建線板「{name}」？\n不會刪除內建資料。' if is_builtin else
                 f'移除線板「{name}」？\n本外掛建立的檔案會移到備份資料夾；來源 SKP 不會刪除。')
        if QMessageBox.question(self,'確認操作',message) != QMessageBox.Yes:return
        try:
            result=self.owner.delete_browser_profile(item)
            self.state['recent']=[r for r in self.state.get('recent',[]) if r!=list(item['key'])]
            self._save_state()
            QMessageBox.information(self,'完成',result)
        except Exception as exc:
            QMessageBox.warning(self,'刪除失敗',str(exc))

    def keyPressEvent(self,event):
        if event.key()==Qt.Key_Delete and self.selected and self.selected.get('type')!='folder':
            self._delete_item(self.selected)
            event.accept()
            return
        super().keyPressEvent(event)

    def _select(self,item):
        self.selected=item
        for card in self.cards:
            card.setChecked(card.item is item)

    def _activate(self,item):
        self._select(item)
        self.apply_selection()

    def apply_selection(self):
        item=self.selected
        if not item: return
        if item['type']=='folder':
            self._change_dir(item['path'])
            return
        if item['source']=='skp':
            self._load_skp_item(item)
            for card in self.cards:
                if card.item is item: card._sync_status()
            self._update_scan_status()
            if not item.get('points'):
                QMessageBox.warning(self,'SKP 檔案診斷',
                    f'檔案：{item["path"].name}\n\n'
                    + (item.get('error') or '尚無法轉換成封閉的 2D 剖面。')
                    + '\n\n原始 SKP 未被修改。')
                return
        elif item['source']=='invalid-json':
            QMessageBox.warning(self,'JSON 檔案診斷',item.get('hint','JSON 無法讀取'))
            return
        points=item['points'] if item['source'] in ('external','skp') else None
        if not self.owner.apply_browser_profile(item['name'],points, source_key=self.owner.browser_identity(item)):
            return
        key=list(item['key'])
        recent=[r for r in self.state.get('recent',[]) if r!=key]
        self.state['recent']=[key]+recent[:11]
        self._save_state()
        self.hide()
