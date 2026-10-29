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


# ---------------------------------------------------------------- 3D 噪声 --
def test_perlin3_lattice_and_golden():
    """3D 梯度噪声：**整数格点上恰好 0.5** + golden + 确定性。

    格点零交叉是梯度噪声的定义性质（角点处位移向量为零 ⇒ 点积为零）。
    值噪声在格点上给的是"那个格点的随机值"，不会恒等于 0.5 —— 所以这一条
    同时也是"这确实是梯度噪声、不是把值噪声改了个名字"的判据。
    """
    for p in ((0, 0, 0), (3, 5, -7), (-123, 456, -789), (10 ** 6, -2 * 10 ** 6, 3)):
        assert N.perlin3(*p, seed=7) == 0.5, f'格点 {p} 应恰好 0.5'
    # golden（**本实现自生成**：procgen 没有 3D 版，无法像 2D 那样跨仓对照）
    assert N.perlin3(0.5, 0.5, 0.5, seed=7) == 0.3125
    assert N.perlin3(3.7, -2.1, 1.3, seed=7) == 0.7508137459441659
    assert N.perlin3(-12.25, 7.75, 4.5, seed=7) == 0.2919788360595703
    assert N.fbm3(0.5, 0.5, 0.5, octaves=4, seed=7) == 0.4
    assert N.fbm3(3.7, -2.1, 1.3, octaves=4, seed=7) == 0.5432902797621109
    # 确定性 + 换 seed 必不同
    assert N.perlin3(1.5, 2.5, 3.5, seed=7) == N.perlin3(1.5, 2.5, 3.5, seed=7)
    assert N.perlin3(1.5, 2.5, 3.5, seed=7) != N.perlin3(1.5, 2.5, 3.5, seed=8)
    print('  ok: 4 个格点恒 0.5 + 5 组 golden + 确定性/换 seed')


def test_perlin3_period_is_seamless():
    """period 环绕：平移整数个周期后**数值不变**（"无缝可铺贴"的定义）。

    ⚠ 容差 1e-12 而不是 `==`：平移后小数部分要重算一次（``7.9-7`` 与
    ``-0.1+1`` 在浮点上不是同一个数），所以我们比对的是"同一个噪声场"，
    不是"同一条指令序列"。真的接缝错位是 O(0.1) 量级，这个容差拦得住。
    """
    period = 8
    for p in ((1.25, 2.5, 3.75), (-0.5, 7.9, 0.1), (3.7, -2.1, 1.3)):
        base = N.perlin3(*p, seed=3, period=period)
        for d in ((period, 0, 0), (0, period, 0), (0, 0, period),
                  (period, period, period), (-period, 0, 0)):
            got = N.perlin3(p[0] + d[0], p[1] + d[1], p[2] + d[2],
                            seed=3, period=period)
            assert abs(got - base) <= 1e-12, \
                f'{p} 平移 {d} 后不再相等：{got!r} vs {base!r}'
        # 对照组：不给 period 时，平移一个周期**必须**是不同的噪声
        if abs(N.perlin3(p[0] + period, p[1], p[2], seed=3) - base) < 1e-6:
            raise AssertionError(f'{p}: 不给 period 竟然也无缝（对照失败）')
    # fbm3 的每一层各自环绕（第 o 层周期 = period·lacunarity^o）⇒ 整体仍无缝
    f0 = N.fbm3(1.25, 2.5, 3.75, octaves=4, seed=3, period=4)
    f1 = N.fbm3(5.25, 2.5, 3.75, octaves=4, seed=3, period=4)
    assert abs(f0 - f1) <= 1e-12, f'fbm3 铺贴失效：{f0!r} vs {f1!r}'
    for bad in (-1, -8):
        try:
            N.perlin3(0.5, 0.5, 0.5, period=bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f'period={bad} 竟然通过')
    print('  ok: 5 种平移逐位相等（≤1e-12）+ 无 period 时必不同 + fbm3 铺贴 + 负 period 被拒')


