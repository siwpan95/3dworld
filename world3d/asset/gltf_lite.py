# -*- coding: utf-8 -*-
"""glTF-lite：glTF 2.0 数据模型与扩展矩阵（纯数据 / 无 GL）。

按 3dworld engine 层「纯数学 / 无 GL / headless 可编译校验」定位实现。
遵循 **Khronos glTF 2.0 规范**（规范，按规范写）自研：

* 解析 glTF JSON 为数据模型：scene / node（TRS 或 matrix 的局部与_world 变换）/
  mesh（primitives：attributes / indices / material / mode）/ material
  （pbrMetallicRoughness 与相关 texture、alpha、extensions）/ accessor /
  bufferView / buffer / texture / image；
* 节点层级前向运动学：由 TRS 或 4x4 matrix 求每个节点的世界矩阵；
* **扩展矩阵**：识别 KHR_materials_* / EXT_meshopt / KHR_texture_basisu /
  KHR_draco_mesh_compression 等已知扩展（仅登记与解析，不取第三方 loader 代码）；
* 轻量 schema 校验：必填字段与 asset.version==2.0，返回错误列表。

**按规范实现扩展，不取第三方 loader 代码**；meshopt 解码子集已在 test_meshopt 覆盖。
"""
from __future__ import annotations

import numpy as np

# glTF 2.0 组件类型 → 每组件字节数
COMPONENT_BYTES = {
    5120: 1,   # BYTE
    5121: 1,   # UNSIGNED_BYTE
    5122: 2,   # SHORT
    5123: 2,   # UNSIGNED_SHORT
    5125: 4,   # UNSIGNED_INT
    5126: 4,   # FLOAT
}
# 访问器 type → 每元素分量数
TYPE_COMPONENTS = {
    "SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4,
    "MAT2": 4, "MAT3": 9, "MAT4": 16,
}

# 已知扩展（仅登记/解析，不实现其二进制解码）
KNOWN_EXTENSIONS = {
    "KHR_materials_unlit",
    "KHR_materials_pbrSpecularGlossiness",
    "KHR_materials_transmission",
    "KHR_materials_emissive_strength",
    "KHR_materials_clearcoat",
    "KHR_materials_sheen",
    "KHR_materials_volume",
    "KHR_materials_ior",
    "KHR_materials_specular",
    "KHR_draco_mesh_compression",
    "KHR_texture_basisu",
    "KHR_mesh_quantization",
    "EXT_meshopt_compression",
    "EXT_meshopt_simulation",
    "KHR_lights_punctual",
}


def _identity4():
    return np.eye(4)


def _trs_matrix(translation, rotation, scale):
    t = np.asarray(translation, dtype=float) if translation is not None else np.zeros(3)
    s = np.asarray(scale, dtype=float) if scale is not None else np.ones(3)
    if rotation is not None:
        w, x, y, z = rotation
        m = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ], dtype=float)
    else:
        m = np.eye(3)
    mat = np.eye(4)
    mat[:3, :3] = m * s[None, :]
    mat[:3, 3] = t
    return mat


class Node:
    def __init__(self, raw, index):
        self.index = index
        self.name = raw.get("name", f"node_{index}")
        self.children = list(raw.get("children", []))
        self.raw = raw
        if "matrix" in raw:
            self.local = np.asarray(raw["matrix"], dtype=float).reshape(4, 4)
        else:
            self.local = _trs_matrix(
                raw.get("translation"), raw.get("rotation"), raw.get("scale"))
        self.mesh = raw.get("mesh")
        self.camera = raw.get("camera")
        self.extensions = raw.get("extensions", {})

    def world_matrix(self, resolve):
        m = self.local
        p = self.raw.get("parent")
        if p is not None:
            m = resolve(p) @ m
        return m


class Accessor:
    def __init__(self, raw, index):
        self.index = index
        self.buffer_view = raw.get("bufferView")
        self.byte_offset = raw.get("byteOffset", 0)
        self.component_type = raw["componentType"]
        self.count = raw["count"]
        self.type = raw["type"]
        self.min = raw.get("min")
        self.max = raw.get("max")
        self.normalized = raw.get("normalized", False)

    @property
    def num_components(self):
        return TYPE_COMPONENTS[self.type]

    @property
    def element_bytes(self):
        return self.num_components * COMPONENT_BYTES[self.component_type]


class BufferView:
    def __init__(self, raw, index):
        self.index = index
        self.buffer = raw["buffer"]
        self.byte_offset = raw.get("byteOffset", 0)
        self.byte_length = raw["byteLength"]
        self.byte_stride = raw.get("byteStride")
        self.target = raw.get("target")


class Buffer:
    def __init__(self, raw, index):
        self.index = index
        self.uri = raw.get("uri")
        self.byte_length = raw["byteLength"]
        self.name = raw.get("name")


class Primitive:
    def __init__(self, raw):
        self.attributes = dict(raw.get("attributes", {}))
        self.indices = raw.get("indices")
        self.material = raw.get("material")
        self.mode = raw.get("mode", 4)  # 默认 TRIANGLES
        self.extensions = raw.get("extensions", {})


class Mesh:
    def __init__(self, raw, index):
        self.index = index
        self.name = raw.get("name", f"mesh_{index}")
        self.primitives = [Primitive(p) for p in raw.get("primitives", [])]
        self.extensions = raw.get("extensions", {})


