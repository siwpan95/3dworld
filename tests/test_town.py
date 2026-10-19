# -*- coding: utf-8 -*-
"""城镇生成测试：结构完整性 + 坐标级确定性 + 几何有效性。

关键不变量：
- **ward 多边形有效**（≥3 顶点、面积 > 0）—— Voronoi 划区与建筑
  递归分割的产出必须仍是合法多边形；
- **坐标级确定性**：同 seed 两座城的每个顶点坐标逐位相同；
- **城墙/路网/建筑的结构语义**（有墙时有城门、路网非空等）。
"""
from __future__ import annotations

import json
import sys

from world3d.logic.town import generate_town


def _ser(o):
    """把 Pt/Polygon/Patch/嵌套结构序列化成可 JSON 比较的形态。"""
    if hasattr(o, 'x') and hasattr(o, 'y'):
        return (round(o.x, 9), round(o.y, 9))
    if hasattr(o, 'verts'):
        return [_ser(v) for v in o.verts]
    if hasattr(o, 'shape'):
        return {'shape': _ser(o.shape), 'ward': o.ward}
    if isinstance(o, (list, tuple)):
        return [_ser(i) for i in o]
    if isinstance(o, dict):
        return {k: _ser(v) for k, v in sorted(o.items())}
    return o


def _fp(t):
    return json.dumps(_ser(t), sort_keys=True)


def test_town_structure():
    """城镇结构：ward 数、类型合法、建筑多边形有效。"""
    t = generate_town(seed=42, n_wards=12)
    wards = t['wards']
    assert len(wards) == 12, 'ward 数应等于 n_wards'
    valid_types = {'Market', 'Castle', 'Craftsmen', 'Merchant', 'Slum',
                   'Patriciate', 'Administration', 'Military', 'Common',
                   'Park', 'Farm', 'Cathedral', 'GateWard'}
    n_bld = 0
    for w in wards:
        assert w['type'] in valid_types, f"未知 ward 类型: {w['type']}"
        poly = w['polygon']
        assert len(poly) >= 3, 'ward 多边形至少 3 顶点'
        assert poly.square > 0, 'ward 面积必须为正'
        for b in w['buildings']:
            n_bld += 1
            assert len(b) >= 3, '建筑多边形至少 3 顶点'
            assert b.square > 0, '建筑面积必须为正'
    assert n_bld > 0, '至少有一些建筑'
    print(f'  ok: 12 ward 类型合法 + {n_bld} 个建筑多边形全部有效')


def test_town_determinism():
    """坐标级确定性：同 seed 逐位一致；换 seed 必不同。"""
    a = generate_town(seed=42, n_wards=12)
    b = generate_town(seed=42, n_wards=12)
    assert _fp(a) == _fp(b), '同 seed 坐标不一致'
    c = generate_town(seed=43, n_wards=12)
    assert _fp(a) != _fp(c), '换 seed 坐标没变化'
    print('  ok: 坐标级确定性（同 seed 逐位 / 换 seed 不同）')


def test_town_walls_and_streets():
    """有城墙时：城门非空、塔楼存在、路网非空。"""
    t = generate_town(seed=42, n_wards=12)
    wall = t['wall']
    assert wall is not None, 'seed=42 应有城墙'
    assert len(wall['gates']) >= 1, '至少一个城门'
    assert len(wall['towers']) >= 1, '至少一座塔楼'
    assert len(wall['shape']) >= 3, '城墙是多边形'
    assert len(t['streets']) >= 1, '路网非空'
    # walled=False：无城墙也正常生成
    t2 = generate_town(seed=7, n_wards=8, walled=False)
    assert t2['wall'] is None
    assert len(t2['wards']) == 8
    print('  ok: 城墙/城门/塔楼/路网 + walled=False 通路')


def test_town_geometry_sanity():
    """几何健全性：bounds 包含所有 ward 顶点；坐标有限。"""
    t = generate_town(seed=11, n_wards=10)
    xs, ys = [], []
    for w in t['wards']:
        for v in w['polygon'].verts:
            assert v.x == v.x and abs(v.x) != float('inf'), '坐标必须有限'
            xs.append(v.x)
            ys.append(v.y)
    bx0, by0, bx1, by1 = t['bounds']
    assert bx0 <= min(xs) and bx1 >= max(xs), 'bounds 应包住 x'
    assert by0 <= min(ys) and by1 >= max(ys), 'bounds 应包住 y'
    # 中心在 bounds 内
    c = t['center']
    assert bx0 <= c.x <= bx1 and by0 <= c.y <= by1
    print('  ok: bounds 包含性 + 坐标有限 + 中心在界内')


def test_town_ward_distribution():
    """ward 分配：类型来自权重表、无未分配 ward。"""
    t = generate_town(seed=99, n_wards=15)
    types = [w['type'] for w in t['wards']]
    assert all(w['type'] for w in t['wards']), '不允许未分配 ward'
    # 权重表里 Craftsmen 高频 —— 15 区里应出现
    assert 'Craftsmen' in types, 'Craftsmen 应出现（权重表高频）'
    print(f'  ok: 15 ward 全部已分配（类型分布: {len(set(types))} 种）')


def main() -> int:
    tests = [
        test_town_structure,
        test_town_determinism,
        test_town_walls_and_streets,
        test_town_geometry_sanity,
        test_town_ward_distribution,
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
    print(f'[test_town] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for n, e in failed:
            print(f'  - {n}: {e}')
        print('ALL_TOWN_TESTS_FAILED')
        return 1
    print('ALL_TOWN_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
