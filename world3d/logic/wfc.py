# -*- coding: utf-8 -*-
"""WFC（Wave Function Collapse）波函数坍缩：瓦片平铺模型（纯标准库）。

自研（非移植）
--------------
本地仓库盘点（2026-10-19）结论：本地无 WFC 实现，故从零自研。立场与
``logic/`` 一致：**纯标准库、零依赖、确定性、可 headless 单测**
（不碰 pyglet / GL / 全局随机状态）。

算法（瓦片平铺模型）
-------------------
* 每个格子持有「可能瓦片集合」（波）；初始为全集。
* **观察（observe）**：选熵最低（候选最少且 >1）的格子，按权重随机坍缩成
  一个瓦片（用注入的 ``random.Random(seed)``，保证可复现）。
* **传播（propagate）**：AC-3 式约束传播 —— 邻居的可选瓦片被收缩到「与当前
  格子的所有候选都相容」的子集；出现空集即矛盾。
* **回溯**：本实现用「整局重启」而非显式回溯栈（实现简单、确定性好、足够稳）；
  在 ``max_attempts`` 内找不到合法解则返回 ``None``。

瓦片与邻接
----------
每个瓦片用 ``Tile(name, edges)`` 描述，``edges`` 为 4 元组 ``(N, E, S, W)``
的「边插槽」字符串。两条边相容 = 字符串相等。邻接规则**自动派生**：
* 右邻：左瓦片 ``E`` == 右瓦片 ``W``
* 下邻：上瓦片 ``S`` == 下瓦片 ``N``
（左 / 上邻为其反向，亦自动派生，保证传播四向一致。）
"""
from __future__ import annotations

import random

__all__ = ['Tile', 'WFC', 'check_adjacency']


class Tile:
    """一个瓦片：名字 + 四条边插槽 ``(N, E, S, W)``。"""

    __slots__ = ('name', 'edges')

    def __init__(self, name, edges):
        if len(edges) != 4:
            raise ValueError('edges 必须是 4 元组 (N, E, S, W)')
        self.name = name
        self.edges = tuple(edges)

    def __repr__(self):
        return f'Tile({self.name!r}, {self.edges!r})'


# 方向：0=右, 1=左, 2=下, 3=上。每项为 (dx, dy, 该方向相容键, 反向键)。
# ⚠️ cdir 必须与 comp 表的语义对齐：comp[0][t] 是"允许在 t **右边**的瓦片"
# （t.E == u.W），comp[1] 左、comp[2] 下、comp[3] 上。曾把右/左/上三个方向
# 的键写错位（右用了 comp[1]、上用了 comp[0]）—— 对称边瓦片完全测不出来
# （E/W 相容集 == W/E 相容集），道路这种不对称瓦片集则步步矛盾。
_DIRS = (
    (1, 0, 0, 1),    # 右：左瓦片 E(1) == 右瓦片 W(3)
    (-1, 0, 1, 0),   # 左：左瓦片 W(3) == 右瓦片 E(1)
    (0, 1, 2, 3),    # 下：上瓦片 S(2) == 下瓦片 N(0)
    (0, -1, 3, 2),   # 上：上瓦片 N(0) == 下瓦片 S(2)
)


