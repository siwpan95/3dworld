# -*- coding: utf-8 -*-
"""Filament-lite：材质变体编译缓存（纯数据 / 无 GL）。

按 3dworld engine 层「纯数学 / 无 GL / headless 可编译校验」定位实现。
借鉴 **Filament** `matc` 的思路（Apache-2.0）自研：

* 材质模板声明一组可选特性（feature）/ 数值参数；一次「变体」即对特性子集的取舍；
* 变体键（variant key）由特性选择确定性地序列化为稳定 digest，不依赖进程/平台；
* 「编译」= 把所选 #define 注入模板、生成着色器源码串与确定性字节产物
  （本实现不调用任何 GLSL/MSL 编译器，仅做无 GL 的确定性烘焙，符合红线）；
* 变体缓存：内存 LRU + 可选磁盘持久化（pickle），并统计命中/未命中。

**若将来真要搬 Filament 的编译产物/代码，须补 `THIRD_PARTY_NOTICES` 与 Apache
专利条款声明**（见 docs/借鉴记账.md）。
"""
from __future__ import annotations

import hashlib
import json
import pickle
import os

_EPS = 1e-9


class MaterialDef:
    """材质模板：声明可选特性名与数值参数默认值。"""

    def __init__(self, name, features=(), params=()):
        # features: 可开关的布尔特性名序列
        # params: (参数名, 默认值) 序列，值参与产物生成（如 NUM_LIGHTS）
        self.name = name
        self.features = list(features)
        self.params = dict(params)


class Variant:
    """一次变体：特性开关集合 + 参数取值。"""

    def __init__(self, enabled=(), params=None):
        self.enabled = frozenset(enabled)
        self.params = dict(params or {})


def _jsonable(v):
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, float):
        return round(v, 6)
    return v


def make_key(material, variant):
    feats = tuple(sorted(variant.enabled & set(material.features)))
    pars = tuple(sorted((k, _jsonable(variant.params.get(k, material.params.get(k))))
                        for k in set(list(variant.params.keys()) + list(material.params.keys()))))
    raw = json.dumps({"mat": material.name, "feats": feats, "params": pars},
                     sort_keys=True, separators=(",", ":"))
    return raw


def compile_variant(material, variant):
    """「编译」变体：确定性地生成 #define 列表、着色器源码串与产物 digest。

    返回 Product(defines, source, digest)。
    """
    key = make_key(material, variant)
    defines = []
    for f in sorted(variant.enabled & set(material.features)):
        defines.append(f"#define {f} 1")
    for k in sorted(set(list(variant.params.keys()) + list(material.params.keys()))):
        val = variant.params.get(k, material.params.get(k))
        if isinstance(val, bool):
            defines.append(f"#define {k} {1 if val else 0}")
        elif isinstance(val, (int, float)):
            defines.append(f"#define {k} {val}")
        else:
            defines.append(f"#define {k} {val}")
    header = "\n".join(defines)
    source = f"{header}\n// material:{material.name}\n"
    digest = hashlib.sha256((key + source).encode("utf-8")).digest()
    return Product(defines, source, digest, key)


class Product:
    __slots__ = ("defines", "source", "digest", "key")

    def __init__(self, defines, source, digest, key):
        self.defines = defines
        self.source = source
        self.digest = digest
        self.key = key

    def __eq__(self, other):
        return isinstance(other, Product) and other.digest == self.digest

    def __hash__(self):
        return hash(self.digest)


# ---------------------------------------------------------------------------
# 真实 GLSL 发射（可注入 GLSL：产出着色器源串，不调用编译器，headless 可测）
# 这是「增强」而非「缝补」——material 变体直接编译成可喂给 GL 的 GLSL 源。
# ---------------------------------------------------------------------------

_GLSL_VERSION = "330 core"


