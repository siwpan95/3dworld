# -*- coding: utf-8 -*-
"""ozz-lite 动画层的无窗口单测（纯数学 / 无 GL）。

`python -m tests.test_ozz_lite` 直接跑。聚合 runner 按 `  ok:` 行统计。

借鉴来源：ozz-animation（MIT）的采样 / 混合 / 加法 / 分层 / root motion 思路；
本实现按算法自研，不搬源码。
"""

import base64
import sys

import numpy as np

from world3d.anim.ozz_lite import (
    Clip, Skeleton, apply_additive, apply_layer, blend, extract_root_motion,
    make_additive, q_to_matrix, sample_clip, skeleton_from_gltf,
    clip_from_gltf_animation, compute_skin_matrices, skin_vertices,
    skinned_mesh_playback,
)
from world3d.asset.gltf_lite import parse_gltf


def _rot_y(a):
    return np.array([np.cos(a / 2), 0, np.sin(a / 2), 0], dtype=float)


def _two_bone_skeleton():
    joints = [
        ("root", -1, (np.zeros(3), np.array([1.0, 0, 0, 0]), np.ones(3))),
        ("spine", 0, (np.array([0, 1.0, 0]), np.array([1.0, 0, 0, 0]), np.ones(3))),
        ("head", 1, (np.array([0, 1.0, 0]), np.array([1.0, 0, 0, 0]), np.ones(3))),
    ]
    return Skeleton(joints)


def test_skeleton_forward_kinematics():
    sk = _two_bone_skeleton()
    worlds = sk.world_matrices()
    # head 世界位置应为 root + spine + head 的局部平移 = (0, 2, 0)
    assert np.allclose(worlds[2][:3, 3], [0, 2.0, 0], atol=1e-6), worlds[2][:3, 3]


def test_sample_clip_interp():
    sk = _two_bone_skeleton()
    # root 平移沿 y 从 0 升到 2，时长 1s
    tracks = {
        0: {"translation": (np.array([0.0, 1.0]),
                            np.array([[0, 0, 0], [0, 2.0, 0]], dtype=float))},
    }
    clip = Clip("lift", 1.0, tracks)
    p0 = sample_clip(clip, 0.0)[0]
    p1 = sample_clip(clip, 1.0)[0]
    pm = sample_clip(clip, 0.5)[0]
    assert np.allclose(p0[0], [0, 0, 0])
    assert np.allclose(p1[0], [0, 2.0, 0])
    assert np.allclose(pm[0], [0, 1.0, 0], atol=1e-6), pm[0]


def test_sample_clip_rotation_slerp():
    tracks = {
        0: {"rotation": (np.array([0.0, 1.0]),
                         np.array([_rot_y(0.0), _rot_y(1.5708)], dtype=float))},
    }
    clip = Clip("spin", 1.0, tracks)
    pm = sample_clip(clip, 0.5)[0]
    # 半程应为绕 y 轴转 ~0.785（90°/2）
    m = q_to_matrix(pm[1])
    assert abs(m[0, 0] - np.cos(0.7854)) < 0.05, m[0, 0]


def test_blend_two_poses():
    sk = _two_bone_skeleton()
    tracks_a = {0: {"translation": (np.array([0.0, 1.0]),
                                np.array([[0, 0, 0], [0, 1.0, 0]], dtype=float))}}
    tracks_b = {0: {"translation": (np.array([0.0, 1.0]),
                                np.array([[0, 0, 0], [0, 3.0, 0]], dtype=float))}}
    pose_a = sample_clip(Clip("a", 1.0, tracks_a), 1.0)
    pose_b = sample_clip(Clip("b", 1.0, tracks_b), 1.0)
    blended = blend([pose_a, pose_b], [0.5, 0.5])
    # 两 pose 的 root 平移分别为 (0,1,0) 与 (0,3,0)，混合应为 (0,2,0)
    assert np.allclose(blended[0][0], [0, 2.0, 0], atol=1e-6), blended[0][0]


def test_additive_layer():
    sk = _two_bone_skeleton()
    # 基 pose：root 静止
    base_track = {0: {"translation": (np.array([0.0]), np.array([[0, 0, 0]], dtype=float))}}
    base = sample_clip(Clip("base", 1.0, base_track), 0.0)
    # 加法 clip：root 平移 y 从 0 到 1（相对参考）
    ref_pose = sample_clip(Clip("ref", 1.0, {}), 0.0)
    add_track = {0: {"translation": (np.array([0.0, 1.0]),
                                   np.array([[0, 0, 0], [0, 1.0, 0]], dtype=float))}}
    add_clip = Clip("add", 1.0, add_track)
    add = make_additive(add_clip, ref_pose)
    add_pose = sample_clip(add, 1.0)
    result = apply_additive(base, add_pose, 1.0)
    # 基静止 + 加法平移 1 → root 平移 (0,1,0)
    assert np.allclose(result[0][0], [0, 1.0, 0], atol=1e-6), result[0][0]


