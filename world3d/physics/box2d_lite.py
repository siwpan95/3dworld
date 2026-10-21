# -*- coding: utf-8 -*-
"""Box2D-lite：轻量 2D 刚体约束求解（纯数学 / 无 GL）。

按 3dworld engine 层「纯数学 / 无 GL / headless 可编译校验」定位实现。
借鉴 **Box2D**（Erin Catto，MIT）的若干核心算法思路自研：

* 顺序冲量（sequential impulse）+ 累积冲量（warm start）的接触求解；
* Baumgarte 位置修正（带 slop）避免穿透抖动；
* 摩擦冲量夹在 ±μ·法向冲量内（Coulomb 摩擦锥）；
* OBB-OBB 用 SAT 找最小穿透轴 + 参考面/入射面裁剪得到 1~2 个接触点；
* 圆-圆 / 圆-OBB 解析接触流形。

**不搬 Box2D 的 C++ 源码、不照搬命名/结构**，仅按公开算法思路自研。
本模块刻意只做 headless 可行的 2D 子集（盒 + 圆 + 地面 + 基础求解）。
"""
from __future__ import annotations

import numpy as np

_EPS = 1e-9


def _rot(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s], [s, c]], dtype=float)


def _cross_vv(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _cross_rv(r, v):
    # 2D: r × v  -> 标量（r 叉乘向量得到角分量）
    return r[0] * v[1] - r[1] * v[0]


def _cross_rs(r, s):
    # 2D: 标量 s 叉乘向量 r -> 向量
    return np.array([-s * r[1], s * r[0]], dtype=float)


# --------------------------------------------------------------- 形状 --
class Shape:
    """2D 凸体基类。"""


class Box(Shape):
    def __init__(self, half):
        self.half = np.asarray(half, dtype=float)  # (hx, hy)


class Circle(Shape):
    def __init__(self, radius):
        self.radius = float(radius)


# --------------------------------------------------------------- 刚体 --
class Body:
    _next_id = 0

    def __init__(self, shape, mass=1.0, pos=(0.0, 0.0), angle=0.0,
                 vel=(0.0, 0.0), ang_vel=0.0, static=False, friction=0.3):
        self.id = Body._next_id
        Body._next_id += 1
        self.shape = shape
        self.pos = np.asarray(pos, dtype=float)
        self.angle = float(angle)
        self.vel = np.asarray(vel, dtype=float).copy()
        self.ang_vel = float(ang_vel)
        self.static = static
        self.friction = float(friction)
        self.sleeping = False
        if static or mass <= 0:
            self.inv_mass = 0.0
            self.inv_inertia = 0.0
        else:
            self.inv_mass = 1.0 / mass
            if isinstance(shape, Box):
                h = shape.half
                I = mass * (h[0] * h[0] + h[1] * h[1]) / 3.0
            elif isinstance(shape, Circle):
                I = mass * shape.radius * shape.radius / 2.0
            else:
                I = mass
            self.inv_inertia = 1.0 / I
        self._sleep_frames = 0

    def rot(self):
        return _rot(self.angle)

    def aabb(self):
        if isinstance(self.shape, Circle):
            r = self.shape.radius
            return self.pos - r, self.pos + r
        # OBB 近似为 AABB（测试用，足够 broadphase）
        R = self.rot()
        h = self.shape.half
        ext = np.abs(R) @ h
        return self.pos - ext, self.pos + ext


# ----------------------------------------------------- 碰撞检测 / 流形 --
def _collide_circles(a, b):
    pa, pb = a.pos, b.pos
    ra, rb = a.shape.radius, b.shape.radius
    d = pb - pa
    dist = np.linalg.norm(d)
    rsum = ra + rb
    if dist >= rsum:
        return None
    if dist < _EPS:
        n = np.array([0.0, 1.0])
        pen = rsum
        point = pa + n * ra
    else:
        n = d / dist
        pen = rsum - dist
        point = pa + n * ra
    return {"normal": n, "points": [(point, pen)], "sep": -pen}


def _collide_circle_box(circle, box):
    """圆（circle）对 OBB（box）。返回由 box 指向 circle 的法线。"""
    pc = circle.pos
    pb = box.pos
    R = box.rot()
    RT = R.T
    # 圆心变换到 box 局部
    d = RT @ (pc - pb)
    h = box.shape.half
    cl = np.clip(d, -h, h)
    closest = R @ cl + pb
    diff = pc - closest
    dist = np.linalg.norm(diff)
    if dist >= circle.shape.radius:
        return None
    if dist < _EPS:
        # 圆心在盒内：沿最近轴推出
        overlap = h - np.abs(d)
        ax = int(np.argmin(overlap))
        n = R @ np.eye(2)[ax]
        if d[ax] < 0:
            n = -n
        pen = overlap[ax] + circle.shape.radius
        point = pc - n * circle.shape.radius
    else:
        n = diff / dist
        pen = circle.shape.radius - dist
        point = pc - n * circle.shape.radius
    return {"normal": n, "points": [(point, pen)], "sep": -pen}


def _clip(v1, v2, n, o):
    """把线段 (v1,v2) 裁到半平面 dot(n,x) <= o，返回保留的点（0~2 个）。"""
    out = []
    d1 = np.dot(n, v1) - o
    d2 = np.dot(n, v2) - o
    if d1 <= 0:
        out.append(v1)
    if d2 <= 0:
        out.append(v2)
    if d1 * d2 < 0:
        t = d1 / (d1 - d2)
        out.append(v1 + t * (v2 - v1))
    return out


def _compute_incident_edge(h, pos, Rot, world_normal):
    """求入射盒上与参考面法线最反平行的那个面（2 个世界顶点）。

    借鉴 Box2D-lite ComputeIncidentEdge：把世界法线变换到入射盒局部系，
    选 |n| 最大分量对应的面，输出该面两顶点（已变换回世界系）。
    """
    n = -(Rot.T @ world_normal)
    n_abs = np.abs(n)
    if n_abs[0] > n_abs[1]:
        if n[0] > 0:
            c0 = np.array([h[0], -h[1]])
            c1 = np.array([h[0], h[1]])
        else:
            c0 = np.array([-h[0], h[1]])
            c1 = np.array([-h[0], -h[1]])
    else:
        if n[1] > 0:
            c0 = np.array([h[0], h[1]])
            c1 = np.array([-h[0], h[1]])
        else:
            c0 = np.array([-h[0], -h[1]])
            c1 = np.array([h[0], -h[1]])
    return [pos + Rot @ c0, pos + Rot @ c1]


def _collide_boxes(a, b):
    """OBB-OBB：SAT 找最小穿透轴 + 参考/入射面裁剪，得到 1~2 接触点。

    借鉴 **Box2D-lite**（Erin Catto，MIT）的 Collide 算法思路自研：
    选最小穿透面作参考面，求入射盒上最反平行的面并裁剪到参考面的侧向范围，
    裁剪后落在参考面前方的点即为接触点。法线方向沿用 Box2D-lite 的返回约定
    （供顺序冲量求解器直接使用，a.vel -= P；b.vel += P）。
    """
    hA = a.shape.half
    hB = b.shape.half
    posA, posB = a.pos, b.pos
    RotA, RotB = a.rot(), b.rot()
    RotAT, RotBT = RotA.T, RotB.T
    dp = posB - posA
    dA = RotAT @ dp
    dB = RotBT @ dp
    C = RotAT @ RotB
    absC = np.abs(C)
    absCT = absC.T
    faceA = np.abs(dA) - hA - absC @ hB
    if faceA[0] > 0 or faceA[1] > 0:
        return None
    faceB = np.abs(dB) - absCT @ hA - hB
    if faceB[0] > 0 or faceB[1] > 0:
        return None

    # 选最小穿透轴（带相对/绝对容差，避免抖动）
    axis = 0  # FACE_A_X
    separation = faceA[0]
    normal = RotA[:, 0] if dA[0] > 0 else -RotA[:, 0]
    rel_tol, abs_tol = 0.95, 0.01
    if faceA[1] > rel_tol * separation + abs_tol * hA[1]:
        axis, separation, normal = 1, faceA[1], (RotA[:, 1] if dA[1] > 0 else -RotA[:, 1])
    if faceB[0] > rel_tol * separation + abs_tol * hB[0]:
        axis, separation, normal = 2, faceB[0], (RotB[:, 0] if dB[0] > 0 else -RotB[:, 0])
    if faceB[1] > rel_tol * separation + abs_tol * hB[1]:
        axis, separation, normal = 3, faceB[1], (RotB[:, 1] if dB[1] > 0 else -RotB[:, 1])

    if axis == 0:
        front_normal = normal
        front = np.dot(posA, front_normal) + hA[0]
        side_normal = RotA[:, 1]
        side = np.dot(posA, side_normal)
        neg_side, pos_side = -side + hA[1], side + hA[1]
        incident = _compute_incident_edge(hB, posB, RotB, front_normal)
    elif axis == 1:
        front_normal = normal
        front = np.dot(posA, front_normal) + hA[1]
        side_normal = RotA[:, 0]
        side = np.dot(posA, side_normal)
        neg_side, pos_side = -side + hA[0], side + hA[0]
        incident = _compute_incident_edge(hB, posB, RotB, front_normal)
    elif axis == 2:
        front_normal = -normal
        front = np.dot(posB, front_normal) + hB[0]
        side_normal = RotB[:, 1]
        side = np.dot(posB, side_normal)
        neg_side, pos_side = -side + hB[1], side + hB[1]
        incident = _compute_incident_edge(hA, posA, RotA, front_normal)
    else:
        front_normal = -normal
        front = np.dot(posB, front_normal) + hB[1]
        side_normal = RotB[:, 0]
        side = np.dot(posB, side_normal)
        neg_side, pos_side = -side + hB[0], side + hB[0]
        incident = _compute_incident_edge(hA, posA, RotA, front_normal)

    cp = _clip(incident[0], incident[1], -side_normal, neg_side)
    if len(cp) < 2:
        return None
    cp = _clip(cp[0], cp[1], side_normal, pos_side)
    if len(cp) < 2:
        return None

    contacts = []
    for p in cp:
        sep = np.dot(front_normal, p) - front
        if sep <= 0:
            contacts.append((p - sep * front_normal, -sep))
    if not contacts:
        return None
    return {"normal": normal, "points": contacts, "sep": separation}


def collide(a, b):
    sa, sb = a.shape, b.shape
    if isinstance(sa, Circle) and isinstance(sb, Circle):
        return _collide_circles(a, b)
    if isinstance(sa, Circle) and isinstance(sb, Box):
        m = _collide_circle_box(a, b)
        return m
    if isinstance(sa, Box) and isinstance(sb, Circle):
        m = _collide_circle_box(b, a)
        if m is None:
            return None
        # _collide_circle_box 返回「由 box(a) 指向 circle(b)」即 a→b，符合约定，不取反
        return {"normal": m["normal"], "points": m["points"], "sep": m["sep"]}
    if isinstance(sa, Box) and isinstance(sb, Box):
        return _collide_boxes(a, b)
    return None


# ----------------------------------------------------------- 接触求解 --
class _Contact:
    __slots__ = ("pos", "normal", "pen", "pn", "pt", "bias", "mass_n", "mass_t", "r1", "r2")

    def __init__(self, pos, normal, pen):
        self.pos = np.asarray(pos, dtype=float)
        self.normal = np.asarray(normal, dtype=float)
        self.pen = float(pen)
        self.pn = 0.0
        self.pt = 0.0
        self.bias = 0.0
        self.mass_n = 0.0
        self.mass_t = 0.0
        self.r1 = np.zeros(2)
        self.r2 = np.zeros(2)


class Arbiter:
    def __init__(self, a, b, manifold):
        self.a = a
        self.b = b
        self.contacts = [_Contact(p, manifold["normal"], pen) for p, pen in manifold["points"]]
        self.normal = manifold["normal"]
        self.friction = np.sqrt(a.friction * b.friction)

    def pre_step(self, dt, slop=0.01, beta=0.2):
        a, b = self.a, self.b
        for c in self.contacts:
            r1 = c.pos - a.pos
            r2 = c.pos - b.pos
            c.r1 = r1
            c.r2 = r2
            rn1 = _cross_rv(r1, c.normal)
            rn2 = _cross_rv(r2, c.normal)
            k_n = a.inv_mass + b.inv_mass + a.inv_inertia * rn1 * rn1 + b.inv_inertia * rn2 * rn2
            c.mass_n = 1.0 / k_n if k_n > _EPS else 0.0
            t = np.array([-c.normal[1], c.normal[0]])
            rt1 = _cross_rv(r1, t)
            rt2 = _cross_rv(r2, t)
            k_t = a.inv_mass + b.inv_mass + a.inv_inertia * rt1 * rt1 + b.inv_inertia * rt2 * rt2
            c.mass_t = 1.0 / k_t if k_t > _EPS else 0.0
            c.bias = beta * (1.0 / dt) * max(0.0, c.pen - slop)
        # warm start：先施加上一帧累积冲量
        for c in self.contacts:
            P = c.normal * c.pn + np.array([-c.normal[1], c.normal[0]]) * c.pt
            a.vel -= a.inv_mass * P
            a.ang_vel -= a.inv_inertia * _cross_rv(c.r1, P)
            b.vel += b.inv_mass * P
            b.ang_vel += b.inv_inertia * _cross_rv(c.r2, P)

    def apply_impulse(self):
        a, b = self.a, self.b
        for c in self.contacts:
            r1, r2 = c.r1, c.r2
            dv = (b.vel + _cross_rs(r2, b.ang_vel)) - (a.vel + _cross_rs(r1, a.ang_vel))
            vn = np.dot(dv, c.normal)
            dPn = c.mass_n * (-vn + c.bias)
            newPn = max(c.pn + dPn, 0.0)
            dPn = newPn - c.pn
            c.pn = newPn
            P = c.normal * dPn
            a.vel -= a.inv_mass * P
            a.ang_vel -= a.inv_inertia * _cross_rv(r1, P)
            b.vel += b.inv_mass * P
            b.ang_vel += b.inv_inertia * _cross_rv(r2, P)
            # 摩擦
            dv = (b.vel + _cross_rs(r2, b.ang_vel)) - (a.vel + _cross_rs(r1, a.ang_vel))
            t = np.array([-c.normal[1], c.normal[0]])
            vt = np.dot(dv, t)
            dPt = c.mass_t * (-vt)
            maxPt = self.friction * c.pn
            newPt = np.clip(c.pt + dPt, -maxPt, maxPt)
            dPt = newPt - c.pt
            c.pt = newPt
            Pt = t * dPt
            a.vel -= a.inv_mass * Pt
            a.ang_vel -= a.inv_inertia * _cross_rv(r1, Pt)
            b.vel += b.inv_mass * Pt
            b.ang_vel += b.inv_inertia * _cross_rv(r2, Pt)


# --------------------------------------------------------------- 世界 --
class World:
    def __init__(self, gravity=(0.0, -9.8), slop=0.01, beta=0.2, sleep_lin=0.05,
                 sleep_ang=0.05, sleep_frames=60):
        self.bodies = []
        self.gravity = np.asarray(gravity, dtype=float)
        self.slop = slop
        self.beta = beta
        self.sleep_lin = sleep_lin
        self.sleep_ang = sleep_ang
        self.sleep_frames = sleep_frames
        self._arbiters = {}

    def add(self, body):
        self.bodies.append(body)
        return body

    def step(self, dt, iterations=10):
        if dt <= 0:
            return
        n = len(self.bodies)
        # broadphase + narrowphase
        new_arbiters = {}
        for i in range(n):
            for j in range(i + 1, n):
                bi, bj = self.bodies[i], self.bodies[j]
                if bi.static and bj.static:
                    continue
                lo_i, hi_i = bi.aabb()
                lo_j, hi_j = bj.aabb()
                if np.any(hi_i < lo_j) or np.any(hi_j < lo_i):
                    continue
                m = collide(bi, bj)
                if m is None:
                    continue
                key = (bi.id, bj.id)
                arb = Arbiter(bi, bj, m)
                prev = self._arbiters.get(key)
                if prev is not None and len(prev.contacts) == len(arb.contacts):
                    for c, pc in zip(arb.contacts, prev.contacts):
                        c.pn = pc.pn
                        c.pt = pc.pt
                new_arbiters[key] = arb
        self._arbiters = new_arbiters
        # 积分速度（重力）
        for b in self.bodies:
            if b.static or b.sleeping:
                continue
            b.vel += self.gravity * dt
        # 预计算 + warm start
        for arb in self._arbiters.values():
            arb.pre_step(dt, self.slop, self.beta)
        # 迭代求解速度约束
        for _ in range(iterations):
            for arb in self._arbiters.values():
                arb.apply_impulse()
        # 积分位置
        for b in self.bodies:
            if b.static or b.sleeping:
                continue
            b.pos += b.vel * dt
            b.angle += b.ang_vel * dt
            speed = np.linalg.norm(b.vel)
            aspd = abs(b.ang_vel)
            if speed < self.sleep_lin and aspd < self.sleep_ang:
                b._sleep_frames += 1
                if b._sleep_frames >= self.sleep_frames:
                    b.sleeping = True
                    b.vel = np.zeros(2)
                    b.ang_vel = 0.0
            else:
                b._sleep_frames = 0
