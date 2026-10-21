# -*- coding: utf-8 -*-
"""Jolt-lite 刚体物理核心的无窗口单测（纯数学 / 无 GL）。

`python -m tests.test_jolt_lite` 直接跑。聚合 runner 按 `  ok:` 行统计。

借鉴来源：Jolt Physics（MIT，算法档）的 GJK/EPA、顺序冲量、warm start、
sleeping 思路；本实现按算法自研，不搬源码。
"""

import sys

import numpy as np

from world3d.physics.jolt_lite import (
    Box, Capsule, RigidBody, Sphere, World, _gjk_distance, epa, manifold,
)


def test_gjk_separation_distance():
    """两球分离：GJK 返回表面间距 == 中心距 - 半径和。"""
    a = RigidBody(Sphere(1.0), pos=(0, 0, 0))
    b = RigidBody(Sphere(1.0), pos=(3, 0, 0))
    colliding, dist, *_ = _gjk_distance(a.shape, b.shape, a.xf, b.xf)
    assert not colliding, "分离球体不应判为碰撞"
    assert abs(dist - 1.0) < 1e-6, dist


def test_epa_overlap_depth():
    """两球重叠：EPA 给出穿透深度 ≈ 半径和 - 中心距，法线沿 x。"""
    a = RigidBody(Sphere(1.0), pos=(0, 0, 0))
    b = RigidBody(Sphere(1.0), pos=(1.5, 0, 0))
    colliding, _, pa, pb, d, simplex = _gjk_distance(a.shape, b.shape, a.xf, b.xf)
    assert colliding, "重叠球体应判为碰撞"
    n, depth = epa(a.shape, b.shape, a.xf, b.xf, simplex)
    assert 0.4 < depth < 0.6, depth
    assert abs(abs(n[0]) - 1.0) < 0.25, n


def test_sphere_rests_on_ground():
    """球落到地面（静态盒）后趋于静止，不下沉、不爆炸。"""
    w = World(baumgarte=0.2, slop=0.005)
    ground = RigidBody(Box((100.0, 0.5, 100.0)), pos=(0, -0.5, 0), is_static=True)
    ball = RigidBody(Sphere(1.0), mass=1.0, pos=(0, 3.0, 0))
    w.add(ground)
    w.add(ball)
    for _ in range(400):
        w.step(1 / 60.0, iterations=12)
    # 静止后球心应在 y≈1（半径），速度收敛
    assert 0.9 < ball.xf.pos[1] < 1.1, ball.xf.pos[1]
    assert np.linalg.norm(ball.vel) < 0.4, ball.vel


def test_box_rests_on_ground():
    """盒落到地面后趋于静止（轴对齐，AABB 流形）。"""
    w = World(baumgarte=0.2, slop=0.005)
    ground = RigidBody(Box((100.0, 0.5, 100.0)), pos=(0, -0.5, 0), is_static=True)
    box = RigidBody(Box((0.5, 0.5, 0.5)), mass=1.0, pos=(0, 3.0, 0))
    w.add(ground)
    w.add(box)
    for _ in range(400):
        w.step(1 / 60.0, iterations=12)
    assert 0.4 < box.xf.pos[1] < 0.6, box.xf.pos[1]
    assert np.linalg.norm(box.vel) < 0.4, box.vel


def test_energy_dissipates():
    """下落球的总动能最终趋于 0（不发散）。"""
    w = World(baumgarte=0.2, slop=0.005)
    ground = RigidBody(Box((100.0, 0.5, 100.0)), pos=(0, -0.5, 0), is_static=True)
    ball = RigidBody(Sphere(1.0), mass=1.0, pos=(0, 4.0, 0), vel=(0, -2.0, 0))
    w.add(ground)
    w.add(ball)
    ke_start = 0.5 * 1.0 * np.dot(ball.vel, ball.vel)
    for _ in range(500):
        w.step(1 / 60.0, iterations=12)
    ke_end = 0.5 * 1.0 * np.dot(ball.vel, ball.vel)
    assert ke_end < 0.1 * ke_start, (ke_start, ke_end)


def test_manifold_separated_none():
    a = RigidBody(Sphere(1.0), pos=(0, 0, 0))
    b = RigidBody(Sphere(1.0), pos=(3, 0, 0))
    assert manifold(a, b) is None


def test_capsule_has_support():
    c = Capsule(0.5, 1.0)
    body = RigidBody(c, pos=(0, 0, 0))
    s = c.support(body.xf, (0, 1, 0))
    assert abs(s[1] - 1.0) < 1e-6, s


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
    print(f'[test_jolt_lite] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_JOLT_LITE_TESTS_FAILED')
        return 1
    print('ALL_JOLT_LITE_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
