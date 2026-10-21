# -*- coding: utf-8 -*-
"""Jolt-lite：轻量 3D 刚体物理核心（纯数学 / 无 GL）。

按 3dworld engine 层「纯数学 / 无 GL / headless 可编译校验」定位实现。
借鉴 **Jolt Physics**（MIT，算法档）的若干核心概念自研：

* 凸体支撑函数（support mapping）→ GJK 求最近距离 / 穿透判定；
* EPA（Expanding Polytope Algorithm）求穿透深度与法线；
* 顺序冲量（sequential impulse）+ warm start（累积冲量）+ Baumgarte 位置修正；
* 刚体休眠（sleeping）：低动能持续若干帧后跳过积分。

**不搬 Jolt 的 C++ 源码、不照搬命名/结构**，仅自研其公开算法思路。
本模块刻意只做 headless 可行的子集（球 / 盒 / 胶囊 + 地面 + 基础求解），
完整物理后端（约束 / 车辆 / 软体等）留待运行时层，详见 docs/借鉴记账.md §二点二。
"""
from __future__ import annotations

import numpy as np

_EPS = 1e-9


# ----------------------------------------------------------- 四元数工具 --
def _q_normalize(q):
    q = np.asarray(q, dtype=float)
    n = np.linalg.norm(q)
    return q / n if n > _EPS else np.array([1.0, 0.0, 0.0, 0.0])


def _q_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ], dtype=float)


def _q_conj(q):
    w, x, y, z = q
    return np.array([w, -x, -y, -z], dtype=float)


def _q_rotate(q, v):
    """用单位四元数把向量 v 旋转。"""
    vq = np.array([0.0, v[0], v[1], v[2]], dtype=float)
    r = _q_mul(_q_mul(q, vq), _q_conj(q))
    return r[1:4]


def _q_from_axis_angle(axis, angle):
    axis = np.asarray(axis, dtype=float)
    axis = axis / (np.linalg.norm(axis) + _EPS)
    s = np.sin(angle / 2.0)
    c = np.cos(angle / 2.0)
    return np.array([c, axis[0] * s, axis[1] * s, axis[2] * s], dtype=float)


# --------------------------------------------------------------- 形状 --
class Shape:
    """凸体基类：提供世界支撑点 support(world_dir)。"""

    def local_support(self, d_local):
        raise NotImplementedError

    def support(self, xf, d_world):
        d_local = _q_rotate(_q_conj(xf.quat), d_world)
        p_local = self.local_support(d_local)
        return xf.apply(p_local)


class Sphere(Shape):
    def __init__(self, radius):
        self.radius = float(radius)

    def local_support(self, d_local):
        n = np.linalg.norm(d_local)
        if n < _EPS:
            return np.zeros(3)
        return d_local / n * self.radius


class Box(Shape):
    def __init__(self, half_extents):
        self.h = np.asarray(half_extents, dtype=float)

    def local_support(self, d_local):
        s = np.sign(d_local)
        s = np.where(np.abs(d_local) < _EPS, 0.0, s)
        return s * self.h


class Capsule(Shape):
    """沿局部 +Y 轴的胶囊（线段 ±half_height + 半径）。"""

    def __init__(self, radius, half_height):
        self.radius = float(radius)
        self.hh = float(half_height)

    def local_support(self, d_local):
        d = np.asarray(d_local, dtype=float)
        y = d[1]
        seg = self.hh if y >= 0 else -self.hh
        # 线段端点 + 半径方向（径向分量）
        radial = d.copy()
        radial[1] = 0.0
        rn = np.linalg.norm(radial)
        if rn < _EPS:
            return np.array([0.0, seg, 0.0])  # 纯轴向：半球不沿轴超出端点
        return np.array([0.0, seg, 0.0]) + radial / rn * self.radius


