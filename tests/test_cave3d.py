# -*- coding: utf-8 -*-
"""三维密度场世界（洞穴 / 悬垂）测试套件 —— `world3d/logic/voxel.py`。

运行::

    python -m tests.test_cave3d

本套件盯住三件事，每件都有一个**能被打穿**的判据：

1. **它真的不是 2.5D 了**。高度图世界里每个柱子都从地基连到地表，所以"头顶有岩石、
   脚下是通天空气"的格恒为 0、封闭空腔恒为 0。这里拿同一个 `cave_stats` 去量一个
   **合成的 2.5D 世界**做对照 —— 三条统计必须全为 0，否则判据是空的。
2. **面剔除是解析值，不是"看起来差不多"**。5³ 世界里放一个 3³ 实心块：表面 54 面、
   不剔除 162 面。数错了就是错的，没有商量。
3. **颜色真的来自实体纹理**（按世界坐标求值），不是一张固定调色板 —— 用注入的
   `color_fn` 与一次逐位比对把它钉住。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.logic import solidtex  # noqa: E402
from world3d.logic import voxel as V  # noqa: E402

_W, _H, _D = 32, 16, 32
_SEED = 5

_CACHE = {}


def _world():
    """建一次世界（≈0.7 s），后续用例共用 —— 免得 6 条用例各建一遍。"""
    if 'occ' not in _CACHE:
        _CACHE['occ'] = V.cave_occupancy(_SEED, w=_W, h=_H, d=_D)
        _CACHE['stats'] = V.cave_stats(_CACHE['occ'], _W, _H, _D)
    return _CACHE['occ'], _CACHE['stats']


def _solid_block(w, h, d, lo, hi):
    """手工造一个 [lo, hi] 的实心方块（用于解析值校验）。"""
    occ = bytearray(w * h * d)
    for y in range(lo, hi + 1):
        for z in range(lo, hi + 1):
            for x in range(lo, hi + 1):
                occ[V._ci(x, y, z, w, d)] = 1
    return occ


def test_occupancy_deterministic_and_mixed():
    """同 seed 逐位一致、换 seed 必变；实心/空气都真的存在。"""
    occ, st = _world()
    again = V.cave_occupancy(_SEED, w=_W, h=_H, d=_D)
    assert bytes(occ) == bytes(again), '同 seed 两次生成不一致（不可复现）'
    other = V.cave_occupancy(_SEED + 1, w=_W, h=_H, d=_D)
    assert bytes(occ) != bytes(other), '换 seed 世界没变（seed 没接进噪声）'
    assert 0.25 < st['ratio'] < 0.75, f'实心占比 {st["ratio"]:.2f} 不合理（该是"有地也有天"）'
    assert st['open_air'] > 0 and st['sealed_air'] > 0, '空气应当既有通天的也有封闭的'
    print(f'  ok: {_W}×{_H}×{_D} 世界确定可复现；实心 {st["ratio"]*100:.0f}%、'
          f'通天空气 {st["open_air"]}、封闭空气 {st["sealed_air"]}')


def test_world_is_not_25d_anymore():
    """**核心判据**：同一个统计函数，2.5D 世界必须全 0，这里必须全 >0。

    只断言"这里是 >0"是不够的 —— 万一度量的东西本来就恒为 0，那这条断言永远绿。
    所以配一个**合成的 2.5D 世界**做对照：高度图世界里柱子必然从地基连到地表，
    "实-空-实" 与"封闭空腔"在结构上不可能出现。
    """
    _occ, st = _world()
    assert st['overhang'] > 0, '没有任何悬垂（头顶岩石 + 脚下通天空气）—— 还是高度图'
    assert st['sealed_air'] > 0, '没有任何封闭洞穴 —— 还是高度图'
    assert st['gap_cols'] > 0, '没有任何一列出现"实-空-实" —— 还是高度图'
    # 对照组：合成一个 2.5D 世界（每列高度 3..6，柱内全实心）
    w, h, d = 12, 12, 12
    occ25 = bytearray(w * h * d)
    for z in range(d):
        for x in range(w):
            for y in range(3 + (x * 7 + z * 13) % 4):
                occ25[V._ci(x, y, z, w, d)] = 1
    st25 = V.cave_stats(occ25, w, h, d)
    assert st25['overhang'] == 0 and st25['sealed_air'] == 0 and st25['gap_cols'] == 0, \
        f'2.5D 对照竟然也有悬垂/洞穴（判据失效）：{st25}'
    print(f'  ok: 3D 世界 悬垂 {st["overhang"]} 格 / 封闭洞穴 {st["sealed_air"]} 格 / '
          f'夹层列 {st["gap_cols"]}；2.5D 对照三项全 0')


def test_cull_is_analytic():
    """5³ 世界里一个 3³ 实心块：剔除后 **54** 面、不剔除 **162** 面。

    54 = 6 个面 × 3×3 格（只有表面朝外）；162 = 6 × 27 格。数错就是数错。
    """
    w = h = d = 5
    occ = _solid_block(w, h, d, 1, 3)
    _p, _c, faces = V.build_cave_mesh(occ, seed=1, w=w, h=h, d=d, cull=True)
    _p2, _c2, faces2 = V.build_cave_mesh(occ, seed=1, w=w, h=h, d=d, cull=False)
    assert faces == 54, f'剔除后应 54 面（6×3×3），实际 {faces}'
    assert faces2 == 162, f'不剔除应 162 面（6×27），实际 {faces2}'
    assert faces2 - faces == 108, '内部面数量不对（162-54=108）'
    print(f'  ok: 3³ 实心块 剔除 {faces} 面 / 不剔除 {faces2} 面（解析值 54 / 162）；'
          f'内部面 {faces2 - faces} 个全被剔掉')


def test_mesh_geometry_is_well_formed():
    """顶点数 == 面数×6；坐标落在世界包围盒内；颜色四通道合法。"""
    occ, st = _world()
    pos, col, faces = V.build_cave_mesh(occ, seed=_SEED, w=_W, h=_H, d=_D)
    assert len(pos) == faces * 6 * 3, f'顶点数与面数不匹配：{len(pos)//3} vs {faces*6}'
    assert len(col) == faces * 6 * 4, f'颜色分量数不匹配：{len(col)} vs {faces*6*4}'
    xs, ys, zs = pos[0::3], pos[1::3], pos[2::3]
    assert min(xs) >= 0 and max(xs) <= _W, f'x 越界 {min(xs)}..{max(xs)}'
    assert min(ys) >= 0 and max(ys) <= _H, f'y 越界 {min(ys)}..{max(ys)}'
    assert min(zs) >= 0 and max(zs) <= _D, f'z 越界 {min(zs)}..{max(zs)}'
    assert all(0.0 <= v <= 1.0 for v in col), '颜色通道越界'
    assert all(abs(v - 1.0) < 1e-9 for v in col[3::4]), 'alpha 应恒为 1'
    assert faces < 6 * st['solid'], '剔除率为 0（内部面没被剔）'
    rate = 1.0 - faces / (6 * st['solid'])
    print(f'  ok: {faces} 面 / {faces*6} 顶点，全在 0..{_W}/{_H}/{_D} 包围盒内，'
          f'剔除率 {rate*100:.0f}%')


def test_colors_come_from_solidtex_by_world_position():
    """颜色 = 实体纹理按**世界坐标**求值 —— 不是固定调色板。"""
    w = h = d = 5
    occ = _solid_block(w, h, d, 1, 3)
    # ① 注入固定色：输出必须精确等于 固定色 × 该面亮度（证明没有别的逻辑改写颜色）
    fixed = (0.25, 0.5, 0.75)
    _p, col, _f = V.build_cave_mesh(occ, w=w, h=h, d=d,
                                    color_fn=lambda x, y, z: fixed)
    # 首个发出的面是**底面**（ni=1, 亮度 0.45）而不是顶面：按 y 升序扫描，
    # 最下面那块砖的 +y 面被上邻挡掉了，-y 面朝天（空气）先发。
    expect0 = tuple(fixed[i] * V.FACE_SHADE[1] for i in range(3))
    got0 = tuple(col[0:3])
    assert all(abs(a - b) < 1e-6 for a, b in zip(got0, expect0)), \
        f'注入色没被原样使用：{got0} vs {expect0}'
    # ② 默认路径：孤立单格（6 面全发），首面颜色必须等于 solidtex 在该格中心的取值
    occ2 = bytearray(w * h * d)
    occ2[V._ci(1, 1, 1, w, d)] = 1
    _p2, col2, faces2 = V.build_cave_mesh(occ2, seed=3, w=w, h=h, d=d)
    assert faces2 == 6, f'孤立格应发 6 面，实际 {faces2}'
    r, g, b, _rough, _metal = solidtex.sample(1.5, 1.5, 1.5, 'rock', 3)
    k = V.FACE_SHADE[0]
    for got, want in zip(col2[0:3], (r * k, g * k, b * k)):
        assert abs(got - want) < 1e-6, f'首面颜色 {tuple(col2[0:3])} ≠ 实体纹理 {want:.4f}'
    # ③ 默认路径的颜色必须真的随位置变化（岩层感），不是一片同色
    occ3, _st = _world()
    _p3, col3, _f3 = V.build_cave_mesh(occ3, seed=_SEED, w=_W, h=_H, d=_D)
    uniq = {(round(col3[i], 3), round(col3[i + 1], 3), round(col3[i + 2], 3))
            for i in range(0, len(col3), 4)}
    assert len(uniq) > 20, f'整片世界只有 {len(uniq)} 种颜色 —— 更像固定调色板'
    print(f'  ok: 注入色精确生效；孤立格首面 == solidtex.sample×亮度；'
          f'整片世界 {len(uniq)} 种颜色（>20，随位置变化）')


def test_greedy_mesh_merges_coplanar_faces():
    """贪心合并的**解析判据**：3³ 实心块的六个面各并成 **1 个 quad**。

    逐面剔除给出 6×9=54 面（上一用例的解析值），贪心合并给出 **6**。
    这个数字没有解释空间：合并错了要么多（没并上）、要么少（并过头吃掉了面）。
    """
    w = h = d = 5
    occ = _solid_block(w, h, d, 1, 3)
    mesh = V.cave_mesh_greedy(occ, seed=1, w=w, h=h, d=d)
    quads = len(mesh.indices) // 6
    assert quads == 6, f'3³ 块应并成 6 个 quad（每面 1 个），实际 {quads}'
    assert mesh.triangle_count == 12 and mesh.vertex_count == 24
    # 真世界上的收益：必须显著少于"逐面剔除"，但也不能少于三角形的最小面数
    occ_w, st = _world()
    real = V.cave_mesh_greedy(occ_w, seed=_SEED, w=_W, h=_H, d=_D)
    _p, _c, culled = V.build_cave_mesh(occ_w, seed=_SEED, w=_W, h=_H, d=_D)
    q_real = len(real.indices) // 6
    assert q_real * 2 < culled, f'贪心合并收益不足：{q_real} quad vs {culled} 面'
    assert min(real.positions[i] for i in range(1, len(real.positions), 3)) >= 0.0
    print(f'  ok: 3³ 块 54 面 → 6 quad（解析值）；真世界 {culled} 面 → {q_real} quad'
          f'（降 {100 * (1 - q_real / culled):.0f}%）')


def test_bad_arguments_are_rejected():
    """坏参数要报错（静默返回一个"看起来很合理"的世界最难查）。"""
    cases = (
        (dict(w=2, h=8, d=8), '世界太窄'),
        (dict(w=8, h=3, d=8), '世界太矮'),
        (dict(w=8, h=8, d=2), '世界太浅'),
        (dict(cave=-0.1), 'cave 为负'),
        (dict(cave=0.5), 'cave 过大'),
        (dict(vscale=0.0), 'vscale=0'),
        (dict(overhang=0.0), 'overhang=0'),
    )
    for kwargs, label in cases:
        try:
            V.cave_occupancy(1, **kwargs)
        except ValueError:
            continue
        raise AssertionError(f'{label} 竟然通过：{kwargs}')
    print(f'  ok: {len(cases)} 种非法参数全部被拒')


def main() -> int:
    tests = [
        ('test_occupancy_deterministic_and_mixed', test_occupancy_deterministic_and_mixed),
        ('test_world_is_not_25d_anymore', test_world_is_not_25d_anymore),
        ('test_cull_is_analytic', test_cull_is_analytic),
        ('test_mesh_geometry_is_well_formed', test_mesh_geometry_is_well_formed),
        ('test_colors_come_from_solidtex_by_world_position',
         test_colors_come_from_solidtex_by_world_position),
        ('test_greedy_mesh_merges_coplanar_faces',
         test_greedy_mesh_merges_coplanar_faces),
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
    print(f'[test_cave3d] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for n, exc in failed:
            print(f'  - {n}: {exc}')
        print('ALL_CAVE3D_TESTS_FAILED')
        return 1
    print('ALL_CAVE3D_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