def test_layer_mask():
    sk = _two_bone_skeleton()
    base = [  # 3 关节
        (np.zeros(3), np.array([1.0, 0, 0, 0]), np.ones(3)),
        (np.array([0, 1.0, 0]), np.array([1.0, 0, 0, 0]), np.ones(3)),
        (np.array([0, 1.0, 0]), np.array([1.0, 0, 0, 0]), np.ones(3)),
    ]
    layer = [
        (np.array([5, 0, 0]), np.array([1.0, 0, 0, 0]), np.ones(3)),  # 改 root 平移
        (np.array([0, 9.0, 0]), np.array([1.0, 0, 0, 0]), np.ones(3)),
        (np.array([0, 9.0, 0]), np.array([1.0, 0, 0, 0]), np.ones(3)),
    ]
    # 只把 root(0) 用 layer 覆盖
    out = apply_layer(base, layer, mask={0}, weight=1.0)
    assert np.allclose(out[0][0], [5, 0, 0]), out[0][0]
    # spine/head 不受影响
    assert np.allclose(out[1][0], [0, 1.0, 0])


def test_root_motion():
    tracks = {
        0: {"translation": (np.array([0.0, 1.0]),
                            np.array([[0, 0, 0], [0, 0, 2.0]], dtype=float))},
    }
    clip = Clip("walk", 1.0, tracks)
    dt, dr = extract_root_motion(clip, 0.0, 1.0)
    assert np.allclose(dt, [0, 0, 2.0], atol=1e-6), dt
    # 无旋转增量 → 单位四元数
    assert abs(dr[0] - 1.0) < 1e-6, dr


# --------------------------- 真实蒙皮网格回放（消费 gltf_lite） --
def _sample_skinned_gltf():
    """构造内嵌 data-URI buffer 的蒙皮 glTF：

    * 两关节节点 root(0) / bone(1，root 子节点，静态平移 (1,0,0))；
    * skin 2 关节，inverseBind = [I, T(-1,0,0)]；
    * 3 顶点三角：v0/v1 给 bone 权重、v2 给 root 权重；
    * 动画把 bone 绕 Y 从 0° 旋转到 90°。
    """
    pos = np.array([[1, 0, 1], [1, 0, -1], [0, 0, 0]], dtype=np.float32)
    joints = np.array([[1, 0, 0, 0], [1, 0, 0, 0], [0, 0, 0, 0]], dtype=np.uint8)
    weights = np.array([[1, 0, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0]], dtype=np.float32)
    idx = np.array([0, 1, 2], dtype=np.uint16)
    ibm0 = np.eye(4, dtype=np.float32)
    ibm1 = np.array([[1, 0, 0, -1], [0, 1, 0, 0],
                     [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float32)
    ibm = np.stack([ibm0, ibm1])
    times = np.array([0.0, 1.0], dtype=np.float32)
    s = float(np.sin(np.pi / 4))
    c = float(np.cos(np.pi / 4))
    # glTF 四元数序 (x,y,z,w)
    rot = np.array([[0, 0, 0, 1], [0, s, 0, c]], dtype=np.float32)
    # 按对齐拼接字节，记录各段真实偏移
    raw = bytearray()
    off = {}

    def put(name, arr, align=4):
        while len(raw) % align != 0:
            raw.append(0)
        off[name] = len(raw)
        raw.extend(arr.tobytes())

    put("pos", pos, align=4)
    put("joints", joints, align=1)
    put("weights", weights, align=4)
    put("idx", idx, align=2)
    put("ibm", ibm, align=4)
    put("times", times, align=4)
    put("rot", rot, align=4)
    uri = "data:application/octet-stream;base64," + base64.b64encode(bytes(raw)).decode()
    doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"uri": uri, "byteLength": len(raw)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": off["pos"], "byteLength": pos.nbytes, "target": 34962},
            {"buffer": 0, "byteOffset": off["joints"], "byteLength": joints.nbytes},
            {"buffer": 0, "byteOffset": off["weights"], "byteLength": weights.nbytes, "target": 34962},
            {"buffer": 0, "byteOffset": off["idx"], "byteLength": idx.nbytes, "target": 34963},
            {"buffer": 0, "byteOffset": off["ibm"], "byteLength": ibm.nbytes},
            {"buffer": 0, "byteOffset": off["times"], "byteLength": times.nbytes},
            {"buffer": 0, "byteOffset": off["rot"], "byteLength": rot.nbytes},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5121, "count": 3, "type": "VEC4"},
            {"bufferView": 2, "componentType": 5126, "count": 3, "type": "VEC4"},
            {"bufferView": 3, "componentType": 5123, "count": 3, "type": "SCALAR"},
            {"bufferView": 4, "componentType": 5126, "count": 2, "type": "MAT4"},
            {"bufferView": 5, "componentType": 5126, "count": 2, "type": "SCALAR"},
            {"bufferView": 6, "componentType": 5126, "count": 2, "type": "VEC4"},
        ],
        "nodes": [
            {"name": "root", "translation": [0, 0, 0], "children": [1], "mesh": 0},
            {"name": "bone", "translation": [1, 0, 0]},
        ],
        "meshes": [{"primitives": [
            {"attributes": {"POSITION": 0, "JOINTS_0": 1, "WEIGHTS_0": 2},
             "indices": 3}]}],
        "skins": [{"joints": [0, 1], "inverseBindMatrices": 4, "name": "arm"}],
        "animations": [{"name": "wave", "channels": [
            {"sampler": 0, "target": {"node": 1, "path": "rotation"}}],
            "samplers": [{"input": 5, "output": 6, "interpolation": "LINEAR"}]}],
        "scenes": [{"nodes": [0, 1]}],
    }
    return doc, pos, s, c


