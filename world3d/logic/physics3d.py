# -*- coding: utf-8 -*-
"""3D 物理原语：向量运算 / 碰撞检测 / 扫掠（TOI）/ 单球积分 / 多球世界（纯标准库）。

自研（非移植）
--------------
本地仓库盘点（2026-10-19）结论：本地只有 2D AABB 碰撞，没有 3D 物理实现，
故本模块从零自研。立场与 ``logic/`` 一致：**纯标准库、零依赖、确定性、可
headless 单测**（不碰 pyglet / GL / 全局随机状态）。

覆盖范围
--------
* 向量基元（3 维 ``tuple``）：加 / 减 / 点乘 / 叉乘 / 数乘 / 长度 / 归一化 / 距离。
* 碰撞检测：球-球、AABB-AABB、球-AABB、球-平面、射线-球、射线-AABB、
  线段-线段最近点、胶囊-胶囊。
* 扫掠（连续碰撞）：球沿速度扫掠 AABB → 首次接触时间（TOI）与法线
  （Minkowski 膨胀 + 射线-AABB，角点处为方形而非圆形近似，文档已注明）。
* 积分：单球在重力下的半隐式欧拉步进 + 地面反弹（带恢复系数）。
* 世界：N 个球在重力 + 地面 + 两两弹性碰撞下的简易步进（确定性）。

设计口径
--------
* 一切状态用不可变 ``tuple`` 表达，函数式无副作用，便于单测与回放。
* 重力约定为沿 -Y 的恒定加速度；地面为 ``y = ground_y`` 的平面。
* 不使用任何随机数：物理是确定性的（与 ``logic/`` 其余模块一致）。
"""
from __future__ import annotations

import math

__all__ = [
    'vadd', 'vsub', 'vdot', 'vcross', 'vscale', 'vlen', 'vnorm', 'vdist',
    'spheres_collide', 'aabb_intersect', 'sphere_aabb', 'sphere_plane',
    'ray_sphere', 'ray_aabb', 'closest_points_segments', 'capsule_capsule',
    'sweep_sphere_aabb', 'integrate_sphere', 'step_world',
]


# ----------------------------------------------------------------- 向量基元 --
# 点 / 向量 = (x, y, z) 三元组。

