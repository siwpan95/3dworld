"""CDLOD 地形测试：选层无缝/无重叠、层间差 ≤1、morph 消缝、面数亚线性。

全部无窗口（terrain_lod 不碰 GL），`python -m tests.test_terrain_lod` 直接跑。
"""

import math

import numpy as np

from world3d.engine.geometry import heightfield
from world3d.engine.terrain_lod import LodConfig, LodNode, TerrainLod, MORPH_GLSL


def _hm(n=65, seed=3):
    rng = np.random.RandomState(seed)
    r = np.linspace(-1, 1, n)[:, None]
    c = np.linspace(-1, 1, n)[None, :]
    base = 0.5 + 0.15 * np.sin(3 * c) * np.cos(2 * r)
    return (base + 0.05 * rng.rand(n, n)).astype(np.float32)


def _terrain(**kw):
    cfg = LodConfig(grid=8, levels=5, leaf_size=16.0, leaf_range=24.0)
    args = dict(heightmap=_hm(), size=cfg.root_size, height_scale=180.0, config=cfg)
    args.update(kw)
    return TerrainLod(**args)


def test_config_validation():
    for bad in (dict(grid=1), dict(grid=7), dict(levels=0), dict(levels=13),
                dict(leaf_size=0), dict(leaf_range=-1), dict(morph_start=1.0)):
        try:
            LodConfig(**bad)
        except ValueError:
            continue
        raise AssertionError(f"非法参数未报错: {bad}")


def test_config_derived_values():
    cfg = LodConfig(grid=8, levels=4, leaf_size=16.0, leaf_range=24.0)
    assert cfg.root_size == 16.0 * 8
    assert cfg.cell_size == 2.0
    assert cfg.range_of(0) == 24.0 and cfg.range_of(2) == 96.0
    assert cfg.size_of(1) == 32.0


