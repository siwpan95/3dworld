# -*- coding: utf-8 -*-
"""鼠标拾取：屏幕坐标 -> 世界射线 -> 与包围体/三角形求交。

三步::

    屏幕像素 --(1)--> NDC --(2)--> 世界射线 --(3)--> 求交（AABB 粗 -> 三角形精）

第 (2) 步的反投影：把 NDC 点 (x, y, -1) 和 (x, y, 1) 分别乘逆 view-projection
得到近/远平面上的两个世界点，射线 = 近点指向远点。**必须做透视除法**
（除以 w），否则近点位置是错的——这是拾取偏最常见的成因。

第 (3) 步先测包围盒（便宜，几十次浮点），命中后再逐三角形做
Möller-Trumbore（贵，一个 500 面的模型就是 500 次），不中的直接跳过。
"""
import math
from collections import namedtuple

from engine.frustum import AABB
from engine.mathkit import mat4_inverse

__all__ = ['Ray', 'Hit', 'screen_to_ndc', 'ray_from_screen',
           'ray_aabb', 'ray_sphere', 'ray_triangle', 'ray_mesh', 'Picker']


Ray = namedtuple('Ray', 'origin direction')
Hit = namedtuple('Hit', 'drawable distance point triangle')


def screen_to_ndc(x, y, width, height):
    """屏幕像素（原点左上，pyglet 约定）-> NDC [-1, 1]。"""
    return (2.0 * x / max(1.0, float(width)) - 1.0,
            2.0 * y / max(1.0, float(height)) - 1.0)


def _unproject(ndc_x, ndc_y, ndc_z, inv_vp):
    x = inv_vp[0] * ndc_x + inv_vp[4] * ndc_y + inv_vp[8] * ndc_z + inv_vp[12]
    y = inv_vp[1] * ndc_x + inv_vp[5] * ndc_y + inv_vp[9] * ndc_z + inv_vp[13]
    z = inv_vp[2] * ndc_x + inv_vp[6] * ndc_y + inv_vp[10] * ndc_z + inv_vp[14]
    w = inv_vp[3] * ndc_x + inv_vp[7] * ndc_y + inv_vp[11] * ndc_z + inv_vp[15]
    w = w or 1e-6
    return (x / w, y / w, z / w)


def ray_from_screen(x, y, width, height, inv_view_projection, origin=None):
    """屏幕像素 -> 世界射线（近平面点 -> 远平面点）。"""
    nx, ny = screen_to_ndc(x, y, width, height)
    near = _unproject(nx, ny, -1.0, inv_view_projection)
    far = _unproject(nx, ny, 1.0, inv_view_projection)
    if origin is None:
        origin = near
    dx, dy, dz = far[0] - origin[0], far[1] - origin[1], far[2] - origin[2]
    l = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
    return Ray(tuple(origin), (dx / l, dy / l, dz / l))


# ------------------------------------------------------------------ 求交 --
def ray_aabb(origin, direction, aabb, t_max=1e9):
    """slab 法求交，返回最近正距离 t（未命中返回 None）。

    对每个轴算 (tmin, tmax)，取所有轴的 tmin 最大值 / tmax 最小值；
    方向分量为 0 时要用 1/0 = ±inf 处理（靠 IEEE 语义自动成立，
    但要小心 0*inf = NaN 的情况，所以显式分支）。
    """
    tmin, tmax = 0.0, t_max
    for i in range(3):
        o, d = origin[i], direction[i]
        if abs(d) < 1e-9:
            if o < aabb.min[i] or o > aabb.max[i]:
                return None
            continue
        inv = 1.0 / d
        t1 = (aabb.min[i] - o) * inv
        t2 = (aabb.max[i] - o) * inv
        if t1 > t2:
            t1, t2 = t2, t1
        tmin = max(tmin, t1)
        tmax = min(tmax, t2)
        if tmin > tmax:
            return None
    return tmin


def ray_sphere(origin, direction, center, radius):
    """射线-球求交，返回最近正 t（None = 未命中）。"""
    ox, oy, oz = origin[0] - center[0], origin[1] - center[1], origin[2] - center[2]
    b = ox * direction[0] + oy * direction[1] + oz * direction[2]
    c = ox * ox + oy * oy + oz * oz - radius * radius
    disc = b * b - c
    if disc < 0.0:
        return None
    s = math.sqrt(disc)
    t0, t1 = -b - s, -b + s
    if t0 >= 0.0:
        return t0
    return t1 if t1 >= 0.0 else None


