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


class MaterialCache:
    """变体键 → 产物 的缓存（内存 + 可选磁盘）。"""

    def __init__(self, capacity=128):
        self.capacity = capacity
        self._store = {}
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

    def _touch(self, key):
        if key in self._order:
            self._order.remove(key)
        self._order.append(key)

    def _evict(self):
        while len(self._store) > self.capacity and self._order:
            old = self._order.pop(0)
            self._store.pop(old, None)

    def clear(self):
        self._store.clear()
        self._order.clear()
        self.hits = 0
        self.misses = 0

    def persist(self, path):
        with open(path, "wb") as f:
            pickle.dump({"store": self._store, "order": self._order,
                         "hits": self.hits, "misses": self.misses}, f)

    def load(self, path):
        if not os.path.exists(path):
            return False
        with open(path, "rb") as f:
            data = pickle.load(f)
        self._store = data["store"]
        self._order = data["order"]
        self.hits = data.get("hits", 0)
        self.misses = data.get("misses", 0)
        return True

    def stats(self):
        total = self.hits + self.misses
        rate = (self.hits / total) if total else 0.0
        return {"hits": self.hits, "misses": self.misses, "size": len(self._store),
                "hit_rate": rate}