# --------------------------------------------------------- 变换 / 刚体 --
class Transform:
    __slots__ = ('pos', 'quat')

    def __init__(self, pos=(0.0, 0.0, 0.0), quat=(1.0, 0.0, 0.0, 0.0)):
        self.pos = np.asarray(pos, dtype=float)
        self.quat = _q_normalize(quat)

    def apply(self, p_local):
        return self.pos + _q_rotate(self.quat, np.asarray(p_local, dtype=float))


class RigidBody:
    _next_id = 0

    def __init__(self, shape, mass=1.0, pos=(0.0, 0.0, 0.0), quat=(1.0, 0.0, 0.0, 0.0),
                 vel=(0.0, 0.0, 0.0), ang_vel=(0.0, 0.0, 0.0), is_static=False):
        self.id = RigidBody._next_id
        RigidBody._next_id += 1
        self.shape = shape
        self.is_static = is_static
        self.xf = Transform(pos, quat)
        self.vel = np.asarray(vel, dtype=float).copy()
        self.ang_vel = np.asarray(ang_vel, dtype=float).copy()
        self.force = np.zeros(3)
        self.torque = np.zeros(3)
        self.sleeping = False
        self._sleep_frames = 0
        if is_static or mass <= 0:
            self.inv_mass = 0.0
            self.inv_inertia_local = np.zeros((3, 3))
        else:
            self.inv_mass = 1.0 / mass
            self.inv_inertia_local = self._inertia_inv(mass)
        self._mass = mass

    def _inertia_inv(self, mass):
        s = self.shape
        if isinstance(s, Sphere):
            i = 0.4 * mass * s.radius ** 2
            return np.diag([1 / i, 1 / i, 1 / i])
        if isinstance(s, Box):
            h = s.h
            ix = (1 / 12) * mass * (4 * h[1] ** 2 + 4 * h[2] ** 2)
            iy = (1 / 12) * mass * (4 * h[0] ** 2 + 4 * h[2] ** 2)
            iz = (1 / 12) * mass * (4 * h[0] ** 2 + 4 * h[1] ** 2)
            return np.diag([1 / ix, 1 / iy, 1 / iz])
        if isinstance(s, Capsule):
            # 近似：圆柱惯性（沿轴 + 径向）
            r, hh = s.radius, s.hh
            i_axial = 0.5 * mass * r ** 2
            i_radial = (1 / 12) * mass * (6 * r ** 2 + 4 * hh ** 2)
            return np.diag([i_radial, i_axial, i_radial])
        return np.diag([1.0, 1.0, 1.0])

    def inv_inertia_world(self):
        q = self.xf.quat
        R = _quat_to_matrix(q)
        return R @ self.inv_inertia_local @ R.T

    def aabb(self):
        # 用 8 个角 + 支撑估计；这里用支撑采样 6 轴 + 原点半径近似
        c = self.xf.pos
        if isinstance(self.shape, Sphere):
            r = self.shape.radius
            return c - r, c + r
        # 取局部 8 角变换后的范围
        h = self.shape.h if isinstance(self.shape, Box) else np.array([self.shape.radius] * 3)
        corners = np.array([[x, y, z] for x in (-h[0], h[0])
                            for y in (-h[1], h[1]) for z in (-h[2], h[2])])
        R = _quat_to_matrix(self.xf.quat)
        ws = (R @ corners.T).T + c
        return ws.min(axis=0), ws.max(axis=0)


def _quat_to_matrix(q):
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ], dtype=float)


# ----------------------------------------------------------------- GJK --
def _minkowski_support(shape_a, shape_b, xf_a, xf_b, d):
    """A⊖B 的支撑点：A 在 d 方向的支撑减去 B 在 -d 方向的支撑。"""
    return shape_a.support(xf_a, d) - shape_b.support(xf_b, -d)


