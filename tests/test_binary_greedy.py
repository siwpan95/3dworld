# -*- coding: utf-8 -*-
"""二进制贪心网格化（binary_greedy_mesh / _binary_greedy_cover）的无窗口单测。

全部纯数据（不碰 GL），`python -m tests.test_binary_greedy` 直接跑。
聚合 runner 按 `  ok:` 行统计用例数（脚本尾部标记见 ``main``）。

借鉴来源：cgerikj 的 Rust `binary-greedy-meshing`（MIT / Apache-2.0 双许可）；
本实现为自研 numpy 版（uint64 位掩码 + quadtree 全覆盖合并），**不搬 Rust 的
chunk 布局**。详见 `world3d/engine/voxel_mesh.py` 的模块 docstring 与 §二台账。
"""

import sys

import numpy as np

from world3d.engine.voxel_mesh import (
    AO_BRIGHTNESS_MIN, AO_BRIGHTNESS_STEP, binary_greedy_mesh, culled_face_count,
    mesh_stats,
)


def _quad_count(mesh):
    return len(mesh.indices) // 6


def _quad_center(mesh, q):
    b = 4 * q
    acc = [0.0, 0.0, 0.0]
    for k in range(4):
        i = 3 * (b + k)
        for c in range(3):
            acc[c] += mesh.positions[i + c]
    return tuple(v / 4.0 for v in acc)


def _quad_normal(mesh, q):
    b = 4 * q
    return tuple(mesh.normals[3 * b:3 * b + 3])


def _quad_ao(mesh, q):
    """顶点色里解码出的 AO 档位（0..3，见 ao_brightness 的映射）。"""
    b = 4 * q
    return [int(round((mesh.colors[4 * (b + k)] - AO_BRIGHTNESS_MIN) / AO_BRIGHTNESS_STEP))
            for k in range(4)]


# ----------------------------------------------------------- 2D 覆盖不变式 --
def test_cover_is_exact_for_any_grid():
    """`_binary_greedy_cover` 对任意布尔网格都是**精确覆盖**：

    * 每个发出的矩形内部全是 True（不覆盖空气 → 不造假面）；
    * 每个 True 格恰好被覆盖一次（不漏面）；
    * 覆盖面积之和 == True 格数（完整 + 不重叠）。
    """
    rng = np.random.default_rng(1234)
    for _ in range(300):
        h = int(rng.integers(1, 41))
        w = int(rng.integers(1, 41))
        g = rng.integers(0, 2, size=(h, w)).astype(bool)
        from world3d.engine.voxel_mesh import _binary_greedy_cover
        rects = _binary_greedy_cover(g)
        cov = np.zeros_like(g, dtype=int)
        area = 0
        for (i0, j0, i1, j1) in rects:
            assert g[i0:i1, j0:j1].all(), f'矩形覆盖到空气: {(i0,j0,i1,j1)}'
            cov[i0:i1, j0:j1] += 1
            area += (i1 - i0) * (j1 - j0)
        assert (cov[g] == 1).all(), '有实心格没被覆盖'
        assert (cov[~g] == 0).all(), '有空气格被覆盖'
        assert area == int(g.sum()), f'面积 {area} != True 数 {int(g.sum())}'


def test_cover_solid_block_is_single_rect():
    """实心 16×16 → 一个矩形（2 的幂对齐，干净合并）。"""
    from world3d.engine.voxel_mesh import _binary_greedy_cover
    g = np.ones((16, 16), dtype=bool)
    rects = _binary_greedy_cover(g)
    assert len(rects) == 1, rects
    assert rects[0] == (0, 0, 16, 16), rects


def test_cover_single_cell_is_one_rect():
    """单个 True 格 → 一个 1×1 矩形。"""
    from world3d.engine.voxel_mesh import _binary_greedy_cover
    g = np.zeros((5, 5), dtype=bool)
    g[2, 3] = True
    rects = _binary_greedy_cover(g)
    assert rects == [(2, 3, 3, 4)], rects


# -------------------------------------------------------------- 3D 主入口 --
def test_solid_cube_n_is_six_quads():
    """任意实心 N³ → 6 个 quad（与三轴贪心一样干净）。"""
    for n in (1, 2, 3, 4, 8, 16):
        v = np.ones((n, n, n), dtype=bool)
        m = binary_greedy_mesh(v)
        assert _quad_count(m) == 6, (n, _quad_count(m))
        assert m.triangle_count == 12


def test_binary_quads_are_valid_surface():
    """随机体素：二进制贪心生成的面数**恒 ≤ 仅剔除不合并**的面数。

    这是合并算法的基本不变量——合并只会减少、不会增加面数；同时面积守恒
    （每层的矩形面积之和 == 该层面掩码 True 数），由 exact-cover 单测钉住。
    """
    rng = np.random.default_rng(7)
    for _ in range(8):
        v = rng.integers(0, 2, size=(16, 16, 16)).astype(bool)
        m = binary_greedy_mesh(v)
        cf = culled_face_count(v)
        q = _quad_count(m)
        assert q <= cf, f'合并反而多了面: {q} > {cf}'
        st = mesh_stats(m, v)
        # 合并率应在 [0, 1)（除非整块是实心 → 合并率极高但仍 <1 因 cf>6）
        assert 0.0 <= st['merged_ratio'] < 1.0, st


def test_binary_matches_greedy_face_count_on_solid():
    """实心块：二进制贪心与三轴贪心面数一致（都是 6）。"""
    from world3d.engine.voxel_mesh import greedy_mesh_3d
    v = np.ones((8, 8, 8), dtype=bool)
    mb = binary_greedy_mesh(v)
    mg = greedy_mesh_3d(v)
    assert _quad_count(mb) == _quad_count(mg) == 6


