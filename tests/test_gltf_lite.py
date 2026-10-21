# -*- coding: utf-8 -*-
"""glTF-lite 数据模型与扩展矩阵的无窗口单测（纯数据 / 无 GL）。

`python -m tests.test_gltf_lite` 直接跑。聚合 runner 按 `  ok:` 行统计。

依据：Khronos glTF 2.0 规范（规范实现，不取第三方 loader 代码）。
"""

import sys

import numpy as np

from world3d.asset.gltf_lite import parse_gltf


def _sample_gltf():
    return {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0, 1]}],
        "nodes": [
            {"name": "root", "translation": [0, 0, 0],
             "children": [1], "mesh": 0,
             "extensions": {"KHR_lights_punctual": {}}},
            {"name": "child", "translation": [0, 2.0, 0], "mesh": 0,
             "extensions": {"KHR_draco_mesh_compression": {}}},
        ],
        "meshes": [
            {"name": "cube", "primitives": [
                {"attributes": {"POSITION": 0, "NORMAL": 1},
                 "indices": 2, "material": 0, "mode": 4,
                 "extensions": {"KHR_materials_unlit": {}}},
            ]},
        ],
        "materials": [
            {"name": "red", "pbrMetallicRoughness": {
                "baseColorFactor": [1, 0, 0, 1], "metallicFactor": 0.0,
                "roughnessFactor": 0.8},
             "emissiveFactor": [0, 0, 0],
             "extensions": {"KHR_materials_emissive_strength": {"emissiveStrength": 2.0}}},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 24, "type": "VEC3", "min": [-1, -1, -1], "max": [1, 1, 1]},
            {"bufferView": 0, "componentType": 5126, "count": 24, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": 36, "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 288, "target": 34962},
            {"buffer": 0, "byteOffset": 288, "byteLength": 72, "target": 34963},
        ],
        "buffers": [
            {"uri": "data:,", "byteLength": 360},
        ],
        "extensionsUsed": ["KHR_draco_mesh_compression", "KHR_materials_unlit",
                          "KHR_materials_emissive_strength", "KHR_lights_punctual"],
    }


def test_parse_basic():
    m = parse_gltf(_sample_gltf())
    assert len(m.nodes) == 2
    assert len(m.meshes) == 1
    assert len(m.materials) == 1
    assert m.meshes[0].primitives[0].material == 0


def test_world_matrices_hierarchy():
    m = parse_gltf(_sample_gltf())
    # child 局部平移 (0,2,0)，无父变换 → 世界平移 (0,2,0)
    w = m.world_matrix(1)
    assert np.allclose(w[:3, 3], [0, 2.0, 0], atol=1e-6), w[:3, 3]
    # root 世界平移为原点
    assert np.allclose(m.world_matrix(0)[:3, 3], [0, 0, 0], atol=1e-6)


def test_material_fields():
    m = parse_gltf(_sample_gltf())
    mat = m.materials[0]
    assert mat.pbr["metallicFactor"] == 0.0
    assert mat.pbr["baseColorFactor"] == [1, 0, 0, 1]
    assert mat.emissive_factor == [0, 0, 0]
    assert mat.alpha_mode == "OPAQUE"


def test_extension_detection():
    m = parse_gltf(_sample_gltf())
    ext = m.detected_extensions()
    # 应识别多处已知的 KHR_* 扩展
    assert "KHR_materials_unlit" in ext
    assert "KHR_draco_mesh_compression" in ext
    assert "KHR_materials_emissive_strength" in ext
    assert "KHR_lights_punctual" in ext


def test_validate_ok():
    m = parse_gltf(_sample_gltf())
    errs = m.validate()
    assert errs == [], errs


def test_validate_missing_version():
    doc = _sample_gltf()
    doc["asset"] = {"version": "1.0"}
    m = parse_gltf(doc)
    errs = m.validate()
    assert any("version" in e for e in errs), errs


def test_validate_missing_required():
    doc = _sample_gltf()
    del doc["buffers"]
    m = parse_gltf(doc)
    errs = m.validate()
    assert any("buffers" in e for e in errs), errs


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
    print(f'[test_gltf_lite] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_GLTF_LITE_TESTS_FAILED')
        return 1
    print('ALL_GLTF_LITE_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
