# SPDX-License-Identifier: GPL-3.0-or-later
"""Portable D5 light definitions, in metres, persisted with the host document."""
import math
import uuid
import copy

KEY = "ingetrazo_d5_livesync"
KINDS = {"point": "點光", "spot": "聚光", "strip": "燈條"}


def vector(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("燈光座標須包含三個數值")
    values = [float(x) for x in value]
    if not all(math.isfinite(x) for x in values):
        raise ValueError("燈光座標無效")
    return values


def clean_lights(raw):
    result, ids, groups = [], set(), {}
    for source in raw or []:
        light = copy.deepcopy(source)
        if light.get("kind") not in KINDS:
            raise ValueError("不支援的 D5 光源種類")
        light["id"] = uuid.UUID(str(light["id"])).hex
        if light["id"] in ids:
            raise ValueError("D5 燈光識別重複")
        ids.add(light["id"])
        light["pos"] = vector(light["pos"])
        light["end"] = vector(light.get("end", [light['pos'][0] + 1, *light['pos'][1:]]))
        light["direction"] = vector(light.get("direction", [0, 0, -1]))
        if math.dist(light["direction"], [0, 0, 0]) < 1e-8:
            raise ValueError("照射方向不能為零")
        if light["kind"] == "strip" and math.dist(light["pos"], light["end"]) < .001:
            raise ValueError("燈條長度須至少 1 mm")
        for key, default, lo, hi in (("brightness", 1000, 0, 1e7), ("kelvin", 4000, 1800, 10000),
                                     ("width", .05, .001, 10), ("angle", 60, 5, 175)):
            value = float(light.get(key, default))
            if not math.isfinite(value) or not lo <= value <= hi:
                raise ValueError("燈光參數超出範圍：" + key)
            light[key] = value
        light["on"] = bool(light.get("on", True))
        light["name"] = str(light.get("name") or KINDS[light["kind"]])[:128]
        if light.get('group_id'):
            light['group_id']=uuid.UUID(str(light['group_id'])).hex
            light['group_name']=str(light.get('group_name') or '燈組')[:128]
            signature=(light['kind'],light['group_name'])
            if groups.setdefault(light['group_id'],signature)!=signature:
                raise ValueError('同一燈組必須使用同一光源種類與組名')
        result.append(light)
    return result


def new_light(kind, pos, end=None):
    return clean_lights([{"id": uuid.uuid4().hex, "kind": kind, "pos": list(pos),
                         "end": list(end) if end is not None else [pos[0] + 1, pos[1], pos[2]],
                         "name": "D5 " + KINDS[kind], "on": True}])[0]
