# -*- coding: utf-8 -*-
"""实体纹理（solid texture）测试套件 —— `world3d/logic/solidtex.py`。

运行::

    python -m tests.test_solidtex

本套件盯住四件"看着像但其实是错的"的事：

1. **确定性 + 值域**：材质是纯函数，同坐标同参数必须逐位相同；五个通道都要落在
   0..1（PBR 里 `roughness=1.4` 不会报错，只会让材质看起来"莫名其妙地发白"）。
2. **五种材质真的不同**：名字换一换就返回同一个调色板是最容易发生的偷懒。
3. **没有 UV 就没有接缝**：这是实体纹理存在的**全部理由**。判据是"跨整数坐标
   连续"—— 贴图法在 u=1 回到 u=0 处必然跳变，空间函数不会。
4. **大理石真有脉**：只看值域的话，"一片均匀灰"也能满分通过。所以要数极值。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.logic import solidtex as S  # noqa: E402

#: 采样点：含负数、跨整数格点、大坐标（int64 回绕路径）
_POINTS = ((0.0, 0.0, 0.0), (1.3, 2.7, -0.4), (-5.5, 0.25, 8.75),
           (12.0, -3.0, 4.0), (100.5, -200.25, 33.125))

_CHANNELS = ('r', 'g', 'b', 'rough', 'metal')


def test_sample_deterministic_and_in_range():
    """纯函数：同参逐位相同；换 seed 必变；五通道都在 0..1。"""
    lo = {c: 1.0 for c in _CHANNELS}
    hi = {c: 0.0 for c in _CHANNELS}
    for kind in S.KINDS:
        for p in _POINTS:
            a = S.sample(*p, kind=kind, seed=7)
            b = S.sample(*p, kind=kind, seed=7)
            assert a == b, f'{kind} 在 {p} 两次采样不一致：{a} vs {b}'
            assert len(a) == 5, f'{kind} 应返回 5 个通道，实际 {len(a)}'
            for name, v in zip(_CHANNELS, a):
                assert 0.0 <= v <= 1.0, f'{kind} 的 {name} 通道 = {v} 越界'
                lo[name] = min(lo[name], v)
                hi[name] = max(hi[name], v)
    # 换 seed 至少要有一个通道变（哈希没生效的话五个通道会一模一样）
    assert S.sample(1.3, 2.7, -0.4, 'marble', 7) != S.sample(1.3, 2.7, -0.4, 'marble', 8)
    spread = '、'.join(f'{c}∈[{lo[c]:.2f},{hi[c]:.2f}]' for c in _CHANNELS)
    print(f'  ok: 5 种材质 × 5 个采样点确定性且值域合法（{spread}）')


def test_kinds_are_distinguishable():
    """五种材质是**不同的函数**，不是同一个调色板换名字。

    ⚠ 判据不能是"每个点都不同"：`moss` 在没被苔藓覆盖的地方**本来就该**等于
    裸岩（这是材质的语义，不是 bug）。所以按"函数是否相同"判：
    至少三分之一的采样点明显不同，且最像的那一对也要有 0.10 级的最大差。
    """
    names = list(S.KINDS)
    got = {(k, p): S.sample(*p, kind=k, seed=3) for k in names for p in _POINTS}
    weakest = (1.0, None)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            diffs = [max(abs(x - y) for x, y in zip(got[(names[i], p)], got[(names[j], p)]))
                     for p in _POINTS]
            spread = max(diffs)                       # 这一对"最不像"能差多少
            n_diff = sum(1 for d in diffs if d > 0.02)
            if spread < weakest[0]:
                weakest = (spread, (names[i], names[j], n_diff, len(diffs)))
            assert n_diff >= len(_POINTS) // 3, \
                f'{names[i]} 与 {names[j]} 在 {len(_POINTS)} 个点上只有 {n_diff} 个能区分'
    spread, (a, b, n_diff, total) = weakest
    assert spread > 0.10, f'最像的一对 {a}/{b} 最大差只有 {spread:.3f}（等同别名）'
    print(f'  ok: 10 对材质都能区分；最像的一对 {a}/{b} 最大差 {spread:.3f}'
          f'（{n_diff}/{total} 个点可辨）')


def test_no_uv_seam_across_integer_coordinates():
    """**没有 UV 就没有接缝**：跨整数坐标必须连续（贴图法在 u=1→0 处必然跳）。

    这是实体纹理存在的全部理由，所以要拿它当判据而不是当宣传语：
    在整数坐标两侧各取 ε，材质差必须随 ε 一起趋于 0。
    """
    eps = 1e-3
    worst = (0.0, None)
    for kind in S.KINDS:
        for base in (0.0, 1.0, -3.0, 7.0):
            for axis in range(3):
                a = [base, base + 0.5, base - 0.25]
                b = list(a)
                a[axis] -= eps
                b[axis] += eps
                ca = S.sample(*a, kind=kind, seed=5)
                cb = S.sample(*b, kind=kind, seed=5)
                d = max(abs(x - y) for x, y in zip(ca, cb))
                if d > worst[0]:
                    worst = (d, (kind, base, axis))
    assert worst[0] < 0.05, \
        f'{worst[1][0]} 在 x={worst[1][1]} 沿轴 {worst[1][2]} 处跳变 {worst[0]:.4f}（不连续）'
    print(f'  ok: 5 材质 × 4 个整数坐标 × 3 轴，ε={eps} 处最大通道跳变 {worst[0]:.4f}'
          f'（贴图法此处是 1.0 级跳变）')


def test_marble_has_veins_not_flat():
    """大理石必须"有脉"：沿线扫一遍要有多个极值，且明暗有落差。

    反例：如果脉纹退化成云团或均匀灰，值域断言照样过，只有数极值才拦得住。
    """
    n = 240
    lum = []
    for i in range(n):
        z = -2.0 + 4.0 * i / (n - 1)
        r, g, b, rough, metal = S.sample(0.3, 0.7, z, 'marble', 11)
        lum.append(0.299 * r + 0.587 * g + 0.114 * b)
    assert metal == 0.0, '大理石不该是金属'
    # 局部极值数（三阶差分符号翻转）
    extrema = 0
    for i in range(1, n - 1):
        if (lum[i] - lum[i - 1]) * (lum[i + 1] - lum[i]) < 0:
            extrema += 1
    lo, hi = min(lum), max(lum)
    assert extrema >= 6, f'沿 4 个单位只数到 {extrema} 个极值 —— 脉纹没成形（像云团）'
    assert hi - lo > 0.10, f'明暗落差只有 {hi - lo:.3f} —— 几乎是纯色'
    roughs = [S.sample(0.3, 0.7, -2.0 + 4.0 * i / 40, 'marble', 11)[3] for i in range(41)]
    assert min(roughs) != max(roughs), '脉纹处粗糙度应当跟着变（暗脉更哑）'
    print(f'  ok: 大理石沿 4 个单位 {extrema} 个极值、明暗落差 {hi - lo:.3f}、'
          f'粗糙度 {min(roughs):.2f}~{max(roughs):.2f}')


def test_vertex_colors_batch():
    """批量烘焙 == 逐点采样（顺序、长度、值域都要对得上）。"""
    pts = [(i * 0.37, i * 0.11 - 2.0, i * 0.53) for i in range(37)]
    cols = S.vertex_colors(pts, 'rock', seed=13)
    assert len(cols) == len(pts), f'顶点数不符：{len(cols)} vs {len(pts)}'
    for p, c in zip(pts, cols):
        r, g, b, _rough, _metal = S.sample(*p, kind='rock', seed=13)
        assert c == (r, g, b), f'{p} 的批量色与单点采样不一致：{c} vs {(r, g, b)}'
        assert all(0.0 <= v <= 1.0 for v in c), f'{p} 顶点色越界：{c}'
    assert S.vertex_colors([], 'rock') == [], '空输入应返回空列表'
    print(f'  ok: 37 个顶点批量色与单点采样逐位一致，值域合法，空输入返回空')


def test_bad_arguments_are_rejected():
    """坏参数要有明确报错（不然会静默返回一个"看起来正常"的材质）。"""
    for bad_kind in ('mabels', 'Marble', '', 'rock '):
        try:
            S.sample(0.0, 0.0, 0.0, bad_kind)
        except ValueError:
            pass
        else:
            raise AssertionError(f'未知材质 {bad_kind!r} 竟然通过')
    for bad_scale in (0.0, -1.0):
        try:
            S.sample(0.0, 0.0, 0.0, 'marble', scale=bad_scale)
        except ValueError:
            pass
        else:
            raise AssertionError(f'scale={bad_scale} 竟然通过')
    try:
        S.material_preview('marble', axis='xq')
    except ValueError:
        pass
    else:
        raise AssertionError('非法切片轴竟然通过')
    # 切片尺寸/长度
    prev = S.material_preview('moss', size=16, seed=2, axis='xz')
    assert len(prev) == 256 and all(len(c) == 3 for c in prev)
    print('  ok: 4 个未知材质 + 2 个非法 scale + 非法切片轴全部被拒；切片 16×16 长度正确')


def main() -> int:
    tests = [
        ('test_sample_deterministic_and_in_range', test_sample_deterministic_and_in_range),
        ('test_kinds_are_distinguishable', test_kinds_are_distinguishable),
        ('test_no_uv_seam_across_integer_coordinates',
         test_no_uv_seam_across_integer_coordinates),
        ('test_marble_has_veins_not_flat', test_marble_has_veins_not_flat),
        ('test_vertex_colors_batch', test_vertex_colors_batch),
        ('test_bad_arguments_are_rejected', test_bad_arguments_are_rejected),
    ]
    failed = []
    for name, fn in tests:
        try:
            print(name)
            fn()
        except AssertionError as exc:
            failed.append((name, exc))
            print(f'  FAIL: {exc}')
        except Exception as exc:                            # noqa: BLE001
            failed.append((name, exc))
            print(f'  ERROR: {type(exc).__name__}: {exc}')
    print('=' * 60)
    print(f'[test_solidtex] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for n, exc in failed:
            print(f'  - {n}: {exc}')
        print('ALL_SOLIDTEX_TESTS_FAILED')
        return 1
    print('ALL_SOLIDTEX_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
