# -*- coding: utf-8 -*-
"""「沙垒 / Sandward」逻辑测试套件（纯数学，零 GL、零窗口）。

运行::

    python -m tests.test_sandward

**钉死的性质**（断言数值，不是"看起来对"）：

1. **场地合法**——brush 表规模/朝向/材质齐全，无退化盒；
2. **巡游路线可通行**——每个直线段全程无碰撞（半径余量按玩家半径）；
3. **auto-pilot 全程**——headless 巡完 5 检查点（顺序 C1..C5、限时）；
4. **碰撞与滑动**——撞墙不穿透、沿墙滑动（分轴）；
5. **UV 规则接线**——几何里每面的贴图密度 == 其材质档位（3.0/4.0/4.5/5.0/5.5 米，
   = CS2 实测档 ÷32 等比缩放），
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


def test_geometry_tangents():
    """§19 Step 2：切线 = 投影 ∇u 方向（用实际 UV 数值反解梯度做独立验证）。"""
    geo = build_geometry()
    pos, uv = geo['positions'], geo['uvs']
    tan, hands = geo['tangents'], geo['hands']
    assert len(tan) == len(pos), 'tangents 应为逐顶点 3 分量'
    assert len(hands) == geo['vertex_count'], 'hands 应为逐顶点 1 分量'
    nquad = len(geo['indices']) // 6
    worst_t = worst_b = 0.0
    for f in range(nquad):
        o = f * 4
        p0 = pos[o * 3:o * 3 + 3]
        p1 = pos[(o + 1) * 3:(o + 1) * 3 + 3]
        p2 = pos[(o + 2) * 3:(o + 2) * 3 + 3]
        t0 = uv[o * 2:o * 2 + 2]
        t1 = uv[(o + 1) * 2:(o + 1) * 2 + 2]
        t2 = uv[(o + 2) * 2:(o + 2) * 2 + 2]
        # 由实际 UV 数值反解 ∇u/∇v（Gram 2×2；与 source2_survey 同法）
        e1 = [p1[i] - p0[i] for i in range(3)]
        e2 = [p2[i] - p0[i] for i in range(3)]
        g11 = sum(a * a for a in e1)
        g12 = sum(a * b for a, b in zip(e1, e2))
        g22 = sum(a * a for a in e2)
        det = g11 * g22 - g12 * g12
        assert abs(det) > 1e-9, f'面 {f} 退化'

        def grad(d1, d2):
            a = (d1 * g22 - g12 * d2) / det
            b = (g11 * d2 - g12 * d1) / det
            return [a * e1[i] + b * e2[i] for i in range(3)]

        gu = grad(t1[0] - t0[0], t2[0] - t0[0])
        gv = grad(t1[1] - t0[1], t2[1] - t0[1])
        lu = math.sqrt(sum(c * c for c in gu))
        lv = math.sqrt(sum(c * c for c in gv))
        nu = [c / lu for c in gu]
        nv = [c / lv for c in gv]
        T = tan[o * 3:o * 3 + 3]
        h = hands[o]
        assert abs(h) == 1.0, f'面 {f} 手性非 ±1：{h}'
        worst_t = max(worst_t, max(abs(T[i] - nu[i]) for i in range(3)))
        fn = _FACES[f % 6][0]
        # B = cross(N, T) * hand == ∇v 方向
        B = [h * (fn[1] * T[2] - fn[2] * T[1]),
             h * (fn[2] * T[0] - fn[0] * T[2]),
             h * (fn[0] * T[1] - fn[1] * T[0])]
        worst_b = max(worst_b, max(abs(B[i] - nv[i]) for i in range(3)))
        # 四个顶点切线一致 + 切线在面内（⊥ 法线）
        for k in range(4):
            Tk = tan[(o + k) * 3:(o + k) * 3 + 3]
            assert max(abs(Tk[i] - T[i]) for i in range(3)) < 1e-12, \
                f'面 {f} 顶点 {k} 切线与首顶点不一致'
        dot_n = sum(T[i] * fn[i] for i in range(3))
        assert abs(dot_n) < 1e-12, f'面 {f} 切线不在面内（T·N={dot_n}）'
    assert worst_t < 1e-9, f'T ≈ ∇u 最大偏差 {worst_t:.2e}'
    assert worst_b < 1e-9, f'B ≈ ∇v 最大偏差 {worst_b:.2e}'
    print(f'  ok: 切线=∇u / 手性使 B=∇v（{nquad} 面全部逐面验证，'
          f'偏差 <{max(worst_t, worst_b):.1e}）')


def _pom_reference(uv, vts, depth, height_fn, steps=12):
    """POM 参考实现（**与沙垒 shader 同式**：陡峭视差 + 步内线性插值）。

    抄自 ``presentation/sandward_scene.py`` 的 GLSL ``pom_uv``——它存在的意义
    是：① 日后改 shader 时本 py 版必须同步（不一样了 = 有一方漏改）；
    ② 让 POM 的数值性质可单测（GLSL 没法在 CI 里跑）。
    """
    layer_step = 1.0 / steps
    dz = max(vts[2], 0.22)
    delta = (vts[0] / dz * depth / steps, vts[1] / dz * depth / steps)
    layer = 0.0
    cur = uv
    h = height_fn(cur)
    for _ in range(steps):
        if layer >= h:
            break
        cur = (cur[0] - delta[0], cur[1] - delta[1])
        h = height_fn(cur)
        layer += layer_step
    h_prev = height_fn((cur[0] + delta[0], cur[1] + delta[1]))
    layer_prev = layer - layer_step
    denom = (h_prev - layer_prev) - (h - layer)
    if abs(denom) < 1e-4:
        w = 0.5
    else:
        w = max(0.0, min(1.0, (h_prev - layer_prev) / denom))
    # mix(cur + delta, cur, w) = (cur + delta)(1-w) + cur·w = cur + delta(1-w)
    return (cur[0] + delta[0] * (1.0 - w),
            cur[1] + delta[1] * (1.0 - w))


def test_pom_reference():
    """§19.7 验收 1：POM 参考实现数值性质。"""
    depth = 0.03
    flat_full = lambda _uv: 1.0        # noqa: E731  满高（表面无凹陷）
    flat_gone = lambda _uv: 0.0        # noqa: E731  谷底
    # ① 垂直视线：零位移（不论高度场）
    for hf in (flat_full, flat_gone):
        u, v = _pom_reference((3.7, 2.1), (0.0, 0.0, 1.0), depth, hf)
        assert abs(u - 3.7) < 1e-12 and abs(v - 2.1) < 1e-12, '垂直视线必须零偏移'
    # ② 谷底（h=0）：视线直达，零位移
    u, v = _pom_reference((3.7, 2.1), (0.6, 0.0, 0.8), depth, flat_gone)
    assert abs(u - 3.7) < 1e-12 and abs(v - 2.1) < 1e-12, 'h=0 应零位移'
    # ③ 斜视线 + 满高：位移朝 -vts.xy 方向（视线水平分量的反方向）
    u, v = _pom_reference((3.7, 2.1), (0.6, 0.0, 0.8), depth, flat_full)
    assert u < 3.7, 'u 应向视线水平分量的反方向（-x）位移'
    assert abs(v - 2.1) < 1e-12, '视线无 y 分量则 v 不动'
    # ④ 掠射 clamp：vts.z 极小时位移受 0.22 下限约束（不爆掉）
    u_s, _ = _pom_reference((3.7, 2.1), (0.99, 0.0, 0.02), depth, flat_full)
    u_c = _pom_reference((3.7, 2.1), (0.99, 0.0, 0.22), depth, flat_full)[0]
    assert abs(u_s - u_c) < 1e-12, '掠射角应被 clamp 到同一结果（穿帮防护）'
    max_shift = 0.99 / 0.22 * depth      # 12 步满载的位移上限（|vts.xy|/clamp*depth）
    assert 3.7 - u_s < max_shift + 1e-9, '位移不得超过步长上限'
    # ⑤ 半高平坦场（h=0.5）：位移量 ≈ 走完 0.5 层深对应的水平偏移
    #    = 0.5 · depth · |vts.xy|/vts.z（± 一个步长的插值余量）
    half = lambda _uv: 0.5             # noqa: E731
    u, v = _pom_reference((3.7, 2.1), (0.6, 0.0, 0.8), depth, half)
    expect = 0.5 * depth * (0.6 / 0.8)
    got = 3.7 - u
    assert abs(got - expect) < depth * 0.6 / 0.8 / 12 + 1e-9, \
        f'半高场位移 {got:.5f} 应 ≈ {expect:.5f}'
    assert abs(v - 2.1) < 1e-12, '视线无 y 分量则 v 不动'
    print('  ok: POM 参考实现（垂直/谷底零偏移、斜视反向位移、掠射 clamp、半高位移量）')


def main():
    print('test_arena_brushes_sane');   test_arena_brushes_sane()
    print('test_route_clear');          test_route_clear()
    print('test_tour_completes');       test_tour_completes()
    print('test_collision_and_slide');  test_collision_and_slide()
    print('test_geometry_uv_density');  test_geometry_uv_density()
    print('test_geometry_tangents');    test_geometry_tangents()
    print('test_pom_reference');        test_pom_reference()
    print('ALL_SANDWARD_TESTS_PASSED')


if __name__ == '__main__':
    main()
