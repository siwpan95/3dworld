# -*- coding: utf-8 -*-
"""3D 物理测试：向量 / 碰撞检测 / 扫掠 TOI / 单球积分 / 多球世界 / 确定性。

全部纯函数、零随机：碰撞检测用**手算可复核**的几何用例，扫掠 TOI 与积分落点
取**精确 golden 值**，钉住「实现没走样」+ 物理不变量（步进后不互相穿透）。
"""
from __future__ import annotations

from world3d.logic import physics3d as P


def test_vector_ops():
    a, b = (1.0, 2.0, 3.0), (4.0, -1.0, 2.0)
    assert P.vadd(a, b) == (5.0, 1.0, 5.0)
    assert P.vsub(a, b) == (-3.0, 3.0, 1.0)
    assert P.vdot(a, b) == 1 * 4 + 2 * -1 + 3 * 2
    assert P.vcross(a, b) == (2 * 2 - 3 * -1, 3 * 4 - 1 * 2, 1 * -1 - 2 * 4)
    assert abs(P.vlen(a) - math_hypot(a)) < 1e-12
    n = P.vnorm(b)
    assert abs(P.vlen(n) - 1.0) < 1e-12, '归一化后长度应为 1'
    assert P.vdist(a, b) == P.vlen(P.vsub(a, b))
    print('  ok: 向量加/减/点乘/叉乘/长度/归一化/距离')


def test_spheres_collide():
    assert P.spheres_collide((0, 0, 0), 1.0, (1.5, 0, 0), 1.0)        # 距 1.5<2 相交
    assert not P.spheres_collide((0, 0, 0), 1.0, (2.1, 0, 0), 1.0)    # 距 2.1>2 分离
    assert P.spheres_collide((0, 0, 0), 1.0, (2.0, 0, 0), 1.0)        # 恰相切（eps）
    print('  ok: 球-球 相交/分离/相切')


def test_aabb_intersect():
    m1, x1 = (0, 0, 0), (2, 2, 2)
    assert P.aabb_intersect(m1, x1, (1, 1, 1), (3, 3, 3))             # 重叠
    assert not P.aabb_intersect(m1, x1, (3, 0, 0), (4, 4, 4))         # x 分离
    print('  ok: AABB-AABB 重叠/分离')


def test_sphere_aabb():
    bmin, bmax = (-1, -1, -1), (1, 1, 1)
    assert P.sphere_aabb((0, 0, 0), 0.5, bmin, bmax)                 # 球心在内
    assert P.sphere_aabb((1.5, 0, 0), 0.6, bmin, bmax)               # 距面 0.5<0.6 相交
    assert not P.sphere_aabb((2.0, 0, 0), 0.5, bmin, bmax)           # 距面 1.0>0.5 分离
    print('  ok: 球-AABB 内含/贴面/分离')


def test_sphere_plane():
    n = (0.0, 1.0, 0.0)
    assert P.sphere_plane((0, 0.3, 0), 0.5, (0, 0, 0), n)            # |0.3|<0.5 相交
    assert not P.sphere_plane((0, 1.0, 0), 0.5, (0, 0, 0), n)        # |1.0|>0.5 分离
    print('  ok: 球-平面 相交/分离')


def test_ray_sphere():
    hit, t = P.ray_sphere((0, 0, -5), (0, 0, 1), (0, 0, 0), 1.0)
    assert hit and abs(t - 4.0) < 1e-9, f'沿 +z 命中球应在 t=4，得到 {t}'  # 距心 5，半径1
    miss, _ = P.ray_sphere((0, 0, 5), (0, 0, 1), (0, 0, 0), 1.0)     # 背离球
    assert not miss
    # 起点在球内：取出口，t 应>0 且 < 起点到表面
    hit2, t2 = P.ray_sphere((0, 0, 0), (0, 0, 1), (0, 0, 0), 1.0)
    assert hit2 and abs(t2 - 1.0) < 1e-9, f'球内射线出口 t=1，得到 {t2}'
    print('  ok: 射线-球 命中 t=4 / 背离 miss / 球内出口 t=1')


def test_ray_aabb():
    bmin, bmax = (-1, -1, -1), (1, 1, 1)
    hit, tmin, tmax = P.ray_aabb((0, 0, -3), (0, 0, 1), bmin, bmax)
    assert hit and abs(tmin - 2.0) < 1e-9 and abs(tmax - 4.0) < 1e-9  # 进入 2 退出 4
    miss, _, _ = P.ray_aabb((5, 0, 0), (0, 0, 1), bmin, bmax)        # 起点在盒外且平行
    assert not miss
    print('  ok: 射线-AABB 进入 2/退出 4 / 平行 miss')