def test_skin_vertices_identity():
    pos = np.array([[1, 2, 3], [4, 5, 6]], dtype=float)
    joints = np.array([[0, 0, 0, 0], [1, 0, 0, 0]], dtype=int)
    weights = np.array([[1, 0, 0, 0], [1, 0, 0, 0]], dtype=float)
    out = skin_vertices(pos, joints, weights, [np.eye(4), np.eye(4)])
    assert np.allclose(out, pos), out


def test_skin_vertices_rotation():
    # 单骨骼，蒙皮矩阵 = R_y(90°)，+z 顶点应转到 +x
    ry = np.array([[0, 0, 1, 0], [0, 1, 0, 0], [-1, 0, 0, 0], [0, 0, 0, 1]],
                  dtype=float)
    pos = np.array([[0, 0, 1]], dtype=float)
    joints = np.array([[0, 0, 0, 0]], dtype=int)
    weights = np.array([[1, 0, 0, 0]], dtype=float)
    out = skin_vertices(pos, joints, weights, [ry])
    assert np.allclose(out[0], [1, 0, 0], atol=1e-5), out[0]


def test_skeleton_from_gltf():
    doc, _pos, _s, _c = _sample_skinned_gltf()
    m = parse_gltf(doc)
    skin = m.skins()[0]
    sk, n2j = skeleton_from_gltf(m, skin)
    assert sk.names == ["root", "bone"]
    assert sk.parents == [-1, 0]
    # bone 静态平移 (1,0,0) 应进入 bind-local
    assert np.allclose(sk.bind[1][0], [1, 0, 0])


def test_clip_from_gltf_animation():
    doc, _pos, _s, c = _sample_skinned_gltf()
    m = parse_gltf(doc)
    skin = m.skins()[0]
    anim = m.animations()[0]
    sk, n2j = skeleton_from_gltf(m, skin)
    clip = clip_from_gltf_animation(m, anim, n2j)
    assert 1 in clip.tracks
    # 关键帧 1（ozz 序 (w,x,y,z)）应为 R_y(90°) = (cos45, 0, sin45, 0)
    rot = clip.tracks[1]["rotation"]
    assert np.allclose(rot[1][1], [c, 0, c, 0], atol=1e-5), rot[1][1]
    assert abs(clip.duration - 1.0) < 1e-5


def test_skinned_mesh_playback():
    doc, pos, _s, _c = _sample_skinned_gltf()
    m = parse_gltf(doc)
    # t=0：静止（bone 旋转单位）→ 蒙皮应等于 bind（顶点不动）
    r0 = skinned_mesh_playback(m, t=0.0)
    assert np.allclose(r0["positions"], pos, atol=1e-5), r0["positions"]
    # root 加权顶点始终在原点
    assert np.allclose(r0["positions"][2], [0, 0, 0], atol=1e-5)
    # t=1：bone 绕 Y 转 90° → v0 从 (1,0,1) 转到 (2,0,0)
    r1 = skinned_mesh_playback(m, t=1.0)
    assert not np.allclose(r1["positions"][0], pos[0]), r1["positions"][0]
    assert np.allclose(r1["positions"][0], [2, 0, 0], atol=1e-5), r1["positions"][0]
    # 旋转关于 Y 轴，y 坐标保持 0
    assert abs(r1["positions"][0][1]) < 1e-5
    # t=0.5：bone 转 45°，v0 的 x 应增大、z 在 0~1 之间、y≈0
    rm = skinned_mesh_playback(m, t=0.5)
    v0 = rm["positions"][0]
    assert v0[0] > 1.0 and 0.0 <= v0[2] <= 1.0 and abs(v0[1]) < 1e-5, v0


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
    print(f'[test_ozz_lite] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_OZZ_LITE_TESTS_FAILED')
        return 1
    print('ALL_OZZ_LITE_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
