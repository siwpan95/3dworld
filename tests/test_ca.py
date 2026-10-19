# -*- coding: utf-8 -*-
"""元胞自动机引擎测试：注册表完整性 + 各规则族经典行为 + 确定性。

关键断言：
- **注册表 141 条**（142 条数据去重后 —— wolfram_150 在原 JSON 里
  重复，与原版 an-engine 的 141 口径一致）；
- **GoL 经典行为**（方块静物 / 闪烁器周期 2）—— 规则逻辑没走样的
  最直接证据；
- **确定性**：同 seed 演化逐位一致（含带随机性的规则：森林火灾/
  晶体生长），且**两个 Automaton 交错运行互不干扰**（深拷贝隔离，
  2026-10-19 修复的共享 rng 缺陷的回归保护）。
"""
from __future__ import annotations

import sys

from world3d.logic import ca
from world3d.logic.ca import CellularAutomaton


def test_registry_completeness():
    """注册表：141 条（去重后）、分类齐全、默认规则在册。"""
    assert ca.total_count == 141, f'期望 141 条，实际 {ca.total_count}'
    by_cat = ca.list_rules_by_category()
    assert by_cat.get('life') and len(by_cat['life']) == 63
    assert by_cat.get('complex') and len(by_cat['complex']) == 11
    assert by_cat.get('multi_state') and len(by_cat['multi_state']) == 9
    assert by_cat.get('continuous') and len(by_cat['continuous']) == 58
    assert 'game_of_life' in ca.RULES, '默认规则必须在册'
    assert ca.get_rule('nonexistent') is None
    assert ca.get_description('game_of_life'), '描述非空'
    assert ca.get_category('game_of_life') == 'life'
    print(f"  ok: 141 条规则（life 63 / complex 11 / multi_state 9 / continuous 58）")


def test_game_of_life_classics():
    """GoL 经典：方块是静物；闪烁器（3 连横）周期 2。"""
    g = CellularAutomaton(10, 10, rule_name='game_of_life', seed=1)
    g.clear()
    for y, x in ((4, 4), (4, 5), (5, 4), (5, 5)):
        g.set_cell(y, x, 1)
    before = [row[:] for row in g.grid]
    g.step()
    assert g.grid == before, '方块（block）必须是静物'

    g.clear()
    for x in (4, 5, 6):
        g.set_cell(5, x, 1)
    g1 = [row[:] for row in g.grid]
    g.step()
    g2 = [row[:] for row in g.grid]
    g.step()
    assert g.grid == g1 and g2 != g1, '闪烁器必须周期 2'
    print('  ok: GoL 方块静物 + 闪烁器周期 2')


def test_outer_totalistic_rule():
    """B/S 记号直查：HighLife（B36/S23）与 GoL（B3/S23）的差集行为。"""
    hl = ca.get_rule('highlife')
    assert hl.born == {3, 6} and hl.survive == {2, 3}
    gol = ca.get_rule('game_of_life')
    assert gol.born == {3} and gol.survive == {2, 3}
    # 6 邻居死格：GoL 不生，HighLife 生
    grid = [[0] * 5 for _ in range(5)]
    for y, x in ((1, 1), (1, 2), (1, 3), (2, 1), (2, 3), (3, 2)):
        grid[y][x] = 1
    nb = ca.count_neighbours(grid)
    assert nb[2][2] == 6
    assert gol.step(grid, nb)[2][2] == 0, 'GoL 6 邻居不生'
    assert hl.step(grid, nb)[2][2] == 1, 'HighLife 6 邻居生'
    print('  ok: B/S 记号直查 + HighLife/GoL 差集行为')


def test_wireworld_diode():
    """Wireworld：电子头→尾→导体的信号传播。"""
    w = CellularAutomaton(8, 12, rule_name='wireworld', seed=1)
    w.clear()
    w.set_cell(4, 1, 2)
    for x in range(2, 10):
        w.set_cell(4, x, 1)
    g0 = [row[:] for row in w.grid]
    w.step()
    g1 = [row[:] for row in w.grid]
    w.step()
    g2 = [row[:] for row in w.grid]
    assert g0[4][1] == 2 and g1[4][1] == 3, '头→尾'
    assert g2[4][1] == 1, '尾→导体'
    print('  ok: Wireworld 头→尾→导体信号传播')