def test_closest_points_segments():
    # 两条垂直且交叉的线段：[(0,0,0),(0,0,0)] 退化 + [(0,0,-1),(0,0,1)] → 最近点 (0,0,0)
    c1, c2, s, t = P.closest_points_segments(
        (0, 0, 0), (0, 0, 0), (0, 0, -1), (0, 0, 1))
    assert P.vdist(c1, c2) < 1e-9
    # 平行线段
    c1, c2, s, t = P.closest_points_segments(
        (0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0))
    assert abs(P.vdist(c1, c2) - 1.0) < 1e-9, '平行线段最近距离应为 1'
    print('  ok: 线段-线段 退化/垂直/平行 最近点')


def test_capsule_capsule():
    assert P.capsule_capsule((0, 0, 0), (0, 0, 2), 0.5, (1, 0, 0), (1, 0, 2), 0.5)  # 距 1<1 相交
    assert not P.capsule_capsule((0, 0, 0), (0, 0, 2), 0.3, (2, 0, 0), (2, 0, 2), 0.3)  # 距2>0.6
    print('  ok: 胶囊-胶囊 相交/分离')


def test_sweep_sphere_aabb():
    bmin, bmax = (5, -1, -1), (7, 1, 1)
    hit, toi, normal = P.sweep_sphere_aabb(
        (0, 0, 0), 1.0, (10, 0, 0), bmin, bmax, 1.0)
    assert hit, '球向右扫掠应命中'
    assert abs(toi - 0.4) < 1e-9, f'首次接触应在 t=0.4（膨胀盒左面 x=4），得到 {toi}'
    assert normal == (-1.0, 0.0, 0.0), f'命中法线应为 -x，得到 {normal}'
    # 背离盒：无命中
    hit2, toi2, _ = P.sweep_sphere_aabb(
        (0, 0, 0), 1.0, (-10, 0, 0), bmin, bmax, 1.0)
    assert not hit2 and toi2 == 1.0, '背离盒应无命中且 toi==dt'
    # 起点已在膨胀盒内：立即接触
    hit3, toi3, n3 = P.sweep_sphere_aabb(
        (4, 0, 0), 1.0, (1, 0, 0), bmin, bmax, 1.0)
    assert hit3 and toi3 == 0.0, '起点已穿透应 toi=0'
    print('  ok: 扫掠球-AABB 命中 t=0.4/法线 -x / 背离 miss / 已穿透 toi=0')


def test_integrate_sphere():
    pos = (0.0, 10.0, 0.0)
    vel = (0.0, 0.0, 0.0)
    new_pos, new_vel = P.integrate_sphere(
        pos, vel, 1.0, -9.8, 0.0, ground_y=0.0, radius=1.0)
    assert new_pos == (0.0, 1.0, 0.0), f'落体应停在地面 floor=1，得到 {new_pos}'
    assert new_vel == (0.0, 0.0, 0.0), '恢复系数 0 → 落地后法向速度归零'
    # 确定性：同输入同输出
    p2, v2 = P.integrate_sphere(pos, vel, 1.0, -9.8, 0.0, 0.0, 1.0)
    assert (p2, v2) == (new_pos, new_vel)
    print('  ok: 单球积分 落体停地面 + 确定性')


def test_step_world():
    bodies = [((0.0, 10.0, 0.0), (0.0, 0.0, 0.0), 1.0, 1.0),
              ((1.5, 10.0, 0.0), (0.0, 0.0, 0.0), 1.0, 1.0)]   # 初始重叠（距 1.5<2）
    out = P.step_world(bodies, 0.1, -9.8, ground_y=0.0, restitution=0.4)
    # 不变量：步进后两两不再互相穿透
    for i in range(len(out)):
        for j in range(i + 1, len(out)):
            d = P.vdist(out[i][0], out[j][0])
            assert d >= out[i][2] + out[j][2] - 1e-6, f'球 {i},{j} 仍重叠（距 {d}）'
    # 都在地面之上
    for p, _v, r, _m in out:
        assert p[1] >= 0.0 + r - 1e-6, '球不应穿地'
    # 确定性
    out2 = P.step_world(bodies, 0.1, -9.8, 0.0, 0.4)
    assert out == out2
    print('  ok: 多球世界 步进后不穿透 + 在地面之上 + 确定性')


def math_hypot(v):
    import math
    return math.sqrt(sum(x * x for x in v))


def main() -> int:
    tests = [
        test_vector_ops,
        test_spheres_collide,
        test_aabb_intersect,
        test_sphere_aabb,
        test_sphere_plane,
        test_ray_sphere,
        test_ray_aabb,
        test_closest_points_segments,
        test_capsule_capsule,
        test_sweep_sphere_aabb,
        test_integrate_sphere,
        test_step_world,
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
    print(f'[test_physics3d] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for n, e in failed:
            print(f'  - {n}: {e}')
        print('ALL_PHYSICS3D_TESTS_FAILED')
        return 1
    print('ALL_PHYSICS3D_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
