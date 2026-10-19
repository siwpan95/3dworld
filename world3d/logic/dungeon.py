# -*- coding: utf-8 -*-
"""地牢与洞穴生成：随机房间 + L 形走廊 / BSP / 元胞自动机洞穴（纯标准库）。

借鉴记账（铁律 10）
------------------
移植自 ``procgen``（MIT，<https://gitee.com/siwpan/procgen>，
``procgen/dungeon.py``）：``generate_dungeon``（随机房间 + L 走廊）、
``generate_dungeon_bsp``（二叉空间分割，保证连通）、``generate_cave``
（RogueBasin 4-5 规则元胞自动机洞穴 + 洪水填充保留最大连通块）、
``to_ascii``。借用范围：算法与网格约定（1=地/0=墙）。

适配（相对原版的改动）
---------------------
- **numpy → 纯标准库**：网格是 list of lists（行主序 ``grid[y][x]``）；
  ``np.roll`` 的环移邻居统计改写为取模索引；``bincount/argmax`` 改写为
  手写计数。本仓「纯标准库 + 标量」立场（同 ``logic/noise.py``）。
- **RNG 换源**：``np.random.RandomState(seed)`` → ``random.Random(seed)``
  （stdlib 确定性，mc-clone 口径）。⚠ 两者序列不同，故输出与 procgen
  原版**数值不同**，但**自一致**（同 seed 同输出）—— 本仓要求的是
  逐位可复现，不是与 procgen 逐位相同。
- ``randint(a, b)`` 的区间语义对齐：numpy 是 ``[a, b)``，stdlib 的
  ``randint`` 是 ``[a, b]`` 闭区间 —— 一律改用 ``randrange``（半开）。
- 移除了原版的 ``warnings.warn``（房间放不下时）：改为返回值里的
  ``placed`` 字段如实反映，调用方自判（库不替用户决定警告策略）。

确定性口径
----------
- 同 ``(尺寸, seed)`` 必然得到同一网格 —— 关卡生成因此可复现、可测试。
- BSP 的连通性由构造保证（姐妹子树必连），测试 ``test_dungeon.py``
  用洪水填充独立验证「所有房间可达」。
"""
from __future__ import annotations

import random

__all__ = ['generate_dungeon', 'generate_dungeon_bsp', 'generate_cave',
           'to_ascii']


# ------------------------------------------------------------------ 工具 --

def _zeros(h, w):
    return [[0] * w for _ in range(h)]


def _carve_corridor(grid, x1, y1, x2, y2):
    """L 形走廊：先水平后垂直。"""
    h_dir = 1 if x2 >= x1 else -1
    x = x1
    for x in range(x1, x2 + h_dir, h_dir):
        grid[y1][x] = 1
    v_dir = 1 if y2 >= y1 else -1
    for y in range(y1, y2 + v_dir, v_dir):
        grid[y][x2] = 1


def to_ascii(grid, wall: str = '#', floor: str = '.') -> str:
    """把网格转成 ASCII 地图字符串（便于打印/调试）。"""
    return '\n'.join(''.join(floor if c else wall for c in row)
                     for row in grid)


# ------------------------------------------------------------------ 随机房间 --

