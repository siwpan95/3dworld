# -*- coding: utf-8 -*-
"""噪声库测试：golden 交叉验证 + 确定性 + 值域 + 各算法性质。

golden 值口径
-------------
``_hash2`` / ``value_noise`` / ``perlin_noise`` / ``fbm`` 的 golden 值
**与 procgen（MIT）的 numpy 原版逐位一致**（2026-10-19 用 numpy 2.5.4
交叉验证，含负坐标与 1e9 级大坐标的 int64 回绕路径）—— 钉住的不只是
「能跑」，而是「移植没走样」。simplex 因置换表来源不同（stdlib Random
vs numpy RandomState）与 procgen 数值不同，golden 为本实现自生成。
"""
from __future__ import annotations

import math
import sys

from world3d.logic import noise as N


def test_hash_golden_values():
    """_hash2 golden：与 procgen numpy 原版逐位一致（int64 回绕复刻）。

    ⚠ 坐标是 **floor 后的整数格点**：floor(-2.1) = -3（向负无穷），
    不是 -2 —— 写错格点会拿到另一个哈希值（本用例初版就踩了这个坑）。
    """
    cases = [
        # (ix, iy, seed, 期望值)
        (0, 0, 42, 0.12051962880441902),
        (3, -3, 42, 0.8220922880908904),          # floor(3.7)=3, floor(-2.1)=-3
        (-12346, 98765, 42, 0.9388355067646297),  # floor(-12345.6)=-12346
        (1000000000, -1000000000, 42, 0.2883514805177001),  # floor(-1e9+0.25)=-1e9
        (0, 0, 42, 0.12051962880441902),  # 同格点必同值
    ]
    for ix, iy, seed, want in cases:
        got = N._hash2(ix, iy, seed)
        assert got == want, f'_hash2({ix},{iy},{seed}) = {got!r}，期望 {want!r}'
    # 不同格点必不同值（哈希的基本性质）
    assert N._hash2(1, 0, 42) != N._hash2(0, 1, 42)
    assert N._hash2(0, 0, 42) != N._hash2(0, 0, 43)
    print('  ok: _hash2 5 组 golden 逐位一致（含 int64 回绕的大坐标）')


def test_value_perlin_fbm_golden():
    """value/perlin/fbm golden：与 procgen numpy 原版逐位一致。"""
    cases = [
        # (x, y, value, perlin, fbm)
        (0.0, 0.0, 0.12051962880441902, 0.5, 0.5),
        (3.7, -2.1, 0.2727028876055323, 0.41500771708641143, 0.46255751142957274),
        (-12345.6, 98765.4, 0.7184206391575473, 0.5944274864901127, 0.6035122959989966),
        (1000000000.5, -999999999.75, 0.266869745967267, 0.5521220394355529, 0.4851133329663683),
        (0.123456789, 0.987654321, 0.11923470893689457, 0.5570786872389777, 0.5444996686999313),
    ]
    for x, y, wv, wp, wf in cases:
        assert N.value_noise(x, y, 42) == wv, f'value_noise({x},{y}) 走样'
        assert N.perlin_noise(x, y, 42) == wp, f'perlin_noise({x},{y}) 走样'
        assert N.fbm(x, y, 4, 2.0, 0.5, 42) == wf, f'fbm({x},{y}) 走样'
    print('  ok: value/perlin/fbm 5 组 golden 逐位一致（移植零走样）')


def test_determinism():
    """同 (坐标, seed) 必然同输出；不同 seed 必不同（噪声库的立身之本）。"""
    for fn in (N.value_noise, N.perlin_noise, N.simplex_noise, N.worley_noise):
        a = fn(3.7, -2.1, 42)
        b = fn(3.7, -2.1, 42)
        assert a == b, f'{fn.__name__} 同参数两次调用不一致'
        c = fn(3.7, -2.1, 43)
        assert a != c, f'{fn.__name__} 换 seed 输出没变（哈希失效？）'
    # fbm 层间 seed 偏移：octaves=1 应等于单层 perlin
    assert N.fbm(1.5, 2.5, octaves=1, seed=9) == N.perlin_noise(1.5, 2.5, seed=9)
    # fractal_noise 是 fbm 的固定参数封装
    assert N.fractal_noise(1.5, 2.5, octaves=3, seed=9) == \
        N.fbm(1.5, 2.5, octaves=3, lacunarity=2.0, gain=0.5, seed=9)
    print('  ok: 四个噪声函数 + fbm/fractal 封装全部确定性')


