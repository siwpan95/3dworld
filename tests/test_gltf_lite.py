# -*- coding: utf-8 -*-
"""glTF-lite 数据模型与扩展矩阵的无窗口单测（纯数据 / 无 GL）。

`python -m tests.test_gltf_lite` 直接跑。聚合 runner 按 `  ok:` 行统计。

依据：Khronos glTF 2.0 规范（规范实现，不取第三方 loader 代码）。
"""

import base64
import json
import os
import struct
import sys
import tempfile

import numpy as np

from world3d.asset.gltf_lite import parse_gltf, load_glb


def _pad4(b: bytes, pad_byte: int = 0x00) -> bytes:
    rem = len(b) % 4
    return b + bytes([pad_byte]) * (4 - rem) if rem else b


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


def _sample_with_buffers():
    """构造内嵌 data-URI buffer 的 glTF（positions/normals/indices）。"""
    pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], dtype=np.float32)
    nrm = np.tile(np.array([0, 0, 1], dtype=np.float32), (4, 1))
    idx = np.array([0, 1, 2, 2, 1, 3], dtype=np.uint16)
    raw = pos.tobytes() + nrm.tobytes() + idx.tobytes()
    uri = "data:application/octet-stream;base64," + base64.b64encode(raw).decode()
    doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"uri": uri, "byteLength": len(raw)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": pos.nbytes, "target": 34962},
            {"buffer": 0, "byteOffset": pos.nbytes, "byteLength": nrm.nbytes, "target": 34962},
            {"buffer": 0, "byteOffset": pos.nbytes + nrm.nbytes,
             "byteLength": idx.nbytes, "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5126, "count": 4, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5123, "count": 6, "type": "SCALAR"},
        ],
        "meshes": [{"primitives": [
            {"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2}]}],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
    }
    return doc, pos, nrm, idx


def test_decode_accessor_data_uri():
    doc, pos, nrm, idx = _sample_with_buffers()
    m = parse_gltf(doc)
    p = m.decode_accessor(0)
    assert p.shape == (4, 3), p.shape
    assert np.allclose(p, pos)
    n = m.decode_accessor(1)
    assert np.allclose(n, nrm)
    i = m.decode_accessor(2)
    assert i.dtype == np.uint16
    assert np.array_equal(i, idx)


def test_primitive_arrays():
    doc, pos, nrm, idx = _sample_with_buffers()
    m = parse_gltf(doc)
    arr = m.primitive_arrays(m.meshes[0].primitives[0])
    assert "POSITION" in arr and "NORMAL" in arr and "indices" in arr
    assert np.allclose(arr["POSITION"], pos)
    assert np.allclose(arr["NORMAL"], nrm)
    assert np.array_equal(arr["indices"], idx)


def test_load_glb_binary():
    pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
    idx = np.array([0, 1, 2], dtype=np.uint16)
    bin_raw = pos.tobytes() + idx.tobytes()
    doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(bin_raw)}],   # uri=None → 走 BIN chunk
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": pos.nbytes, "target": 34962},
            {"buffer": 0, "byteOffset": pos.nbytes, "byteLength": idx.nbytes, "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
    }
    json_chunk = _pad4(json.dumps(doc).encode("utf-8"), 0x20)
    bin_chunk = _pad4(bin_raw, 0x00)
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    glb = struct.pack("<III", 0x46546C67, 2, total)
    glb += struct.pack("<II", len(json_chunk), 0x4E4F534A) + json_chunk
    glb += struct.pack("<II", len(bin_chunk), 0x004E4942) + bin_chunk
    path = os.path.join(tempfile.mkdtemp(), "sample.glb")
    with open(path, "wb") as f:
        f.write(glb)
    m = load_glb(path)
    p = m.decode_accessor(0)
    assert p.shape == (3, 3)
    assert np.allclose(p, pos)
    i = m.decode_accessor(1)
    assert np.array_equal(i, idx)


def test_skin_and_animation():
    ibm = np.tile(np.eye(4, dtype=np.float32), (2, 1, 1))
    rot = np.array([[0, 0, 0, 1], [0, 1, 0, 0]], dtype=np.float32)
    times = np.array([0.0, 1.0], dtype=np.float32)
    raw = ibm.tobytes() + times.tobytes() + rot.tobytes()
    uri = "data:application/octet-stream;base64," + base64.b64encode(raw).decode()
    ibm_off = 0
    times_off = ibm.nbytes
    rot_off = times_off + times.nbytes
    doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"uri": uri, "byteLength": len(raw)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": ibm_off, "byteLength": ibm.nbytes},
            {"buffer": 0, "byteOffset": times_off, "byteLength": times.nbytes},
            {"buffer": 0, "byteOffset": rot_off, "byteLength": rot.nbytes},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 2, "type": "MAT4"},
            {"bufferView": 1, "componentType": 5126, "count": 2, "type": "SCALAR"},
            {"bufferView": 2, "componentType": 5126, "count": 2, "type": "VEC4"},
        ],
        "nodes": [{"name": "root"}, {"name": "bone"}],
        "skins": [{"joints": [0, 1], "inverseBindMatrices": 0, "name": "arm"}],
        "animations": [{"name": "wave", "channels": [
            {"sampler": 0, "target": {"node": 1, "path": "rotation"}}],
            "samplers": [{"input": 1, "output": 2, "interpolation": "LINEAR"}]}],
        "scenes": [{"nodes": [0, 1]}],
    }
    m = parse_gltf(doc)
    sk = m.skins()
    assert len(sk) == 1 and sk[0].joints == [0, 1]
    assert sk[0].inverse_bind.shape == (2, 4, 4)
    anims = m.animations()
    assert len(anims) == 1
    ch = anims[0].channels[0]
    assert ch.node == 1 and ch.path == "rotation"
    assert np.allclose(ch.sampler.times, [0, 1])
    assert ch.sampler.values.shape == (2, 4)


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