def generate_dungeon(width: int = 48, height: int = 32, rooms: int = 8,
                     seed: int = 0) -> dict:
    """生成简单地牢（随机房间 + L 形走廊，按放置顺序链式连通）。

    返回：
        {
          'grid': 二维列表 (height, width)，0=墙 1=地,
          'rooms': [{'x','y','w','h','cx','cy'}, ...],
          'connections': [(room_i, room_j), ...],
          'placed': 实际放置的房间数（空间不足时 < rooms）,
        }
    """
    if width < 8 or height < 8:
        raise ValueError(f'地牢尺寸过小，至少需要 8x8，收到 {width}x{height}')
    if rooms < 1:
        raise ValueError('rooms 必须 >= 1')

    rng = random.Random(seed)
    grid = _zeros(height, width)

    # 房间尺寸上限受网格约束
    w_max = min(10, width - 2)
    h_max = min(8, height - 2)

    placed = []
    attempts = 0
    while len(placed) < rooms and attempts < rooms * 20:
        attempts += 1
        w = rng.randrange(4, w_max)
        h = rng.randrange(4, h_max)
        x = rng.randrange(1, width - w - 1)
        y = rng.randrange(1, height - h - 1)
        # 简单重叠检测（允许轻微相邻）
        overlap = any(not (x + w < r['x'] - 1 or x > r['x'] + r['w'] + 1 or
                           y + h < r['y'] - 1 or y > r['y'] + r['h'] + 1)
                      for r in placed)
        if overlap:
            continue
        room = {'x': x, 'y': y, 'w': w, 'h': h,
                'cx': x + w // 2, 'cy': y + h // 2}
        placed.append(room)
        for yy in range(y, y + h):
            row = grid[yy]
            for xx in range(x, x + w):
                row[xx] = 1

    connections = []
    for i in range(1, len(placed)):
        a, b = placed[i - 1], placed[i]
        _carve_corridor(grid, a['cx'], a['cy'], b['cx'], b['cy'])
        connections.append((i - 1, i))

    return {'grid': grid, 'rooms': placed, 'connections': connections,
            'placed': len(placed)}


# ------------------------------------------------------------------ BSP --

def generate_dungeon_bsp(width: int = 48, height: int = 32, seed: int = 0,
                         min_leaf: int = 10, max_leaf: int = 22,
                         room_min: int = 4, room_max: int = 11) -> dict:
    """基于二叉空间分割（BSP）的地牢生成。

    1. 递归把地图二分，直到子区足够小；
    2. 每个叶子区内放一间不重叠的随机房间；
    3. 自底向上连接姐妹子树，保证整图连通。

    返回与 ``generate_dungeon`` 相同结构（外加 ``placed``）。
    """
    if width < 8 or height < 8:
        raise ValueError(f'地牢尺寸过小，至少需要 8x8，收到 {width}x{height}')
    rng = random.Random(seed)
    grid = _zeros(height, width)

    def split(x, y, w, h, depth):
        if depth > 12 or (w <= max_leaf and h <= max_leaf and rng.random() < 0.25) \
           or (w < min_leaf * 2 and h < min_leaf * 2):
            return {'x': x, 'y': y, 'w': w, 'h': h,
                    'left': None, 'right': None, 'room': None}
        if w > h and w >= min_leaf * 2:
            horizontal = False
        elif h > w and h >= min_leaf * 2:
            horizontal = True
        else:
            horizontal = rng.random() < 0.5
        if horizontal:
            cut = rng.randrange(min_leaf, h - min_leaf + 1)
            a = split(x, y, w, cut, depth + 1)
            b = split(x, y + cut, w, h - cut, depth + 1)
        else:
            cut = rng.randrange(min_leaf, w - min_leaf + 1)
            a = split(x, y, cut, h, depth + 1)
            b = split(x + cut, y, w - cut, h, depth + 1)
        return {'x': x, 'y': y, 'w': w, 'h': h,
                'left': a, 'right': b, 'room': None}

    root = split(0, 0, width, height, 0)
    leaves = []

    def collect(node):
        if node['left'] is None:
            leaves.append(node)
        else:
            collect(node['left'])
            collect(node['right'])

    collect(root)

    rooms = []
    for lf in leaves:
        maxw = min(room_max, lf['w'] - 2)
        maxh = min(room_max, lf['h'] - 2)
        if maxw < room_min or maxh < room_min:
            continue
        rw = rng.randrange(room_min, maxw + 1)
        rh = rng.randrange(room_min, maxh + 1)
        rx = lf['x'] + rng.randrange(1, lf['w'] - rw)
        ry = lf['y'] + rng.randrange(1, lf['h'] - rh)
        room = {'x': rx, 'y': ry, 'w': rw, 'h': rh,
                'cx': rx + rw // 2, 'cy': ry + rh // 2}
        lf['room'] = room
        rooms.append(room)
        for yy in range(ry, ry + rh):
            row = grid[yy]
            for xx in range(rx, rx + rw):
                row[xx] = 1

    connections = []

    def connect(node):
        if node['left'] is None:
            return node['room']
        lr = connect(node['left'])
        rr = connect(node['right'])
        if lr is not None and rr is not None:
            _carve_corridor(grid, lr['cx'], lr['cy'], rr['cx'], rr['cy'])
            connections.append((rooms.index(lr), rooms.index(rr)))
            return lr
        return lr if lr is not None else rr

    connect(root)

    return {'grid': grid, 'rooms': rooms, 'connections': connections,
            'placed': len(rooms)}


# ------------------------------------------------------------------ CA 洞穴 --

def _wall_neighbors(grid):
    """统计每格 3x3（含自身）内的墙数（环移边界）。"""
    h = len(grid)
    w = len(grid[0]) if h else 0
    counts = _zeros(h, w)
    for y in range(h):
        ym, yp = (y - 1) % h, (y + 1) % h
        rowm, row, rowp = grid[ym], grid[y], grid[yp]
        crow = counts[y]
        for x in range(w):
            xm, xp = (x - 1) % w, (x + 1) % w
            crow[x] = (rowm[xm] + rowm[x] + rowm[xp] +
                       row[xm] + row[xp] +
                       rowp[xm] + rowp[x] + rowp[xp])
    return counts


def _label_regions(mask):
    """4-连通标记（洪水填充），返回 (labeled, n_regions)。0 为背景。"""
    h = len(mask)
    w = len(mask[0]) if h else 0
    labeled = _zeros(h, w)
    k = 0
    for y in range(h):
        for x in range(w):
            if mask[y][x] and labeled[y][x] == 0:
                k += 1
                labeled[y][x] = k
                stack = [(y, x)]
                while stack:
                    cy, cx = stack.pop()
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < h and 0 <= nx < w \
                                and mask[ny][nx] and labeled[ny][nx] == 0:
                            labeled[ny][nx] = k
                            stack.append((ny, nx))
    return labeled, k


def generate_cave(width: int = 48, height: int = 32, seed: int = 0,
                  fill_prob: float = 0.45, iterations: int = 5,
                  border_wall: bool = True) -> dict:
    """元胞自动机洞穴生成（RogueBasin 4-5 规则）。

    - 初始随机填充（墙概率 fill_prob）；
    - 迭代平滑：3x3 墙数 >= 5 则该格变墙；
    - 洪水填充保留最大的可走连通块，其余回填为墙。

    返回：{'grid': 1=地/0=墙, 'regions': 连通块数, 'floor_count': 地板格数}。
    """
    if width < 8 or height < 8:
        raise ValueError(f'洞穴尺寸过小，至少需要 8x8，收到 {width}x{height}')
    rng = random.Random(seed)
    grid = [[1 if rng.random() < fill_prob else 0 for _ in range(width)]
            for _ in range(height)]  # 1=墙

    def _frame(g):
        if border_wall:
            g[0] = [1] * width
            g[-1] = [1] * width
            for row in g:
                row[0] = 1
                row[-1] = 1
        return g

    grid = _frame(grid)
    for _ in range(iterations):
        counts = _wall_neighbors(grid)
        grid = _frame([[1 if counts[y][x] >= 5 else 0 for x in range(width)]
                       for y in range(height)])

    floor = [[1 if grid[y][x] == 0 else 0 for x in range(width)]
             for y in range(height)]  # 内部约定：1=地板
    labeled, n_regions = _label_regions(floor)
    if n_regions == 0:
        return {'grid': _zeros(height, width), 'regions': 0, 'floor_count': 0}

    # 最大连通块（bincount+argmax 的手写版）
    sizes = {}
    for row in labeled:
        for v in row:
            if v:
                sizes[v] = sizes.get(v, 0) + 1
    biggest = max(sizes, key=lambda k: sizes[k])
    kept = [[1 if labeled[y][x] == biggest else 0 for x in range(width)]
            for y in range(height)]
    floor_count = sum(sizes.values()) and sizes[biggest]
    # 输出约定与 dungeon 一致：1=地(可走) 0=墙
    out = [[1 if kept[y][x] else 0 for x in range(width)]
           for y in range(height)]
    if border_wall:
        out[0] = [0] * width
        out[-1] = [0] * width
        for row in out:
            row[0] = 0
            row[-1] = 0
    return {'grid': out, 'regions': n_regions, 'floor_count': floor_count}