def ray_triangle(origin, direction, a, b, c, eps=1e-9):
    """Möller-Trumbore：返回 t（None = 未命中，含背面）。"""
    e1 = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    e2 = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    h = (direction[1] * e2[2] - direction[2] * e2[1],
         direction[2] * e2[0] - direction[0] * e2[2],
         direction[0] * e2[1] - direction[1] * e2[0])
    det = e1[0] * h[0] + e1[1] * h[1] + e1[2] * h[2]
    if abs(det) < eps:
        return None                       # 射线与三角形平行
    inv = 1.0 / det
    s = (origin[0] - a[0], origin[1] - a[1], origin[2] - a[2])
    u = (s[0] * h[0] + s[1] * h[1] + s[2] * h[2]) * inv
    if u < 0.0 or u > 1.0:
        return None
    q = (s[1] * e1[2] - s[2] * e1[1],
         s[2] * e1[0] - s[0] * e1[2],
         s[0] * e1[1] - s[1] * e1[0])
    v = (direction[0] * q[0] + direction[1] * q[1] + direction[2] * q[2]) * inv
    if v < 0.0 or u + v > 1.0:
        return None
    t = (e2[0] * q[0] + e2[1] * q[1] + e2[2] * q[2]) * inv
    return t if t > eps else None


def ray_mesh(ray, mesh, precise=True, t_max=1e9):
    """对一个 Mesh 求交：先包围盒，再（可选）逐三角形。

    Args:
        precise: True 时做三角形级精确拾取（返回命中三角形序号）；
                 False 只测包围盒（够用于"点选大体位置"的交互）。
    """
    box = getattr(mesh, 'world_aabb', None)
    if box is None:
        return None
    t_box = ray_aabb(ray.origin, ray.direction, box, t_max)
    if t_box is None:
        return None
    if not precise:
        return Hit(mesh, t_box, _point_at(ray, t_box), -1)

    data = mesh.data
    pos = data.positions
    idx = data.indices or list(range(data.vertex_count))
    best_t, best_i = t_max, -1
    for t in range(0, len(idx) - 2, 3):
        ia, ib, ic = idx[t] * 3, idx[t + 1] * 3, idx[t + 2] * 3
        a = (pos[ia], pos[ia + 1], pos[ia + 2])
        b = (pos[ib], pos[ib + 1], pos[ib + 2])
        c = (pos[ic], pos[ic + 1], pos[ic + 2])
        # 局部空间的三角形要变到世界空间：用 mesh.model 变换
        m = mesh.model
        a = _xform(m, a)
        b = _xform(m, b)
        c = _xform(m, c)
        tt = ray_triangle(ray.origin, ray.direction, a, b, c)
        if tt is not None and tt < best_t:
            best_t, best_i = tt, t // 3
    if best_i < 0:
        return Hit(mesh, t_box, _point_at(ray, t_box), -1)
    return Hit(mesh, best_t, _point_at(ray, best_t), best_i)


def _xform(m, p):
    x, y, z = p
    return (m[0] * x + m[4] * y + m[8] * z + m[12],
            m[1] * x + m[5] * y + m[9] * z + m[13],
            m[2] * x + m[6] * y + m[10] * z + m[14])


def _point_at(ray, t):
    return (ray.origin[0] + ray.direction[0] * t,
            ray.origin[1] + ray.direction[1] * t,
            ray.origin[2] + ray.direction[2] * t)


# ------------------------------------------------------------------ 拾取器 --
class Picker:
    """把"屏幕点一下"变成"命中了哪个物体"。

    用法::

        picker = Picker()
        hit = picker.pick(mx, my, window.width, window.height,
                          camera, scene, precise=True)
    """

    def __init__(self):
        self.last_ray = None

    def ray(self, x, y, width, height, camera, aspect):
        inv_vp = camera.inverse_view_projection(aspect)
        r = ray_from_screen(x, y, width, height, inv_vp, camera.position)
        self.last_ray = r
        return r

    def pick(self, x, y, width, height, camera, scene, aspect=None,
             precise=True, t_max=1e9):
        """返回最近的 :class:`Hit`（未命中返回 None）。"""
        aspect = aspect if aspect else (width / max(1.0, float(height)))
        ray = self.ray(x, y, width, height, camera, aspect)
        best = None
        for d in scene.drawables():
            hit = ray_mesh(ray, d, precise=precise, t_max=t_max)
            if hit is not None and (best is None or hit.distance < best.distance):
                best = hit
        return best

    def pick_all(self, x, y, width, height, camera, scene, aspect=None,
                 precise=True, t_max=1e9):
        """返回全部命中（按距离升序）。"""
        aspect = aspect if aspect else (width / max(1.0, float(height)))
        ray = self.ray(x, y, width, height, camera, aspect)
        hits = []
        for d in scene.drawables():
            hit = ray_mesh(ray, d, precise=precise, t_max=t_max)
            if hit is not None:
                hits.append(hit)
        hits.sort(key=lambda h: h.distance)
        return hits
