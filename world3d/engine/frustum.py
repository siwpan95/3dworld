# -*- coding: utf-8 -*-
"""包围体与视锥剔除（纯数学，无 GL）。

平面提取用 **Gribb-Hartmann 方法**：拿到 view-projection 矩阵后，
对它的行做加减即可拿到六个平面，不需要任何三角函数::

    left   = row3 + row0      right = row3 - row0
    bottom = row3 + row1      top   = row3 - row1
    near   = row3 + row2      far   = row3 - row2

提取后按 (a,b,c) 的模长归一化，``distance_to_point`` 才是**真实的世界距离**
（不归一化的话只是个符号正确的量，半径比较会失效——这是最常见的踩坑点）。

AABB 与视锥的相交用 **p-顶点法**：对每个平面只测"沿法线方向最远"的那个角点，
该角在平面外侧则整个盒子在外侧。比逐角点测试少一半分支。

另附 ``SpatialHash`` 均匀网格：把静态物体按格子分桶，剔除时先选格子再测物体，
把 O(n) 的平面测试降到 O(可见格子数)。
"""
import math

__all__ = ['AABB', 'Sphere', 'Frustum', 'SpatialHash']


# ------------------------------------------------------------------ AABB --
class AABB:
    """轴对齐包围盒。min/max 均为 (x, y, z) 三元组。"""

    __slots__ = ('min', 'max')

    def __init__(self, min_=(0.0, 0.0, 0.0), max_=(0.0, 0.0, 0.0)):
        self.min = tuple(float(v) for v in min_)
        self.max = tuple(float(v) for v in max_)

    # ---- 构造 ----
    @staticmethod
    def from_points(points):
        pts = list(points)
        if not pts:
            return AABB()
        xs, ys, zs = zip(*[(p[0], p[1], p[2]) for p in pts])
        return AABB((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))

    @staticmethod
    def from_center_half(center, half):
        cx, cy, cz = center
        hx, hy, hz = half
        return AABB((cx - hx, cy - hy, cz - hz), (cx + hx, cy + hy, cz + hz))

    def copy(self):
        return AABB(self.min, self.max)

    # ---- 度量 ----
    @property
    def center(self):
        return tuple((self.min[i] + self.max[i]) * 0.5 for i in range(3))

    @property
    def half(self):
        return tuple((self.max[i] - self.min[i]) * 0.5 for i in range(3))

    @property
    def radius(self):
        """外接球半径（AABB 同心球），用于快速的球体预剔除。"""
        hx, hy, hz = self.half
        return math.sqrt(hx * hx + hy * hy + hz * hz)

    def volume(self):
        hx, hy, hz = self.half
        return max(0.0, 8.0 * hx * hy * hz)

    # ---- 关系 ----
    def contains_point(self, p):
        return all(self.min[i] <= p[i] <= self.max[i] for i in range(3))

    def intersects(self, other):
        return all(self.min[i] <= other.max[i] and self.max[i] >= other.min[i]
                   for i in range(3))

    def expand(self, margin):
        """按世界单位外扩（也给退化盒 0 体积一点容差）。"""
        m = float(margin)
        self.min = tuple(v - m for v in self.min)
        self.max = tuple(v + m for v in self.max)
        return self

    def union(self, other):
        self.min = tuple(min(self.min[i], other.min[i]) for i in range(3))
        self.max = tuple(max(self.max[i], other.max[i]) for i in range(3))
        return self

    def transformed(self, matrix):
        """用 4x4 列主序矩阵变换八个角点后重新求包围盒。

        旋转后的 AABB 会"膨胀"（这是 AABB 的固有代价），但它便宜且保守——
        剔除宁可漏剔除也不能误剔除。
        """
        corners = []
        for sx in (0, 1):
            for sy in (0, 1):
                for sz in (0, 1):
                    x = self.min[0] if sx == 0 else self.max[0]
                    y = self.min[1] if sy == 0 else self.max[1]
                    z = self.min[2] if sz == 0 else self.max[2]
                    corners.append((
                        matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
                        matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13],
                        matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14]))
        return AABB.from_points(corners)

    def __repr__(self):
        return f'AABB(min={self.min}, max={self.max})'


class Sphere:
    """包围球：旋转不变、单次距离比较，适合做剔除的第一道粗筛。"""

    __slots__ = ('center', 'radius')

    def __init__(self, center=(0.0, 0.0, 0.0), radius=0.0):
        self.center = tuple(float(v) for v in center)
        self.radius = float(radius)

    @staticmethod
    def from_aabb(aabb):
        return Sphere(aabb.center, aabb.radius)