def test_normals_unit_and_axis_aligned():
    """每个 quad 的法线是单位向量、且平行于某一坐标轴（硬边、朝外）。"""
    rng = np.random.default_rng(99)
    v = rng.integers(0, 2, size=(6, 6, 6)).astype(bool)
    m = binary_greedy_mesh(v)
    seen = set()
    for q in range(_quad_count(m)):
        n = _quad_normal(m, q)
        assert abs(np.linalg.norm(n) - 1.0) < 1e-9, n
        assert sum(1 for c in n if abs(c) > 0.5) == 1, n
        seen.add(tuple(round(c) for c in n))
    assert len(seen) <= 6


def test_ao_brightness_levels():
    """AO 档位只能落在 0..3，对应亮度 {0.55, 0.70, 0.85, 1.00}。"""
    v = np.ones((4, 4, 4), dtype=bool)
    m = binary_greedy_mesh(v)
    for q in range(_quad_count(m)):
        for lvl in _quad_ao(m, q):
            assert 0 <= lvl <= 3, lvl
    # 全实心块：所有外表面角点毫无遮挡 → AO 满档 3、亮度 1.0
    for q in range(_quad_count(m)):
        assert _quad_ao(m, q) == [3, 3, 3, 3], _quad_ao(m, q)
        b = 4 * q
        assert all(abs(m.colors[4 * b + k] - 1.0) < 1e-9 for k in range(4))


def test_ao_off_disables_darkening():
    """``ao=False`` 时所有顶点亮度为 1.0（不做遮挡变暗）。"""
    v = np.ones((3, 3, 3), dtype=bool)
    m = binary_greedy_mesh(v, ao=False)
    for q in range(_quad_count(m)):
        b = 4 * q
        assert all(abs(m.colors[4 * b + k] - 1.0) < 1e-9 for k in range(4))


def test_ao_concave_corner_darker_than_open():
    """复用三轴贪心的探针体素：凹角 AO 严格小于开阔面 AO。"""
    v = np.zeros((4, 4, 4), dtype=bool)
    v[1, 1, 1] = True                     # 目标体素
    v[2, 2, 1] = True                     # side1
    v[2, 1, 0] = True                     # side2
    v[2, 2, 0] = True                     # 对角
    v[2, 1, 2] = True
    v[2, 2, 2] = True
    v[0, 0, 3] = True                     # 远处独立体素（开阔）
    m = binary_greedy_mesh(v)

    def _find(center, normal):
        for q in range(_quad_count(m)):
            c = _quad_center(m, q)
            n = _quad_normal(m, q)
            if all(abs(c[i] - center[i]) < 1e-6 for i in range(3)) and \
               all(abs(n[i] - normal[i]) < 1e-6 for i in range(3)):
                return q
        raise AssertionError(f'找不到 center={center} normal={normal}')

    q_target = _find((1.5, 2.0, 1.5), (0.0, 1.0, 0.0))
    q_open = _find((3.5, 1.0, 0.5), (0.0, 1.0, 0.0))
    ao_c = _quad_ao(m, q_target)
    ao_o = _quad_ao(m, q_open)
    assert ao_o == [3, 3, 3, 3], ao_o
    assert ao_c[1] == 0, f'凹角 AO 应为 0: {ao_c}'
    assert ao_c[1] < ao_o[0]


def test_empty_volume():
    """空数组 → 0 面。"""
    for v in (np.zeros((0, 3, 3), dtype=bool), np.zeros((2, 0, 2), dtype=bool), []):
        m = binary_greedy_mesh(v)
        assert _quad_count(m) == 0 and m.triangle_count == 0, _quad_count(m)


def test_array_index_order_y_z_x():
    """索引顺序钉死为 ``voxels[y][z][x]``：只放 voxels[1][2][3]，x∈[3,4) z∈[2,3)。"""
    v = [[[0] * 4 for _ in range(3)] for _ in range(2)]     # [y][z][x] = 2×3×4
    v[1][2][3] = 1
    m = binary_greedy_mesh(v)
    assert _quad_count(m) == 6, _quad_count(m)
    q = None
    for qq in range(_quad_count(m)):
        c = _quad_center(m, qq)
        n = _quad_normal(m, qq)
        if n[1] > 0.5 and abs(c[1] - 2.0) < 1e-6:
            q = qq
            break
    assert q is not None
    b = 4 * q
    xs = sorted({m.positions[3 * (b + k)] for k in range(4)})
    zs = sorted({m.positions[3 * (b + k) + 2] for k in range(4)})
    assert xs == [3.0, 4.0], xs
    assert zs == [2.0, 3.0], zs


def test_deterministic_output():
    """同输入两次输出逐位相等（含 AO 颜色与索引）。"""
    rng = np.random.RandomState(20261020)
    v = rng.rand(5, 6, 7) > 0.55
    a = binary_greedy_mesh(v, size=0.5, origin=(1.0, 2.0, 3.0))
    b = binary_greedy_mesh(v, size=0.5, origin=(1.0, 2.0, 3.0))
    assert a.positions == b.positions
    assert a.normals == b.normals
    assert a.uvs == b.uvs
    assert a.colors == b.colors
    assert a.indices == b.indices
    assert _quad_count(a) > 0


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
    print(f'[test_binary_greedy] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_BINARY_GREEDY_TESTS_FAILED')
        return 1
    print('ALL_BINARY_GREEDY_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