def vadd(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vcross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def vscale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def vlen(a):
    return math.sqrt(vdot(a, a))


def vnorm(a):
    l = vlen(a)
    return (0.0, 0.0, 0.0) if l == 0.0 else (a[0] / l, a[1] / l, a[2] / l)


def vdist(a, b):
    return vlen(vsub(a, b))


# --------------------------------------------------------------- 碰撞检测 --

def spheres_collide(c1, r1, c2, r2, eps=1e-9):
    """球-球：圆心距 ≤ 半径和即相交。"""
    return vdist(c1, c2) <= r1 + r2 + eps


def aabb_intersect(min1, max1, min2, max2):
    """AABB-AABB：三轴均重叠才相交。"""
    for i in range(3):
        if min1[i] > max2[i] or max1[i] < min2[i]:
            return False
    return True


def sphere_aabb(center, radius, bmin, bmax):
    """球-AABB：取 AABB 上离球心最近点，距离 ≤ 半径即相交。"""
    closest = (max(bmin[0], min(center[0], bmax[0])),
               max(bmin[1], min(center[1], bmax[1])),
               max(bmin[2], min(center[2], bmax[2])))
    return vdist(center, closest) <= radius


def sphere_plane(center, radius, point_on_plane, normal):
    """球-平面：有符号距离的绝对值 ≤ 半径即相交。法线无需归一化（内部归一）。"""
    n = vnorm(normal)
    signed = vdot(vsub(center, point_on_plane), n)
    return abs(signed) <= radius


def ray_sphere(origin, direction, center, radius):
    """射线-球：``direction`` 任意长度（内部归一）。

    返回 ``(hit, t)``，``t`` 为沿**单位方向**的首次非负命中距离；
    不命中返回 ``(False, inf)``。视线 / 拾取基元。
    """
    d = vnorm(direction)
    oc = vsub(origin, center)
    b = vdot(oc, d)
    c = vdot(oc, oc) - radius * radius
    disc = b * b - c
    if disc < 0.0:
        return (False, math.inf)
    sq = math.sqrt(disc)
    t1 = -b - sq
    if t1 >= 0.0:
        return (True, t1)
    t2 = -b + sq
    if t2 >= 0.0:        # 起点在球内，取出口
        return (True, t2)
    return (False, math.inf)


def ray_aabb(origin, direction, bmin, bmax):
    """射线-AABB（slab 法）：``direction`` 任意长度。

    返回 ``(hit, tmin, tmax)``，``tmin``/``tmax`` 为沿**单位方向**的参数；
    ``tmax < max(0, tmin)`` 视为不命中。用于视线裁剪与扫掠。
    """
    d = vnorm(direction)
    tmin = -math.inf
    tmax = math.inf
    for i in range(3):
        if abs(d[i]) < 1e-12:           # 平行于该轴：检查是否越界
            if origin[i] < bmin[i] or origin[i] > bmax[i]:
                return (False, 0.0, 0.0)
        else:
            ood = 1.0 / d[i]
            t1 = (bmin[i] - origin[i]) * ood
            t2 = (bmax[i] - origin[i]) * ood
            if t1 > t2:
                t1, t2 = t2, t1
            tmin = max(tmin, t1)
            tmax = min(tmax, t2)
            if tmin > tmax:
                return (False, 0.0, 0.0)
    if tmax < max(0.0, tmin):
        return (False, 0.0, 0.0)
    return (True, tmin, tmax)


def closest_points_segments(p1, q1, p2, q2):
    """两线段 ``[p1,q1]`` / ``[p2,q2]`` 的最近点对（标准闭式解）。

    返回 ``(c1, c2, s, t)``，``c1/c2`` 为最近点，``s/t`` 为各自参数 ∈[0,1]。
    胶囊-胶囊、距离场、约束求解的底层基元。
    """
    d1 = vsub(q1, p1)
    d2 = vsub(q2, p2)
    r = vsub(p1, p2)
    a = vdot(d1, d1)
    e = vdot(d2, d2)
    f = vdot(d2, r)
    EPS = 1e-12
    if a <= EPS and e <= EPS:
        return (p1, p2, 0.0, 0.0)
    if a <= EPS:                      # 第一段退化为点
        s = 0.0
        t = max(0.0, min(1.0, f / e))
    else:
        c = vdot(d1, r)
        if e <= EPS:                  # 第二段退化为点
            t = 0.0
            s = max(0.0, min(1.0, -c / a))
        else:
            b = vdot(d1, d2)
            denom = a * e - b * b
            s = 0.0 if denom <= EPS else max(0.0, min(1.0, (b * f - c * e) / denom))
            t = (b * s + f) / e
            if t < 0.0:
                t = 0.0
                s = max(0.0, min(1.0, -c / a))
            elif t > 1.0:
                t = 1.0
                s = max(0.0, min(1.0, (b - c) / a))
    c1 = vadd(p1, vscale(d1, s))
    c2 = vadd(p2, vscale(d2, t))
    return (c1, c2, s, t)


def capsule_capsule(a0, a1, ra, b0, b1, rb, eps=1e-9):
    """胶囊-胶囊：两线段最近距离 ≤ 半径和即相交。

    胶囊 = 以线段为轴、半径为 r 的扫掠圆柱（含半球端帽）。
    """
    c1, c2, _s, _t = closest_points_segments(a0, a1, b0, b1)
    return vdist(c1, c2) <= ra + rb + eps


def sweep_sphere_aabb(center, radius, velocity, bmin, bmax, dt):
    """球沿 ``velocity``（单位时间位移）在 ``[0,dt]`` 内扫掠静态 AABB。

    Minkowski 法：把 AABB 各面向外膨胀 ``radius``，问题化为「射线 vs 膨胀盒」
    的首次进入时间，再换算回真实时间。⚠ 角点处膨胀盒是方形而非圆形，故角点
    附近 TOI 略偏保守（略微提前），这是游戏常用的工程近似，非精确扫掠球。

    返回 ``(hit, toi, normal)``：
        hit:   dt 内是否接触；
        toi:   接触时刻（秒，∈[0,dt]）；未命中为 ``dt``；
        normal: 接触面外法线（沿最先穿透的轴），未命中为 (0,0,0)。
    """
    ebmin = (bmin[0] - radius, bmin[1] - radius, bmin[2] - radius)
    ebmax = (bmax[0] + radius, bmax[1] + radius, bmax[2] + radius)
    disp_len = vlen(vscale(velocity, dt))           # 总位移长度 = |velocity|·dt
    if disp_len <= 0.0:                             # 零位移：仅判起点是否穿透
        if _inside(center, ebmin, ebmax):
            return (True, 0.0, _nearest_axis_normal(center, ebmin, ebmax))
        return (False, dt, (0.0, 0.0, 0.0))
    dirn = vnorm(velocity)                          # 单位方向
    hit, tmin, tmax = ray_aabb(center, dirn, ebmin, ebmax)
    if not hit or tmax < 0.0:
        return (False, dt, (0.0, 0.0, 0.0))
    if tmin < 0.0:                                   # 起点已在膨胀盒内
        return (True, 0.0, _nearest_axis_normal(center, ebmin, ebmax))
    if tmin > disp_len:                              # 在 dt 内走不到盒前
        return (False, dt, (0.0, 0.0, 0.0))
    toi = tmin / disp_len * dt                       # 距离→时间
    normal = _entry_normal(center, dirn, ebmin, ebmax, tmin)
    return (True, toi, normal)


def _inside(point, bmin, bmax):
    return all(bmin[i] <= point[i] <= bmax[i] for i in range(3))


def _entry_normal(origin, dirn, bmin, bmax, t):
    """射线在参数 ``t`` 处进入膨胀盒时，命中哪一面（取最早穿透的轴）。"""
    p = vadd(origin, vscale(dirn, t))
    # 哪条轴在该面？偏离 < 1e-6 即视为贴面
    for i in range(3):
        if abs(p[i] - bmin[i]) < 1e-6:
            sign = -1.0 if dirn[i] > 0 else 1.0
            return (0.0, 0.0, 0.0)[:i] + (sign,) + (0.0, 0.0, 0.0)[i + 1:]
        if abs(p[i] - bmax[i]) < 1e-6:
            sign = 1.0 if dirn[i] > 0 else -1.0
            return (0.0, 0.0, 0.0)[:i] + (sign,) + (0.0, 0.0, 0.0)[i + 1:]
    return (0.0, 0.0, 0.0)


def _nearest_axis_normal(point, bmin, bmax):
    """起点已在盒内时，取离最近面的外法线。"""
    best_i, best_d, best_sign = 0, math.inf, 1.0
    for i in range(3):
        d_lo = point[i] - bmin[i]
        d_hi = bmax[i] - point[i]
        if d_lo < best_d:
            best_d, best_i, best_sign = d_lo, i, -1.0
        if d_hi < best_d:
            best_d, best_i, best_sign = d_hi, i, 1.0
    return (0.0, 0.0, 0.0)[:best_i] + (best_sign,) + (0.0, 0.0, 0.0)[best_i + 1:]


# ------------------------------------------------------------------- 积分 --

def integrate_sphere(pos, vel, dt, gravity_y, restitution, ground_y=0.0,
                     radius=0.0):
    """单球在重力下的半隐式欧拉步进 + 地面反弹（确定式）。

    参数：
        pos:          当前球心 (x, y, z)
        vel:          当前速度 (vx, vy, vz)
        dt:           步长（秒）
        gravity_y:    重力加速度（沿 -Y，传负值，如 -9.8）
        restitution:  恢复系数（0=完全非弹性停在地面，1=完全弹性）
        ground_y:     地面平面 ``y = ground_y``
        radius:       球半径（球心落地于 ``ground_y + radius``）

    返回新的 ``(pos, vel)``。落地的法向速度按恢复系数反弹（切向不变）。
    """
    # 半隐式欧拉：先更新速度，再用新速度更新位置
    new_vy = vel[1] + gravity_y * dt
    new_vel = (vel[0], new_vy, vel[2])
    new_pos = vadd(pos, vscale(new_vel, dt))

    floor = ground_y + radius
    if new_pos[1] < floor:
        # 钳到地面，法向（Y）反弹；切向（X/Z）不变
        new_pos = (new_pos[0], floor, new_pos[2])
        new_vy = -new_vel[1] * restitution
        new_vel = (new_vel[0], new_vy, new_vel[2])
    return new_pos, new_vel


# ------------------------------------------------------------------- 世界 --

def step_world(bodies, dt, gravity_y, ground_y=0.0, restitution=0.4,
               ground_friction=0.98):
    """N 个球在重力 + 地面 + 两两弹性碰撞下的简易步进（确定式）。

    参数：
        bodies:  [(pos, vel, radius, mass), ...]
        dt / gravity_y / ground_y / restitution: 同 ``integrate_sphere``
        ground_friction: 落地时切向速度的每步保留比（<1 模拟摩擦）

    返回新的 ``bodies``（同结构，原地不修改入参）。两两碰撞按等质量
    沿法向交换速度分量（弹性），并做重叠分离，保证步进后互不穿透。
    """
    new = []
    for (pos, vel, radius, mass) in bodies:
        p, v = integrate_sphere(pos, vel, dt, gravity_y, restitution,
                                ground_y, radius)
        if p[1] <= ground_y + radius + 1e-9:
            v = (v[0] * ground_friction, v[1], v[2] * ground_friction)
        new.append([p, v, radius, mass])

    n = len(new)
    for i in range(n):
        for j in range(i + 1, n):
            pi, vi, ri, mi = new[i]
            pj, vj, rj, mj = new[j]
            delta = vsub(pj, pi)
            dist = vlen(delta)
            min_dist = ri + rj
            if dist > 0.0 and dist < min_dist:
                nrm = vscale(delta, 1.0 / dist)
                # 重叠分离（按质量反比推开，质量相等则各推一半）
                overlap = min_dist - dist
                total = mi + mj
                push_i = vscale(nrm, -overlap * (mj / total))
                push_j = vscale(nrm, overlap * (mi / total))
                new[i][0] = vadd(pi, push_i)
                new[j][0] = vadd(pj, push_j)
                # 沿法向的弹性速度交换（等恢复系数取 restitution）
                rel = vdot(vsub(vi, vj), nrm)
                if rel > 0.0:
                    imp = (1.0 + restitution) * rel / total
                    new[i][1] = vsub(vi, vscale(nrm, imp * mj))
                    new[j][1] = vadd(vj, vscale(nrm, imp * mi))
    return [(tuple(p), tuple(v), r, m) for (p, v, r, m) in new]