def test_cyclic_and_1d():
    """Cyclic 推进 + Rule 90 单细胞分叉（Sierpinski 起点）。"""
    cy = CellularAutomaton(12, 12, rule_name='cyclic', seed=3)
    cy.clear()
    for y in range(12):
        for x in range(12):
            cy.set_cell(y, x, (x + y) % 14)
    before = [row[:] for row in cy.grid]
    cy.step()
    changed = sum(1 for y in range(12) for x in range(12)
                  if cy.grid[y][x] != before[y][x])
    assert changed > 0, 'Cyclic 应有格推进'

    e = CellularAutomaton(8, 16, rule_name='wolfram_90', seed=1)
    e.clear()
    e.grid = e.rule.init_grid(8, 16)
    e.step()
    assert e.grid[0].count(1) == 2, 'Rule 90 单细胞下一步应长两格'
    print('  ok: Cyclic 推进 + Rule 90 分叉')


def test_continuous_rules():
    """连续规则：Gray-Scott / Lenia / SmoothLife / BZ 演化且值域合法。"""
    for name in ('gray_scott_spots', 'lenia_orbium', 'smooth_life',
                 'bz_reaction'):
        obj = CellularAutomaton(24, 24, rule_name=name, seed=5)
        obj.step()
        vals = [v for row in obj.grid for v in row]
        assert all(0 <= v <= 255 for v in vals), f'{name} 值域越界'
        assert any(v > 0 for v in vals), f'{name} 应有非零输出'
    print('  ok: 四个连续规则演化 + 值域 [0,255]')


def test_sandpile_topple():
    """沙堆：临界格崩塌（中心降、四邻升）。"""
    sp = CellularAutomaton(6, 6, rule_name='sandpile', seed=1)
    sp.clear()
    sp.set_cell(2, 2, 3)
    sp.step()
    assert sp.grid[2][2] < 3, '临界格应崩塌'
    neighbors = (sp.grid[1][2] + sp.grid[3][2] +
                 sp.grid[2][1] + sp.grid[2][3])
    assert neighbors > 0, '四邻应收到沙粒'
    print('  ok: 沙堆崩塌（中心降、四邻升）')


def test_determinism_and_isolation():
    """确定性 + Automaton 间隔离（深拷贝回归保护）。

    两个同规则同 seed 的 Automaton **交错** step，结果必须与单独
    跑一致 —— 共享 rng 的缺陷会在这里现形（2026-10-19 实测踩中）。

    换 seed 检查用**首步**：crystal_growth 这类快速饱和的规则
    （16×16 第 2 步即全冻结）随机性只体现在第 1 步，多步后两种子
    网格相同是规则性质而非缺陷。
    """
    for name in ('forest_fire', 'crystal_growth', 'snowflake',
                 'hodgepodge', 'highlife'):
        solo = CellularAutomaton(16, 16, rule_name=name, seed=9)
        for _ in range(4):
            solo.step()
        a = CellularAutomaton(16, 16, rule_name=name, seed=9)
        b = CellularAutomaton(16, 16, rule_name=name, seed=9)
        for _ in range(4):
            a.step()
            b.step()
        assert a.grid == solo.grid, f'{name}: 交错运行与单独跑不一致'
        assert b.grid == solo.grid, f'{name}: 两个实例结果不一致'
        # 换 seed：首步必不同（含纯随机初始化差异）
        c1 = CellularAutomaton(16, 16, rule_name=name, seed=9)
        c2 = CellularAutomaton(16, 16, rule_name=name, seed=10)
        c1.step()
        c2.step()
        assert c1.grid != c2.grid, f'{name}: 换 seed 首步没变化'
    print('  ok: 五个规则（含四个带随机性）交错隔离 + 同 seed 一致 + 换 seed 首步不同')


def test_automaton_api():
    """Automaton API：clear / set_cell / toggle_cell / set_rule / 统计。"""
    g = CellularAutomaton(8, 8, rule_name='game_of_life', seed=1)
    g.clear()
    assert g.alive_count == 0
    g.set_cell(3, 3, 1)
    assert g.alive_count == 1 and g.state_count(1) == 1
    g.toggle_cell(3, 3)
    assert g.grid[3][3] == 0, 'toggle 应回零'
    g.set_cell(3, 3, 1)
    g.set_rule('highlife')
    assert g.rule.name == 'highlife' and g.alive_count == 0, '切规则应清网格'
    # 未知规则名：保持原规则（fail-quiet，与原版一致）
    g.set_rule('nonexistent')
    assert g.rule.name == 'highlife'
    print('  ok: clear/set/toggle/set_rule/统计 API')


def main() -> int:
    tests = [
        test_registry_completeness,
        test_game_of_life_classics,
        test_outer_totalistic_rule,
        test_wireworld_diode,
        test_cyclic_and_1d,
        test_continuous_rules,
        test_sandpile_topple,
        test_determinism_and_isolation,
        test_automaton_api,
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
    print(f'[test_ca] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for n, e in failed:
            print(f'  - {n}: {e}')
        print('ALL_CA_TESTS_FAILED')
        return 1
    print('ALL_CA_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
