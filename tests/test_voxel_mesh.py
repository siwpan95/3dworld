# -*- coding: utf-8 -*-
"""体素网格化 v2（全 3D 三轴贪心 + 逐顶点 AO）的无窗口单测。

全部纯数据（不碰 GL），`python -m tests.test_voxel_mesh` 直接跑。
聚合 runner 按 `  ok:` 行统计用例数（脚本尾部标记见 ``main``）。
"""

import sys

import numpy as np

from world3d.engine.voxel_mesh import (
    AO_BRIGHTNESS_MIN, AO_BRIGHTNESS_STEP, culled_face_count, greedy_mesh_3d,
    mesh_stats, naive_face_count,
)

# --------------------------------------------------------------- 小工具 --
# 约定（见 voxel_mesh 模块 docstring）：每个 quad 连续占 4 个顶点 c0..c3，
# 索引为 6 个；所以第 q 个 quad 的顶点索引是 4*q .. 4*q+3。


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


def _quad_local_indices(mesh, q):
    return [i - 4 * q for i in mesh.indices[6 * q:6 * q + 6]]


def _find_quad(mesh, center, normal, tol=1e-6):
    for q in range(_quad_count(mesh)):
        c = _quad_center(mesh, q)
        n = _quad_normal(mesh, q)
        if (all(abs(c[i] - center[i]) <= tol for i in range(3))
                and all(abs(n[i] - normal[i]) <= tol for i in range(3))):
            return q
    raise AssertionError(f'找不到中心={center} 法线={normal} 的 quad')


def _probe_volume():
    """「一个目标体素 + 顶面外侧 5 个挡块 + 一个远处的独立体素」的小构型。

    目标体素是 ``voxels[y=1][z=1][x=1]``；挡块都在 **y=2 这一层**（也就是它顶面
    朝外的那一层），所以只影响目标顶面 4 个角的 AO，不改变面的存在性::

        a0(2) —— a1(0)        a0 = 两边一挡、对角空
            顶面            a1 = 三面围死（凹角）
        a3(2) —— a2(0)        a2 = 两侧都挡 → 0
                              a3 = 完全开阔 → 3
    """
    v = np.zeros((4, 4, 4), dtype=bool)
    v[1, 1, 1] = True                     # 目标体素（y=1, z=1, x=1）
    v[2, 2, 1] = True                     # a1/a2 的 side1
    v[2, 1, 0] = True                     # a0/a1 的 side2
    v[2, 2, 0] = True                     # a0/a1 的对角
    v[2, 1, 2] = True                     # a2/a3 的 side2
    v[2, 2, 2] = True                     # a2 的 side1
    v[0, 0, 3] = True                     # 远处的独立体素（AO 全开阔；x=3 避开目标的对角）
    return v


# ---------------------------------------------------------------- 用例 --
def test_single_voxel_six_quads():
    """单个体素：6 面 / 12 三角；几何角点 8 个。"""
    v = np.zeros((1, 1, 1), dtype=bool)
    v[0, 0, 0] = True
    m = greedy_mesh_3d(v)
    st = mesh_stats(m)
    assert st['quads'] == 6, st
    assert st['triangles'] == 12, st
    # 顶点缓冲是「每 quad 4 个」= 24：面法线要硬边、AO 要逐角，顶点不能跨面共享。
    # 但位置去重后恰好是立方体的 8 个角点 —— 面没重复、没多用。
    assert st['vertices'] == 24, st
    corners = {(round(m.positions[i], 6), round(m.positions[i + 1], 6),
                round(m.positions[i + 2], 6))
               for i in range(0, len(m.positions), 3)}
    assert len(corners) == 8, f'角点数 {len(corners)} != 8'


def test_solid_2x2x2_block_six_quads():
    """2×2×2 实心：内部面全剔除，且大面被合并 —— 6 个 quad 而不是 24 个。"""
    v = np.ones((2, 2, 2), dtype=bool)
    m = greedy_mesh_3d(v)
    assert _quad_count(m) == 6, _quad_count(m)
    assert m.triangle_count == 12
    assert naive_face_count(v) == 48           # 不剔除：8 体素 × 6 面
    assert culled_face_count(v) == 24          # 剔除后：2×2×6
    # 每个 quad 都是 2×2 的大面（法线是单位向量，边长才说明合并了）
    for q in range(6):
        b = 4 * q
        p0 = m.positions[3 * b:3 * b + 3]
        p1 = m.positions[3 * (b + 1):3 * (b + 1) + 3]
        p3 = m.positions[3 * (b + 3):3 * (b + 3) + 3]
        for p in (p1, p3):
            edge = sum((p[k] - p0[k]) ** 2 for k in range(3)) ** 0.5
            assert abs(edge - 2.0) < 1e-9, f'quad {q} 边长 {edge} != 2'