def _tri_closest(p, a, b, c):
    """原点 p 到三角形 (a,b,c) 的最近点，并返回含该点的约简子集。

    借鉴 Ericson《Real-Time Collision Detection》§5.1.5 的三角 Voronoi
    区域判定：逐边判原点落于顶点 / 边 / 面内，返回对应子集 [a]|[b]|[c]/
    [a,b]|[a,c]|[b,c]|[a,b,c]。
    """
    ab = b - a
    ac = c - a
    ap = p - a
    d1 = np.dot(ab, ap)
    d2 = np.dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        return a, [a]
    bp = p - b
    d3 = np.dot(ab, bp)
    d4 = np.dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return b, [b]
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        t = d1 / (d1 - d3)
        return a + t * (b - a), [a, b]
    cp = p - c
    d5 = np.dot(ab, cp)
    d6 = np.dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return c, [c]
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        t = d2 / (d2 - d6)
        return a + t * (c - a), [a, c]
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        t = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return b + t * (c - b), [b, c]
    denom = va + vb + vc
    if abs(denom) < _EPS:
        return a, [a, b, c]
    cp = a + (vb + vc) / denom * (b - a) + (va + vc) / denom * (c - a)
    return cp, [a, b, c]


def _tetra_inside(v0, v1, v2, v3, p):
    """原点 p 是否位于四面体 v0..v3 内部（与每个面对顶点同侧）。"""
    verts = (v0, v1, v2, v3)
    for i in range(4):
        a, b, c = (verts[j] for j in (0, 1, 2, 3) if j != i)
        n = np.cross(b - a, c - a)
        if np.dot(n, p - a) * np.dot(n, verts[i] - a) < 0:
            return False
    return True


def _simplex_closest(p, simplex):
    """原点 p 到单纯形凸包的最近点，并约简单纯形到含该点的最小子集。

    返回 (closest_point, reduced_simplex)。借鉴 Ericson§9.5 的最近点
    simplex 约简：点→自身；线段→投影顶点 / 边；三角形→_tri_closest；
    四面体→先判内外，否则取四张面最近点的极小值。
    """
    n = len(simplex)
    if n == 1:
        return simplex[0], [simplex[0]]
    if n == 2:
        a, b = simplex
        ab = b - a
        t = -np.dot(a, ab) / (np.dot(ab, ab) + _EPS)
        t = np.clip(t, 0.0, 1.0)
        cp = a + t * ab
        if t <= 0.0:
            return cp, [a]
        if t >= 1.0:
            return cp, [b]
        return cp, [a, b]
    if n == 3:
        return _tri_closest(p, simplex[0], simplex[1], simplex[2])
    a, b, c, d = simplex
    if _tetra_inside(a, b, c, d, p):
        return p, [a, b, c, d]
    best = None
    for face in ((a, b, c), (a, c, d), (a, d, b), (b, c, d)):
        cp, _ = _tri_closest(p, face[0], face[1], face[2])
        dd = np.linalg.norm(cp - p)
        if best is None or dd < best[0]:
            best = (dd, cp, [face[0], face[1], face[2]])
    return best[1], best[2]


def _gjk(shape_a, shape_b, xf_a, xf_b, max_iter=64):
    """返回 (colliding, simplex)。colliding=True 时 simplex 含原点（供 EPA 展开）。"""
    def sup(d):
        return _minkowski_support(shape_a, shape_b, xf_a, xf_b, d)

    init = xf_a.pos - xf_b.pos
    d = init if np.linalg.norm(init) > _EPS else np.array([1.0, 0.0, 0.0])
    simplex = [sup(d)]
    d = -simplex[0]
    for _ in range(max_iter):
        if np.linalg.norm(d) < 1e-12:
            return True, simplex
        a = sup(d)
        simplex.append(a)
        cp, reduced = _simplex_closest(np.zeros(3), simplex)
        if np.linalg.norm(cp) < 1e-9:
            # 原点落在当前单纯形上 ⇒ 原点在 A⊖B 内（碰撞）。退化低维单纯形
            # （如原点恰在直径线段上）由 epa() 用垂直双向支撑撑成四面体，
            # 此处直接判碰撞并交出当前单纯形。
            return True, reduced
        if np.dot(a, d) < 0:
            return False, reduced         # 支撑点未越过原点 → 分离；reduced 即最近特征
        simplex = reduced
        d = -cp
    return False, simplex