def test_perlin3_range_and_continuity():
    """值域 [0,1]、**上界真的取到 1.0**、跨格界连续。

    上界那条别删：归一化常数写成 2（单角点的粗上界）画面就被压扁成灰糊，
    写成 0.75 会在极值处截断成纯白斑 —— 两条都不是崩溃，是"看起来有点闷"，
    没有断言根本发现不了。
    """
    # argmax / argmin：x 落格界、y/z 落格心，八角色棋盘梯度同号相加（穷举 16³ 格 × 11³ 内点找到）。
    # 两端都**恰好**触到 0 与 1 ⇒ 归一化常数 1.0 正好是理论最大幅度，既不压扁也不截断
    assert N.perlin3(-4.0, -2.5, -3.5, seed=7) == 1.0, '上界不再是 1.0（归一化走样）'
    assert N.perlin3(-3.5, -1.5, 6.0, seed=7) == 0.0, '下界不再是 0.0（归一化走样）'
    lo = 1.0
    for gx in range(-8, 40, 3):
        for gy in range(-8, 40, 3):
            for gz in range(-8, 40, 3):
                v = N.perlin3(gx * 0.37, gy * 0.41, gz * 0.29, seed=5)
                assert 0.0 <= v <= 1.0, f'perlin3({gx},{gy},{gz}) = {v} 越界'
                lo = min(lo, v)
    assert 0.05 < lo < 0.15, f'均匀网格的最小值 {lo:.4f} 超出预期区间（幅度或分布走样）'
    # 跨格界连续：格界两侧 ε 处的值必须几乎相等（fade 写错会在这里跳一下）
    eps = 1e-7
    for p in ((2.0, 1.5, 0.5), (0.0, 0.0, 0.0), (-3.0, 2.0, 5.0)):
        for axis in range(3):
            a, b = list(p), list(p)
            a[axis] -= eps
            b[axis] += eps
            d = abs(N.perlin3(*a, seed=9) - N.perlin3(*b, seed=9))
            assert d < 1e-5, f'{p} 沿轴 {axis} 跨格界跳变 {d:.2e}'
    print(f'  ok: 24³ 网格全在 [0,1]、两端恰好 0.0/1.0、9 处跨格界连续')


def test_fbm3_mean_and_octaves():
    """fbm3 的中心 ≈ 0.5 —— 体积雾"开关噪声不整体改浓度"的前提。

    着色器里把噪声当 ``1 + (n-0.5)·2·strength`` 用，这个式子的隐含假设就是
    ``n`` 的均值 0.5。均值要是 0.42，一开噪声整幅雾就淡 16%，而画面只是
    "好像变通透了"，谁也不会怀疑到均值头上。
    """
    vals = [N.fbm3(i * 0.31, j * 0.27, k * 0.23, octaves=4, seed=5)
            for i in range(24) for j in range(24) for k in range(6)]
    mean = sum(vals) / len(vals)
    assert abs(mean - 0.5) < 0.02, f'均值 {mean:.4f} 偏离 0.5（雾会随开关整体变浓/变淡）'
    assert all(0.0 <= v <= 1.0 for v in vals), 'fbm3 越界'
    for oct_n in (1, 2, 3, 4):
        vs = [N.fbm3(i * 0.19, j * 0.23, k * 0.11, octaves=oct_n, seed=5)
              for i in range(12) for j in range(12) for k in range(6)]
        m = sum(vs) / len(vs)
        assert abs(m - 0.5) < 0.03, f'octaves={oct_n} 均值 {m:.4f} 偏了'
    # octaves=1 必须就是单层 perlin3（层间 seed 偏移不能把第 0 层也顶掉）
    assert N.fbm3(1.5, 2.5, 3.5, octaves=1, seed=9) == \
        N.perlin3(1.5, 2.5, 3.5, seed=9)
    assert N.fbm3(1.5, 2.5, 3.5, octaves=2, seed=9) != \
        N.fbm3(1.5, 2.5, 3.5, octaves=1, seed=9)
    print(f'  ok: 体采样均值 {mean:.4f}（n=3456）；1~4 层均值都在 0.5±0.03；'
          f'octaves=1 退化为单层 perlin3')


