# -*- coding: utf-8 -*-
"""glTF-lite：glTF 2.0 真实 loader（数据解码 + 节点 FK + 扩展矩阵）。

遵循 **Khronos glTF 2.0 规范**（规范，按规范自研，不取第三方 loader 代码）实现：

* 解析 glTF JSON / 二进制 .glb 为数据模型：scene / node（TRS 或 matrix 的局部与
  world 变换）/ mesh（primitives：attributes / indices / material / mode）/
  material（pbrMetallicRoughness 与 texture / alpha / extensions）/ accessor /
  bufferView / buffer / texture / image / skin / animation；
* **真实解码**：把 accessor（经 bufferView → buffer，含 byteStride / normalized）
  解码为 numpy 数组，装配 primitive 的 POSITION / NORMAL / TEXCOORD / indices，
  **这才是「loader」而非「model」**；
* 节点层级前向运动学：由 TRS 或 4x4 matrix 求每个节点的世界矩阵（带缓存）；
* skin（joints + inverseBindMatrices）与 animation（channels + samplers →
  numpy 关键帧）提取，供蒙皮渲染与动画回放消费；
* **扩展矩阵**：识别 KHR_materials_* / EXT_meshopt / KHR_texture_basisu /
  KHR_draco_mesh_compression 等已知扩展（仅登记与解析，不取第三方 loader 代码）；
* 轻量 schema 校验：必填字段与 asset.version==2.0，返回错误列表。

解码核心是纯 numpy、零 GL、可 headless 单测（与 3dworld 既有 engine 模块
「纯数学 + 可注入 GLSL，全部可 headless 断言」的约定一致）；GL/渲染是消费方的事。
"""

from __future__ import annotations