def _glsl_float(x):
    if isinstance(x, bool):
        return "1.0" if x else "0.0"
    if isinstance(x, int):
        return f"{x}.0"
    s = repr(round(float(x), 6))
    return s if "." in s else s + ".0"


def _glsl_vec4(v):
    return "vec4(" + ", ".join(_glsl_float(c) for c in v) + ")"


def _define_header(material, variant):
    return "\n".join(compile_variant(material, variant).defines)


def emit_vertex_glsl(material, variant):
    header = _define_header(material, variant)
    skin_decl = (
        "#ifdef SKINNING\n"
        "layout(location = 3) in vec4 a_joint;\n"
        "layout(location = 4) in vec4 a_weight;\n"
        "uniform mat4 u_jointMatrices[64];\n"
        "#endif\n"
    )
    body = (
        "void main() {\n"
        "#ifdef SKINNING\n"
        "    mat4 skinMat =\n"
        "        a_weight.x * u_jointMatrices[int(a_joint.x)] +\n"
        "        a_weight.y * u_jointMatrices[int(a_joint.y)] +\n"
        "        a_weight.z * u_jointMatrices[int(a_joint.z)] +\n"
        "        a_weight.w * u_jointMatrices[int(a_joint.w)];\n"
        "    vec4 localPos = skinMat * vec4(a_position, 1.0);\n"
        "#else\n"
        "    vec4 localPos = u_model * vec4(a_position, 1.0);\n"
        "#endif\n"
        "    gl_Position = u_proj * u_view * localPos;\n"
        "    v_normal = mat3(u_model) * a_normal;\n"
        "    v_uv = a_uv;\n"
        "    v_worldPos = (u_model * vec4(a_position, 1.0)).xyz;\n"
        "}\n"
    )
    return ("#version " + _GLSL_VERSION + "\n" + header + "\n"
            "layout(location = 0) in vec3 a_position;\n"
            "layout(location = 1) in vec3 a_normal;\n"
            "layout(location = 2) in vec2 a_uv;\n"
            + skin_decl +
            "uniform mat4 u_model;\nuniform mat4 u_view;\nuniform mat4 u_proj;\n"
            "out vec3 v_normal;\nout vec2 v_uv;\nout vec3 v_worldPos;\n" + body)


def emit_fragment_glsl(material, variant):
    header = _define_header(material, variant)
    num_lights = int(variant.params.get("NUM_LIGHTS", material.params.get("NUM_LIGHTS", 1)))
    base = variant.params.get("BASE_COLOR", material.params.get("BASE_COLOR", [1, 1, 1, 1]))
    base_lit = _glsl_vec4(base) if isinstance(base, (list, tuple)) else _glsl_float(base)
    extra = ""
    if "NORMAL_MAP" in variant.enabled:
        extra += "uniform sampler2D u_normalMap;\n"
    if "FOG" in variant.enabled:
        extra += "uniform vec3 u_fogColor;\nuniform float u_fogDensity;\n"
    if "ALPHA_TEST" in variant.enabled:
        extra += "uniform float u_alphaCutoff;\n"
    body = (
        "void main() {\n"
        "    vec4 color = u_baseColor;\n"
        "#ifdef ALPHA_TEST\n"
        "    if (color.a < u_alphaCutoff) discard;\n"
        "#endif\n"
        "    vec3 N = normalize(v_normal);\n"
        "    vec3 V = normalize(u_cameraPos - v_worldPos);\n"
        "    vec3 lit = vec3(0.0);\n"
        f"    for (int i = 0; i < {num_lights}; i++) {{\n"
        "        vec3 L = normalize(u_lightPos[i] - v_worldPos);\n"
        "        float ndl = max(dot(N, L), 0.0);\n"
        "        lit += u_lightColor[i] * ndl;\n"
        "    }\n"
        "    color.rgb *= lit;\n"
        "#ifdef FOG\n"
        "    float d = length(u_cameraPos - v_worldPos);\n"
        "    float f = 1.0 - exp(-u_fogDensity * d);\n"
        "    color.rgb = mix(color.rgb, u_fogColor, clamp(f, 0.0, 1.0));\n"
        "#endif\n"
        "    fragColor = color;\n"
        "}\n"
    )
    return ("#version " + _GLSL_VERSION + "\n" + header + "\n"
            "in vec3 v_normal;\nin vec2 v_uv;\nin vec3 v_worldPos;\n"
            f"uniform vec4 u_baseColor = {base_lit};\n"
            "uniform vec3 u_cameraPos;\n"
            f"uniform vec3 u_lightPos[{num_lights}];\n"
            f"uniform vec3 u_lightColor[{num_lights}];\n"
            + extra +
            "out vec4 fragColor;\n" + body)