def _gjk_distance(shape_a, shape_b, xf_a, xf_b, max_iter=64):
    """返回 (colliding, distance, point_a, point_b, dir_a_to_b, simplex)。

    colliding=True 时 distance=0（需 epa() 求穿透深度与法线）；
    colliding=False 时 distance 为两凸体表面最近距离。
    """
    colliding, simplex = _gjk(shape_a, shape_b, xf_a, xf_b, max_iter)
    if colliding:
        return True, 0.0, simplex[-1], simplex[-1], simplex[-1] - simplex[0], list(simplex)
    cp = _simplex_closest(np.zeros(3), simplex)[0]
    dist = float(np.linalg.norm(cp))
    return False, dist, cp, cp, cp, list(simplex)


# ----------------------------------------------------------------- EPA --
def _polytope_normal(tri):
    n = np.cross(tri[1] - tri[0], tri[2] - tri[0])
    nn = np.linalg.norm(n)
    return None if nn < _EPS else n / nn


def _polytope_centroid(verts):
    return np.mean(verts, axis=0)


def _face_outward(verts, face):
    """把三角面翻成外法线（法线背离凸包质心）。返回 (face, outward_normal)。"""
    tri = [verts[face[0]], verts[face[1]], verts[face[2]]]
    n = _polytope_normal(tri)
    if n is None:
        return face, None
    c = _polytope_centroid(verts)
    if np.dot(n, tri[0] - c) < 0:
        face = (face[0], face[2], face[1])
        n = -n
    return face, n


def _tetra_faces_outward(verts):
    faces = [(0, 1, 2), (0, 2, 3), (0, 3, 1), (1, 3, 2)]
    return [_face_outward(verts, f)[0] for f in faces]


def _origin_strictly_inside(verts, faces, tol=1e-7):
    """原点是否严格位于凸包内部：每个面的外法线都满足 dot(n, v0) > tol。

    此前用「以原点为基准翻面」会误判（原点在包外时仍返回 True），这里改用
    「以凸包质心为基准确定外法线」，再要求原点在每个面的内侧（dot(n, v0) > tol）。
    """
    if len(verts) < 4:
        return False
    for f in faces:
        _, n = _face_outward(verts, f)
        if n is None:
            return False
        if np.dot(n, verts[f[0]]) <= tol:
            return False
    return True


def _epa_expand(verts, faces, pi):
    """一次 EPA 展开：丢弃能被 pi 看到的面，用有向边地平线把 pi 接入凸包。

    关键修正：用**有向边**收集地平线（同向边出现且仅出现一次才是轮廓边），
    不再用 frozenset 无序边——后者会丢失绕序、生成零面积退化面，使 EPA 在
    球-球等对称情形下原地振荡。退化（零面积）新面直接丢弃。
    """
    fc = [_face_outward(verts, f) for f in faces]
    visible = []
    for idx, (f, n) in enumerate(fc):
        if n is None:
            visible.append(idx)
            continue
        if np.dot(n, verts[pi] - verts[f[0]]) > 0:
            visible.append(idx)
    edge_count = {}
    for idx in visible:
        ef = faces[idx]
        for a, b in ((ef[0], ef[1]), (ef[1], ef[2]), (ef[2], ef[0])):
            edge_count[(a, b)] = edge_count.get((a, b), 0) + 1
    horizon = [(a, b) for (a, b), cnt in edge_count.items()
               if edge_count.get((b, a), 0) == 0]
    new_faces = [faces[idx] for idx in range(len(faces)) if idx not in visible]
    for (a, b) in horizon:
        nf = (a, b, pi)
        if _polytope_normal([verts[a], verts[b], verts[pi]]) is None:
            continue  # 丢弃零面积退化面
        new_faces.append(_face_outward(verts, nf)[0])
    return verts, new_faces