import base64
import json
import os
import struct

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
# glTF 2.0 组件类型 → numpy dtype
_NP_DTYPE = {
    5120: np.int8,
    5121: np.uint8,
    5122: np.int16,
    5123: np.uint16,
    5125: np.uint32,
    5126: np.float32,
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

# glTF chunk 类型
_CHUNK_JSON = 0x4E4F534A
_CHUNK_BIN = 0x004E4942
_GLB_MAGIC = 0x46546C67


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


def _apply_normalized(arr, component_type):
    """glTF normalized accessor：整数映射到 [-1,1] 或 [0,1]。"""
    if component_type == 5121:      # UNSIGNED_BYTE
        return arr.astype(np.float32) / 255.0
    if component_type == 5123:      # UNSIGNED_SHORT
        return arr.astype(np.float32) / 65535.0
    if component_type == 5120:      # BYTE
        return np.maximum(arr.astype(np.float32) / 127.0, -1.0)
    if component_type == 5122:      # SHORT
        return np.maximum(arr.astype(np.float32) / 32767.0, -1.0)
    return arr


def _pad4(b: bytes, pad_byte: int = 0x00) -> bytes:
    rem = len(b) % 4
    if rem:
        return b + bytes([pad_byte]) * (4 - rem)
    return b


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
        self.skin = raw.get("skin")
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
        self.sparse = raw.get("sparse")

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


class Skin:
    def __init__(self, joints, inverse_bind, name, skeleton=None):
        self.joints = joints            # [node index, ...]
        self.inverse_bind = inverse_bind  # (N,4,4) 或 None
        self.name = name
        self.skeleton = skeleton


class AnimationSampler:
    def __init__(self, times, values, interpolation):
        self.times = times          # (F,) float
        self.values = values        # (F, C) float
        self.interpolation = interpolation


class AnimationChannel:
    def __init__(self, node, path, sampler):
        self.node = node            # target node index
        self.path = path            # translation / rotation / scale / weights
        self.sampler = sampler


class Animation:
    def __init__(self, name, channels):
        self.name = name
        self.channels = channels


class GltfModel:
    def __init__(self, doc, glb_bin=None, base_dir=None):
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
        self._buffer_bytes = None
        self._glb_bin = glb_bin
        self._base_dir = base_dir

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

    # ---- 真实解码：buffer → numpy ----
    def _resolve_buffer_bytes(self, base_dir=None):
        if self._buffer_bytes is not None:
            return self._buffer_bytes
        if base_dir is None:
            base_dir = self._base_dir
        out = []
        for b in self._buffers:
            uri = b.uri
            if uri is None:
                # .glb 的 BIN chunk，或单 buffer 缺 URI
                out.append(self._glb_bin if self._glb_bin is not None
                           else (b"" * 0))
            elif uri.startswith("data:"):
                comma = uri.find(",")
                meta = uri[:comma]
                payload = uri[comma + 1:]
                if ";base64" in meta:
                    out.append(base64.b64decode(payload))
                else:
                    out.append(payload.encode("utf-8"))
            else:
                path = uri if base_dir is None else os.path.join(base_dir, uri)
                with open(path, "rb") as f:
                    out.append(f.read())
        self._buffer_bytes = out
        return out

    def decode_accessor(self, idx, base_dir=None):
        """把第 idx 个 accessor 解码为 numpy 数组。

        返回形状：(count, num_components)（向量/矩阵）或 (count,)（SCALAR）。
        处理 byteStride、byteOffset、normalized；稀疏访问器当前回退为全零基底
        （解码核心仍 headless 可测，稀疏叠加为后续增强）。
        """
        acc = self._accessors[idx]
        comps = acc.num_components
        if acc.buffer_view is None:
            shape = (acc.count, comps) if comps > 1 else (acc.count,)
            return np.zeros(shape, dtype=_NP_DTYPE[acc.component_type])
        bufs = self._resolve_buffer_bytes(base_dir)
        bv = self._buffer_views[acc.buffer_view]
        raw = bufs[bv.buffer]
        dtype = _NP_DTYPE[acc.component_type]
        comp_bytes = COMPONENT_BYTES[acc.component_type]
        elem = comps * comp_bytes
        start = (bv.byte_offset or 0) + (acc.byte_offset or 0)
        stride = bv.byte_stride
        if stride and stride != elem:
            rows = []
            for i in range(acc.count):
                off = start + i * stride
                sub = raw[off:off + elem]
                rows.append(np.frombuffer(sub, dtype=dtype, count=comps))
            arr = np.stack(rows)
        else:
            arr = np.frombuffer(raw, dtype=dtype,
                                count=acc.count * comps, offset=start)
            arr = arr.reshape(acc.count, comps) if comps > 1 else arr.reshape(acc.count)
        if acc.normalized:
            arr = _apply_normalized(arr, acc.component_type)
        return arr

    def primitive_arrays(self, prim, base_dir=None):
        """解码一个 primitive 的全部 attribute + indices 为 numpy 数组。"""
        out = {}
        for attr, acc_idx in prim.attributes.items():
            out[attr] = self.decode_accessor(acc_idx, base_dir)
        if prim.indices is not None:
            out["indices"] = self.decode_accessor(prim.indices, base_dir)
        return out

    def mesh_arrays(self, mesh_idx, base_dir=None):
        """解码一个 mesh 下每个 primitive 的顶点/索引数组。"""
        return [self.primitive_arrays(p, base_dir)
                for p in self._meshes[mesh_idx].primitives]

    # ---- skin / animation ----
    def skins(self, base_dir=None):
        res = []
        for s in self.doc.get("skins", []):
            joints = list(s.get("joints", []))
            ibm = None
            if "inverseBindMatrices" in s:
                ibm = self.decode_accessor(s["inverseBindMatrices"], base_dir)
                if ibm.ndim == 2:
                    ibm = ibm.reshape(len(joints), 4, 4)
            res.append(Skin(joints, ibm, s.get("name", ""), s.get("skeleton")))
        return res

    def animations(self, base_dir=None):
        res = []
        for a in self.doc.get("animations", []):
            samplers = {}
            for i, s in enumerate(a.get("samplers", [])):
                times = self.decode_accessor(s["input"], base_dir)
                if times.ndim > 1:
                    times = times.reshape(-1)
                values = self.decode_accessor(s["output"], base_dir)
                samplers[i] = AnimationSampler(
                    times, values, s.get("interpolation", "LINEAR"))
            channels = []
            for ch in a.get("channels", []):
                tgt = ch["target"]
                channels.append(AnimationChannel(
                    tgt.get("node"), tgt.get("path"), samplers[ch["sampler"]]))
            res.append(Animation(a.get("name", ""), channels))
        return res

    # ---- 节点世界矩阵 ----
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


def load_glb(path):
    """解析二进制 .glb（12 字节头 + JSON chunk + BIN chunk）。"""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 12:
        raise ValueError("文件过短，不是 glTF 二进制")
    magic, _version, length = struct.unpack("<III", data[:12])
    if magic != _GLB_MAGIC:
        raise ValueError("magic 不匹配，不是 glTF 二进制容器")
    json_bytes = None
    bin_bytes = None
    off = 12
    while off + 8 <= min(length, len(data)):
        chunk_len, chunk_type = struct.unpack("<II", data[off:off + 8])
        chunk_data = data[off + 8:off + 8 + chunk_len]
        if chunk_type == _CHUNK_JSON:
            json_bytes = chunk_data
        elif chunk_type == _CHUNK_BIN:
            bin_bytes = chunk_data
        off += 8 + chunk_len
        if chunk_len % 4:
            off += 4 - (chunk_len % 4)
    if json_bytes is None:
        raise ValueError("缺少 JSON chunk")
    doc = json.loads(json_bytes.decode("utf-8"))
    return GltfModel(doc, glb_bin=bin_bytes)


def load_gltf(path):
    """按扩展名加载 .glb 或 .gltf；.gltf 的外部 buffer 相对文件目录解析。"""
    path = str(path)
    if path.lower().endswith(".glb"):
        return load_glb(path)
    base_dir = os.path.dirname(os.path.abspath(path))
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    return GltfModel(doc, base_dir=base_dir)
