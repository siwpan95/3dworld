# -*- coding: utf-8 -*-
"""ozz-lite 动画层的无窗口单测（纯数学 / 无 GL）。

`python -m tests.test_ozz_lite` 直接跑。聚合 runner 按 `  ok:` 行统计。

借鉴来源：ozz-animation（MIT）的采样 / 混合 / 加法 / 分层 / root motion 思路；
本实现按算法自研，不搬源码。
"""

import sys

import numpy as np

from world3d.anim.ozz_lite import (
    Clip, Skeleton, apply_additive, apply_layer, blend, extract_root_motion,
    make_additive, q_to_matrix, sample_clip,
)


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
