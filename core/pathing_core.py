# -*- coding: utf-8 -*-
"""寻路/移动共同核 ``pathing``（实现 ``core.contracts.PathingCore``）。

P0-1 新建：把「群体行为」与「网格寻路」收成一个可复用核心。

* 群体：复用 ``logic.boids.Flock``（Reynolds 群集，纯数学）；
* 寻路：本模块提供**网格 A\\***（8 邻接 + 对角代价 √2）与**编队目标点**生成。

> 依赖：``logic.boids``（纯 Python）+ ``cordis``。headless 安全。
"""
from __future__ import annotations

import heapq
import math

from core._cordis import Service

SQRT2 = math.sqrt(2.0)


class PathingService(Service):
    """网格 A* + 群体编队（由 boids 改造）。"""

    def __init__(self, ctx, config):
        super().__init__(ctx, "pathing")
        cfg = config or {}
        self._count = int(cfg.get('count', 60))
        self._bounds = float(cfg.get('bounds', 12.0))
        self._seed = int(cfg.get('seed', 20261006))
        self._flock = None            # 懒建：logic.boids.Flock

    # ---- 群体（委托 Flock）----
    def _ensure_flock(self):
        if self._flock is None:
            from logic.boids import Flock
            self._flock = Flock(count=self._count, bounds=self._bounds, seed=self._seed)
        return self._flock

    def step(self, dt: float):
        self._ensure_flock().step(dt)

    def positions(self):
        return self._ensure_flock().positions()

    def velocities(self):
        return self._ensure_flock().velocities()

    def set_predator(self, pos):
        self._ensure_flock().set_predator(pos)

    # ---- 网格 A* ----
    def find_path(self, start, goal, grid=None):
        """网格 A\\* 求路。

        Args:
            start / goal: ``(i, j)`` 栅格坐标（整数）。
            grid: 可通行性查询——支持 ① 二维可调用 ``grid(i, j) -> bool``；
                  ② 二维序列 ``grid[j][i]``（真值 = 可通行）；
                  ③ ``None``（无障碍，直接返回直线两点）。

        Returns:
            从 start 到 goal 的栅格点列表（含首尾）；不可达返回 ``[]``。
        """
        start = (int(start[0]), int(start[1]))
        goal = (int(goal[0]), int(goal[1]))
        if grid is None:
            return [start, goal]

        def walkable(i, j):
            try:
                if callable(grid):
                    return bool(grid(i, j))
                return bool(grid[j][i])
            except (IndexError, KeyError):
                return False

        if not walkable(*goal) or not walkable(*start):
            return []
        if start == goal:
            return [start]

        def h(a):
            dx, dy = abs(a[0] - goal[0]), abs(a[1] - goal[1])
            return (dx + dy) + (SQRT2 - 2.0) * min(dx, dy)   # 八向对角启发

        open_heap = [(h(start), 0.0, start)]
        came = {}
        g = {start: 0.0}
        seen = {start}
        neigh = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                 (1, 1, SQRT2), (1, -1, SQRT2), (-1, 1, SQRT2), (-1, -1, SQRT2)]
        while open_heap:
            _, gcur, cur = heapq.heappop(open_heap)
            if cur == goal:
                path = [cur]
                while cur in came:
                    cur = came[cur]
                    path.append(cur)
                path.reverse()
                return path
            if gcur > g.get(cur, float('inf')):
                continue
            for dx, dy, cost in neigh:
                nxt = (cur[0] + dx, cur[1] + dy)
                if not walkable(*nxt):
                    continue
                # 对角移动：两个正交邻居都必须可走（防"穿墙角"）
                if dx and dy and not (walkable(cur[0] + dx, cur[1])
                                      and walkable(cur[0], cur[1] + dy)):
                    continue
                ng = gcur + cost
                if ng < g.get(nxt, float('inf')):
                    g[nxt] = ng
                    came[nxt] = cur
                    heapq.heappush(open_heap, (ng + h(nxt), ng, nxt))
                    seen.add(nxt)
        return []

    # ---- 编队 ----
    def formation(self, anchor, n, spacing, pattern='ring'):
        """围绕锚点生成 n 个编队目标点（世界坐标）。

        Args:
            anchor: ``(x, y, z)`` 锚点。
            n: 单位数。
            spacing: 相邻间距。
            pattern: ``'ring'``（默认，等角分布）或 ``'grid'``（近正方网格）。

        Returns:
            ``[(x, y, z), ...]``，长度 = n。
        """
        n = int(n)
        ax, ay, az = anchor
        if n <= 0:
            return []
        if pattern == 'grid':
            side = int(math.ceil(math.sqrt(n)))
            out = []
            for k in range(n):
                r, c = divmod(k, side)
                out.append((ax + (c - (side - 1) / 2.0) * spacing, ay,
                            az + (r - (side - 1) / 2.0) * spacing))
            return out
        # ring：等角分布（n==1 时落在锚点）
        if n == 1:
            return [(ax, ay, az)]
        r = spacing * n / (2.0 * math.pi)
        out = []
        for k in range(n):
            ang = 2.0 * math.pi * k / n
            out.append((ax + r * math.cos(ang), ay, az + r * math.sin(ang)))
        return out

    def __cordis_init__(self):
        return None


__all__ = ['PathingService']
