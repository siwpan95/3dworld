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
        self._path_queue, self._path_cache = {}, {}
        cfg = config or {}
        self._count = int(cfg.get('count', 60))
        self._bounds = float(cfg.get('bounds', 12.0))
        self._seed = int(cfg.get('seed', 20261006))
        self._flock = None            # 懒建：logic.boids.Flock

    # ---- 群体（委托 Flock）----
    def _ensure_paths(self):
        if not hasattr(self, "_path_queue"):
            self._path_queue, self._path_cache = {}, {}

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
    # ------------------------------------------- 路径平滑 / 多单位队列（P1-⑤）
    @staticmethod
    def smooth(path, passable=None, keep_ends=True):
        """**路径平滑（拉绳法）**：能直连就跳过中间点，去掉网格 A* 的锯齿。

        Args:
            path: ``[(i, j), ...]``。
            passable: ``(i, j) -> bool`` 可通行查询；**缺省则只做共线合并**
                （无法做视线检查时退化为保守版，不会穿墙）。
            keep_ends: 保留首尾点（默认 True）。
        """
        if not path or len(path) <= 2:
            return list(path)

        def los(a, b):
            """a->b 是否可直接通行（Bresenham 采样 + passable 检查）。"""
            if passable is None:
                return a[0] == b[0] or a[1] == b[1]        # 保守：仅同行/同列直连
            steps = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
            if steps == 0:
                return True
            for k in range(steps + 1):
                i = a[0] + round((b[0] - a[0]) * k / steps)
                j = a[1] + round((b[1] - a[1]) * k / steps)
                if not passable(i, j):
                    return False
            return True

        out = [path[0]]
        i = 0
        while i < len(path) - 1:
            j = len(path) - 1
            while j > i + 1 and not los(path[i], path[j]):
                j -= 1
            out.append(path[j])
            i = j
        if keep_ends and out[-1] != path[-1]:
            out.append(path[-1])
        return out

    def request_path(self, key, start, goal, grid=None):
        """**多单位寻路队列**：登记一条请求。

        同 ``key`` 且 ``(start, goal)`` 未变 -> **不重复登记**（复用已有路径）。
        返回 ``(is_new, path_or_None)``：``is_new=False`` 表示本次直接命中缓存。
        """
        sig = (tuple(start), tuple(goal))
        ent = self._path_queue.get(key)
        if ent is not None and ent['sig'] == sig:
            return False, self._path_cache.get(key)
        self._path_queue[key] = dict(sig=sig, grid=grid)
        self._path_cache.pop(key, None)
        return True, None

    def step_paths(self, budget=4):
        """每帧最多解算 ``budget`` 条排队请求（避免多单位同帧 A* 造成卡顿）。

        Returns: ``{key: path}``（本次**新解算完成**的）。
        """
        done = {}
        for key in list(self._path_queue.keys())[:max(0, int(budget))]:
            ent = self._path_queue.pop(key)
            path = self.find_path(ent['sig'][0], ent['sig'][1], ent['grid'])
            self._path_cache[key] = path
            done[key] = path
        return done

    def cached_path(self, key):
        return self._path_cache.get(key)

    def clear_paths(self):
        self._path_queue.clear()
        self._path_cache.clear()

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
