# -*- coding: utf-8 -*-
"""FOV 投射测试：遮挡不变量 + 对称性 + 确定性。

关键不变量（比「能跑」强得多的断言）：
- **空地全可见**：无遮挡时半径内所有格可见，距离与实际平方距离一致；
- **墙后不可见**：一堵完整墙完全截断视线（墙本身可见）；
- **门缝透光**：墙上开一格，缝后有限区域可见；
- **L 形墙角不泄露**：shadowcasting 的经典正确性判据——内侧墙角
  后的对角格不可见（不算穿墙作弊）；
- **对称性**：从 A 看 B 与从 B 看 A 结果一致（对可见格集合）；
- **visible_through 与 compute_fov 语义一致**：射线判定可见的格
  必在 FOV 集合中。
"""
from __future__ import annotations

import sys

from world3d.logic.fov import (
    compute_fov, default_light_curve, light_level, visible_through,
)


def _grid_opaque(walls):
    """构造 opaque 回调：walls 为 (x, y) 集合。"""
    return lambda x, y: (x, y) in walls


def test_open_field():
    """空地：半径内全可见，距离 = 平方欧氏距离。"""
    vis = compute_fov(_grid_opaque(set()), (0, 0), 4)
    assert (0, 0) in vis and vis[(0, 0)] == 0
    assert (4, 0) in vis and vis[(4, 0)] == 16      # 半径边界含入
    assert (5, 0) not in vis, '半径外不可见'
    assert (3, 2) in vis and vis[(3, 2)] == 13
    assert (4, 4) not in vis, '32 > 16 超出平方半径'
    print('  ok: 空地全可见 + 平方距离正确')


def test_wall_blocks():
    """整墙截断：墙格本身可见，墙后一律不可见。"""
    wall = {(3, y) for y in range(-6, 7)}
    vis = compute_fov(_grid_opaque(wall), (0, 0), 8)
    assert (3, 0) in vis, '墙格本身可见'
    assert (4, 0) not in vis, '墙后正后方不可见'
    assert (4, 3) not in vis, '墙后偏移不可见'
    assert (6, -2) not in vis, '墙后远处不可见'
    print('  ok: 整墙截断（墙可见、墙后不可见）')


def test_door_slit():
    """门缝透光：墙上开一格，缝后锥形区域可见、两侧仍不可见。"""
    wall = {(3, y) for y in range(-6, 7)}
    wall.discard((3, 0))                            # 开一格门缝
    vis = compute_fov(_grid_opaque(wall), (0, 0), 8)
    assert (5, 0) in vis, '缝后正后方可见'
    assert (5, 1) in vis, '缝后斜向小范围可见（扇形展开）'
    assert (6, 3) not in vis, '缝的侧向大偏移仍被墙挡住'
    print('  ok: 门缝透光锥形展开')


def test_corner_leak():
    """拐角阴影：内凹拐角（竖墙接横墙）的背面区域不可见。"""
    wall = {(3, y) for y in range(0, 7)}       # 竖墙 x=3
    wall |= {(x, 3) for x in range(3, 8)}      # 横墙 y=3（在 (3,3) 相接）
    op = _grid_opaque(wall)
    vis = compute_fov(op, (0, 0), 10)
    assert (3, 0) in vis, '竖墙面可见'
    assert (3, 3) in vis, '拐角格本身可见'
    assert (5, 2) not in vis, '拐角下方横墙背面不可见'
    assert (6, 1) not in vis, '横墙背面深处不可见'
    assert (5, 4) not in vis, '拐角上方竖墙背面不可见'
    print('  ok: 拐角阴影不泄露')


def test_symmetry():
    """对称性：可见性互易（同遮挡语义下 A↔B 一致）。"""
    wall = {(2, 2), (2, 3), (2, 4), (3, 4)}
    op = _grid_opaque(wall)
    a = compute_fov(op, (0, 0), 9)
    b = compute_fov(op, (5, 5), 9)
    # 互易：p 可见于 A 的视野 ⇔ p 相对 A 无遮挡。对单格做互易判定。
    for p in [(4, 4), (5, 0), (0, 5), (4, 0)]:
        vis_from_a = p in a
        vis_back = visible_through(op, p[0], p[1], 0, 0)
        assert vis_from_a == vis_back, f'{p}: fov={vis_from_a} ray={vis_back}'
    print('  ok: FOV 与射线判定语义一致')


def test_visible_through():
    """visible_through：直线通、墙挡、端点是墙仍可达、距离上限。"""
    op = _grid_opaque({(2, 0)})
    assert visible_through(op, 0, 0, 4, 0) is False, '墙挡直线'
    assert visible_through(_grid_opaque({(2, 2)}), 0, 0, 4, 0) is True, \
        '旁边有墙不影响直线'
    assert visible_through(_grid_opaque({(4, 0)}), 0, 0, 4, 0) is True, \
        '端点是墙仍可达（看得见墙）'
    assert visible_through(_grid_opaque(set()), 0, 0, 100, 0,
                           max_dist=50) is False, '超距离上限判不可见'
    print('  ok: 射线判定四态')


def test_light_curve():
    """光照衰减：单调递减、端点正确、半径外为 0。"""
    assert light_level(0, 8) == 1.0
    assert light_level(8, 8) == 0.0
    assert light_level(9, 8) == 0.0, '半径外为 0'
    r8 = [light_level(d, 8) for d in range(9)]
    assert all(a > b for a, b in zip(r8, r8[1:])), '严格单调递减'
    assert 0.0 < default_light_curve(0.5) < 1.0
    print('  ok: 衰减曲线单调 + 端点')


def test_determinism():
    """确定性：同输入两次结果逐位一致。"""
    wall = {(3, 1), (3, 2), (4, 4)}
    op = _grid_opaque(wall)
    a = compute_fov(op, (0, 0), 10)
    b = compute_fov(op, (0, 0), 10)
    assert a == b
    print('  ok: 同输入逐位一致')


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            print(f'[{name}]')
            fn()
        except Exception as exc:                       # noqa: BLE001
            failed.append((name, exc))
    print(f'[test_fov] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for n, e in failed:
            print(f'  - {n}: {e}')
        print('ALL_FOV_TESTS_FAILED')
        return 1
    print('ALL_FOV_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