class GLSLProgram:
    __slots__ = ("vertex", "fragment", "defines", "digest", "key")

    def __init__(self, vertex, fragment, defines, digest, key):
        self.vertex = vertex
        self.fragment = fragment
        self.defines = defines
        self.digest = digest
        self.key = key

    def __eq__(self, other):
        return isinstance(other, GLSLProgram) and other.digest == self.digest

    def __hash__(self):
        return hash(self.digest)


def compile_glsl(material, variant):
    """把材质变体编译为真实 GLSL 顶点/片段着色器源串（可注入 GL，不调用编译器）。"""
    key = make_key(material, variant)
    prod = compile_variant(material, variant)
    vert = emit_vertex_glsl(material, variant)
    frag = emit_fragment_glsl(material, variant)
    digest = hashlib.sha256((key + vert + frag).encode("utf-8")).digest()
    return GLSLProgram(vert, frag, prod.defines, digest, key)


class MaterialCache:
    """变体键 → 产物 的缓存（内存 + 可选磁盘）。"""

    def __init__(self, capacity=128):
        self.capacity = capacity
        self._store = {}
        self._glsl = {}           # 同一键空间的 GLSL 产物缓存
        self._order = []          # LRU 顺序（键）
        self.hits = 0
        self.misses = 0

    def get(self, material, variant):
        key = make_key(material, variant)
        if key in self._store:
            self.hits += 1
            self._touch(key)
            return self._store[key]
        self.misses += 1
        prod = compile_variant(material, variant)
        self._store[key] = prod
        self._touch(key)
        self._evict()
        return prod

    def get_glsl(self, material, variant):
        """取变体对应的 GLSL 程序（缺则编译并缓存）。"""
        key = make_key(material, variant)
        if key in self._glsl:
            self.hits += 1
            self._touch(key)
            return self._glsl[key]
        self.misses += 1
        prog = compile_glsl(material, variant)
        self._glsl[key] = prog
        self._touch(key)
        self._evict()
        return prog

    def _touch(self, key):
        if key in self._order:
            self._order.remove(key)
        self._order.append(key)

    def _evict(self):
        while len(self._store) + len(self._glsl) > self.capacity and self._order:
            old = self._order.pop(0)
            self._store.pop(old, None)
            self._glsl.pop(old, None)

    def clear(self):
        self._store.clear()
        self._glsl.clear()
        self._order.clear()
        self.hits = 0
        self.misses = 0

    def persist(self, path):
        with open(path, "wb") as f:
            pickle.dump({"store": self._store, "glsl": self._glsl, "order": self._order,
                         "hits": self.hits, "misses": self.misses}, f)

    def load(self, path):
        if not os.path.exists(path):
            return False
        with open(path, "rb") as f:
            data = pickle.load(f)
        self._store = data["store"]
        self._glsl = data.get("glsl", {})
        self._order = data["order"]
        self.hits = data.get("hits", 0)
        self.misses = data.get("misses", 0)
        return True

    def stats(self):
        total = self.hits + self.misses
        rate = (self.hits / total) if total else 0.0
        return {"hits": self.hits, "misses": self.misses, "size": len(self._store),
                "hit_rate": rate}