# ------------------------------------------------- 四份实现的契约（不整合的凭据）--
def test_four_noise_impls_agree_on_contract():
    """四份噪声实现（noise / terrain / blocks / proctex）**该一致的部分**必须一致。

    为什么不合并成一份（2026-10-29 决定，记在这里免得下次又讨论一遍）：

    * ``logic/noise.py``  —— 通用库，2D+3D，与 procgen 逐位对齐（有 golden 对照）；
    * ``logic/terrain.py`` / ``logic/blocks.py`` —— 世界坐标 + scale，哈希各用一套常数，
      **没有周期诉求**（地图有限大）。它们的世界长相是既有 demo 的一部分；
    * ``engine/proctex.py`` —— ``(u,v)∈[0,1)²`` + **cells 计数**，周期是语义的一部分
      （REPEAT 必须无缝，连 `vein_scale` 都得取偶数）。坐标语义与上面三者根本不同。

    统一哈希 = 每个既有世界重排一遍（视觉全变），而它们本来没有"互相相等"的诉求。
    所以这里只钉**契约**：值域、确定性、``octaves=1`` 退化成单层、层数真的生效。
    数值本身**刻意不同** —— 最后一条断言就是这条决定的守卫（见下面的注释）。
    """
    from world3d.engine import proctex
    from world3d.logic import blocks, terrain

    # (名字, fbm 入口, 单层入口, 额外参数)
    impls = (
        ('noise', N.fbm, N.perlin_noise, dict(lacunarity=2.0, gain=0.5)),
        ('terrain', terrain._fbm, terrain._value_noise, {}),
        ('blocks', blocks.fbm, blocks._noise2, dict(lacunarity=2.0, gain=0.5)),
        ('proctex', proctex.fbm, proctex.value_noise, dict(cells=6)),
    )
    pts = ((0.5, 0.5), (3.7, -2.1), (-8.25, 15.75), (0.123, 0.987))
    values = {}
    for name, fbm_fn, one_fn, kw in impls:
        for x, y in pts:
            a = fbm_fn(x, y, seed=4, **kw) if name != 'terrain' else fbm_fn(x, y, 4)
            assert 0.0 <= a <= 1.0, f'{name}.fbm({x},{y}) = {a} 越界'
            b = fbm_fn(x, y, seed=4, **kw) if name != 'terrain' else fbm_fn(x, y, 4)
            assert a == b, f'{name}.fbm 不确定（同参两次不同）'
            values.setdefault(name, []).append(a)
        # octaves=1 必须**精确**退化成单层（层间 seed 偏移写成 seed+i 时 i=0 要等于 0）
        if name == 'terrain':
            assert fbm_fn(1.5, 2.5, 9, octaves=1) == one_fn(1.5, 2.5, 9)
            assert fbm_fn(1.5, 2.5, 9, octaves=2) != fbm_fn(1.5, 2.5, 9, octaves=1)
        elif name == 'proctex':
            assert fbm_fn(0.31, 0.62, octaves=1, cells=6, seed=9) == \
                one_fn(0.31, 0.62, cells_u=6, cells_v=6, seed=9)
            assert fbm_fn(0.31, 0.62, octaves=2, cells=6, seed=9) != \
                fbm_fn(0.31, 0.62, octaves=1, cells=6, seed=9)
        else:
            assert fbm_fn(1.5, 2.5, octaves=1, seed=9) == one_fn(1.5, 2.5, seed=9)
            assert fbm_fn(1.5, 2.5, octaves=2, seed=9) != \
                fbm_fn(1.5, 2.5, octaves=1, seed=9)
        # 换 seed 必变
        s8 = (fbm_fn(3.7, -2.1, seed=8, **kw) if name != 'terrain'
              else fbm_fn(3.7, -2.1, 8))
        assert s8 != values[name][1], f'{name}.fbm 换 seed 没变'

    # ⚠ **这条红了不一定是坏事**：说明有人把四份实现的哈希统一了。
    # 那是一次"所有既有世界重排"的变更 —— 请重录 demo 并更新本 docstring 的决定记录，
    # 而不是把断言删掉了事。
    same = [name for name in values if values[name][1] == values['noise'][1]]
    assert len(same) == 1, f'这些实现与 noise 数值相同（哈希被统一了？）：{same[1:]}'
    print('  ok: 4 份实现 值域/确定性/octaves=1 退化/换 seed 全一致；'
          f'同点数值刻意不同（noise={values["noise"][1]:.3f} '
          f'terrain={values["terrain"][1]:.3f} blocks={values["blocks"][1]:.3f} '
          f'proctex={values["proctex"][1]:.3f}）')


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
        test_perlin3_lattice_and_golden,
        test_perlin3_period_is_seamless,
        test_perlin3_range_and_continuity,
        test_fbm3_mean_and_octaves,
        test_four_noise_impls_agree_on_contract,
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
