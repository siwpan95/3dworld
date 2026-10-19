# -*- coding: utf-8 -*-
"""曲线库测试：Bézier（任意阶）/ Catmull-Rom（过控制点）/ 长度 / 切线 / 确定性。

全部纯函数、零随机：golden 值是**自生成且可手算复核**的精确值
（线性插值、二次 Bézier 中点公式），钉住「实现没走样」+ 不变量 + 确定性。
"""
from __future__ import annotations

import math

from world3d.logic import curves as C


def test_bezier_linear_golden():
    """线性（2 控制点）Bézier 在 t 处 == 线性插值。"""
    assert C.bezier_point(((0.0, 0.0), (10.0, 0.0)), 0.3) == (3.0, 0.0)
    assert C.bezier_point(((0.0, 0.0), (0.0, 5.0)), 0.5) == (0.0, 2.5)
    print('  ok: 线性 Bézier 插值 golden（t=0.3 / t=0.5）')


def test_bezier_quadratic_midpoint():
    """二次 Bézier 在 t=0.5 处 == 0.25·p0 + 0.5·p1 + 0.25·p2（手算复核）。"""
    p0, p1, p2 = (0.0, 0.0), (0.0, 10.0), (10.0, 10.0)
    got = C.bezier_point((p0, p1, p2), 0.5)
    want = (0.25 * p0[0] + 0.5 * p1[0] + 0.25 * p2[0],
            0.25 * p0[1] + 0.5 * p1[1] + 0.25 * p2[1])
    assert got == want, f'{got!r} != {want!r}'
    print('  ok: 二次 Bézier 中点公式 golden')


def test_bezier_endpoints():
    """Bézier 离散曲线首尾必为控制点（端点插值性质）。"""
    ctrl = ((0.0, 0.0), (4.0, 9.0), (10.0, 2.0), (12.0, 8.0))
    curve = C.bezier_curve(ctrl, segments=32)
    assert curve[0] == ctrl[0], '曲线起点必须是第一个控制点'
    assert curve[-1] == ctrl[-1], '曲线终点必须是最后一个控制点'
    assert len(curve) == 33, 'segments=32 应得到 33 个采样点'
    print('  ok: Bézier 曲线端点 == 控制点 + 采样数正确')


def test_bezier_3d():
    """Bézier 支持 3D 控制点，端点仍为控制点。"""
    ctrl = ((0.0, 0.0, 0.0), (1.0, 2.0, 3.0), (4.0, 0.0, -1.0))
    curve = C.bezier_curve(ctrl, segments=8)
    assert curve[0] == ctrl[0] and curve[-1] == ctrl[-1]
    # 中间点 z 取 0.5·p1.z = 1.5（线性插值退化段的两端点）——
    # 实际二次中点 z = 0.25*0+0.5*3+0.25*(-1)=1.25
    mid = C.bezier_point(ctrl, 0.5)
    assert mid[2] == 1.25, '二次 Bézier z 中点应为 1.25'
    print('  ok: 3D Bézier 端点 + z 中点 golden')


def test_bezier_tangent_horizontal():
    """水平线段的切线应为水平（y 分量≈0）。"""
    ctrl = ((0.0, 0.0), (10.0, 0.0))
    tan = C.bezier_tangent(ctrl, 0.5)
    assert abs(tan[1]) < 1e-9, '水平线切线 y 分量必须≈0'
    assert tan[0] > 0, '切线应指向 +x'
    print('  ok: 水平 Bézier 切线方向正确')


def test_catmull_rom_passes_through():
    """Catmull-Rom **必过所有控制点**（采样点含每个控制点本身，误差<1e-9）。"""
    ctrl = ((0.0, 0.0), (2.0, 3.0), (5.0, 1.0), (8.0, 4.0), (10.0, 0.0))
    curve = C.catmull_rom_spline(ctrl, segments_per_span=16)
    for p in ctrl:
        best = min(math.hypot(c[0] - p[0], c[1] - p[1]) for c in curve)
        assert best < 1e-9, f'控制点 {p} 未被样条穿过（最小距离 {best}）'
    print('  ok: Catmull-Rom 经过全部 5 个控制点')


def test_catmull_rom_length_monotonic():
    """采样更密 → 折线长度不减（收敛上界）。"""
    ctrl = ((0.0, 0.0), (1.0, 4.0), (3.0, -2.0), (6.0, 5.0), (9.0, 0.0))
    short = C.curve_length(C.catmull_rom_spline(ctrl, segments_per_span=2))
    long = C.curve_length(C.catmull_rom_spline(ctrl, segments_per_span=64))
    assert long >= short - 1e-9, '更密的采样不应让折线变短'
    print('  ok: Catmull-Rom 折线长度随采样不减（2 段 vs 64 段）')


def test_catmull_rom_tangent_straight():
    """直线控制点的切线应沿直线方向。"""
    ctrl = ((0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0))
    tan = C.catmull_rom_tangent_at(ctrl, 1)
    assert tan == (2.0, 0.0), f'直线中点切线应为 (2,0)，得到 {tan!r}'
    print('  ok: 直线 Catmull-Rom 切线方向 golden')


def test_determinism():
    """同参数必然同输出（纯函数、无随机）。"""
    ctrl = ((0.0, 0.0), (2.0, 3.0), (5.0, 1.0), (8.0, 4.0))
    a = C.catmull_rom_spline(ctrl, segments_per_span=10)
    b = C.catmull_rom_spline(ctrl, segments_per_span=10)
    assert a == b
    c1 = C.bezier_curve(((0.0, 0.0), (1.0, 2.0), (3.0, 1.0)), 20)
    c2 = C.bezier_curve(((0.0, 0.0), (1.0, 2.0), (3.0, 1.0)), 20)
    assert c1 == c2
    print('  ok: Bézier / Catmull-Rom 同参数逐位一致')


def main() -> int:
    tests = [
        test_bezier_linear_golden,
        test_bezier_quadratic_midpoint,
        test_bezier_endpoints,
        test_bezier_3d,
        test_bezier_tangent_horizontal,
        test_catmull_rom_passes_through,
        test_catmull_rom_length_monotonic,
        test_catmull_rom_tangent_straight,
        test_determinism,
    ]
    failed = []
    for t in tests:
        print(t.__name__)
        try:
            t()
        except Exception as e:
            failed.append((t.__name__, e))
            import traceback
            traceback.print_exc()
    print('=' * 60)
    print(f'[test_curves] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for n, e in failed:
            print(f'  - {n}: {e}')
        print('ALL_CURVES_TESTS_FAILED')
        return 1
    print('ALL_CURVES_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
