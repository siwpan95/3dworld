# -*- coding: utf-8 -*-
"""几何 clipmap（terrain_clipmap）的无窗口单测。

全部纯数据（不碰 GL），`python -m tests.test_terrain_clipmap` 直接跑。
聚合 runner 按 `  ok:` 行统计用例数（脚本尾部标记见 ``main``）。

借鉴来源：Losasso & Hoppe 2004《Geometry Clipmaps: Terrain Rendering Using
Nested Regular Grids》（论文算法，公开知识）；本实现按论文公式自写，不搬示例代码。
"""

import sys

import numpy as np

from world3d.engine.terrain_clipmap import (
    ClipmapConfig, ClipRing, build_clipmap, build_ring_mesh, clipmap_stats,
    displace, ring_cells,
)


def _cfg(**kw):
    return ClipmapConfig(**kw)


def test_layer_count_equals_config():
    """生成的 ring 数 == 配置层数。"""
    for levels in (1, 3, 5, 8):
        cfg = _cfg(levels=levels, grid=16)
        rings = build_clipmap((0.0, 0.0), cfg)
        assert len(rings) == levels, (levels, len(rings))
        assert [r.level for r in rings] == list(range(levels))


def test_cell_size_doubles_per_level():
    """第 l+1 层的格距 == 第 l 层的 2 倍（clipmap 的逐层变粗）。"""
    cfg = _cfg(levels=6, grid=32, base_cell=1.0)
    rings = build_clipmap((3.3, 7.7), cfg)
    for l in range(len(rings) - 1):
        assert abs(rings[l + 1].cell - 2.0 * rings[l].cell) < 1e-12


def test_level_extents_nest():
    """外层 ring 的世界跨度严格大于内层（同心嵌套）。"""
    cfg = _cfg(levels=5, grid=32, base_cell=2.0)
    rings = build_clipmap((0.0, 0.0), cfg)
    for l in range(len(rings) - 1):
        assert rings[l + 1].size > rings[l].size + 1e-9
        # 外层包围盒严格包含内层包围盒
        assert rings[l + 1].x0 <= rings[l].x0 - 1e-9
        assert rings[l + 1].x1 >= rings[l].x1 + 1e-9
        assert rings[l + 1].z0 <= rings[l].z0 - 1e-9
        assert rings[l + 1].z1 >= rings[l].z1 + 1e-9


def test_outer_ring_is_annulus():
    """内层（level 0）满铺；外层发射的单元数 < 满铺、且 > 0（是环形而非整块）。"""
    cfg = _cfg(levels=5, grid=16, base_cell=1.0)
    rings = build_clipmap((0.0, 0.0), cfg)
    full = (cfg.grid - 1) ** 2
    # level 0：洞为 0，发射全部单元
    assert len(ring_cells(rings[0])) == full, len(ring_cells(rings[0]))
    # 外层：annulus，单元数介于 (0, full) 之间
    for r in rings[1:]:
        n = len(ring_cells(r))
        assert 0 < n < full, (r.level, n, full)


def test_hole_aligned_with_inner_extent():
    """外层洞的半幅 == 内层整张网格的半幅（annulus 紧贴内层外缘）。"""
    cfg = _cfg(levels=4, grid=16, base_cell=1.0)
    rings = build_clipmap((0.0, 0.0), cfg)
    for l in range(1, len(rings)):
        expected = cfg.size_of(l - 1) * 0.5
        assert abs(rings[l].hole_half - expected) < 1e-9, (l, rings[l].hole_half, expected)


def test_lattice_vertex_count():
    """每个 ring 的 lattice 顶点数 == grid × grid（grid = 每边顶点数）。"""
    cfg = _cfg(levels=3, grid=24, base_cell=1.0)
    rings = build_clipmap((5.0, 5.0), cfg)
    for r in rings:
        lat = r.lattice()
        assert lat.shape == (cfg.grid, cfg.grid, 2), lat.shape


def test_displace_samples_height_function():
    """位移函数逐顶点采样高度：``h(x,z) = 2x + 3z`` 时结果与手算一致。"""
    cfg = _cfg(levels=2, grid=8, base_cell=1.0)
    rings = build_clipmap((0.0, 0.0), cfg)
    r = rings[0]

    def h(x, z):
        return 2.0 * x + 3.0 * z

    hs = displace(r, h)
    lat = r.lattice()
    expected = 2.0 * lat[..., 0] + 3.0 * lat[..., 1]
    assert np.allclose(hs, expected)


def test_displace_vectorized_height_function():
    """位移兼容「直接对 numpy 数组广播」的高度函数（不强制逐点调用）。"""
    cfg = _cfg(levels=2, grid=8, base_cell=1.0)
    rings = build_clipmap((0.0, 0.0), cfg)
    r = rings[0]
    hs = displace(r, lambda x, z: x * x + z)      # 接受数组、返回同形数组
    lat = r.lattice()
    assert np.allclose(hs, lat[..., 0] ** 2 + lat[..., 1])