def test_solid_box_4x3x2_six_quads_with_size_origin():
    """4×3×2 实心长方体：quads == 6、triangles == 12；顺带验 size/origin 的仿射。"""
    v = np.ones((4, 3, 2), dtype=bool)         # (ny, nz, nx)
    m = greedy_mesh_3d(v, size=2.0, origin=(-1.0, -2.0, -3.0))
    st = mesh_stats(m, v)
    assert st['quads'] == 6, st
    assert st['triangles'] == 12, st
    xs, ys, zs = m.positions[0::3], m.positions[1::3], m.positions[2::3]
    assert (min(xs), max(xs)) == (-1.0, -1.0 + 2 * 2.0)
    assert (min(ys), max(ys)) == (-2.0, -2.0 + 4 * 2.0)
    assert (min(zs), max(zs)) == (-3.0, -3.0 + 3 * 2.0)


def test_empty_volume():
    """空数组（含 0 维退化与纯空列表）：0 面。"""
    for v in (np.zeros((0, 3, 3), dtype=bool), np.zeros((2, 0, 2), dtype=bool), []):
        m = greedy_mesh_3d(v)
        st = mesh_stats(m, v)
        assert st['quads'] == 0 and st['triangles'] == 0 and st['vertices'] == 0, st
        assert culled_face_count(v) == 0
        assert naive_face_count(v) == 0


def test_thin_plate_8x1x8_six_quads():
    """薄板 8×1×8：顶/底各 1 quad、四周各 1 quad → 6 quads。"""
    v = np.ones((1, 8, 8), dtype=bool)          # 单层 y
    m = greedy_mesh_3d(v)
    assert _quad_count(m) == 6, _quad_count(m)
    normals = sorted(_quad_normal(m, q) for q in range(6))
    assert len(set(normals)) == 6, normals
    # 顶/底是 8×8 的大面，四个侧面是 8×1 的长条
    assert culled_face_count(v) == 2 * 64 + 4 * 8


def test_merged_ratio_16x16x16():
    """16³ 实心：相对"剔除但不合并"的合并率 > 0.98。"""
    v = np.ones((16, 16, 16), dtype=bool)
    m = greedy_mesh_3d(v)
    st = mesh_stats(m, v)
    assert st['quads'] == 6, st
    assert st['merged_ratio'] is not None
    assert st['merged_ratio'] > 0.98, st
    # 不剔除时 4096×6 = 24576 面；剔除后 6×256 = 1536 面；合并后 6
    assert naive_face_count(v) == 24576
    assert culled_face_count(v) == 1536
    assert abs(st['merged_ratio'] - (1.0 - 6 / 1536.0)) < 1e-12


def test_normals_agree_with_winding():
    """每个三角形的几何法线（右手法）与顶点法线同向 → 绕序确为"外部看逆时针"。"""
    v = np.zeros((4, 4, 4), dtype=bool)
    v[0:2, 0:2, 0:2] = True                     # 2³ 块
    v[3, 3, 0] = True                           # 孤立体素（六个朝向都露出来）
    m = greedy_mesh_3d(v)
    assert m.triangle_count > 0
    for t in range(m.triangle_count):
        i0, i1, i2 = m.indices[3 * t:3 * t + 3]
        p = [m.positions[3 * i:3 * i + 3] for i in (i0, i1, i2)]
        e1 = [p[1][k] - p[0][k] for k in range(3)]
        e2 = [p[2][k] - p[0][k] for k in range(3)]
        n = (e1[1] * e2[2] - e1[2] * e2[1],
             e1[2] * e2[0] - e1[0] * e2[2],
             e1[0] * e2[1] - e1[1] * e2[0])
        vn = m.normals[3 * i0:3 * i0 + 3]
        dot = sum(n[k] * vn[k] for k in range(3))
        assert dot > 0, f'三角 {t} 绕序与法线相反（dot={dot}）'


def test_hollow_shell_has_no_internal_faces():
    """挖空的壳：面数 == 外壳内外表面积之和，一个内部面都不生成。

    4³ 实心块挖掉中间的 2³ ⇒ 外表面 6×16 = 96、内腔 6×4 = 24，合计 120 个
    单位面；贪心合并后正好 12 个 quad（外 6 + 内 6，每面一块）。
    """
    v = np.ones((4, 4, 4), dtype=bool)
    v[1:3, 1:3, 1:3] = False
    m = greedy_mesh_3d(v)
    assert culled_face_count(v) == 120, culled_face_count(v)
    assert _quad_count(m) == 12, _quad_count(m)
    assert m.triangle_count == 24, m.triangle_count
    # 12 个 quad 的面积和也必须是 120（没有重叠、没有漏面）
    area = 0.0
    for q in range(12):
        b = 4 * q
        p = [m.positions[3 * (b + k):3 * (b + k) + 3] for k in range(4)]
        w = abs(p[1][0] - p[0][0]) + abs(p[1][1] - p[0][1]) + abs(p[1][2] - p[0][2])
        h = abs(p[3][0] - p[0][0]) + abs(p[3][1] - p[0][1]) + abs(p[3][2] - p[0][2])
        area += w * h
    assert abs(area - 120.0) < 1e-9, area