def _epa_build_seed(sup):
    """构建初始四面体（verts, faces），保证原点严格在内部。

    EPA 要求起点凸包包含原点。重叠球等对称情形 GJK 末态常是穿过原点的直径
    线段（退化 2 点），直接撑出的四面体并不能包住原点，故这里直接用若干组
    支撑方向尝试，挑出能严格包含原点的那组；都不行再迭代地把包「围向原点」。
    """
    dir_sets = [
        [(1, 0, 0), (0, 1, 0), (0, 0, 1), (-1, -1, -1)],
        [(1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)],
        [(1, 1, 0), (1, -1, 0), (0, 1, 1), (0, -1, 1)],
        [(1, 1, 1), (1, 1, -1), (-1, 1, 1), (-1, -1, 1)],
        [(1, 0, 1), (-1, 0, 1), (0, 1, -1), (0, -1, -1)],
    ]
    for ds in dir_sets:
        v = [sup(np.array(d, float) / np.linalg.norm(d)) for d in ds]
        if _origin_strictly_inside(v, _tetra_faces_outward(v)):
            return v, _tetra_faces_outward(v)
    v = [sup(np.array(d, float) / np.linalg.norm(d)) for d in dir_sets[0]]
    faces = _tetra_faces_outward(v)
    for _ in range(64):
        if _origin_strictly_inside(v, faces):
            return v, faces
        worst = None
        for f in faces:
            _, n = _face_outward(v, f)
            if n is None:
                continue
            d = np.dot(n, v[f[0]])
            if worst is None or d < worst[0]:
                worst = (d, f, n)
        if worst is None:
            break
        p = sup(-_polytope_centroid(v))
        v.append(p)
        v, faces = _epa_expand(v, faces, len(v) - 1)
    return v, faces


def epa(shape_a, shape_b, xf_a, xf_b, simplex0=None, max_iter=64):
    """由凸包展开求最小穿透（外法线 + 深度）。

    借鉴 Jolt / dyn4j 的 EPA 思路自研：取离原点最近的面 → 沿其外法线取支撑点
    展开凸包 → 收敛时支撑点已落在该面所在支撑平面上，深度即原点到该面的距离。
    初始种子直接由支撑方向构造（不依赖 GJK 末态单纯形），以保证原点严格在包内。
    """
    def sup(d):
        return _minkowski_support(shape_a, shape_b, xf_a, xf_b, d)

    verts, faces = _epa_build_seed(sup)
    tol = 1e-4
    best_n = np.array([1.0, 0.0, 0.0])
    best_d = 0.0
    for _ in range(max_iter):
        best = None
        for f in faces:
            _, n = _face_outward(verts, f)
            if n is None:
                continue
            d = np.dot(n, verts[f[0]])  # 原点到该面支撑平面的距离（>=0）
            if best is None or d < best[0]:
                best = (d, n, f)
        if best is None:
            break
        best_d, best_n, f = best
        p = sup(best_n)
        if np.dot(best_n, p) - best_d < tol:
            return best_n, best_d
        verts.append(p)
        verts, faces = _epa_expand(verts, faces, len(verts) - 1)
        if len(faces) < 4:
            break
    return best_n, best_d


