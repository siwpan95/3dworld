# -*- coding: utf-8 -*-
"""Jolt-lite 刚体物理核心的无窗口单测（纯数学 / 无 GL）。

`python -m tests.test_jolt_lite` 直接跑。聚合 runner 按 `  ok:` 行统计。

借鉴来源：Jolt Physics（MIT，算法档）的 GJK/EPA、顺序冲量、warm start、
sleeping 思路；本实现按算法自研，不搬源码。
"""

import sys

import numpy as np

from world3d.physics.jolt_lite import (
    Box, Capsule, RigidBody, Sphere, World, _gjk_distance, epa, manifold,
    _q_from_axis_angle,
)


def test_gjk_separation_distance():
    """两球分离：GJK 返回表面间距 == 中心距 - 半径和。"""
    a = RigidBody(Sphere(1.0), pos=(0, 0, 0))
    b = RigidBody(Sphere(1.0), pos=(3, 0, 0))
    colliding, dist, *_ = _gjk_distance(a.shape, b.shape, a.xf, b.xf)
    assert not colliding, "分离球体不应判为碰撞"
    assert abs(dist - 1.0) < 1e-6, dist


def test_epa_overlap_depth():
    """两球重叠：EPA 给出穿透深度 ≈ 半径和 - 中心距，法线沿 x。"""
    a = RigidBody(Sphere(1.0), pos=(0, 0, 0))
    b = RigidBody(Sphere(1.0), pos=(1.5, 0, 0))
    colliding, _, pa, pb, d, simplex = _gjk_distance(a.shape, b.shape, a.xf, b.xf)
    assert colliding, "重叠球体应判为碰撞"
    n, depth = epa(a.shape, b.shape, a.xf, b.xf, simplex)
    assert 0.4 < depth < 0.6, depth
    assert abs(abs(n[0]) - 1.0) < 0.25, n


def test_sphere_rests_on_ground():
    """球落到地面（静态盒）后趋于静止，不下沉、不爆炸。"""
    w = World(baumgarte=0.2, slop=0.005)
    ground = RigidBody(Box((100.0, 0.5, 100.0)), pos=(0, -0.5, 0), is_static=True)
    ball = RigidBody(Sphere(1.0), mass=1.0, pos=(0, 3.0, 0))
    w.add(ground)
    w.add(ball)
    for _ in range(400):
        w.step(1 / 60.0, iterations=12)
    # 静止后球心应在 y≈1（半径），速度收敛
    assert 0.9 < ball.xf.pos[1] < 1.1, ball.xf.pos[1]
    assert np.linalg.norm(ball.vel) < 0.4, ball.vel


def test_box_rests_on_ground():
    """盒落到地面后趋于静止（轴对齐，AABB 流形）。"""
    w = World(baumgarte=0.2, slop=0.005)
    ground = RigidBody(Box((100.0, 0.5, 100.0)), pos=(0, -0.5, 0), is_static=True)
    box = RigidBody(Box((0.5, 0.5, 0.5)), mass=1.0, pos=(0, 3.0, 0))
    w.add(ground)
    w.add(box)
    for _ in range(400):
        w.step(1 / 60.0, iterations=12)
    assert 0.4 < box.xf.pos[1] < 0.6, box.xf.pos[1]
    assert np.linalg.norm(box.vel) < 0.4, box.vel


def test_energy_dissipates():
    """下落球的总动能最终趋于 0（不发散）。"""
    w = World(baumgarte=0.2, slop=0.005)
    ground = RigidBody(Box((100.0, 0.5, 100.0)), pos=(0, -0.5, 0), is_static=True)
    ball = RigidBody(Sphere(1.0), mass=1.0, pos=(0, 4.0, 0), vel=(0, -2.0, 0))
    w.add(ground)
    w.add(ball)
    ke_start = 0.5 * 1.0 * np.dot(ball.vel, ball.vel)
    for _ in range(500):
        w.step(1 / 60.0, iterations=12)
    ke_end = 0.5 * 1.0 * np.dot(ball.vel, ball.vel)
    assert ke_end < 0.1 * ke_start, (ke_start, ke_end)


def test_manifold_separated_none():
    a = RigidBody(Sphere(1.0), pos=(0, 0, 0))
    b = RigidBody(Sphere(1.0), pos=(3, 0, 0))
    assert manifold(a, b) is None