class Material:
    def __init__(self, raw, index):
        self.index = index
        self.name = raw.get("name", f"material_{index}")
        self.pbr = raw.get("pbrMetallicRoughness", {})
        self.normal_texture = raw.get("normalTexture")
        self.occlusion_texture = raw.get("occlusionTexture")
        self.emissive_texture = raw.get("emissiveTexture")
        self.emissive_factor = raw.get("emissiveFactor")
        self.alpha_mode = raw.get("alphaMode", "OPAQUE")
        self.alpha_cutoff = raw.get("alphaCutoff", 0.5)
        self.double_sided = raw.get("doubleSided", False)
        self.extensions = raw.get("extensions", {})


class GltfModel:
    def __init__(self, doc):
        self.doc = doc
        self.asset = doc.get("asset", {})
        self.scene = doc.get("scene", 0 if doc.get("scenes") else None)
        self.scenes = doc.get("scenes", [])
        self._nodes = [Node(n, i) for i, n in enumerate(doc.get("nodes", []))]
        self._meshes = [Mesh(m, i) for i, m in enumerate(doc.get("meshes", []))]
        self._materials = [Material(m, i) for i, m in enumerate(doc.get("materials", []))]
        self._accessors = [Accessor(a, i) for i, a in enumerate(doc.get("accessors", []))]
        self._buffer_views = [BufferView(b, i) for i, b in enumerate(doc.get("bufferViews", []))]
        self._buffers = [Buffer(b, i) for i, b in enumerate(doc.get("buffers", []))]
        self.textures = doc.get("textures", [])
        self.images = doc.get("images", [])
        # 回填父子关系
        for node in self._nodes:
            for c in node.children:
                if 0 <= c < len(self._nodes):
                    self._nodes[c].raw["parent"] = node.index
        self._world_cache = {}

    # ---- 访问器 ----
    @property
    def nodes(self):
        return self._nodes

    @property
    def meshes(self):
        return self._meshes

    @property
    def materials(self):
        return self._materials

    @property
    def accessors(self):
        return self._accessors

    @property
    def buffer_views(self):
        return self._buffer_views

    @property
    def buffers(self):
        return self._buffers

    def node(self, i):
        return self._nodes[i]

    def world_matrix(self, i):
        """节点 i 的世界矩阵（含缓存）。"""
        if i in self._world_cache:
            return self._world_cache[i]
        node = self._nodes[i]
        m = node.local
        parent = node.raw.get("parent")
        if parent is not None:
            m = self.world_matrix(parent) @ m
        self._world_cache[i] = m
        return m

    def all_world_matrices(self):
        return {i: self.world_matrix(i) for i in range(len(self._nodes))}

    def detected_extensions(self):
        """扫描全部已知扩展的使用位置，返回 {扩展名: [位置...]}。"""
        found = {}
        locations = [
            ("asset.extensionsUsed", self.doc.get("extensionsUsed", [])),
            ("asset.extensionsRequired", self.doc.get("extensionsRequired", [])),
        ]
        for prefix, items in locations:
            for ext in items:
                if ext in KNOWN_EXTENSIONS:
                    found.setdefault(ext, []).append(prefix)
        for m in self._materials:
            for ext in m.extensions:
                if ext in KNOWN_EXTENSIONS:
                    found.setdefault(ext, []).append(f"materials[{m.index}]")
        for mesh in self._meshes:
            for p in mesh.primitives:
                for ext in p.extensions:
                    if ext in KNOWN_EXTENSIONS:
                        found.setdefault(ext, []).append(f"meshes[{mesh.index}].primitive")
            for ext in mesh.extensions:
                if ext in KNOWN_EXTENSIONS:
                    found.setdefault(ext, []).append(f"meshes[{mesh.index}]")
        for node in self._nodes:
            for ext in node.extensions:
                if ext in KNOWN_EXTENSIONS:
                    found.setdefault(ext, []).append(f"nodes[{node.index}]")
        return found

    def validate(self):
        """轻量 schema 校验；返回错误字符串列表（空=通过）。"""
        errs = []
        if "asset" not in self.doc:
            errs.append("缺少 asset")
        else:
            ver = str(self.doc["asset"].get("version", ""))
            if not ver.startswith("2."):
                errs.append(f"asset.version 非 2.x: {ver!r}")
        for req in ("scenes", "nodes", "meshes", "accessors", "bufferViews", "buffers"):
            if req == "scenes":
                continue  # scenes 可省略（默认单 scene）
            if req not in self.doc:
                errs.append(f"缺少必填字段: {req}")
        # 引用完整性
        for i, a in enumerate(self._accessors):
            if a.buffer_view is not None and not (0 <= a.buffer_view < len(self._buffer_views)):
                errs.append(f"accessors[{i}] 引用越界 bufferView {a.buffer_view}")
        for i, bv in enumerate(self._buffer_views):
            if not (0 <= bv.buffer < len(self._buffers)):
                errs.append(f"bufferViews[{i}] 引用越界 buffer {bv.buffer}")
        for node in self._nodes:
            for c in node.children:
                if not (0 <= c < len(self._nodes)):
                    errs.append(f"nodes[{node.index}] 子节点越界 {c}")
        return errs


def parse_gltf(doc):
    """从 glTF 2.0 JSON（dict）构造 GltfModel。"""
    return GltfModel(doc)
