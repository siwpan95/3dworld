# -*- coding: utf-8 -*-
"""Box2D-lite 2D 约束求解的无窗口单测（纯数学 / 无 GL）。

`python -m tests.test_box2d_lite` 直接跑。聚合 runner 按 `  ok:` 行统计。

借鉴来源：Box2D（Erin Catto，MIT）的顺序冲量 + warm start + Baumgarte + Coulomb
摩擦思路；本实现按算法自研，不搬源码。
"""

import sys

import numpy as np

from world3d.physics.box2d_lite import (
    Body, Box, Circle, World, collide,
)


def test_box_rests_on_ground():
    """盒落到地面（静态盒）后趋于静止，不下沉、不爆炸。"""
    w = World()
    ground = Body(Box((50.0, 0.5)), pos=(0, -0.5), static=True)
    box = Body(Box((0.5, 0.5)), mass=1.0, pos=(0, 5.0))
    w.add(ground)
    w.add(box)
    for _ in range(300):
        w.step(1 / 60.0, iterations=12)
    assert 0.4 < box.pos[1] < 0.6, box.pos[1]
    assert np.linalg.norm(box.vel) < 0.5, box.vel


def test_circle_rests_on_ground():
    """圆落到地面后趋于静止。"""
    w = World()
    ground = Body(Box((50.0, 0.5)), pos=(0, -0.5), static=True)
    ball = Body(Circle(0.5), mass=1.0, pos=(0, 4.0))
    w.add(ground)
    w.add(ball)
    for _ in range(300):
        w.step(1 / 60.0, iterations=12)
    assert 0.4 < ball.pos[1] < 0.6, ball.pos[1]


def test_energy_dissipates():
    """下落盒的总动能最终趋于 0（不发散）。"""
    w = World()
    ground = Body(Box((50.0, 0.5)), pos=(0, -0.5), static=True)
    box = Body(Box((0.5, 0.5)), mass=1.0, pos=(0, 5.0), vel=(0, -3.0))
    w.add(ground)
    w.add(box)
    ke_start = 0.5 * 1.0 * float(np.dot(box.vel, box.vel))
    for _ in range(400):
        w.step(1 / 60.0, iterations=12)
    ke_end = 0.5 * 1.0 * float(np.dot(box.vel, box.vel))
    assert ke_end < 0.1 * ke_start, (ke_start, ke_end)


def test_separated_no_contact():
    a = Body(Circle(1.0), pos=(0, 0))
    b = Body(Circle(1.0), pos=(5, 0))
    assert collide(a, b) is None


def test_box_stack_stable():
    """两个盒竖直堆叠：顶盒不穿透底盒，二者都接近静止。"""
    w = World()
    ground = Body(Box((50.0, 0.5)), pos=(0, -0.5), static=True)
    b0 = Body(Box((0.5, 0.5)), mass=1.0, pos=(0, 0.5))
    b1 = Body(Box((0.5, 0.5)), mass=1.0, pos=(0, 1.5))
    w.add(ground)
    w.add(b0)
    w.add(b1)
    for _ in range(500):
        w.step(1 / 60.0, iterations=14)
    # 顶盒应浮在底盒上方（中心间距 ≈ 1.0，底盒顶在 y=1.0）
    assert b0.pos[1] > 0.4 and b0.pos[1] < 0.7, b0.pos[1]
    assert b1.pos[1] > 1.3 and b1.pos[1] < 1.8, b1.pos[1]
    # 不得严重穿透（中心距不应 < 0.9）
    assert (b1.pos[1] - b0.pos[1]) > 0.9, b1.pos[1] - b0.pos[1]


def test_box_box_collide_normal():
    """两盒水平重叠：法线应沿 x，且给出接触。"""
    a = Body(Box((0.5, 0.5)), pos=(0, 0))
    b = Body(Box((0.5, 0.5)), pos=(0.8, 0))
    m = collide(a, b)
    assert m is not None, "应检测到重叠"
    assert abs(abs(m["normal"][0]) - 1.0) < 0.2, m["normal"]
    assert len(m["points"]) >= 1


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
    print(f'[test_box2d_lite] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_BOX2D_LITE_TESTS_FAILED')
        return 1
    print('ALL_BOX2D_LITE_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