class WFC:
    """瓦片平铺 WFC 求解器（确定性，靠 seed 复现）。"""

    def __init__(self, width, height, tiles, weights=None, seed=0,
                 max_attempts=50):
        if width < 1 or height < 1:
            raise ValueError('width/height 必须 ≥1')
        if not tiles:
            raise ValueError('tiles 不能为空')
        self.W = width
        self.H = height
        self.tiles = list(tiles)
        self.weights = [
            float(weights[t.name]) if (weights and t.name in weights) else 1.0
            for t in self.tiles
        ]
        self.rng = random.Random(seed)
        self.max_attempts = max_attempts

        n = len(self.tiles)
        # comp[dir][t] = 该方向上与瓦片 t 相容的邻居瓦片索引集合
        self.comp = [[set() for _ in range(n)] for _ in range(4)]
        for t in range(n):
            for u in range(n):
                if self.tiles[t].edges[1] == self.tiles[u].edges[3]:
                    self.comp[0][t].add(u)   # 右
                if self.tiles[t].edges[3] == self.tiles[u].edges[1]:
                    self.comp[1][t].add(u)   # 左
                if self.tiles[t].edges[2] == self.tiles[u].edges[0]:
                    self.comp[2][t].add(u)   # 下
                if self.tiles[t].edges[0] == self.tiles[u].edges[2]:
                    self.comp[3][t].add(u)   # 上

    # ----------------------------------------------------------------- 内部 --

    def _full_grid(self):
        full = set(range(len(self.tiles)))
        return [[set(full) for _ in range(self.W)] for _ in range(self.H)]

    def _observe(self, grid):
        """熵最低格坍缩；全坍缩返回 None。"""
        best = None
        best_len = None
        for y in range(self.H):
            for x in range(self.W):
                s = grid[y][x]
                if len(s) > 1:
                    if best_len is None or len(s) < best_len:
                        best = (x, y)
                        best_len = len(s)
        if best is None:
            return None
        x, y = best
        items = list(grid[y][x])
        wts = [self.weights[i] for i in items]
        total = sum(wts)
        r = self.rng.random() * total
        acc = 0.0
        chosen = items[0]
        for i, wt in zip(items, wts):
            acc += wt
            if r <= acc:
                chosen = i
                break
        grid[y][x] = {chosen}
        return (x, y)

    def _propagate(self, grid, starts):
        queue = list(starts)
        while queue:
            x, y = queue.pop()
            cur = grid[y][x]
            if not cur:                 # 空集 = 矛盾
                return False
            for d, (dx, dy, cdir, _odir) in enumerate(_DIRS):
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.W and 0 <= ny < self.H:
                    nb = grid[ny][nx]
                    allowed = set()
                    for t in cur:
                        allowed |= self.comp[cdir][t]
                    new = nb & allowed
                    if new != nb:
                        if not new:
                            return False
                        grid[ny][nx] = new
                        queue.append((nx, ny))
        return True

    @staticmethod
    def _consistent(grid):
        return all(len(grid[y][x]) == 1
                   for y in range(len(grid)) for x in range(len(grid[0])))

    # ----------------------------------------------------------------- 求解 --

    def solve(self):
        """求解：返回 ``height×width`` 的瓦片名二维表；无解返回 ``None``。

        同一 ``seed`` 必然得到同一结果（确定性）。
        """
        for _ in range(self.max_attempts):
            grid = self._full_grid()
            ok = True
            while True:
                cell = self._observe(grid)
                if cell is None:
                    break
                if not self._propagate(grid, [cell]):
                    ok = False
                    break
            if ok and self._consistent(grid):
                return [[self.tiles[list(grid[y][x])[0]].name
                         for x in range(self.W)]
                        for y in range(self.H)]
        return None


def check_adjacency(grid, tiles):
    """核对网格每对相邻瓦片的边插槽是否相容（求解正确性的核心不变量）。

    返回 ``True`` 当且仅当所有水平 / 垂直相邻都满足：左.E==右.W 且 上.S==下.N。
    可作为任意 WFC 输出的独立正确性校验（测试与下游都可用）。
    """
    name_to_tile = {t.name: t for t in tiles}
    H = len(grid)
    W = len(grid[0]) if H else 0
    for y in range(H):
        for x in range(W):
            t = name_to_tile[grid[y][x]]
            if x + 1 < W:
                r = name_to_tile[grid[y][x + 1]]
                if t.edges[1] != r.edges[3]:     # 右：E == 邻.W
                    return False
            if y + 1 < H:
                d = name_to_tile[grid[y + 1][x]]
                if t.edges[2] != d.edges[0]:     # 下：S == 邻.N
                    return False
    return True
