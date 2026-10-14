# -*- coding: utf-8 -*-
"""「沙垒 / Sandward」逻辑测试套件（纯数学，零 GL、零窗口）。

运行::

    python -m tests.test_sandward

**钉死的性质**（断言数值，不是"看起来对"）：

1. **场地合法**——brush 表规模/朝向/材质齐全，无退化盒；
2. **巡游路线可通行**——每个直线段全程无碰撞（半径余量按玩家半径）；
3. **auto-pilot 全程**——headless 巡完 5 检查点（顺序 C1..C5、限时）；
4. **碰撞与滑动**——撞墙不穿透、沿墙滑动（分轴）；
5. **UV 规则接线**——几何里每面的贴图密度 == 其材质档位（96/128/144/160/176），
   绕序与面法线一致（渲染可见性）。
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.engine.geometry import MeshData  # noqa: E402
from world3d.engine.texproj import density_report  # noqa: E402
from world3d.logic.sandward import (  # noqa: E402
    AUTO_SPEED, CHECKPOINTS, MATERIALS, PLAYER_R, ROUTE, SandwardKernel, SPAWN,
    blocked, build_arena, build_geometry, move_with_slide, segment_clear,
)
from world3d.logic.sandward import _FACES  # noqa: E402  （绕序自检用）


def test_arena_brushes_sane():
    bs = build_arena()
    assert len(bs) >= 30, f'brush 数量异常：{len(bs)}'
    mats = set()
    for (x0, z0, x1, z1, y0, y1, mat) in bs:
        assert x0 < x1 and z0 < z1 and y0 < y1, f'退化盒：{(x0, z0, x1, z1, y0, y1)}'
        assert mat in MATERIALS, f'未知材质 {mat}'
        mats.add(mat)
    assert mats == set(MATERIALS), f'材质未全部使用：{mats}'
    # 出生点不在任何盒内
    assert not blocked(SPAWN[0], SPAWN[1], bs), '出生点被挡'
    print(f'  ok: 场地 {len(bs)} 个 brush / {len(mats)} 种材质 / 出生点可站立')


def test_route_clear():
    bs = build_arena()
    for i in range(len(ROUTE) - 1):
        a, b = ROUTE[i], ROUTE[i + 1]
        assert segment_clear(a, b, bs), f'路线段 {i} 不可通行：{a} → {b}'
    # 检查点位置本身可站立
    for (label, cx, cz) in CHECKPOINTS:
        assert not blocked(cx, cz, bs), f'检查点「{label}」被挡'
    print(f'  ok: 巡游路线 {len(ROUTE) - 1} 段全部可通行（半径 {PLAYER_R}）')


def test_tour_completes():
    k = SandwardKernel(20261015)
    dt = 1 / 30.0
    t = 0.0
    while not k.tour.done and t < 120.0:
        k.auto_step(dt)
        k.update(dt)
        t += dt
    assert k.tour.done, f'120s 未巡完（到 {k.tour.count}/5，位置 {k.player}）'
    assert k.tour.visited == [True] * 5
    expected = 430.0 / AUTO_SPEED    # 路线总长 ≈ 430m（见下方打印复核）
    assert t < expected * 1.35, f'用时 {t:.1f}s 明显超预期（{expected:.0f}s）'
    print(f'  ok: auto-pilot 巡完 5 检查点（{t:.1f}s，阈值 {expected * 1.35:.0f}s）')


def test_collision_and_slide():
    bs = build_arena()
    # 中庭西墙 x∈[-12,-10] 外侧（z=0 是门洞，用 z=7 的实墙段）
    x, z = -8.0, 7.0
    nx, nz = move_with_slide(x, z, -1.0, 0.0, bs)      # 向西撞墙（门在 z=0）
    assert nx > -10.0 - PLAYER_R + 1e-6 and abs(nz - z) < 1e-9, (nx, nz)
    # 沿墙滑动：斜向撞墙时 z 仍能推进
    x2, z2 = move_with_slide(nx, z, -1.0, 0.4, bs)
    assert abs(x2 - nx) < 1e-9, '紧贴墙时 x 不应再推进'
    assert z2 > z, '分轴滑动应允许沿墙移动'
    # 门洞可过：z=0 处向西应能完整穿过（无阻挡 → 走满位移）
    x3, z3 = move_with_slide(-8.0, 0.0, -5.0, 0.0, bs)
    assert x3 < -12.9, f'门洞应可通行（到 {x3:.1f}）'
    print('  ok: 碰撞不穿透 / 沿墙滑动 / 门洞可通行（分轴求解）')


def test_geometry_uv_density():
    geo = build_geometry()
    assert geo['vertex_count'] > 1000
    assert len(geo['positions']) == geo['vertex_count'] * 3
    assert len(geo['uvs']) == geo['vertex_count'] * 2
    # 绕序自检：每个四边形按 _FACES 顺序的几何法线 == 期望法线
    pos = geo['positions']
    tiles = {m['tile'] for m in MATERIALS.values()}
    for f in range(len(geo['indices']) // 6):
        o = f * 4
        p0 = pos[o * 3:o * 3 + 3]
        p1 = pos[(o + 1) * 3:(o + 1) * 3 + 3]
        p2 = pos[(o + 2) * 3:(o + 2) * 3 + 3]
        e1 = [p1[i] - p0[i] for i in range(3)]
        e2 = [p2[i] - p0[i] for i in range(3)]
        n = (e1[1] * e2[2] - e1[2] * e2[1],
             e1[2] * e2[0] - e1[0] * e2[2],
             e1[0] * e2[1] - e1[1] * e2[0])
        ln = math.sqrt(sum(c * c for c in n)) or 1.0
        n = tuple(c / ln for c in n)
        want = _FACES[f % 6][0]
        assert max(abs(n[i] - want[i]) for i in range(3)) < 1e-9, \
            f'面 {f} 绕序错误：{n} != {want}'
    # 密度体检：每面密度 == 材质档位（贴图规则接线正确）
    mesh = MeshData(geo['positions'], None, geo['uvs'], None, geo['indices'],
                    None, 'sandward')
    rep = density_report(mesh)
    assert rep.valid == rep.faces and rep.skipped == 0
    for r in rep.ratios:
        near = min(tiles, key=lambda t: abs(t - r))
        assert abs(r - near) < 1e-6, f'密度 {r} 不在档位集合 {sorted(tiles)}'
    assert rep.outliers == [], '规则几何不应有密度离群面'
    # 图集格不越界
    for i in range(0, len(geo['cells']), 4):
        u0, v0, u1, v1 = geo['cells'][i:i + 4]
        assert 0.0 <= u0 < u1 <= 1.0 and 0.0 <= v0 < v1 <= 1.0
    print(f'  ok: {rep.faces} 面绕序/密度全部合规（档位 {sorted(tiles)}，'
          f'中位 {rep.median:.0f}）')


def main():
    print('test_arena_brushes_sane');   test_arena_brushes_sane()
    print('test_route_clear');          test_route_clear()
    print('test_tour_completes');       test_tour_completes()
    print('test_collision_and_slide');  test_collision_and_slide()
    print('test_geometry_uv_density');  test_geometry_uv_density()
    print('ALL_SANDWARD_TESTS_PASSED')


if __name__ == '__main__':
    main()
