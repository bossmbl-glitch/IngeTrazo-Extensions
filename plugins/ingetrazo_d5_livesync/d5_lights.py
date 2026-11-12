# SPDX-License-Identifier: GPL-3.0-or-later
"""Send only this bridge's lights through the installed official D5 SDK."""
import math
import uuid
from pathlib import Path
from mathutils import Matrix, Vector
from light_data import clean_lights


def serialize(light, model_path):
    from d5_export.ops.conversion import get_light_matrix
    from ingetrazo_io.finish import kelvin_to_rgb
    p = Vector(light["pos"])
    direction = Vector(light["direction"]).normalized()
    rotation = direction.to_track_quat("-Z", "Y").to_matrix().to_4x4()
    kind = light["kind"]
    if kind == "strip":
        end = Vector(light["end"])
        axis = (end-p).normalized()
        normal = -direction
        normal -= axis * normal.dot(axis)
        if normal.length < 1e-6:
            normal = Vector((0, 1, 0)) if abs(axis.y) < .9 else Vector((1, 0, 0))
            normal -= axis * normal.dot(axis)
        normal.normalize()
        rotation = Matrix((axis, normal.cross(axis), normal)).transposed().to_4x4()
        position = (p+end)*.5
        size_x, size_y = light["width"]*1000, (end-p).length*1000
    else:
        position = p
        size_x = size_y = math.radians(light["angle"]) if kind == "spot" else 80.
    matrix = get_light_matrix(Matrix.Translation(position) @ rotation, as_string=False)
    group_id=light.get('group_id')
    properties={"id": light["id"], "path": Path(model_path).name + "/" + (group_id or "D5") + "/" + light["id"],
            "type": {"point": 0, "spot": 1, "strip": 2}[kind], "light_name": light["name"],
            "transform": [float(x) for row in matrix for x in row],
            "brightness": float(light["brightness"]), "size_x": float(size_x), "size_y": float(size_y),
            "light_color": [round(x*255) for x in kelvin_to_rgb(light["kelvin"])],
            "visible_in_reflection": True, "light_template": " "}
    if group_id:properties['group_id']=group_id
    return properties


class LightSender:
    def __init__(self, sdk, converter):
        self.sdk, self.converter = sdk, converter
        self.sent = {}
        self.deleted = set()
        self.groups = {}

    def send(self, definitions, model_path, known_ids=None):
        lights = clean_lights(definitions)
        active = {x["id"]: serialize(x, model_path) for x in lights if x["on"]}
        groups={x['group_id']:x['group_name'] for x in lights if x['on'] and x.get('group_id')}
        # Remove only IDs previously sent by this bridge. Never enumerate or
        # clear the user's D5 lights. D5ClearLights clears the SDK send buffer.
        owned = {uuid.UUID(str(x)).hex for x in (known_ids or [])} | {x['id'] for x in lights}
        removed = (set(self.sent) | owned) - set(active) - self.deleted
        changed = {key: value for key, value in active.items() if self.sent.get(key) != value}
        if not removed and not changed and groups==self.groups:
            return {"light_count": len(active), "lights_changed": 0, "lights_removed": 0}
        session = self.converter.session
        if session is None:
            raise RuntimeError("D5 尚未建立燈光連線")
        code = self.sdk.D5ProcessedSyncConnect(session)
        if int(code) != 0:
            raise RuntimeError("D5 燈光連線失敗：" + str(code))
        try:
            # Official SDK emits these path element names as the JSON groups
            # array. Only delete this sender's own previously registered IDs.
            for key in self.groups.keys()-groups.keys():
                self.sdk.D5DeletePathElementName(session,key)
            for key,name in groups.items():
                self.sdk.D5SetPathElementName(session,key,name)
            for key in removed:
                self.sdk.D5DeleteLight(session, key)
            # D5 interprets each light payload as the complete set for this
            # model path. A delta-only batch would remove unchanged lights.
            for key, properties in active.items():
                self.sdk.D5AddLight(session, key)
                for name, value in properties.items():
                    if isinstance(value, list):
                        self.sdk.D5SetEntityFloatArrayProperty(session, self.sdk.ET_LIGHT, key, name, value)
                    elif isinstance(value, float):
                        self.sdk.D5SetEntityFloatProperty(session, self.sdk.ET_LIGHT, key, name, value)
                    elif isinstance(value, int):
                        self.sdk.D5SetEntityIntProperty(session, self.sdk.ET_LIGHT, key, name, value)
                    elif isinstance(value, str):
                        self.sdk.D5SetEntityStringProperty(session, self.sdk.ET_LIGHT, key, name, value)
            result = self.sdk.D5SendLights(session)
            # Some official SDK builds return void, others a boolean/code.
            if result is False:
                raise RuntimeError("D5 拒絕燈光傳送")
        finally:
            self.sdk.D5ClearLights(session)
            self.sdk.DisConnect(session)
        self.sent = active
        self.groups = groups
        self.deleted = (self.deleted | removed) - set(active)
        receipt = {"light_count": len(active), "lights_changed": len(changed), "lights_removed": len(removed),"light_groups":len(groups)}
        print("D5_LIGHTS_SENT", receipt, flush=True)
        return receipt