def test_ao_monotone_at_concave_corner():
    """三面围成的凹角处 AO 严格小于开阔面的 AO。"""
    v = _probe_volume()
    m = greedy_mesh_3d(v)
    q_target = _find_quad(m, (1.5, 2.0, 1.5), (0.0, 1.0, 0.0))
    q_open = _find_quad(m, (3.5, 1.0, 0.5), (0.0, 1.0, 0.0))
    ao_concave = _quad_ao(m, q_target)
    ao_open = _quad_ao(m, q_open)
    assert ao_open == [3, 3, 3, 3], f'开阔面 AO 应为满档: {ao_open}'
    assert ao_concave[1] == 0, f'凹角（三面围死）AO 应为 0: {ao_concave}'
    assert ao_concave[1] < ao_open[0], f'{ao_concave} vs {ao_open}'
    # 同一块面上，越靠凹角越暗
    assert ao_concave[1] < ao_concave[3]


def test_diagonal_flip_uses_other_diagonal():
    """``ao[0]+ao[3] > ao[1]+ao[2]`` 时必须换成另一条对角线（1-3）。"""
    v = _probe_volume()
    m = greedy_mesh_3d(v)
    q = _find_quad(m, (1.5, 2.0, 1.5), (0.0, 1.0, 0.0))
    ao = _quad_ao(m, q)
    assert ao[0] + ao[3] > ao[1] + ao[2], f'构型没造出各向异性: {ao}'
    local = _quad_local_indices(m, q)
    # 每个 quad 的第 3 个索引：默认对角线 0-2 → 2；翻转后 1-3 → 3
    assert local[2] == 3, f'该翻转却没翻转: {local}'
    assert set(local[:3]) == {1, 2, 3}, local          # 首个三角形走 1-3 对角线
    assert set(local[3:]) == {1, 0, 3}, local          # 第二个三角形补上剩下的角
    # 反例：AO 对称的 quad 必须保持默认对角线（否则上面的断言可能是"永远翻转"）
    q_open = _find_quad(m, (3.5, 1.0, 0.5), (0.0, 1.0, 0.0))
    ao_open = _quad_ao(m, q_open)
    assert ao_open[0] + ao_open[3] <= ao_open[1] + ao_open[2], ao_open
    assert _quad_local_indices(m, q_open)[2] == 2, _quad_local_indices(m, q_open)


def test_array_index_order_y_z_x():
    """索引顺序钉死为 ``voxels[y][z][x]``：只放 voxels[1][2][3]，x 范围必须是 [3,4)。"""
    v = [[[0] * 4 for _ in range(3)] for _ in range(2)]     # [y][z][x] = 2×3×4
    v[1][2][3] = 1
    m = greedy_mesh_3d(v)
    assert _quad_count(m) == 6, _quad_count(m)
    # 只看顶面（+y）：平面 y = 2（体素 y=1 的上表面），x∈[3,4]、z∈[2,3]
    q = _find_quad(m, (3.5, 2.0, 2.5), (0.0, 1.0, 0.0))
    b = 4 * q
    xs = sorted({m.positions[3 * (b + k)] for k in range(4)})
    zs = sorted({m.positions[3 * (b + k) + 2] for k in range(4)})
    ys = sorted({m.positions[3 * (b + k) + 1] for k in range(4)})
    assert xs == [3.0, 4.0], f'x 范围应来自 voxels[...][...][3]: {xs}'
    assert zs == [2.0, 3.0], f'z 范围应来自 voxels[...][2][...]: {zs}'
    assert ys == [2.0], f'y 平面应来自 voxels[1][...][...]: {ys}'


def test_deterministic_output():
    """同输入两次输出逐位相等（四轴一并比，含 AO 颜色与索引）。"""
    rng = np.random.RandomState(20261020)
    v = rng.rand(5, 6, 7) > 0.55
    a = greedy_mesh_3d(v, size=0.5, origin=(1.0, 2.0, 3.0))
    b = greedy_mesh_3d(v, size=0.5, origin=(1.0, 2.0, 3.0))
    assert a.positions == b.positions
    assert a.normals == b.normals
    assert a.uvs == b.uvs
    assert a.colors == b.colors
    assert a.indices == b.indices
    assert a.name == b.name == 'voxel_mesh'
    assert _quad_count(a) > 0


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
    print(f'[test_voxel_mesh] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_VOXEL_MESH_TESTS_FAILED')
        return 1
    print('ALL_VOXEL_MESH_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