def test_capsule_has_support():
    c = Capsule(0.5, 1.0)
    body = RigidBody(c, pos=(0, 0, 0))
    # 胶囊 = 线段 ⊕ 球：支撑 = 轴端 ±hh + r·normalize(d)
    s = c.support(body.xf, (0, 1, 0))
    assert abs(s[1] - 1.5) < 1e-6, s          # 顶端 = hh + r
    s2 = c.support(body.xf, (0, -1, 0))
    assert abs(s2[1] + 1.5) < 1e-6, s2        # 底端 = -(hh + r)
    s3 = c.support(body.xf, (1, 0, 0))
    assert abs(s3[0] - 0.5) < 1e-6, s3        # 最右 = r（球部分）
    assert abs(s3[1] - 1.0) < 1e-6, s3        # 轴端贡献 +hh


def test_restitution_bounces_higher():
    """回弹：高 restitution 落球回弹峰值明显高于零回弹（差分验证）。"""
    def peak(restitution):
        w = World(baumgarte=0.2, slop=0.005)
        ground = RigidBody(Box((100.0, 0.5, 100.0)), pos=(0, -0.5, 0), is_static=True)
        ball = RigidBody(Sphere(1.0), mass=1.0, pos=(0, 3.0, 0), restitution=restitution)
        w.add(ground)
        w.add(ball)
        contacted = False
        peak_y = -1e9
        for _ in range(600):
            w.step(1 / 60.0, iterations=12)
            if ball.xf.pos[1] < 1.1:          # 已接触地面（球半径=1）
                contacted = True
            if contacted:
                peak_y = max(peak_y, ball.xf.pos[1])
        return peak_y
    high = peak(0.9)
    zero = peak(0.0)
    assert high > 1.5, high          # 高回弹应明显弹起
    assert zero < 1.3, zero          # 零回弹应基本贴地
    assert high > zero + 0.3, (high, zero)


def test_friction_couples_spin_and_decelerates():
    """摩擦：滑行的球在 Coulomb 摩擦下唤起自旋（滚动）并减速；零摩擦无自旋。

    用球而非盒：球为天然单接触点，摩擦唤起自旋进入稳定滚动，不会像盒那样
    单点接触导致翻倒/贯穿；盒的多点接触面留待运行时层。
    """
    def slide(friction):
        w = World(baumgarte=0.2, slop=0.005)
        ground = RigidBody(Box((100.0, 0.5, 100.0)), pos=(0, -0.5, 0), is_static=True)
        ball = RigidBody(Sphere(1.0), mass=1.0, pos=(0, 1.0, 0),
                         vel=(5.0, 0.0, 0.0), friction=friction)
        w.add(ground)
        w.add(ball)
        for _ in range(300):
            w.step(1 / 60.0, iterations=12)
        return np.linalg.norm(ball.vel[:2]), np.linalg.norm(ball.ang_vel)
    with_f_v, with_f_w = slide(1.0)
    no_f_v, no_f_w = slide(0.0)
    assert with_f_w > 0.5, with_f_w        # 摩擦唤起自旋（滚动）
    assert no_f_w < 1e-3, no_f_w           # 零摩擦无自旋
    assert with_f_v < no_f_v - 0.5, (with_f_v, no_f_v)  # 摩擦减速（滑→滚）


def test_capsule_manifold_penetration():
    """胶囊-地面穿透：GJK/EPA 兜底给出向上法线 + 合理深度（不穿地）。"""
    ground = RigidBody(Box((100.0, 0.5, 100.0)), pos=(0, -0.5, 0), is_static=True)
    # 胶囊 hh=1.0, r=0.5；中心放到 y=1.0 → 底部 y = 1.0 - 1.5 = -0.5，穿透 0.5
    cap = RigidBody(Capsule(0.5, 1.0), pos=(0, 1.0, 0))
    m = manifold(cap, ground)
    assert m is not None, "胶囊应检测到与地面穿透"
    n, point, depth = m
    assert n[1] > 0.9, n                      # 法线应朝上（b→a，地面指向胶囊）
    assert 0.3 < depth < 0.8, depth           # 深度接近 0.5