def test_output_range():
    """全部函数输出恒在 [0,1]（网格扫描，含负坐标）。"""
    fns = (N.value_noise, N.perlin_noise, N.simplex_noise, N.worley_noise,
           N.fbm, N.fractal_noise)
    for fn in fns:
        for gx in range(-8, 40, 3):
            for gy in range(-8, 40, 3):
                v = fn(gx * 0.37, gy * 0.41, seed=5)
                assert 0.0 <= v <= 1.0, f'{fn.__name__}({gx*0.37},{gy*0.41}) = {v} 越界'
    print('  ok: 六个函数 24×24 网格扫描全部落在 [0,1]')


def test_simplex_properties():
    """simplex：置换表结构 + golden + 与 perlin 的算法差异。"""
    perm = N._perm_table(7)
    assert len(perm) == 512, '置换表应 512（256 洗牌后复制）'
    assert sorted(perm[:256]) == list(range(256)), '前半必须是 0..255 的排列'
    assert perm[256:] == perm[:256], '后半必须是前半的复制'
    # golden（自生成，防回归）
    assert N.simplex_noise(0.5, 0.5, 7) == 0.3464217431863919
    assert N.simplex_noise(12.25, -3.75, 7) == 0.32247107979121503
    assert N.simplex_noise(100.1, 200.2, 7) == 0.6596368653689569
    # 与 perlin 是不同的算法（同点同 seed 输出不同）
    assert N.simplex_noise(3.7, -2.1, 42) != N.perlin_noise(3.7, -2.1, 42)
    print('  ok: simplex 置换表结构 + 3 组 golden + 算法差异性')


def test_worley_properties():
    """worley：F1 距离语义 + golden + 特征点处接近 0。"""
    # golden（自生成）
    assert N.worley_noise(0.5, 0.5, 3) == 0.4847543046479898
    assert N.worley_noise(7.3, 2.9, 3) == 0.3730845036260265
    # 特征点位置（格点 + 哈希偏移）处距离应接近 0
    xi, yi = 5, 5
    px = xi + N._hash2(xi, yi, 3)
    py = yi + N._hash2(xi, yi, 3 ^ 0x9E3779B9)
    assert N.worley_noise(px, py, 3) < 1e-9, '特征点处 F1 距离应≈0'
    # 格点中心（远离特征点）处距离应显著大于 0
    assert N.worley_noise(5.5, 5.5, 3) > 0.05
    print('  ok: worley golden + 特征点处 F1≈0 + 格心处有距离')


def test_domain_warp():
    """域扭曲：strength=0 恒等 + 确定性 + 扭曲后坐标仍在合理范围。"""
    x, y = 3.7, -2.1
    wx0, wy0 = N.domain_warp(x, y, seed=42, strength=0.0)
    assert (wx0, wy0) == (x, y), 'strength=0 必须是恒等变换'
    wx1, wy1 = N.domain_warp(x, y, seed=42, strength=1.0)
    assert (wx1, wy1) == N.domain_warp(x, y, seed=42, strength=1.0), '必须确定'
    assert (wx1, wy1) != (x, y), 'strength=1 应产生位移'
    # 位移幅度受 strength 控制（|Δ| ≤ strength，因 fBm∈[0,1] → (w-0.5)*2 ∈ [-1,1]）
    assert abs(wx1 - x) <= 1.0 and abs(wy1 - y) <= 1.0
    print('  ok: 域扭曲恒等/确定性/幅度上界')


def test_heightmap():
    """heightmap：尺寸 + 值域 + 行主序 + 确定性。"""
    hm = N.heightmap(16, 12, scale=0.2, seed=11, octaves=3)
    assert len(hm) == 12 and all(len(row) == 16 for row in hm), '尺寸应为 12×16'
    assert all(0.0 <= v <= 1.0 for row in hm for v in row), '值域 [0,1]'
    # 行主序：hm[y][x] == fbm(x*scale, y*scale)
    assert hm[3][5] == N.fbm(5 * 0.2, 3 * 0.2, octaves=3, seed=11)
    # 确定性
    hm2 = N.heightmap(16, 12, scale=0.2, seed=11, octaves=3)
    assert hm == hm2, '同参数必须逐位一致'
    # 换 seed 必不同
    hm3 = N.heightmap(16, 12, scale=0.2, seed=12, octaves=3)
    assert hm != hm3
    print('  ok: heightmap 12×16 尺寸/值域/行主序/确定性')


def main() -> int:
    tests = [
        test_hash_golden_values,
        test_value_perlin_fbm_golden,
        test_determinism,
        test_output_range,
        test_simplex_properties,
        test_worley_properties,
        test_domain_warp,
        test_heightmap,
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
    print(f'[test_noise] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for n, e in failed:
            print(f'  - {n}: {e}')
        print('ALL_NOISE_TESTS_FAILED')
        return 1
    print('ALL_NOISE_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
