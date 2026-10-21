# -*- coding: utf-8 -*-
"""Filament-lite 材质变体缓存的无窗口单测（纯数据 / 无 GL）。

`python -m tests.test_matcache_lite` 直接跑。聚合 runner 按 `  ok:` 行统计。

借鉴来源：Filament `matc`（Apache-2.0）的「变体键 → 产物 → 缓存」思路；
本实现按算法自研，不调用 GLSL/MSL 编译器，不搬源码。
"""

import os
import sys
import tempfile

import numpy as np

from world3d.render.matcache_lite import (
    MaterialCache, MaterialDef, Variant, compile_variant, compile_glsl,
    GLSLProgram, make_key,
)


def _mat():
    return MaterialDef(
        "lit",
        features=("USE_SHADOWS", "SKINNING", "FOG"),
        params={"NUM_LIGHTS": 4, "QUALITY": 1},
    )


def test_same_key_same_product():
    m = _mat()
    v = Variant(enabled=("USE_SHADOWS", "FOG"), params={"NUM_LIGHTS": 8})
    p1 = compile_variant(m, v)
    p2 = compile_variant(m, v)
    assert p1 == p2
    assert p1.digest == p2.digest
    # 确定性 digest 跨进程稳定
    assert len(p1.digest) == 32


def test_different_variant_different_key():
    m = _mat()
    va = Variant(enabled=("USE_SHADOWS",), params={})
    vb = Variant(enabled=("FOG",), params={})
    assert make_key(m, va) != make_key(m, vb)


def test_cache_hit_miss():
    m = _mat()
    c = MaterialCache()
    va = Variant(enabled=("USE_SHADOWS",), params={"NUM_LIGHTS": 2})
    vb = Variant(enabled=("SKINNING",), params={"NUM_LIGHTS": 2})
    c.get(m, va)
    c.get(m, vb)
    c.get(m, va)  # 重复 → 命中
    assert c.hits == 1 and c.misses == 2, c.stats()


def test_define_generation():
    m = _mat()
    v = Variant(enabled=("USE_SHADOWS", "SKINNING"), params={"NUM_LIGHTS": 3})
    p = compile_variant(m, v)
    assert "#define USE_SHADOWS 1" in p.defines
    assert "#define SKINNING 1" in p.defines
    assert "#define NUM_LIGHTS 3" in p.defines
    # FOG 未启用 → 不应出现
    assert not any("FOG" in d for d in p.defines)


def test_disk_roundtrip():
    m = _mat()
    c = MaterialCache()
    v = Variant(enabled=("FOG",), params={"NUM_LIGHTS": 1})
    p = c.get(m, v)
    tmp = os.path.join(tempfile.gettempdir(), "matcache_test.pkl")
    c.persist(tmp)
    c2 = MaterialCache()
    assert c2.load(tmp)
    assert c2.get(m, v) == p
    assert c2.hits == 1
    os.remove(tmp)


def test_lru_eviction():
    c = MaterialCache(capacity=3)
    m = _mat()
    for i in range(5):
        c.get(m, Variant(enabled=(), params={"NUM_LIGHTS": i}))
    # 容量 3，最多保留 3 个
    assert len(c._store) <= 3, len(c._store)
    assert len(c._store) == 3


def _gmat():
    return MaterialDef(
        "lit_glsl",
        features=("SKINNING", "FOG", "NORMAL_MAP", "ALPHA_TEST"),
        params={"NUM_LIGHTS": 4, "BASE_COLOR": [1.0, 0.0, 0.0, 1.0]},
    )


def test_glsl_emits_vertex_with_skinning():
    m = _gmat()
    v = Variant(enabled=("SKINNING",), params={})
    prog = compile_glsl(m, v)
    assert isinstance(prog, GLSLProgram)
    assert "#ifdef SKINNING" in prog.vertex
    assert "u_jointMatrices" in prog.vertex
    assert "gl_Position" in prog.vertex


def test_glsl_num_lights_loop_bound():
    m = _gmat()
    v = Variant(enabled=(), params={"NUM_LIGHTS": 3})
    prog = compile_glsl(m, v)
    assert "i < 3" in prog.fragment
    assert "u_lightPos[3]" in prog.fragment
    assert "u_lightColor[3]" in prog.fragment


def test_glsl_fog_and_alpha_blocks():
    m = _gmat()
    v = Variant(enabled=("FOG", "ALPHA_TEST"), params={})
    prog = compile_glsl(m, v)
    assert "#ifdef FOG" in prog.fragment
    assert "u_fogColor" in prog.fragment
    assert "#ifdef ALPHA_TEST" in prog.fragment
    assert "discard" in prog.fragment


def test_glsl_base_color_literal():
    m = _gmat()
    v = Variant(enabled=(), params={"BASE_COLOR": [0.0, 0.5, 1.0, 1.0]})
    prog = compile_glsl(m, v)
    assert "vec4(0.0, 0.5, 1.0, 1.0)" in prog.fragment


def test_glsl_deterministic_and_cached():
    m = _gmat()
    v = Variant(enabled=("SKINNING", "FOG"), params={"NUM_LIGHTS": 2})
    p1 = compile_glsl(m, v)
    p2 = compile_glsl(m, v)
    assert p1 == p2 and p1.digest == p2.digest
    c = MaterialCache()
    a = c.get_glsl(m, v)
    b = c.get_glsl(m, v)   # 命中
    assert a == b and c.hits == 1 and c.misses == 1, c.stats()


def main() -> int:
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith('test_') and callable(fn)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f'  ok: {name}')
        except AssertionError as exc:
            failed.append((name, exc))
            print(f'  FAIL: {name}: {exc}')
        except Exception as exc:                            # noqa: BLE001
            failed.append((name, exc))
            print(f'  ERROR: {type(exc).__name__}: {exc}')
    print('=' * 60)
    print(f'[test_matcache_lite] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_MATCACHE_LITE_TESTS_FAILED')
        return 1
    print('ALL_MATCACHE_LITE_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