def test_rotated_box_manifold_uses_epa():
    """旋转盒-地面：非轴对齐盒走 GJK/EPA 兜底，给出向上法线 + 合理深度。

    EPA 对「极扁/极大 AABB 地面盒」数值条件差（Minkowski 差过度拉长），故此处
    用合理尺寸的地面盒；轴对齐巨地面盒本就走解析 AABB 路径，胶囊等也走 EPA 正常。
    """
    ground = RigidBody(Box((10.0, 0.5, 10.0)), pos=(0, -0.5, 0), is_static=True)
    # 盒半 0.5，绕 X 倾 45°（真实倾斜，最低角点低于底面中心）；中心 y=0.3 穿透
    rot = _q_from_axis_angle((1, 0, 0), np.pi / 4)
    box = RigidBody(Box((0.5, 0.5, 0.5)), pos=(0, 0.3, 0), quat=rot)
    m = manifold(box, ground)
    assert m is not None, "旋转盒应检测到与地面穿透（经 GJK/EPA）"
    n, point, depth = m
    assert n[1] > 0.8, n                       # 法线应朝上
    assert 0.1 < depth < 0.6, depth            # 深度合理
    # 动态落体：旋转盒经 EPA 不应穿过地面，且能静止（不发散）
    w = World(baumgarte=0.2, slop=0.005)
    g2 = RigidBody(Box((10.0, 0.5, 10.0)), pos=(0, -0.5, 0), is_static=True)
    b2 = RigidBody(Box((0.5, 0.5, 0.5)), pos=(0, 3.0, 0), quat=rot)
    w.add(g2)
    w.add(b2)
    for _ in range(400):
        w.step(1 / 60.0, iterations=12)
    assert b2.xf.pos[1] > -0.1, b2.xf.pos[1]   # 未穿地
    assert np.linalg.norm(b2.vel) < 0.8, b2.vel


# ------------------------------- 宽相（sweep-and-prune）与岛管理 / 确定性 --
def _spread_world(n=40, gap=6.0):
    """n 个互不接触、沿 x 均匀铺开的球（宽相应大量剪枝）。"""
    w = World()
    for i in range(n):
        w.add(RigidBody(Sphere(0.5), mass=1.0, pos=(i * gap, 0.0, 0.0)))
    return w


def test_broadphase_prunes_versus_naive():
    """宽相候选对数应显著少于 O(n²) 全对比较（证明扫掠剪枝真的生效）。"""
    n = 40
    w = _spread_world(n)
    w.step(1.0 / 60.0)
    st = w.last_stats
    naive = n * (n - 1) // 2
    assert st['broadphase_candidates'] < naive, \
        f"SAP 候选 {st['broadphase_candidates']} 应少于朴素 {naive}"
    # 彼此离得很远 ⇒ 不应有任何接触
    assert st['narrow_pairs'] == 0, st
    assert st['contacts'] == 0, st
    print(f"  ok: 宽相候选 {st['broadphase_candidates']} << 朴素 {naive}（剪枝生效）")


def test_broadphase_still_finds_real_overlaps():
    """剪枝不能剪掉真碰撞：堆叠球必须被找到。"""
    w = World()
    for i in range(5):
        w.add(RigidBody(Sphere(0.5), mass=1.0, pos=(0.0, i * 0.9, 0.0)))
    w.step(1.0 / 60.0)
    st = w.last_stats
    assert st['narrow_pairs'] >= 4, f"5 个叠球至少 4 对重叠，实际 {st}"
    print(f"  ok: 真碰撞未被剪掉（{st['narrow_pairs']} 对 / {st['contacts']} 接触）")


def test_islands_split_disconnected_stacks():
    """互不相连的两摞球应被分成 2 个岛（岛间互不影响）。"""
    w = World()
    w.add(RigidBody(Box((50.0, 0.5, 50.0)), mass=0.0, pos=(0, -1.0, 0), is_static=True))
    for i in range(3):
        w.add(RigidBody(Sphere(0.5), mass=1.0, pos=(-20.0, i * 0.9, 0.0)))
    for i in range(3):
        w.add(RigidBody(Sphere(0.5), mass=1.0, pos=(20.0, i * 0.9, 0.0)))
    for _ in range(30):
        w.step(1.0 / 60.0)
    st = w.last_stats
    assert st['islands'] >= 2, f"两摞互不相连的球应至少 2 个岛，实际 {st}"
    print(f"  ok: 断连堆分成 {st['islands']} 个岛（接触 {st['contacts']}）")


