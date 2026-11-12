"""Bounded image caches. Live gestures use a small proxy, then full quality."""
from functools import lru_cache
from pathlib import Path
from PySide6.QtCore import Qt,QBuffer,QIODevice
from PySide6.QtGui import QImage,QImageReader
from core.texture import colorize_image,cache_image

def signature(path):
    p=Path(path);s=p.stat();return str(p),s.st_mtime_ns,s.st_size

@lru_cache(maxsize=48)
def readable(key):
    reader=QImageReader(key[0]);return reader.canRead() and reader.size().isValid()

@lru_cache(maxsize=8)
def source_bytes(key,preview):
    if not preview:return Path(key[0]).read_bytes()
    reader=QImageReader(key[0]);size=reader.size()
    if max(size.width(),size.height())>384:reader.setScaledSize(size.scaled(384,384,Qt.KeepAspectRatio))
    im=reader.read()
    if im.isNull():raise ValueError('無法讀取材質圖片。')
    if max(im.width(),im.height())>384:im=im.scaled(384,384,Qt.KeepAspectRatio,Qt.SmoothTransformation)
    buffer=QBuffer();buffer.open(QIODevice.WriteOnly);im.save(buffer,'PNG')
    return bytes(buffer.data())

@lru_cache(maxsize=48)
def tinted_path(key,rgb,mode,preview):
    data=source_bytes(key,preview)
    out=colorize_image(data,rgb,mode)
    return str(cache_image(out,Path(key[0]).name,'material_studio_preview' if preview else 'tinted'))

def tint_texture(tex,color,mode=0,preview=False):
    base=tex.get('base') or tex.get('path')
    if not base:return dict(tex)
    rgb=tuple(round(max(0,min(1,float(v)))*255) for v in color[:3])
    out=dict(tex);out.update(path=tinted_path(signature(base),rgb,int(mode),bool(preview)),
                             base=str(base),tint=list(color[:3]),tint_mode=int(mode))
    return out

@lru_cache(maxsize=16)
def preview_image(key,rgb,mode,tint):
    if tint:return QImage(tinted_path(key,rgb,mode,True))
    return QImage.fromData(source_bytes(key,True))

def image_for(recipe):
    if not recipe['path']:return QImage()
    rgb=tuple(round(max(0,min(1,float(v)))*255) for v in recipe['color'])
    return preview_image(signature(recipe['path']),rgb,recipe.get('tint_mode',0),bool(recipe['tint']))
