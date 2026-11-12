"""Plugin-local legacy SKP fixes. GPL-3.0-or-later; reader is MIT."""
import math
from pathlib import Path
import threading
import time

from .vendor import legacy_reader as reader

_LOCK = threading.RLock()
_base_layer = reader._READERS["CLayer"]
_BaseArchive = reader._Archive


def _read_line_style(ar, r):
    schema = ar.class_schema.get("CCustomLineStyle")
    if schema not in (None, 3):
        raise reader.LegacyParseError(f"Unsupported CCustomLineStyle schema {schema}")
    pre = reader._preamble(ar, r)
    name = r.utf16()
    pattern = r.utf16()
    scales = r.f64s(3)
    if not all(math.isfinite(value) for value in scales):
        raise reader.LegacyParseError("Non-finite custom line-style scale")
    # This is style metadata, not geometry. Keep its archive slots so later
    # references remain aligned; IngeTrazo renders imported edges as usual.
    return {"k": "line_style", "name": name, "pattern": pattern,
            "scales": scales, "attrs": pre["attrs"]}


def _read_layer(ar, r):
    value = _base_layer(ar, r)
    tag = r.peek_u16()
    inline = False
    if tag == 0xffff:
        length = int.from_bytes(r.peek(6)[4:6], "little")
        inline = length == 16 and r.peek(22)[6:] == b"CCustomLineStyle"
    elif tag == 0x7fff:
        slot = int.from_bytes(r.peek(6)[2:], "little") & 0x7fffffff
        entry = ar.slots.get(slot)
        inline = entry is not None and entry[1] == "CCustomLineStyle"
    elif tag:
        slot = tag & 0x7fff if tag & 0x8000 else tag
        entry = ar.slots.get(slot)
        inline = entry is not None and entry[1] == "CCustomLineStyle"
    if inline:
        value["line_style"] = ar.read_object(r, expect="CCustomLineStyle")[2]
    return value


reader._READERS["CCustomLineStyle"] = _read_line_style
reader._READERS["CLayer"] = _read_layer


def _matrix(values):
    """OpenSKP's 13th value is scale = 1/w, NOT the 4x4 matrix's w.

    Fold that multiplier into rotation and translation before composing.
    Source/repro: https://github.com/iamahsanmehmood/openskp/issues/444
    """
    from PySide6.QtGui import QMatrix4x4
    if len(values) not in (12, 13) or not all(math.isfinite(float(v)) for v in values):
        raise ValueError("SKP 元件轉換矩陣無效；無法可靠建立模型位置。")
    s = float(values[12]) if len(values) == 13 else 1.0
    m = [float(v) * s for v in values[:12]]
    return QMatrix4x4(m[0], m[1], m[2], m[9] * .0254,
                      m[3], m[4], m[5], m[10] * .0254,
                      m[6], m[7], m[8], m[11] * .0254,
                      0.0, 0.0, 0.0, 1.0)


def parse(path, progress=None):
    from formats import skp_openskp
    with Path(path).open("rb") as stream:
        header = stream.read(512)
    legacy = reader.is_legacy(header)
    import openskp.legacy as installed
    last_report = [0.0]

    class TrackedArchive(_BaseArchive):
        def _new_obj(self, cursor, name):
            if self.next_slot % 4096 == 0 and progress:
                now = time.monotonic()
                if now - last_report[0] >= .4:
                    last_report[0] = now
                    progress(.1 + .45 * cursor.pos / max(1, len(self.data)),
                             f"解析 SKP 物件：{len(self.slots):,}…")
            return super()._new_obj(cursor, name)

    with _LOCK:
        old_parse = installed.full_parse_legacy
        old_matrix = skp_openskp._matrix
        skp_openskp._matrix = _matrix
        if legacy:
            reader._Archive = TrackedArchive
            installed.full_parse_legacy = reader.full_parse_legacy
        try:
            return skp_openskp.parse(path, progress=progress)
        finally:
            installed.full_parse_legacy = old_parse
            skp_openskp._matrix = old_matrix
            reader._Archive = _BaseArchive