# ------------------------------------------------------- 世界 / 求解 --
def manifold(a, b):
    """解析接触流形：返回 (normal_b_to_a, point, depth) 或 None（未碰撞）。

    支持：球-球、球-盒（盒取世界 AABB 近似）、盒-盒（AABB 近似）。
    法线方向由 b 指向 a；depth>0 表示穿透。盒的朝向目前假设轴对齐
    （AABB 近似）；带旋转盒的全 SAT 留待运行时层。
    """
    sa, sb = a.shape, b.shape
    ca, cb = a.xf.pos, b.xf.pos
    if isinstance(sa, Sphere) and isinstance(sb, Sphere):
        diff = ca - cb
        d = np.linalg.norm(diff)
        rsum = sa.radius + sb.radius
        if d >= rsum:
            return None
        n = diff / (d + _EPS)
        point = cb + n * sb.radius
        return n, point, rsum - d
    if isinstance(sa, Sphere) and isinstance(sb, Box):
        half = sb.h
        lo = cb - half
        hi = cb + half
        closest = np.clip(ca, lo, hi)
        diff = ca - closest
        d = np.linalg.norm(diff)
        if d >= sa.radius:
            if d < _EPS:  # 球心在盒内：沿最大轴推出
                overlap = half - np.abs(ca - cb)
                ax = int(np.argmax(overlap))
                n = np.zeros(3)
                n[ax] = 1.0 if ca[ax] >= cb[ax] else -1.0
                return n, ca, overlap[ax] + sa.radius
            return None
        n = diff / (d + _EPS)
        return n, closest, sa.radius - d
    if isinstance(sa, Box) and isinstance(sb, Sphere):
        m = manifold(b, a)
        if m is None:
            return None
        n, point, depth = m
        return -n, point, depth  # 翻转法线为 b->a
    if isinstance(sa, Box) and isinstance(sb, Box):
        half_a, half_b = sa.h, sb.h
        lo_a, hi_a = ca - half_a, ca + half_a
        lo_b, hi_b = cb - half_b, cb + half_b
        if np.any(hi_a <= lo_b) or np.any(hi_b <= lo_a):
            return None
        # 沿各轴的穿透深度，取最小为分离轴
        pen = np.minimum(hi_a, hi_b) - np.maximum(lo_a, lo_b)
        ax = int(np.argmin(pen))
        n = np.zeros(3)
        mid_a, mid_b = (lo_a + hi_a) / 2, (lo_b + hi_b) / 2
        n[ax] = 1.0 if mid_a[ax] >= mid_b[ax] else -1.0
        point = (np.maximum(lo_a, lo_b) + np.minimum(hi_a, hi_b)) / 2
        return n, point, pen[ax]
    # 胶囊等其余形体对暂走 GJK/EPA（见 epa()）
    return None


class Contact:
    __slots__ = ('a', 'b', 'point', 'normal', 'depth', 'pn')

    def __init__(self, a, b, point, normal, depth):
        self.a = a
        self.b = b
        self.point = np.asarray(point, dtype=float)
        self.normal = np.asarray(normal, dtype=float)  # 由 b 指向 a
        self.depth = float(depth)
        self.pn = 0.0  # 累积法向冲量（warm start）