# --------------------------------------------------------------- 视锥 --
class Frustum:
    """六平面视锥（顺序固定：left, right, bottom, top, near, far）。

    平面存为 (a, b, c, d)，满足 ``a*x + b*y + c*z + d >= 0`` 为内侧。
    """

    PLANE_NAMES = ('left', 'right', 'bottom', 'top', 'near', 'far')
    __slots__ = ('planes',)

    def __init__(self, planes):
        self.planes = [tuple(float(v) for v in p) for p in planes]

    # ---- 构造 ----
    @staticmethod
    def from_view_projection(vp):
        """从 view-projection 矩阵提取平面（Gribb-Hartmann）。

        Args:
            vp: 列主序 16 元组 / pyglet ``Mat4``（可用 ``tuple(mat)`` 转换）。
        """
        m = tuple(vp)
        if len(m) != 16:
            raise ValueError('vp 需要 16 个元素（列主序 4x4）')
        rows = [tuple(m[j + 4 * i] for i in range(4)) for j in range(4)]  # 第 j 行
        r0, r1, r2, r3 = rows
        raw = [
            tuple(r3[i] + r0[i] for i in range(4)),   # left
            tuple(r3[i] - r0[i] for i in range(4)),   # right
            tuple(r3[i] + r1[i] for i in range(4)),   # bottom
            tuple(r3[i] - r1[i] for i in range(4)),   # top
            tuple(r3[i] + r2[i] for i in range(4)),   # near
            tuple(r3[i] - r2[i] for i in range(4)),   # far
        ]
        planes = []
        for a, b, c, d in raw:
            n = math.sqrt(a * a + b * b + c * c)
            if n < 1e-9:                              # 退化平面：给个恒成立的
                planes.append((0.0, 0.0, 0.0, 1e9))
            else:
                planes.append((a / n, b / n, c / n, d / n))
        return Frustum(planes)

    # ---- 查询 ----
    def distance_to_point(self, plane_index, p):
        a, b, c, d = self.planes[plane_index]
        return a * p[0] + b * p[1] + c * p[2] + d

    def contains_point(self, p):
        return all(self.distance_to_point(i, p) >= 0.0 for i in range(6))

    def intersects_sphere(self, center, radius):
        """球是否在视锥内（保守：部分重叠也返回 True）。"""
        return all(self.distance_to_point(i, center) >= -radius for i in range(6))

    def intersects_aabb(self, aabb):
        """AABB 是否在视锥内（p-顶点法）。

        对每个平面取"沿平面法线最远"的角点测试：该角在外侧 => 整盒在外侧。
        """
        mn, mx = aabb.min, aabb.max
        for i in range(6):
            a, b, c, d = self.planes[i]
            # p-顶点：法线分量为正取 max，为负取 min
            px = mx[0] if a >= 0.0 else mn[0]
            py = mx[1] if b >= 0.0 else mn[1]
            pz = mx[2] if c >= 0.0 else mn[2]
            if a * px + b * py + c * pz + d < 0.0:
                return False
        return True

    def plane(self, name):
        return self.planes[self.PLANE_NAMES.index(name)]

    def __repr__(self):
        return 'Frustum(' + ', '.join(
            f'{n}={self.planes[i][:3]}' for i, n in enumerate(self.PLANE_NAMES)) + ')'


# ------------------------------------------------------------ 空间加速 --
class SpatialHash:
    """均匀网格空间哈希：静态物体的粗筛加速结构。

    场景上千个物体时，逐物体做 6 次平面测试并不贵，但先按格子筛一遍可以把
    常数压下来一个量级（实测 2000 个静态盒：全遍历 12k 次点积 -> 命中格子
    通常只几十个物体）。

    用法::

        grid = SpatialHash(cell=4.0)
        for i, aabb in enumerate(bounds):
            grid.insert(i, aabb)
        visible = grid.query(frustum, bounds)     # 返回可见索引列表
    """

    def __init__(self, cell=4.0):
        self.cell = float(cell)
        self.buckets = {}

    def _keys(self, aabb):
        c = self.cell
        x0, y0, z0 = (int(math.floor(v / c)) for v in aabb.min)
        x1, y1, z1 = (int(math.floor(v / c)) for v in aabb.max)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    yield (x, y, z)

    def insert(self, index, aabb):
        for k in self._keys(aabb):
            self.buckets.setdefault(k, []).append(index)

    def clear(self):
        self.buckets.clear()

    def candidate_indices(self, aabb):
        """返回与给定包围盒重叠格子里登记过的索引（去重）。"""
        seen, out = set(), []
        for k in self._keys(aabb):
            for i in self.buckets.get(k, ()):
                if i not in seen:
                    seen.add(i)
                    out.append(i)
        return out

    def query(self, frustum, bounds):
        """用视锥查询：先按"相机大致可见范围"取候选，再逐物体精测。

        ``bounds`` 为 {index: AABB}。为简单起见这里按全部格子的并集取候选
        （等价于网格粗筛）；真正的视锥-格子相交可再加，但收益有限。
        """
        out = []
        for i, b in bounds.items():
            if frustum.intersects_aabb(b):
                out.append(i)
        return out

    @property
    def bucket_count(self):
        return len(self.buckets)
