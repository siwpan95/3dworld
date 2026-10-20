# -*- coding: utf-8 -*-
"""heightfield builder 回归测试（纯 CPU，无 GL 窗口）。

``heightfield`` 是 2026-10-20 为《最后一百公里》短剧补进引擎的地形生成器
（此前只有纯平面的 ``ground_grid``）。本用例钉住四件事：拓扑计数、高程线性
映射、法线朝向（含斜坡倾斜方向）、以及 ``scene_build.validate`` 对二维 ``hm``
参数的放行与拦截。

法线朝向尤其关键：戈壁地形首版渲染发灰，排查链之一就是"法线是不是朝下"
（朝下会让 PBR 直接吃环境光而非太阳），所以这里直接断言平面法线为 +y。
"""
from __future__ import annotations

import math

from world3d.engine import geometry as G
from world3d.engine import scene_build as SB


def _flat(rows=4, cols=5, v=0.0):
    return [[v] * cols for _ in range(rows)]


def test_topology_counts():
    """顶点数 = rows*cols，索引数 = (rows-1)*(cols-1)*6（两个三角形/格子）。"""
    hm = _flat(4, 5)
    md = G.heightfield(hm, width=40.0, depth=30.0, height=6.0)
    assert md.vertex_count == 4 * 5, md.vertex_count
    assert len(md.positions) == 4 * 5 * 3
    assert len(md.indices) == (4 - 1) * (5 - 1) * 6, len(md.indices)
    assert len(md.uvs) == 4 * 5 * 2
    assert md.name == 'heightfield'
    print('  ok: 拓扑计数 %d 顶点 / %d 索引（=(r-1)(c-1)*6）'
          % (md.vertex_count, len(md.indices)))


def test_extents_and_uvs():
    """XZ 范围以原点为中心：x∈[-w/2,w/2]，z∈[-d/2,d/2]；UV ∈ [0,1]。"""
    hm = _flat(3, 3)
    md = G.heightfield(hm, width=40.0, depth=20.0, height=6.0)
    xs = md.positions[0::3]
    zs = md.positions[2::3]
    assert abs(min(xs) + 20.0) < 1e-9 and abs(max(xs) - 20.0) < 1e-9, (min(xs), max(xs))
    assert abs(min(zs) + 10.0) < 1e-9 and abs(max(zs) - 10.0) < 1e-9, (min(zs), max(zs))
    us, vs = md.uvs[0::2], md.uvs[1::2]
    assert abs(min(us) - 0.0) < 1e-9 and abs(max(us) - 1.0) < 1e-9
    assert abs(min(vs) - 0.0) < 1e-9 and abs(max(vs) - 1.0) < 1e-9
    print('  ok: 范围与 UV 居中归一（x±20 / z±10 / uv 0..1）')


def test_height_mapping():
    """y = y_base + hm * height：线性映射，常数高度图落到同一平面。"""
    hm = _flat(3, 3, v=0.5)
    md = G.heightfield(hm, height=10.0, y_base=2.0)
    ys = md.positions[1::3]
    assert all(abs(y - 7.0) < 1e-9 for y in ys), ys[:4]
    # 零图 → 严格贴 y_base
    md0 = G.heightfield(_flat(3, 3), height=10.0, y_base=2.0)
    assert all(abs(y - 2.0) < 1e-12 for y in md0.positions[1::3])
    print('  ok: y = y_base + hm*height（0.5*10+2 = 7）')


def test_flat_normals_point_up():
    """平面法线必须是 +y —— 朝下会让 PBR 只吃环境光（地形发灰的经典坑）。"""
    md = G.heightfield(_flat(4, 4), height=6.0)
    for i in range(0, len(md.normals), 3):
        nx, ny, nz = md.normals[i], md.normals[i + 1], md.normals[i + 2]
        assert abs(nx) < 1e-9 and abs(nz) < 1e-9, (nx, ny, nz)
        assert abs(ny - 1.0) < 1e-9, ny
    print('  ok: 平地法线全部 (0,1,0)（%d 个顶点）' % md.vertex_count)


def test_slope_normals_tilt():
    """沿 z 抬升的斜坡：法线仍朝上（ny>0），但向 -z 倾斜（nz<0）。"""
    rows, cols = 6, 4
    hm = [[r / (rows - 1) for _ in range(cols)] for r in range(rows)]
    md = G.heightfield(hm, width=10.0, depth=10.0, height=5.0)
    nzs = md.normals[2::3]
    nys = md.normals[1::3]
    assert all(ny > 0.0 for ny in nys), min(nys)          # 绝不朝下
    assert any(nz < -0.1 for nz in nzs), max(nzs)         # 确实向 -z 倾
    print('  ok: 斜坡法线 ny>0 且向 -z 倾斜（最陡 nz=%.3f）' % min(nzs))


def test_validate_accepts_grid():
    """validate 放行合规 hm，并真的试构建（统计到顶点/三角）。"""
    data = {'root': {'name': 'terrain',
                     'mesh': {'builder': 'heightfield',
                              'args': {'hm': [[0.0, 0.2], [0.1, 0.3]],
                                       'width': 20.0, 'depth': 20.0,
                                       'height': 4.0}}}}
    issues = SB.validate(data)
    errs = [i for i in issues if i[0] == 'error']
    assert not errs, errs
    # 末尾固定一条 ('stats', '$', {...})：必须真的构建过（顶点 ≥4 = 2x2 网格）
    stats = [i[2] for i in issues if i[0] == 'stats']
    assert stats and stats[-1]['vertices'] >= 4, stats
    print('  ok: validate 放行二维 hm（%s）' % stats[-1])


def test_validate_rejects_bad_grid():
    """ragged 行 / 非数字 / 单行三种坏 hm 都必须被拦成 error。"""
    bad = [
        [[0.0, 0.1], [0.2]],                 # 每行不等长（ragged）
        [[0.0, 0.1], [0.2, 'x']],            # 含非数字
        [[0.0, 0.1]],                        # 只有 1 行
    ]
    for i, hm in enumerate(bad):
        data = {'root': {'name': 'terrain',
                         'mesh': {'builder': 'heightfield', 'args': {'hm': hm}}}}
        issues = SB.validate(data)
        errs = [x for x in issues if x[0] == 'error']
        assert errs, f'坏 hm #{i} 竟被放行: {hm}'
        assert any('二维矩阵' in e[2] for e in errs), errs
    print('  ok: 3 种非法 hm 全部拦截（ragged / 非数字 / 单行）')


def main():
    print('test_topology_counts');            test_topology_counts()
    print('test_extents_and_uvs');            test_extents_and_uvs()
    print('test_height_mapping');             test_height_mapping()
    print('test_flat_normals_point_up');      test_flat_normals_point_up()
    print('test_slope_normals_tilt');         test_slope_normals_tilt()
    print('test_validate_accepts_grid');      test_validate_accepts_grid()
    print('test_validate_rejects_bad_grid');  test_validate_rejects_bad_grid()
    print('ALL_HEIGHTFIELD_TESTS_PASSED')


if __name__ == '__main__':
    main()
