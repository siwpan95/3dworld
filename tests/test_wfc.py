# -*- coding: utf-8 -*-
"""WFC 测试：瓦片平铺求解 / 邻接不变量 / 矛盾处理 / 确定性。

核心正确性由 ``check_adjacency`` 独立核对（每对相邻瓦片边插槽相容）+ 全坍缩
不变量保障；矛盾瓦片组应返回 ``None``；同 seed 必同结果（确定性）。
"""
from __future__ import annotations

from world3d.logic import wfc as W


def _tile_a_only():
    """单瓦片、四边同插槽 → 任何网格合法且全同。"""
    return [W.Tile('S', ('0', '0', '0', '0'))]


def _two_homogeneous():
    """两个瓦片各自四边同插槽（A='0', B='1'）→ 仅同质网格合法。"""
    return [W.Tile('A', ('0', '0', '0', '0')),
            W.Tile('B', ('1', '1', '1', '1'))]


def _h_alternating():
    """水平强制交替：A.E='x'/W='y'，B.E='y'/W='x'；垂直 N/S 均为 '0'（自由）。

    水平：A 右邻须 W='x' → B.W='x' ⇒ A→B；B 右邻须 W='y' ⇒ A.W='y' ⇒ B→A。
    """
    return [W.Tile('A', ('0', 'x', '0', 'y')),
            W.Tile('B', ('0', 'y', '0', 'x'))]


def test_solve_trivial():
    """单瓦片 4×4 → 全 'S'，且邻接合法、全坍缩。"""
    grid = W.WFC(4, 4, _tile_a_only(), seed=1).solve()
    assert grid is not None
    assert grid == [['S'] * 4 for _ in range(4)], '单瓦片网格应全为 S'
    assert W.check_adjacency(grid, _tile_a_only())
    print('  ok: 单瓦片 4×4 全 S + 邻接合法')


def test_solve_adjacency_invariant():
    """多种子/尺寸：输出必全坍缩且每对相邻边插槽相容。"""
    tiles = _two_homogeneous()
    for seed in range(5):
        for (w, h) in ((3, 3), (5, 2), (2, 6)):
            grid = W.WFC(w, h, tiles, seed=seed).solve()
            assert grid is not None, f'seed={seed} {w}x{h} 应可解'
            # 全坍缩
            assert all(len(grid[y][x]) == 1 for y in range(h) for x in range(w))
            # 邻接不变量（核心正确性）
            assert W.check_adjacency(grid, tiles), f'seed={seed} 邻接不合法'
    print('  ok: 多种子/尺寸 全坍缩 + 邻接不变量')


def test_solve_full_collapse():
    """任意可解网格都必须完全坍缩（每格恰好一个瓦片）。"""
    grid = W.WFC(4, 4, _two_homogeneous(), seed=3).solve()
    for row in grid:
        for cell in row:
            assert isinstance(cell, str) and len(cell) == 1, '每格必须为单个瓦片名'
    print('  ok: 可解网格每格恰好一个瓦片')


def test_solve_determinism():
    """同 seed 必同网格。"""
    tiles = _two_homogeneous()
    a = W.WFC(5, 5, tiles, seed=7).solve()
    b = W.WFC(5, 5, tiles, seed=7).solve()
    assert a == b, '同 seed 必须逐位一致'
    print('  ok: 同 seed 网格逐位一致（确定性）')


def test_solve_unsolvable():
    """瓦片 E='1'/W='0'：任何瓦片都无法有水平邻居（左右皆空）→ 宽≥2 必无解。

    用两个**相同**构造的瓦片 A、B 组成瓦片组：单瓦片组因「无候选可坍缩」
    不会触发约束传播，无法暴露矛盾；引入第二个相同瓦片后每格候选数≥2，
    一旦坍缩即逼出邻居空集，整局重启，最终返回 None（矛盾处理路径）。
    """
    bad = [W.Tile('A', ('0', '1', '0', '0')),   # E='1' 但没有任何瓦片 W='1'
           W.Tile('B', ('0', '1', '0', '0'))]   # → 右/左邻皆不可满足
    grid = W.WFC(4, 4, bad, seed=0).solve()
    assert grid is None, '任何瓦片都无法有水平邻居，宽≥2 应判无解'
    print('  ok: 水平邻接不可满足的瓦片组 → None（矛盾处理）')


def test_solve_row_alternation():
    """水平交替瓦片组 2×1：输出必为相邻不同的两瓦片（[A,B] 或 [B,A]）。"""
    tiles = _h_alternating()
    grid = W.WFC(2, 1, tiles, seed=4).solve()
    assert grid is not None
    assert len(grid) == 1 and len(grid[0]) == 2
    a, b = grid[0]
    assert a != b, '水平交替瓦片组相邻两格必须不同'
    assert W.check_adjacency(grid, tiles), '相邻必须满足边插槽相容'
    print('  ok: 水平交替瓦片组 相邻不同 + 邻接合法')


def test_check_adjacency_rejects_bad():
    """校验器应拒绝刻意破坏的网格（独立正确性自检）。"""
    # A.E='1' 但 A.W='0'：并排两个 A 的右/左边不兼容
    tiles = [W.Tile('A', ('0', '1', '0', '0'))]
    bad_grid = [['A', 'A']]
    assert not W.check_adjacency(bad_grid, tiles), '并排 A 的 E≠邻W 应判不合法'
    good = [W.Tile('A', ('0', '0', '0', '0'))]
    assert W.check_adjacency([['A', 'A']], good), '四边同插槽应判合法'
    print('  ok: check_adjacency 拒绝非法 / 接受合法（自检）')


def main() -> int:
    tests = [
        test_solve_trivial,
        test_solve_adjacency_invariant,
        test_solve_full_collapse,
        test_solve_determinism,
        test_solve_unsolvable,
        test_solve_row_alternation,
        test_check_adjacency_rejects_bad,
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
    print(f'[test_wfc] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for n, e in failed:
            print(f'  - {n}: {e}')
        print('ALL_WFC_TESTS_FAILED')
        return 1
    print('ALL_WFC_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