def test_sampling_matches_heightfield_builder():
    """terrain_lod 的采样与 geometry.heightfield 的顶点必须落在同一高度上。"""
    hm = _hm(33)
    size, hs = 64.0, 180.0
    t = TerrainLod(hm, size=size, height_scale=hs,
                   config=LodConfig(grid=8, levels=2, leaf_size=32.0,
                                    leaf_range=48.0))
    md = heightfield(hm, width=size, depth=size, height=hs)
    # heightfield 的顶点顺序：行优先，行对应 +z
    rows = cols = hm.shape[0]
    for (r, c) in ((0, 0), (0, cols - 1), (rows - 1, 0), (rows - 1, cols - 1),
                   (rows // 2, cols // 2)):
        i = (r * cols + c) * 3
        x, y, z = md.positions[i:i + 3]
        assert abs(t.height_at(x, z) - y) < 1e-3, f"({r},{c}) 采样不一致"


def test_height_clamped_outside():
    t = _terrain()
    assert math.isfinite(t.height_at(-1e6, 1e6))
    assert abs(t.height_at(-1e6, 0.0) - t.height_at(-t.size / 2, 0.0)) < 1e-6


def test_selection_tiles_view_without_holes_or_overlap():
    t = _terrain()
    nodes = t.select(camera=(0.0, 0.0))
    rep = t.coverage_report(nodes)
    assert rep["holes"] == 0, f"选层有空洞: {rep}"
    assert rep["min"] == 1 and rep["max"] == 1, f"选层重叠或空洞: {rep}"


def test_neighbor_level_difference_at_most_one():
    t = _terrain()
    nodes = t.select(camera=(3.0, -5.0))
    eps = 1e-9
    for a in nodes:
        for b in nodes:
            if a is b:
                continue
            # 只检查"边相邻"（共享一条边，不只是一点）
            share_v = abs(a.x1 - b.x0) < eps or abs(b.x1 - a.x0) < eps
            share_h = abs(a.z1 - b.z0) < eps or abs(b.z1 - a.z0) < eps
            overlap_v = min(a.z1, b.z1) - max(a.z0, b.z0) > eps
            overlap_h = min(a.x1, b.x1) - max(a.x0, b.x0) > eps
            adjacent = (share_v and overlap_v) or (share_h and overlap_h)
            if adjacent:
                assert abs(a.level - b.level) <= 1, (
                    f"相邻层差 >1: L{a.level}({a.x0},{a.z0}) vs L{b.level}({b.x0},{b.z0})"
                )


def test_near_camera_uses_finer_level():
    t = _terrain()
    nodes = t.select(camera=(0.0, 0.0))
    levels = {n.level for n in nodes}
    assert 0 in levels, "相机脚下应是最高精度层"
    assert max(levels) >= 2, "远处应出现更粗的层"


def test_triangle_count_sublinear_in_view_range():
    t = _terrain()
    near = t.stats(t.select(camera=(0.0, 0.0), view_range=32.0))["triangles"]
    far = t.stats(t.select(camera=(0.0, 0.0), view_range=64.0))["triangles"]
    assert far > near
    # 视距翻倍（面积 4 倍），三角数增长应显著低于二次
    assert far < near * 3.2, f"LOD 没起作用: {near} -> {far}"


def test_triangle_budget_far_below_full_resolution():
    t = _terrain()
    nodes = t.select(camera=(0.0, 0.0))
    tris = t.stats(nodes)["triangles"]
    # 全分辨率（格距=最细格距）覆盖同面积的三角数
    full = (t.size / t.config.cell_size) ** 2 * 2
    # 256m 见方的地形、2m 最细格距：全分辨率 32768 三角，CDLOD 实得 ~5120（15.6%）。
    # 地形越大降幅越夸张（视距不变时三角数近似只随节点数增长）。
    assert tris < full * 0.2, f"LOD 三角数 {tris} 对比全分辨率 {full} 降幅不足"


def test_morph_factor_monotone_and_bounded():
    t = _terrain()
    rng = t.config.range_of(0)
    ks = [t.morph_factor(0, d) for d in np.linspace(0, rng * 1.2, 20)]
    assert ks[0] == 0.0 and ks[-1] == 1.0
    assert all(0.0 <= k <= 1.0 for k in ks)
    assert all(b >= a - 1e-9 for a, b in zip(ks, ks[1:])), "morph 应随距离单调增"


def test_morph_zero_keeps_grid_points():
    t = _terrain()
    node = LodNode(level=1, x0=0.0, z0=0.0, size=32.0, morph=0.0)
    pos, _, _ = t.node_grid(node, morph=0.0)
    cell = node.size / t.config.grid
    assert abs(pos[0] - node.x0) < 1e-9 and abs(pos[2] - node.z0) < 1e-9
    # 第二列顶点恰好在 x0 + cell
    assert abs(pos[3] - (node.x0 + cell)) < 1e-6


def test_morph_one_snaps_to_even_lattice():
    t = _terrain()
    node = LodNode(level=1, x0=8.0, z0=-8.0, size=32.0)
    pos, _, _ = t.node_grid(node, morph=1.0)
    cell = node.size / t.config.grid
    xs = {round(pos[i], 6) for i in range(0, len(pos), 3)}
    assert len(xs) == t.config.grid // 2 + 1, "morph=1 后 x 方向只应剩父层格点"
    for x in xs:
        assert abs((x - node.x0) / (2 * cell) - round((x - node.x0) / (2 * cell))) < 1e-6


def test_no_crack_between_adjacent_levels():
    """细层 morph=1 时，交界边上的顶点必须与粗层完全重合（无裂缝）。"""
    t = _terrain()
    g = t.config.grid
    coarse = LodNode(level=1, x0=0.0, z0=0.0, size=32.0)
    fine = LodNode(level=0, x0=32.0, z0=0.0, size=16.0)   # 贴在粗节点东侧
    cpos, _, _ = t.node_grid(coarse, morph=0.0)
    fpos, _, _ = t.node_grid(fine, morph=1.0)

    def edge_x(positions, x_target, node):
        pts = set()
        for i in range(0, len(positions), 3):
            if abs(positions[i] - x_target) < 1e-6:
                pts.add((round(positions[i + 2], 6), round(positions[i + 1], 6)))
        return pts

    coarse_edge = edge_x(cpos, coarse.x1, coarse)
    fine_edge = edge_x(fpos, fine.x0, fine)
    # 细层边上的点集（去重后）应全部落在粗层边上的点集里
    assert fine_edge, "细层交界边没有顶点"
    missing = {p for p in fine_edge if p not in coarse_edge}
    assert not missing, f"细层边上有 {len(missing)} 个点不在粗层边上（会裂开）"


def test_node_mesh_shape_and_bounds():
    t = _terrain()
    node = LodNode(level=2, x0=-16.0, z0=-16.0, size=64.0)
    md = t.build_node_mesh(node)
    g = t.config.grid
    assert md.vertex_count == (g + 1) ** 2
    assert md.triangle_count == g * g * 2
    xs = md.positions[0::3]
    zs = md.positions[2::3]
    assert min(xs) >= node.x0 - 1e-6 and max(xs) <= node.x1 + 1e-6
    assert min(zs) >= node.z0 - 1e-6 and max(zs) <= node.z1 + 1e-6
    assert len(md.normals) == len(md.positions)


def test_template_is_unit_grid():
    t = _terrain()
    md = t.build_template()
    g = t.config.grid
    assert md.vertex_count == (g + 1) ** 2
    assert md.triangle_count == g * g * 2
    assert max(md.positions[0::3]) == 1.0 and min(md.positions[1::3]) == 0.0


def test_selection_is_deterministic():
    t = _terrain()
    a = t.select(camera=(5.0, 7.0))
    b = t.select(camera=(5.0, 7.0))
    assert [(n.level, n.x0, n.z0, round(n.morph, 12)) for n in a] == \
           [(n.level, n.x0, n.z0, round(n.morph, 12)) for n in b]


def test_view_range_excludes_far_nodes():
    t = _terrain()
    nodes = t.select(camera=(0.0, 0.0), view_range=16.0)
    assert nodes, "近视野不该为空"
    for n in nodes:
        assert n.distance_to(0.0, 0.0) <= 16.0 + 1e-9


def test_glsl_snippet_present():
    assert "u_morph_k" in MORPH_GLSL and "snap_even" in MORPH_GLSL


def main() -> int:
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith('test_') and callable(fn)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            # 聚合 runner 按 '  ok:' 行统计用例数，格式不要改
            print(f'  ok: {name}')
        except AssertionError as exc:
            failed.append((name, exc))
            print(f'  FAIL: {name}: {exc}')
        except Exception as exc:                            # noqa: BLE001
            failed.append((name, exc))
            print(f'  ERROR: {type(exc).__name__}: {exc}')
    print('=' * 60)
    print(f'[test_terrain_lod] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_TERRAIN_LOD_TESTS_FAILED')
        return 1
    print('ALL_TERRAIN_LOD_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