def test_build_ring_mesh_topology():
    """ring mesh：顶点数 = grid^2，index 数是 annulus 单元数 × 6。"""
    cfg = _cfg(levels=3, grid=12, base_cell=1.0)
    rings = build_clipmap((0.0, 0.0), cfg)
    for r in rings:
        m = build_ring_mesh(r, lambda x, z: 0.0)
        n_cells = len(ring_cells(r))
        assert m.vertex_count == cfg.grid ** 2, m.vertex_count
        assert len(m.indices) == n_cells * 6, (r.level, len(m.indices), n_cells)
        assert m.triangle_count == n_cells * 2
        # y 全为高度（这里恒 0）
        ys = m.positions[1::3]
        assert np.allclose(ys, 0.0)


def test_ring_mesh_displacement_applied():
    """mesh 顶点的 y 坐标等于高度函数采样值（位移确实生效）。"""
    cfg = _cfg(levels=1, grid=8, base_cell=1.0)
    rings = build_clipmap((0.0, 0.0), cfg)
    r = rings[0]

    def h(x, z):
        return x - z

    m = build_ring_mesh(r, h)
    lat = r.lattice()
    # 逐个 vertex 核对 y
    idx = 0
    for rr in range(cfg.grid):
        for cc in range(cfg.grid):
            y = m.positions[3 * idx + 1]
            assert abs(y - (lat[rr, cc, 0] - lat[rr, cc, 1])) < 1e-9
            idx += 1


def test_rigid_grid_within_one_base_cell():
    """clipmap 的刚性不变式：相机在同一个「最细格点吸附桶」内移动，ring 原点/格点不变。

    这是几何 clipmap 区别于「自由漂浮 LOD」的关键——网格不动，只有高度数据滚动。
    （吸附桶以最细格点为中心、半幅 base_cell；取两个都落入桶内的相机即可。）
    """
    cfg = _cfg(levels=4, grid=16, base_cell=2.0)
    cam_a = (0.3, 0.3)          # 吸附到 (0.0, 0.0)
    cam_b = (-0.5, 0.9)         # round(-0.25)*2=0, round(0.45)*2=0 → 仍 (0.0, 0.0)
    ra = build_clipmap(cam_a, cfg)
    rb = build_clipmap(cam_b, cfg)
    for a, b in zip(ra, rb):
        assert a.center == b.center, (a.level, a.center, b.center)
        assert a.cell == b.cell
        assert a.size == b.size


def test_camera_moves_grid_by_cell():
    """跨过一个最细格点吸附边界后，ring 中心按 base_cell 的整数倍跳变（仍吸附）。"""
    cfg = _cfg(levels=4, grid=16, base_cell=2.0)
    cam_a = (0.0, 0.0)          # 吸附到 (0.0, 0.0)
    cam_b = (2.5, 0.0)          # round(1.25)*2 = 2.0 → (2.0, 0.0)
    ra = build_clipmap(cam_a, cfg)
    rb = build_clipmap(cam_b, cfg)
    for a, b in zip(ra, rb):
        diff = abs(b.center[0] - a.center[0])
        assert diff > 1e-9, (a.level, a.center, b.center)
        assert abs(diff / cfg.base_cell - round(diff / cfg.base_cell)) < 1e-9, \
            (a.level, a.center, b.center)


def test_stats_counts():
    """clipmap_stats：层数 / 顶点 / 三角 / 各层跨度。"""
    cfg = _cfg(levels=3, grid=16, base_cell=1.0)
    rings = build_clipmap((0.0, 0.0), cfg)
    st = clipmap_stats(rings)
    assert st['levels'] == 3
    assert st['vertices'] == 3 * cfg.grid ** 2
    assert st['triangles'] == 3 * (cfg.grid - 1) ** 2 * 2
    assert len(st['sizes']) == 3
    # 各层跨度逐层翻倍（base_cell 翻倍 → size 翻倍）
    assert abs(st['sizes'][1] / st['sizes'][0] - 2.0) < 1e-6


def test_invalid_config_rejected():
    """非法配置直接抛（grid<2 / levels<1 / base_cell<=0）。"""
    for kw in ({'grid': 1}, {'levels': 0}, {'base_cell': 0.0}):
        try:
            ClipmapConfig(**kw)
            raise AssertionError(f'应拒绝非法配置: {kw}')
        except ValueError:
            pass


def test_deterministic():
    """同相机、同配置两次生成完全一致。"""
    cfg = _cfg(levels=5, grid=20, base_cell=1.5)
    a = build_clipmap((4.2, -3.1), cfg)
    b = build_clipmap((4.2, -3.1), cfg)
    for x, y in zip(a, b):
        assert x.center == y.center and x.cell == y.cell and x.size == y.size \
            and x.hole_half == y.hole_half


def main() -> int:
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith('test_') and callable(fn)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f'  ok: {name}')
        except AssertionError as exc:
            failed.append((name, exc))
            print(f'  FAIL: {name}: {exc}')
        except Exception as exc:                            # noqa: BLE001
            failed.append((name, exc))
            print(f'  ERROR: {type(exc).__name__}: {exc}')
    print('=' * 60)
    print(f'[test_terrain_clipmap] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_TERRAIN_CLIPMAP_TESTS_FAILED')
        return 1
    print('ALL_TERRAIN_CLIPMAP_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