def test_step_is_deterministic_regardless_of_container_order():
    """**确定性核心**：打乱 `world.bodies` 的容器顺序，结果必须逐位相同。

    遍历序由 body id（数据结构层）决定，而不是由列表顺序决定 —— 这正是报告
    ⑮ 说的"把顺序约束放在数据结构遍历层，不要放在算法层"。

    注意：`RigidBody._next_id` 是全局自增的，两次构造出来的 id 本就不同，
    所以两侧刚体按**初始位置**配对（构造相同 ⇒ 初值相同），不按 id 配对。
    """
    def build(shuffled):
        w = World()
        w.add(RigidBody(Box((50.0, 0.5, 50.0)), mass=0.0, pos=(0, -1.0, 0),
                        is_static=True))
        bodies = [RigidBody(Sphere(0.5), mass=1.0,
                            pos=(i * 0.35 - 1.0, 2.0 + i * 0.6, 0.0))
                  for i in range(8)]
        if shuffled:
            bodies = bodies[::-1]          # 反序加入容器（id 不变，容器序变）
        for b in bodies:
            w.add(b)
        return w, {b: tuple(b.xf.pos) for b in w.bodies}

    wa, init_a = build(False)
    wb, init_b = build(True)
    # 两侧按初始位置配对
    ma = {tuple(np.round(p, 9)): b for b, p in init_a.items()}
    mb = {tuple(np.round(p, 9)): b for b, p in init_b.items()}
    assert set(ma) == set(mb), '两侧初始构型应一致'
    for _ in range(60):
        wa.step(1.0 / 60.0)
        wb.step(1.0 / 60.0)
    for k in sorted(ma):
        ba, bb = ma[k], mb[k]
        assert np.array_equal(ba.xf.pos, bb.xf.pos), \
            f'{k} 位置不一致（容器序影响了结果）：{ba.xf.pos} vs {bb.xf.pos}'
        assert np.array_equal(ba.vel, bb.vel), f'{k} 速度不一致'
    print("  ok: 容器顺序打乱后 60 帧结果逐位相同（遍历序由 id 决定）")


def test_repeated_runs_are_bit_identical():
    """同样的构造与输入，两次模拟逐位相同（无集合/哈希遍历引入的不确定性）。"""
    def run():
        w = World()
        w.add(RigidBody(Box((50.0, 0.5, 50.0)), mass=0.0, pos=(0, -1.0, 0),
                        is_static=True))
        for i in range(6):
            w.add(RigidBody(Sphere(0.5), mass=1.0, pos=(i * 0.3, 1.0 + i, 0.0)))
        for _ in range(90):
            w.step(1.0 / 60.0)
        # 按初始位置索引（id 是全局自增的，两次运行必然不同）
        return {tuple(np.round(b.xf.pos, 9)): (b.vel.copy(), b.xf.pos.copy())
                for b in w.bodies}

    a, b = run(), run()
    assert set(a) == set(b), '两次运行的构型集合应一致'
    for k in sorted(a):
        va, pa = a[k]
        vb, pb = b[k]
        assert np.array_equal(pa, pb) and np.array_equal(va, vb), \
            f'{k} 两次运行结果不同：{pa} vs {pb}'
    print("  ok: 两次相同模拟逐位一致（90 帧）")


def test_broadphase_pair_order_is_sorted_by_id():
    """宽相输出的 (id_a, id_b) 必须升序且 id_a < id_b（可快照断言）。"""
    w = World()
    w.add(RigidBody(Box((50.0, 0.5, 50.0)), mass=0.0, pos=(0, -1.0, 0), is_static=True))
    for i in range(6):
        w.add(RigidBody(Sphere(0.5), mass=1.0, pos=(i * 0.4, 0.6, 0.0)))
    aabbs = {b.id: b.aabb() for b in w.bodies}
    pairs, _ = w._broadphase_sap(aabbs)
    keys = [(a.id, b.id) for a, b in pairs]
    assert all(x < y for x, y in keys), keys
    assert keys == sorted(keys), keys
    print(f"  ok: 宽相输出 {len(keys)} 对，按 (id_a, id_b) 升序")


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
    print(f'[test_jolt_lite] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_JOLT_LITE_TESTS_FAILED')
        return 1
    print('ALL_JOLT_LITE_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
