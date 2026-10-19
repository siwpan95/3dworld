# -*- coding: utf-8 -*-
"""地牢/洞穴生成测试：连通性不变量 + 确定性 + 结构约束。

关键不变量（比「能跑」强得多的断言）：
- **BSP/随机房间地牢：所有房间经走廊互相可达**（洪水填充独立验证，
  不依赖生成器的 connections 字段 —— 防「记了连接但没挖通」）；
- **洞穴：输出单连通**（洪水填充后 regions 语义 + 最大块即全部地板）；
- 同 seed 逐位一致、换 seed 必不同。
"""
from __future__ import annotations

import sys

from world3d.logic.dungeon import (
    generate_cave, generate_dungeon, generate_dungeon_bsp, to_ascii,
)


def _flood_reachable(grid, sy, sx):
    """从 (sy,sx) 洪水填充，返回可达地板格集合。"""
    h, w = len(grid), len(grid[0])
    seen = set()
    stack = [(sy, sx)]
    while stack:
        y, x = stack.pop()
        if (y, x) in seen or not (0 <= y < h and 0 <= x < w):
            continue
        if grid[y][x] != 1:
            continue
        seen.add((y, x))
        stack.extend([(y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)])
    return seen


def _all_floor(grid):
    return {(y, x) for y, row in enumerate(grid)
            for x, v in enumerate(row) if v == 1}


def test_dungeon_structure():
    """随机房间地牢：房间不重叠、边界留墙、房间中心是地板。"""
    d = generate_dungeon(48, 32, rooms=8, seed=1)
    grid, rooms = d['grid'], d['rooms']
    assert len(grid) == 32 and len(grid[0]) == 48, '网格尺寸'
    assert d['placed'] >= 1, '至少放得下一间'
    # 房间互不重叠（含 1 格间隔）
    for i, a in enumerate(rooms):
        for b in rooms[i + 1:]:
            overlap = not (a['x'] + a['w'] < b['x'] - 1 or
                           a['x'] > b['x'] + b['w'] + 1 or
                           a['y'] + a['h'] < b['y'] - 1 or
                           a['y'] > b['y'] + b['h'] + 1)
            assert not overlap, f'房间 {i} 与另一间重叠'
        # 房间在边界内（留 1 格墙）
        assert a['x'] >= 1 and a['y'] >= 1
        assert a['x'] + a['w'] <= 47 and a['y'] + a['h'] <= 31
        # 房间中心是地板
        assert grid[a['cy']][a['cx']] == 1, '房间中心必须是地板'
    # 外框是墙
    assert all(v == 0 for v in grid[0]) and all(v == 0 for v in grid[-1])
    assert all(row[0] == 0 and row[-1] == 0 for row in grid)
    print(f"  ok: {d['placed']} 间房不重叠/居中/边界留墙")


def test_dungeon_connectivity():
    """随机房间地牢：所有房间经走廊互相可达（洪水填充独立验证）。"""
    for seed in (1, 7, 42):
        d = generate_dungeon(48, 32, rooms=8, seed=seed)
        rooms = d['rooms']
        if len(rooms) < 2:
            continue
        start = (rooms[0]['cy'], rooms[0]['cx'])
        reach = _flood_reachable(d['grid'], *start)
        for i, r in enumerate(rooms):
            assert (r['cy'], r['cx']) in reach, \
                f'seed={seed}: 房间 {i} 不可达（走廊没挖通？）'
    print('  ok: 三种子的所有房间互相可达（洪水填充验证）')


def test_bsp_connectivity():
    """BSP 地牢：构造保证连通 —— 洪水填充独立验证 + 房间在叶子内。"""
    for seed in (1, 7, 42):
        d = generate_dungeon_bsp(48, 32, seed=seed)
        rooms = d['rooms']
        assert len(rooms) >= 1, 'BSP 至少一间房'
        if len(rooms) >= 2:
            start = (rooms[0]['cy'], rooms[0]['cx'])
            reach = _flood_reachable(d['grid'], *start)
            for i, r in enumerate(rooms):
                assert (r['cy'], r['cx']) in reach, \
                    f'seed={seed}: BSP 房间 {i} 不可达'
        # 房间不重叠（BSP 叶子互不相交，房间在叶子内）
        for i, a in enumerate(rooms):
            for b in rooms[i + 1:]:
                overlap = not (a['x'] + a['w'] <= b['x'] or
                               b['x'] + b['w'] <= a['x'] or
                               a['y'] + a['h'] <= b['y'] or
                               b['y'] + b['h'] <= a['y'])
                assert not overlap, f'seed={seed}: BSP 房间 {i} 重叠'
    print('  ok: BSP 三种子连通 + 房间互不相交')


def test_cave_single_region():
    """洞穴：输出单连通（最大块即全部地板）+ 边界是墙 + 确定性。"""
    for seed in (1, 7, 42):
        c = generate_cave(48, 32, seed=seed)
        grid = c['grid']
        assert c['regions'] >= 1, '至少一个连通块'
        assert c['floor_count'] > 0, '有可走区域'
        # 地板集合 == 最大块（单连通）
        floor = _all_floor(grid)
        assert len(floor) == c['floor_count'], '地板数应等于最大块'
        start = next(iter(floor))
        reach = _flood_reachable(grid, *start)
        assert reach == floor, '洞穴必须单连通（洪水填充全覆盖）'
        # 边界是墙
        assert all(v == 0 for v in grid[0]) and all(v == 0 for v in grid[-1])
        assert all(row[0] == 0 and row[-1] == 0 for row in grid)
    print('  ok: 洞穴三种子单连通 + 边界墙')


def test_determinism():
    """同 seed 逐位一致；换 seed 必不同。"""
    for gen in (generate_dungeon, generate_dungeon_bsp, generate_cave):
        a = gen(48, 32, seed=5)
        b = gen(48, 32, seed=5)
        assert a['grid'] == b['grid'], f'{gen.__name__} 同 seed 不一致'
        c = gen(48, 32, seed=6)
        assert a['grid'] != c['grid'], f'{gen.__name__} 换 seed 没变化'
    print('  ok: 三个生成器同 seed 逐位一致 / 换 seed 不同')


def test_ascii_and_validation():
    """to_ascii 尺寸正确；非法尺寸抛 ValueError。"""
    d = generate_dungeon(24, 16, rooms=4, seed=1)
    art = to_ascii(d['grid'])
    lines = art.split('\n')
    assert len(lines) == 16 and all(len(l) == 24 for l in lines), 'ASCII 尺寸'
    assert set(art) <= {'#', '.', '\n'}, 'ASCII 只含墙/地/换行'
    for gen in (generate_dungeon, generate_dungeon_bsp, generate_cave):
        try:
            gen(4, 4, seed=1)
            raise AssertionError(f'{gen.__name__} 应拒绝 4x4')
        except ValueError:
            pass
    print('  ok: ASCII 尺寸/字符集 + 非法尺寸拒绝')


def main() -> int:
    tests = [
        test_dungeon_structure,
        test_dungeon_connectivity,
        test_bsp_connectivity,
        test_cave_single_region,
        test_determinism,
        test_ascii_and_validation,
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
    print(f'[test_dungeon] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for n, e in failed:
            print(f'  - {n}: {e}')
        print('ALL_DUNGEON_TESTS_FAILED')
        return 1
    print('ALL_DUNGEON_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
