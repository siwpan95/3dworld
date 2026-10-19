# -*- coding: utf-8 -*-
"""城镇生成：Voronoi 划区 + 城墙 + 路网 + ward 分配 + 建筑几何（纯标准库）。

借鉴记账（铁律 10）
------------------
移植自 ``procgen``（MIT，<https://gitee.com/siwpan/procgen>，
``procgen/town.py``）—— 而 procgen 的那一份是**从零移植自 watabou 的
Medieval Fantasy City Generator**（Haxe + OpenFL 原项目，渲染耦合 Flash
图形 API 无法直接 import）。本仓继承这条移植链：借用范围 = 算法管线
（Voronoi 划区 → 城墙 → 路网 → ward 分配 → 建筑几何）与全部常数表
（WARD_COLORS / WARDS 权重表 / 街道宽度）。

适配（相对原版的改动）
---------------------
- **去掉残留的 ``import numpy as np``**：原版 1106 行里 numpy 用量为 0
  （Pt/Polygon/Voronoi 全是手写纯 Python），import 是历史残留。
- **``import heapq`` 提至模块顶层**：原版在 ``dijkstra`` 函数内 import。
- 其余（RNG 的 LCG、几何工具、生成管线）**逐行一致**。

确定性口径
----------
- 随机性全部来自模块级 ``RNG``（LCG，``seed = (seed*48271) % 2147483647``，
  watabou 原版同款），``generate_town`` 入口 ``reset(seed)`` —— 同 seed
  必然得到同一座城（坐标逐位可复现）。
- 坐标为抽象单位（城镇半径约数百），渲染时由调用方按需缩放。

API:
    generate_town(seed=1, n_wards=15, walled=None, scale_to=900)
        -> dict: { 'bounds', 'center', 'wall', 'citadel', 'plaza',
                   'streets', 'wards', 'scale_to' }
        （wards 每项含 type/polygon/buildings/color/within_walls；
        原版 docstring 里的 'patches' 是笔误 —— 实际返回 wards）
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 随机数：复刻 watabou 的 LCG (seed = (seed*48271) % 2147483647)
# ---------------------------------------------------------------------------
class RNG:
    G = 48271
    N = 2147483647

    def __init__(self, seed: int = 1):
        self.seed = seed % self.N

    def reset(self, seed: int):
        self.seed = seed % self.N

    def _next(self) -> int:
        self.seed = (self.seed * self.G) % self.N
        return self.seed

    def float(self) -> float:
        return self._next() / self.N

    def normal(self) -> float:
        return (self.float() + self.float() + self.float()) / 3

    def int(self, lo: int, hi: int) -> int:
        return int(lo + self._next() / self.N * (hi - lo))

    def bool(self, p: float = 0.5) -> bool:
        return self.float() < p

    def fuzzy(self, f: float = 1.0) -> float:
        return 0.5 if f == 0 else (1 - f) / 2 + f * self.normal()


_rng = RNG(1)


# ---------------------------------------------------------------------------
# 点
# ---------------------------------------------------------------------------
class Pt:
    __slots__ = ("x", "y")

    def __init__(self, x: float = 0.0, y: float = 0.0):
        self.x = float(x)
        self.y = float(y)

    def __repr__(self):
        return f"Pt({self.x:.1f},{self.y:.1f})"

    def __eq__(self, o):
        return isinstance(o, Pt) and self.x == o.x and self.y == o.y

    def __hash__(self):
        return hash((round(self.x, 4), round(self.y, 4)))

    def __add__(self, o):
        return Pt(self.x + o.x, self.y + o.y)

    def __sub__(self, o):
        return Pt(self.x - o.x, self.y - o.y)

    def set(self, o):
        self.x, self.y = o.x, o.y
        return self

    def clone(self):
        return Pt(self.x, self.y)

    def offset(self, dx, dy):
        self.x += dx
        self.y += dy
        return self

    def addEq(self, o):
        self.x += o.x
        self.y += o.y
        return self

    def subEq(self, o):
        self.x -= o.x
        self.y -= o.y
        return self

    def scaleEq(self, f):
        self.x *= f
        self.y *= f
        return self

    def scale(self, f):
        return Pt(self.x * f, self.y * f)

    def add(self, o):
        return Pt(self.x + o.x, self.y + o.y)

    def subtract(self, o):
        return Pt(self.x - o.x, self.y - o.y)

    def length(self):
        return math.hypot(self.x, self.y)

    def dot(self, o):
        return self.x * o.x + self.y * o.y

    def norm(self, length: float = 1.0):
        p = self.clone()
        l = math.hypot(p.x, p.y)
        if l > 0:
            p.x = p.x / l * length
            p.y = p.y / l * length
        return p

    def rotate90(self):
        return Pt(-self.y, self.x)

    def atan(self):
        return math.atan2(self.y, self.x)

    def dist(self, o):
        return math.hypot(self.x - o.x, self.y - o.y)


# ---------------------------------------------------------------------------
# 几何工具
# ---------------------------------------------------------------------------
def intersect_lines(x1, y1, dx1, dy1, x2, y2, dx2, dy2):
    """两条线段参数交点。返回 (t1, t2) 或 None。
    t1 沿第 1 条(x1,y1)->(x1+dx1,y1+dy1)，t2 沿第 2 条。"""
    d = dx1 * dy2 - dy1 * dx2
    if d == 0:
        return None
    t2 = (dy1 * (x2 - x1) - dx1 * (y2 - y1)) / d
    t1 = (dx1 != 0 and (x2 - x1 + dx2 * t2) / dx1) or (y2 - y1 + dy2 * t2) / dy1
    return (t1, t2)


def cross(x1, y1, x2, y2):
    return x1 * y2 - y1 * x2


def scalar(x1, y1, x2, y2):
    return x1 * x2 + y1 * y2


def distance2line(x1, y1, dx1, dy1, x0, y0):
    return (dx1 * y0 - dy1 * x0 + (y1 + dy1) * x1 - (x1 + dx1) * y1) / math.hypot(dx1, dy1)


def interpolate(p1, p2, ratio=0.5):
    d = p2 - p1
    return Pt(p1.x + d.x * ratio, p1.y + d.y * ratio)


# ---------------------------------------------------------------------------
# 多边形
# ---------------------------------------------------------------------------
class Polygon:
    def __init__(self, verts=None):
        self.verts = list(verts) if verts else []

    def __len__(self):
        return len(self.verts)

    def __getitem__(self, i):
        return self.verts[i]

    def __iter__(self):
        return iter(self.verts)

    def __repr__(self):
        return f"Polygon({len(self.verts)} pts)"

    # ---- 基本量 ----
    @property
    def square(self):
        v1 = self.verts[-1]
        v2 = self.verts[0]
        s = v1.x * v2.y - v2.x * v1.y
        for i in range(1, len(self.verts)):
            v1 = v2
            v2 = self.verts[i]
            s += v1.x * v2.y - v2.x * v1.y
        return abs(s) * 0.5

    @property
    def perimeter(self):
        return sum(self.verts[i].dist(self.verts[(i + 1) % len(self.verts)])
                   for i in range(len(self.verts)))

    @property
    def compactness(self):
        p = self.perimeter
        return 4 * math.pi * self.square / (p * p) if p else 0.0

    @property
    def center(self):
        c = Pt()
        for v in self.verts:
            c.addEq(v)
        c.scaleEq(1 / len(self.verts))
        return c

    @property
    def centroid(self):
        x = y = a = 0.0
        for i in range(len(self.verts)):
            v0 = self.verts[i]
            v1 = self.verts[(i + 1) % len(self.verts)]
            f = cross(v0.x, v0.y, v1.x, v1.y)
            a += f
            x += (v0.x + v1.x) * f
            y += (v0.y + v1.y) * f
        s6 = 1 / (3 * a) if a else 0.0
        return Pt(s6 * x, s6 * y)

    @property
    def bounds(self):
        xs = [v.x for v in self.verts]
        ys = [v.y for v in self.verts]
        return (min(xs), min(ys), max(xs), max(ys))

    # ---- 索引/邻接 ----
    def index_of(self, v):
        try:
            return self.verts.index(v)
        except ValueError:
            return -1

    def contains(self, v):
        return self.index_of(v) != -1

    def next(self, v):
        return self.verts[(self.index_of(v) + 1) % len(self.verts)]

    def prev(self, v):
        return self.verts[(self.index_of(v) + len(self.verts) - 1) % len(self.verts)]

    def vector(self, v):
        return self.next(v) - v

    def vectori(self, i):
        return self.verts[i + 1 if i < len(self.verts) - 1 else 0] - self.verts[i]

    def find_edge(self, a, b):
        i = self.index_of(a)
        return i if (i != -1 and self.verts[(i + 1) % len(self.verts)] == b) else -1

    def borders(self, another):
        for i in range(len(self.verts)):
            j = another.index_of(self.verts[i])
            if j != -1:
                nxt = self.verts[(i + 1) % len(self.verts)]
                if nxt == another.verts[(j + 1) % len(another.verts)] or \
                   nxt == another.verts[(j + len(another.verts) - 1) % len(another.verts)]:
                    return True
        return False

    def for_edge(self, f):
        n = len(self.verts)
        for i in range(n):
            f(self.verts[i], self.verts[(i + 1) % n])

    def distance(self, p):
        return min(v.dist(p) for v in self.verts)

    def is_convex_vertex(self, v1):
        v0 = self.prev(v1)
        v2 = self.next(v1)
        return cross(v1.x - v0.x, v1.y - v0.y, v2.x - v1.x, v2.y - v1.y) > 0

    def is_convex(self):
        return all(self.is_convex_vertex(v) for v in self.verts)

    # ---- 修改 ----
    def offset(self, p):
        for v in self.verts:
            v.offset(p.x, p.y)
        return self

    def rotate(self, a):
        c, s = math.cos(a), math.sin(a)
        for v in self.verts:
            x = v.x * c - v.y * s
            y = v.x * s + v.y * c
            v.set(Pt(x, y))
        return self

    def smooth_vertex_eq(self, f=1.0):
        n = len(self.verts)
        v1 = self.verts[n - 1]
        v2 = self.verts[0]
        out = []
        for i in range(n):
            v0, v1, v2 = v1, v2, self.verts[(i + 1) % n]
            out.append(Pt((v0.x + v1.x * f + v2.x) / (2 + f),
                          (v0.y + v1.y * f + v2.y) / (2 + f)))
        return Polygon(out)

    def set_verts(self, pts):
        for i in range(len(pts)):
            self.verts[i].set(pts[i])
        return self

    def split(self, p1, p2):
        return self.spliti(self.index_of(p1), self.index_of(p2))

    def spliti(self, i1, i2):
        if i1 > i2:
            i1, i2 = i2, i1
        return [Polygon(self.verts[i1:i2 + 1]),
                Polygon(self.verts[i2:] + self.verts[:i1 + 1])]

    def cut(self, p1, p2, gap=0.0):
        x1, y1 = p1.x, p1.y
        dx1, dy1 = p2.x - p1.x, p2.y - p1.y
        n = len(self.verts)
        edge1 = ratio1 = edge2 = ratio2 = 0
        count = 0
        for i in range(n):
            v0 = self.verts[i]
            v1 = self.verts[(i + 1) % n]
            x2, y2 = v0.x, v0.y
            dx2, dy2 = v1.x - x2, v1.y - y2
            t = intersect_lines(x1, y1, dx1, dy1, x2, y2, dx2, dy2)
            if t is not None and 0 <= t[1] <= 1:
                if count == 0:
                    edge1, ratio1 = i, t[0]
                elif count == 1:
                    edge2, ratio2 = i, t[0]
                count += 1
        if count == 2:
            point1 = p1 + (p2 - p1).scale(ratio1)
            point2 = p1 + (p2 - p1).scale(ratio2)
            half1 = self.verts[edge1 + 1:edge2 + 1]
            half1 = [point1] + half1 + [point2]
            half2 = self.verts[edge2 + 1:] + self.verts[0:edge1 + 1]
            half2 = [point2] + half2 + [point1]
            if gap > 0:
                half1 = Polygon(half1).peel(point2, gap / 2).verts
                half2 = Polygon(half2).peel(point1, gap / 2).verts
            v = self.vectori(edge1)
            if cross(dx1, dy1, v.x, v.y) > 0:
                return [Polygon(half1), Polygon(half2)]
            return [Polygon(half2), Polygon(half1)]
        return [Polygon(self.verts)]

    def peel(self, v1, d):
        i1 = self.index_of(v1)
        i2 = 0 if i1 == len(self.verts) - 1 else i1 + 1
        v2 = self.verts[i2]
        v = v2 - v1
        nrm = v.rotate90().norm(d)
        return self.cut(v1 + nrm, v2 + nrm, 0)[0]

    def shrink(self, distances):
        q = Polygon(self.verts)
        for i in range(len(self.verts)):
            dd = distances[i]
            if dd > 0:
                v1 = q.verts[i]
                v2 = q.verts[(i + 1) % len(q.verts)]
                v = v2 - v1
                nrm = v.rotate90().norm(dd)
                q = q.cut(v1 + nrm, v2 + nrm, 0)[0]
        return q

    def shrink_eq(self, d):
        return self.shrink([d] * len(self.verts))

    def inset_eq(self, d):
        return self.shrink_eq(d)

    # ---- 静态构造 ----
    @staticmethod
    def rect(w=1.0, h=1.0):
        return Polygon([Pt(-w / 2, -h / 2), Pt(w / 2, -h / 2),
                        Pt(w / 2, h / 2), Pt(-w / 2, h / 2)])

    @staticmethod
    def regular(n=8, r=1.0):
        return Polygon([Pt(r * math.cos(i / n * 2 * math.pi),
                           r * math.sin(i / n * 2 * math.pi)) for i in range(n)])

    @staticmethod
    def circle(r=1.0):
        return Polygon.regular(16, r)

    def interpolate_weights(self, p):
        ws = [1 / v.dist(p) for v in self.verts]
        s = sum(ws)
        return [w / s for w in ws]


# ---------------------------------------------------------------------------
# Voronoi (Bowyer-Watson Delaunay + 区域)
# ---------------------------------------------------------------------------
class Triangle:
    def __init__(self, p1, p2, p3):
        s = (p2.x - p1.x) * (p2.y + p1.y) + (p3.x - p2.x) * (p3.y + p2.y) + \
            (p1.x - p3.x) * (p1.y + p3.y)
        self.p1, self.p2, self.p3 = (p1, p2, p3) if s > 0 else (p1, p3, p2)
        x1 = (p1.x + p2.x) / 2
        y1 = (p1.y + p2.y) / 2
        x2 = (p2.x + p3.x) / 2
        y2 = (p2.y + p3.y) / 2
        dx1 = p1.y - p2.y
        dy1 = p2.x - p1.x
        dx2 = p2.y - p3.y
        dy2 = p3.x - p2.x
        tg1 = dy1 / dx1 if dx1 else 0
        t2 = ((y1 - y2) - (x1 - x2) * tg1) / (dy2 - dx2 * tg1) if (dy2 - dx2 * tg1) else 0
        self.c = Pt(x2 + dx2 * t2, y2 + dy2 * t2)
        self.r = self.c.dist(p1)

    def has_edge(self, a, b):
        return (self.p1 == a and self.p2 == b) or \
               (self.p2 == a and self.p3 == b) or \
               (self.p3 == a and self.p1 == b)


class Region:
    def __init__(self, seed):
        self.seed = seed
        self.vertices = []

    def sort_vertices(self):
        self.vertices.sort(key=lambda v: self._angle(v.c))
        return self

    def _angle(self, c):
        x, y = c.x - self.seed.x, c.y - self.seed.y
        if x >= 0 and y < 0:
            return 1
        if x < 0 and y >= 0:
            return -1
        if x == 0 and y == 0:
            return 1 if y > 0 else -1
        return math.copysign(1, x * y - 0) * (1 if x * y >= 0 else -1)  # sign(x2*y1 - x1*y2)

    def center(self):
        c = Pt()
        for v in self.vertices:
            c.addEq(v.c)
        c.scaleEq(1 / len(self.vertices))
        return c

    def borders(self, r2):
        n1 = len(self.vertices)
        n2 = len(r2.vertices)
        for i in range(n1):
            j = _list_index(r2.vertices, self.vertices[i])
            if j != -1:
                return self.vertices[(i + 1) % n1] == r2.vertices[(j + n2 - 1) % n2]
        return False


def _list_index(lst, item):
    try:
        return lst.index(item)
    except ValueError:
        return -1


class Voronoi:
    def __init__(self, minx, miny, maxx, maxy):
        self.triangles = []
        c1 = Pt(minx, miny)
        c2 = Pt(minx, maxy)
        c3 = Pt(maxx, miny)
        c4 = Pt(maxx, maxy)
        self.frame = [c1, c2, c3, c4]
        self.points = [c1, c2, c3, c4]
        self.triangles.append(Triangle(c1, c2, c3))
        self.triangles.append(Triangle(c2, c3, c4))
        self._dirty = False

    def add_point(self, p):
        to_split = [tr for tr in self.triangles if p.dist(tr.c) < tr.r]
        if not to_split:
            return
        self.points.append(p)
        a, b = [], []
        for t1 in to_split:
            e1 = e2 = e3 = True
            for t2 in to_split:
                if t2 is t1:
                    continue
                if e1 and t2.has_edge(t1.p2, t1.p1):
                    e1 = False
                if e2 and t2.has_edge(t1.p3, t1.p2):
                    e2 = False
                if e3 and t2.has_edge(t1.p1, t1.p3):
                    e3 = False
                if not (e1 or e2 or e3):
                    break
            if e1:
                a.append(t1.p1)
                b.append(t1.p2)
            if e2:
                a.append(t1.p2)
                b.append(t1.p3)
            if e3:
                a.append(t1.p3)
                b.append(t1.p1)
        index = 0
        tris = []
        while True:
            tris.append(Triangle(p, a[index], b[index]))
            index = a.index(b[index])
            if index == 0:
                break
        for tr in to_split:
            self.triangles.remove(tr)
        self.triangles.extend(tris)
        self._dirty = True

    def _build_regions(self):
        regions = {}
        for p in self.points:
            regions[p] = Region(p)
        for tr in self.triangles:
            for p in (tr.p1, tr.p2, tr.p3):
                if p in regions:
                    regions[p].vertices.append(tr)
        for r in regions.values():
            r.sort_vertices()
        return regions

    def regions(self):
        return self._build_regions()

    def is_real(self, tr):
        return not (tr.p1 in self.frame or tr.p2 in self.frame or tr.p3 in self.frame)

    def triangulation(self):
        return [tr for tr in self.triangles if self.is_real(tr)]

    def partitioning(self):
        regs = self.regions()
        out = []
        for p in self.points:
            r = regs[p]
            if all(self.is_real(v) for v in r.vertices):
                out.append(r)
        return out

    def get_neighbours(self, r1):
        regs = self.regions()
        return [r2 for r2 in regs.values() if r1.borders(r2)]

    @staticmethod
    def relax(voronoi, to_relax=None):
        regions = voronoi.partitioning()
        points = [p for p in voronoi.points if p not in voronoi.frame]
        if to_relax is None:
            to_relax = list(voronoi.points)
        for r in regions:
            if r.seed in to_relax:
                points.remove(r.seed)
                points.append(r.center())
        return Voronoi.build(points)

    @staticmethod
    def build(vertices):
        minx = min(v.x for v in vertices)
        miny = min(v.y for v in vertices)
        maxx = max(v.x for v in vertices)
        maxy = max(v.y for v in vertices)
        dx = (maxx - minx) * 0.5
        dy = (maxy - miny) * 0.5
        v = Voronoi(minx - dx / 2, miny - dy / 2, maxx + dx / 2, maxy + dy / 2)
        for pt in vertices:
            v.add_point(pt)
        return v


# ---------------------------------------------------------------------------
# 城镇生成管线
# ---------------------------------------------------------------------------
MAIN_STREET = 2.0
REGULAR_STREET = 1.0
ALLEY = 0.6

# ward 颜色 (R,G,B) 与标签
WARD_COLORS = {
    "Market": (222, 205, 155), "Castle": (185, 185, 205),
    "Craftsmen": (202, 172, 142), "Merchant": (212, 192, 162),
    "Slum": (150, 140, 130), "Patriciate": (214, 202, 182),
    "Administration": (192, 187, 205), "Military": (172, 167, 162),
    "Common": (196, 177, 152), "Park": (120, 182, 112),
    "Farm": (205, 195, 125), "Cathedral": (232, 227, 212),
    "GateWard": (188, 173, 153),
}
# watabou 的 WARDS 权重表（高频=工匠区多）
WARDS = ["Craftsmen", "Craftsmen", "Merchant", "Craftsmen", "Craftsmen",
         "Cathedral", "Craftsmen", "Craftsmen", "Craftsmen", "Craftsmen",
         "Craftsmen", "Craftsmen", "Craftsmen", "Craftsmen", "Administration",
         "Craftsmen", "Slum", "Craftsmen", "Slum", "Patriciate", "Market",
         "Slum", "Craftsmen", "Craftsmen", "Craftsmen", "Slum", "Craftsmen",
         "Craftsmen", "Craftsmen", "Military", "Slum", "Craftsmen", "Park",
         "Patriciate", "Market", "Merchant"]


class Patch:
    def __init__(self, verts):
        self.shape = Polygon(verts)
        self.ward = None
        self.within_city = False
        self.within_walls = False


def _key(p):
    return (round(p.x), round(p.y))


# ---- 建筑几何（移植 createAlleys / createOrthoBuilding / Cutter）----
def bisect(poly, vertex, ratio, angle, gap):
    nxt = poly.next(vertex)
    p1 = interpolate(vertex, nxt, ratio)
    d = nxt - vertex
    cosB, sinB = math.cos(angle), math.sin(angle)
    vx = d.x * cosB - d.y * sinB
    vy = d.y * cosB + d.x * sinB
    p2 = Pt(p1.x - vy, p1.y + vx)
    return poly.cut(p1, p2, gap)


def find_longest_edge(poly):
    best, length = None, -1.0
    n = len(poly)
    for i in range(n):
        v0 = poly.verts[i]
        v1 = poly.verts[(i + 1) % n]
        l = v0.dist(v1)
        if l > length:
            length, best = l, v0
    return best


def create_alleys(p, min_sq, grid_chaos, size_chaos, empty_prob=0.04,
                  split=True, depth=0):
    if depth > 14 or len(p) < 3:
        return [p]
    v = find_longest_edge(p)
    spread = 0.8 * grid_chaos
    ratio = (1 - spread) / 2 + _rng.float() * spread
    angle_spread = math.pi / 6 * grid_chaos * (1.0 if p.square < min_sq * 4 else 0.0)
    b = (_rng.float() - 0.5) * angle_spread
    halves = bisect(p, v, ratio, b, ALLEY if split else 0.0)
    buildings = []
    for half in halves:
        threshold = min_sq * (2 ** (4 * size_chaos * (_rng.float() - 0.5)))
        if half.square < threshold:
            if not _rng.bool(empty_prob):
                buildings.append(half)
        else:
            recurse = half.square > min_sq / (_rng.float() * _rng.float())
            buildings += create_alleys(half, min_sq, grid_chaos, size_chaos,
                                       empty_prob, recurse, depth + 1)
    return buildings


def create_ortho_building(poly, min_block_sq, fill):
    def slice_poly(poly, c1, c2):
        v0 = find_longest_edge(poly)
        v1 = poly.next(v0)
        v = v1 - v0
        ratio = 0.4 + _rng.float() * 0.2
        p1 = interpolate(v0, v1, ratio)
        c = c1 if abs(scalar(v.x, v.y, c1.x, c1.y)) < abs(scalar(v.x, v.y, c2.x, c2.y)) else c2
        halves = poly.cut(p1, p1 + c)
        out = []
        for half in halves:
            if half.square < min_block_sq * (2 ** (int((_rng.normal() * 2 - 1) * 4) / 4.0)):
                if _rng.bool(fill):
                    out.append(half)
            else:
                out += slice_poly(half, c1, c2)
        return out

    if poly.square < min_block_sq or len(poly) < 3:
        return [poly]
    c1 = poly.vector(find_longest_edge(poly))
    c2 = c1.rotate90()
    while True:
        blocks = slice_poly(poly, c1, c2)
        if blocks:
            return blocks


def radial(poly, center=None, gap=0.0):
    if center is None:
        center = poly.centroid
    out = []
    poly.for_edge(lambda v0, v1: out.append(Polygon([center, v0, v1])))
    return out


def semi_radial(poly, center=None, gap=0.0):
    if center is None:
        centroid = poly.centroid
        center = min(poly.verts, key=lambda v: v.dist(centroid))
    out = []
    poly.for_edge(lambda v0, v1: out.append(Polygon([center, v0, v1]))
                  if v0 != center and v1 != center else None)
    return [s for s in out if s is not None]


def get_city_block(model, patch):
    if model["wall"] is None:
        return patch.shape.shrink_eq(REGULAR_STREET / 2)
    on_wall = any(model["wall"]["shape"].find_edge(v0, v1) != -1 or
                  model["wall"]["shape"].find_edge(v1, v0) != -1
                  for i in range(len(patch.shape))
                  for v0, v1 in [(patch.shape.verts[i],
                                  patch.shape.verts[(i + 1) % len(patch.shape)])])
    inset = MAIN_STREET / 2 if on_wall else (REGULAR_STREET / 2 if patch.within_city else ALLEY / 2)
    return patch.shape.shrink_eq(inset)


# ---- 城墙 ----
def find_circumference(wards):
    """计算一组 patch 外边界多边形（移植 Model.findCircumference）。"""
    if not wards:
        return Polygon()
    if len(wards) == 1:
        return Polygon(wards[0].shape.verts)
    edges = []
    for w in wards:
        w.shape.for_edge(lambda a, b: _collect_edge(wards, edges, w, a, b))
    if not edges:
        return Polygon()
    # 按连接顺序贪心串成多边形
    result = [edges[0][0]]
    cur = edges[0][1]
    result.append(cur)
    remaining = list(edges[1:])
    guard = 0
    while remaining and guard < 10000:
        guard += 1
        nxt = None
        for e in remaining:
            if e[0] == cur:
                nxt = e
                break
            if e[1] == cur:
                nxt = (e[1], e[0])
                break
        if nxt is None:
            break
        cur = nxt[1]
        result.append(cur)
        remaining = [e for e in remaining if e is not nxt]
    return Polygon(result)


def _collect_edge(wards, edges, w, a, b):
    outer = True
    for w2 in wards:
        if w2 is w:
            continue
        if w2.shape.find_edge(b, a) != -1:
            outer = False
            break
    if outer:
        edges.append((a, b))


def build_walls(model):
    inner = model["inner"]
    if not inner:
        return None
    reserved = list(model["citadel"].shape.verts) if model["citadel"] else []
    shape = find_circumference(inner)
    if len(shape) < 3:
        return None
    # 平滑（保留 reserved 顶点）：原地修改共享顶点，保持与内部 patch 的身份一致
    smooth = min(1.0, 40.0 / len(inner))
    smoothed = shape.smooth_vertex_eq(smooth)
    for i, v in enumerate(shape.verts):
        if v not in reserved:
            v.set(smoothed.verts[i])

    # 城门：属于 >1 个 inner patch 的顶点
    entrances = [v for v in shape.verts
                 if sum(1 for p in inner if p.shape.contains(v)) > 1]
    if not entrances:
        entrances = list(shape.verts)
    gates = []
    ent = list(entrances)
    while len(ent) >= 3:
        idx = _rng.int(0, len(ent))
        gates.append(ent[idx])
        ent = _remove_neighbors(ent, idx)
    if len(ent) > 0 and not gates:
        gates = list(ent)

    # 塔楼：非城门顶点
    towers = [v for v in shape.verts if v not in gates]
    return {"shape": shape, "gates": gates, "towers": towers,
            "radius": max(v.length() for v in shape.verts)}


def _remove_neighbors(lst, idx):
    n = len(lst)
    if idx == 0:
        return lst[2:-1]
    if idx == n - 1:
        return lst[1:n - 1]
    return lst[:idx - 1] + lst[idx + 2:]


# ---- 路网（Topology 图寻路）----
def build_streets(model):
    patches = model["patches"]
    wall = model["wall"]
    border = wall["shape"] if wall else None
    blocked = set()
    if model["citadel"]:
        blocked |= {_key(v) for v in model["citadel"].shape.verts}
    if wall:
        blocked |= {_key(v) for v in wall["shape"].verts}
    for g in model["gates"]:
        blocked.discard(_key(g))

    graph = {}
    node_pt = {}

    def node(p):
        # 仅排除墙/城堡（城门已从 blocked 移除）；边界顶点（含城门）仍建节点并连边
        if _key(p) in blocked:
            return None
        k = _key(p)
        if k not in graph:
            graph[k] = {}
            node_pt[k] = p
        return k

    for p in patches:
        verts = p.shape.verts
        n = len(verts)
        v1 = verts[-1]
        n1 = node(v1)
        for i in range(n):
            v0, v1 = v1, verts[i]
            n0, n1 = n1, node(v1)
            if n0 and n1:
                d = v0.dist(v1)
                graph[n0][n1] = d
                graph[n1][n0] = d

    streets = []

    def dijkstra(start_k, goal_k):
        if start_k not in graph or goal_k not in graph:
            return None
        dist = {start_k: 0.0}
        prev = {}
        visited = set()
        pq = [(0.0, start_k)]
        while pq:
            d, u = heapq.heappop(pq)
            if u in visited:
                continue
            visited.add(u)
            if u == goal_k:
                break
            for nb, w in graph[u].items():
                if nb in visited:
                    continue
                nd = d + w
                if nd < dist.get(nb, 1e18):
                    dist[nb] = nd
                    prev[nb] = u
                    heapq.heappush(pq, (nd, nb))
        if goal_k not in prev and goal_k != start_k:
            return None
        path = [goal_k]
        while path[-1] != start_k:
            path.append(prev[path[-1]])
        path.reverse()
        return [node_pt[k] for k in path]

    center = model["center"]
    center_k = _key(center)
    if center_k not in graph:
        # 找最近节点
        center_k = min(graph.keys(), key=lambda k: math.hypot(k[0] - center.x, k[1] - center.y)) \
            if graph else None
    for g in model["gates"]:
        gk = _key(g)
        if gk in graph and center_k:
            path = dijkstra(gk, center_k)
            if path and len(path) > 1:
                streets.append(path)
    # 平滑
    streets = [s.smooth_vertex_eq(3) if isinstance(s, Polygon) else _smooth_poly(s)
               for s in streets]
    return streets


def _smooth_poly(pts):
    if len(pts) < 3:
        return pts
    out = []
    n = len(pts)
    for i in range(n):
        v0 = pts[(i - 1) % n]
        v1 = pts[i]
        v2 = pts[(i + 1) % n]
        out.append(Pt((v0.x + v1.x + v2.x) / 3, (v0.y + v1.y + v2.y) / 3))
    return out


# ---- ward 分配与建筑 ----
def _ward_buildings(model, patch, wtype):
    try:
        if wtype == "Castle":
            block = patch.shape.shrink_eq(MAIN_STREET * 2)
            return create_ortho_building(block, math.sqrt(block.square) * 4, 0.6)
        if wtype == "Market":
            stat = _rng.bool(0.6)
            obj = Polygon.rect(1 + _rng.float(), 1 + _rng.float()) if stat \
                else Polygon.circle(1 + _rng.float())
            if stat:
                # 对齐最长边
                best_a = best_b = patch.shape.verts[0]
                length = -1.0
                for i in range(len(patch.shape)):
                    a = patch.shape.verts[i]
                    b = patch.shape.verts[(i + 1) % len(patch.shape)]
                    if a.dist(b) > length:
                        length, best_a, best_b = a.dist(b), a, b
                obj.rotate(math.atan2(best_b.y - best_a.y, best_b.x - best_a.x))
            obj.offset(patch.shape.center)
            return [obj]
        if wtype == "Park":
            block = get_city_block(model, patch)
            return (radial(block) if block.compactness >= 0.7 else semi_radial(block))
        if wtype == "Farm":
            house = Polygon.rect(4, 4)
            pos = interpolate(patch.shape.verts[_rng.int(0, len(patch.shape))],
                              patch.shape.centroid, 0.3 + _rng.float() * 0.4)
            house.rotate(_rng.float() * math.pi)
            house.offset(pos)
            return [house]
        if wtype == "Cathedral":
            block = get_city_block(model, patch)
            return create_ortho_building(block, 50, 0.8)
        # 通用（含 Craftsmen/Merchant/Slum/Patriciate/Administration/Military/Common/GateWard）
        params = {
            "Craftsmen": (10 + 80 * _rng.float() * _rng.float(), 0.5 + _rng.float() * 0.2, 0.6),
            "Merchant": (10 + 60 * _rng.float(), 0.4 + _rng.float() * 0.2, 0.6),
            "Slum": (10 + 30 * _rng.float() * _rng.float(), 0.6 + _rng.float() * 0.4, 0.8),
            "Patriciate": (20 + 60 * _rng.float(), 0.3 + _rng.float() * 0.2, 0.4),
            "Administration": (15 + 40 * _rng.float(), 0.4, 0.5),
            "Military": (12 + 50 * _rng.float(), 0.5, 0.6),
            "Common": (12 + 50 * _rng.float(), 0.5, 0.6),
            "GateWard": (12 + 40 * _rng.float(), 0.55, 0.6),
        }.get(wtype, (14, 0.5, 0.6))
        block = get_city_block(model, patch)
        return create_alleys(block, params[0], params[1], params[2])
    except Exception:
        return [patch.shape]


def generate_town(seed: int = 1, n_wards: int = 15, walled=None, scale_to: int = 900):
    """生成一座中世纪奇幻城镇。

    返回 dict：bounds / center / wall / citadel / plaza / streets / wards / patches。
    坐标为抽象单位，渲染时由调用方按需缩放。
    """
    _rng.reset(seed)
    plaza_needed = _rng.bool()
    citadel_needed = _rng.bool()
    walls_needed = _rng.bool() if walled is None else walled

    # 1) Voronoi 划区
    sa = _rng.float() * 2 * math.pi
    points = []
    for i in range(n_wards * 8):
        a = sa + math.sqrt(i) * 5
        r = 0.0 if i == 0 else 10 + i * (2 + _rng.float())
        points.append(Pt(math.cos(a) * r, math.sin(a) * r))
    vor = Voronoi.build(points)
    to_relax = [p for p in (vor.points[0], vor.points[1], vor.points[2], vor.points[n_wards])
                if p in vor.points]
    for _ in range(3):
        vor = Voronoi.relax(vor, to_relax)
    regions = vor.partitioning()

    model = {"patches": [], "inner": [], "wall": None, "citadel": None,
             "plaza": None, "gates": [], "center": Pt(0, 0), "border": None}
    plaza = citadel = None
    for idx, r in enumerate(regions):
        patch = Patch([tr.c for tr in r.vertices])
        if idx == 0:
            model["center"] = min(patch.shape.verts, key=lambda v: v.length()).clone()
            if plaza_needed:
                plaza = patch
        elif idx == n_wards and citadel_needed:
            citadel = patch
            patch.within_city = True
        if idx < n_wards:
            patch.within_city = True
            patch.within_walls = walls_needed
            model["inner"].append(patch)
        model["patches"].append(patch)
    model["plaza"] = plaza
    model["citadel"] = citadel

    # 2) 城墙
    if walls_needed:
        wall = build_walls(model)
        if wall:
            model["wall"] = wall
            model["gates"] = wall["gates"]
            model["border"] = wall["shape"]
            radius = wall["radius"]
            model["patches"] = [p for p in model["patches"]
                                if p.shape.distance(model["center"]) < radius * 3]

    # 3) 路网
    model["streets"] = build_streets(model)

    # 4) ward 分配
    unassigned = [p for p in model["inner"]]
    if plaza is not None:
        plaza.ward = "Market"
        unassigned.remove(plaza)
    if citadel is not None:
        citadel.ward = "Castle"
        if citadel in unassigned:
            unassigned.remove(citadel)
    pool = list(WARDS)
    for _ in range(int(len(pool) / 10)):
        i = _rng.int(0, len(pool) - 1)
        pool[i], pool[i + 1] = pool[i + 1], pool[i]
    pi = 0
    while unassigned:
        wtype = pool[pi] if pi < len(pool) else "Slum"
        pi += 1
        patch = unassigned.pop(_rng.int(0, len(unassigned)))
        patch.ward = wtype

    # 5) 建筑几何
    wards_out = []
    for p in model["inner"]:
        wtype = p.ward or "Common"
        blds = _ward_buildings(model, p, wtype)
        wards_out.append({
            "type": wtype,
            "polygon": p.shape,
            "buildings": blds,
            "color": WARD_COLORS.get(wtype, (196, 177, 152)),
            "within_walls": p.within_walls,
        })

    # 6) 汇总 bounds
    all_pts = []
    for w in wards_out:
        all_pts += w["polygon"].verts
    if model["wall"]:
        all_pts += model["wall"]["shape"].verts
    for s in model["streets"]:
        all_pts += s
    xs = [p.x for p in all_pts]
    ys = [p.y for p in all_pts]
    bounds = (min(xs), min(ys), max(xs), max(ys)) if all_pts else (0, 0, 1, 1)

    return {
        "bounds": bounds,
        "center": model["center"],
        "wall": model["wall"],
        "citadel": citadel,
        "plaza": plaza,
        "streets": model["streets"],
        "wards": wards_out,
        "scale_to": scale_to,
    }