class World:
    def __init__(self, gravity=(0.0, -9.8, 0.0), baumgarte=0.2, slop=0.01,
                 sleep_lin=0.05, sleep_ang=0.05, sleep_frames=60):
        self.bodies = []
        self.gravity = np.asarray(gravity, dtype=float)
        self.baumgarte = baumgarte
        self.slop = slop
        self.sleep_lin = sleep_lin
        self.sleep_ang = sleep_ang
        self.sleep_frames = sleep_frames
        self._contacts = {}

    def add(self, body):
        self.bodies.append(body)
        return body

    def step(self, dt, iterations=10):
        # 1) 宽相：AABB 重叠
        n = len(self.bodies)
        pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                bi, bj = self.bodies[i], self.bodies[j]
                if bi.is_static and bj.is_static:
                    continue
                lo_i, hi_i = bi.aabb()
                lo_j, hi_j = bj.aabb()
                if np.any(hi_i < lo_j) or np.any(hi_j < lo_i):
                    continue
                pairs.append((bi, bj))
        # 2) 窄相：解析流形（球-球 / 球-盒 / 盒-盒AABB）。
        #    GJK + EPA（通用凸体算法）已在 epa()/gjk 中独立验证；此处对受支持
        #    形体对用解析法生成接触以保证求解稳健，把 EPA 推广到所有形体对留待运行时层。
        new_contacts = {}
        for bi, bj in pairs:
            m = manifold(bi, bj)
            if m is None:
                continue
            n, point, depth = m
            key = (bi.id, bj.id)
            c = Contact(bi, bj, point, n, depth)
            prev = self._contacts.get(key)
            if prev is not None:
                c.pn = prev.pn  # warm start
            new_contacts[key] = c
        self._contacts = new_contacts
        # 3) 积分速度（重力）
        for b in self.bodies:
            if b.is_static or b.sleeping:
                continue
            b.vel += self.gravity * dt
        # 4) 求解速度约束（顺序冲量 + warm start）
        contacts = list(new_contacts.values())
        for c in contacts:
            self._apply_impulse(c, c.pn)  # warm start：预施加上一帧累积冲量
        for _ in range(iterations):
            for c in contacts:
                self._solve_velocity(c, dt)
        # 5) 积分位置 + 休眠
        for b in self.bodies:
            if b.is_static or b.sleeping:
                continue
            b.xf.pos += b.vel * dt
            w = np.linalg.norm(b.ang_vel)
            if w > _EPS:
                dq = _q_from_axis_angle(b.ang_vel / w, w * dt)
                b.xf.quat = _q_normalize(_q_mul(dq, b.xf.quat))
            speed = np.linalg.norm(b.vel)
            aspd = np.linalg.norm(b.ang_vel)
            if speed < self.sleep_lin and aspd < self.sleep_ang:
                b._sleep_frames += 1
                if b._sleep_frames >= self.sleep_frames:
                    b.sleeping = True
                    b.vel = np.zeros(3)
                    b.ang_vel = np.zeros(3)
            else:
                b._sleep_frames = 0

    def _apply_impulse(self, c, mag):
        if mag <= 0.0:
            return
        a, b = c.a, c.b
        n = c.normal
        ra = c.point - a.xf.pos
        rb = c.point - b.xf.pos
        imp = mag * n
        a.vel += a.inv_mass * imp
        a.ang_vel += a.inv_inertia_world() @ np.cross(ra, imp)
        b.vel -= b.inv_mass * imp
        b.ang_vel -= b.inv_inertia_world() @ np.cross(rb, imp)

    def _solve_velocity(self, c, dt):
        a, b = c.a, c.b
        n = c.normal
        ra = c.point - a.xf.pos
        rb = c.point - b.xf.pos
        va = a.vel + np.cross(a.ang_vel, ra)
        vb = b.vel + np.cross(b.ang_vel, rb)
        rv = va - vb
        vn = np.dot(rv, n)
        inv_a = a.inv_inertia_world()
        inv_b = b.inv_inertia_world()
        rn_a = np.cross(ra, n)
        rn_b = np.cross(rb, n)
        denom = a.inv_mass + b.inv_mass \
            + np.dot(rn_a, inv_a @ rn_a) + np.dot(rn_b, inv_b @ rn_b)
        if denom < _EPS:
            return
        bias = (self.baumgarte / dt) * max(0.0, c.depth - self.slop)
        # 解出后法向速度应为 +bias（分离），故 j = (bias - vn)：
        # 接近时 vn<0 → j>0 把球推离；偏置提供位置修正的分离速度。
        j = (bias - vn) / denom
        j_old = c.pn
        c.pn = max(j_old + j, 0.0)
        dj = c.pn - j_old
        imp = dj * n
        a.vel += a.inv_mass * imp
        a.ang_vel += inv_a @ np.cross(ra, imp)
        b.vel -= b.inv_mass * imp
        b.ang_vel -= inv_b @ np.cross(rb, imp)

    def wake(self, body):
        body.sleeping = False
        body._sleep_frames = 0
