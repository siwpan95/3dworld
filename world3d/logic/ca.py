# -*- coding: utf-8 -*-
"""元胞自动机引擎：142 条规则 + 统一演化循环（纯标准库）。

借鉴记账（铁律 10）
------------------
- **规则数据**（142 条：Life-like 63 / Complex 11 / Multi-state 9 /
  Gray-Scott 24 / Lenia 6 / LtL 9 / Elementary 1D 15 / 其他连续 5）：
  移植自 ``cellular_automaton``（MIT）经 ``an-engine`` 转借 —— 数据在
  ``ca_rules_data.py``（由原 JSON 逐字节转出）。
- **计算逻辑**：移植自 an-engine 的 ``media/ca/rules/*.py``（base /
  multi_state / complex / continuous 四个模块的规则类与 RuleManager）。
  记账源头认 ``cellular_automaton``（原始库），an-engine 是转手方。

适配（相对原版的改动，均因本仓「纯标准库 + 确定性」立场）
------------------------------------------------------
- **numpy → 纯标准库**：网格是 list of lists（行主序 ``grid[y][x]``）；
  邻居统计 / Laplacian / 卷积全部改写为取模索引的嵌套循环；连续规则的
  浮点场是 list of lists of float。
- **float32 → float64**：原版场用 ``np.float32``，纯 Python 的 float 是
  IEEE 754 双精度 —— 连续规则（Gray-Scott/Lenia/SmoothLife）的数值与
  原版**不同**，但**自一致**（同 seed 同输出，跨平台逐位相同）。
- **随机数可注入**：原版多处用 ``np.random`` 全局状态（ForestFire 的
  生长/雷击、Snowflake 的冻结、Hodgepodge 的感染、Gray-Scott/Lenia/
  SmoothLife/BZ 的种子区）—— 本版全部走规则自带的 ``self.rng``
  （``random.Random`` 实例），``CellularAutomaton(seed=...)`` 注入，
  确定性因此可测（``test_ca.py`` 钉住）。
- **``load_pattern`` 移除**：原版依赖 an-engine 的 ``rendering.patterns``
  （渲染耦合），本仓无此依赖；图案装载由调用方直接写网格（``set_cell``）。

确定性口径
----------
- 同 ``(网格, seed)`` 必然得到同一演化序列 —— 洞穴/图案生成可复现。
- 边界一律环移（toroidal），与原版一致。
"""
from __future__ import annotations

import copy
import math
import random

from .ca_rules_data import RULES_DATA

__all__ = [
    'CARule', 'OuterTotalisticRule', 'CellularAutomaton', 'RuleManager',
    'RULES', 'count_neighbours', 'count_von_neumann_neighbours',
    'get_rule', 'list_rules', 'get_description', 'get_category',
    'list_rules_by_category', 'total_count',
]


# ------------------------------------------------------------------ 网格工具 --

def count_neighbours(grid):
    """Moore 8-邻域计数（环移边界）。grid: list of lists of int。"""
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    out = [[0] * cols for _ in range(rows)]
    for y in range(rows):
        ym, yp = (y - 1) % rows, (y + 1) % rows
        rowm, row, rowp = grid[ym], grid[y], grid[yp]
        crow = out[y]
        for x in range(cols):
            xm, xp = (x - 1) % cols, (x + 1) % cols
            crow[x] = (rowm[xm] + rowm[x] + rowm[xp] +
                       row[xm] + row[xp] +
                       rowp[xm] + rowp[x] + rowp[xp])
    return out


def count_von_neumann_neighbours(grid):
    """von Neumann 4-邻域计数（环移边界）。"""
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    out = [[0] * cols for _ in range(rows)]
    for y in range(rows):
        ym, yp = (y - 1) % rows, (y + 1) % rows
        rowm, row, rowp = grid[ym], grid[y], grid[yp]
        crow = out[y]
        for x in range(cols):
            xm, xp = (x - 1) % cols, (x + 1) % cols
            crow[x] = rowm[x] + rowp[x] + row[xm] + row[xp]
    return out


def _zeros(rows, cols, value=0):
    return [[value] * cols for _ in range(rows)]


# ------------------------------------------------------------------ 规则基类 --

class CARule:
    """所有 CA 规则的抽象基类。

    ``rng`` 为规则自带的确定性随机源（``random.Random`` 实例）——
    ``CellularAutomaton`` 构造时注入；默认种子 0。
    """

    def __init__(self, name, num_states=2):
        self.name = name
        self.num_states = num_states
        self.rng = random.Random(0)

    def set_rng(self, rng):
        """注入确定性随机源（CellularAutomaton 用）。"""
        self.rng = rng
        return self

    def step(self, grid, neighbours=None):
        """计算下一代网格（子类实现）。"""
        raise NotImplementedError

    def __repr__(self):
        return f'<{self.__class__.__name__} {self.name!r}>'


class OuterTotalisticRule(CARule):
    """传统二态外 totalistic 规则（Conway 式 B/S 记号）。

    born:    死细胞变活的邻居数集合
    survive: 活细胞存活的邻居数集合
    """

    def __init__(self, name, born, survive):
        super().__init__(name, num_states=2)
        self.born = set(born)
        self.survive = set(survive)

    def step(self, grid, neighbours=None):
        if neighbours is None:
            neighbours = count_neighbours(grid)
        rows, cols = len(grid), len(grid[0])
        out = _zeros(rows, cols)
        for y in range(rows):
            grow, orow, nrow = grid[y], out[y], neighbours[y]
            for x in range(cols):
                n = nrow[x]
                if grow[x] == 0:
                    orow[x] = 1 if n in self.born else 0
                else:
                    orow[x] = 1 if n in self.survive else 0
        return out

    def __repr__(self):
        b = ''.join(str(n) for n in sorted(self.born))
        s = ''.join(str(n) for n in sorted(self.survive))
        return f'OuterTotalisticRule({self.name!r}, B{b}/S{s})'


# ------------------------------------------------------------------ 多态规则 --

class BriansBrainRule(CARule):
    """Brian's Brain（3 态）：死→活 恰 2 个活邻居；活→将死；将死→死。"""

    def __init__(self):
        super().__init__('brians_brain', num_states=3)

    def step(self, grid, neighbours=None):
        rows, cols = len(grid), len(grid[0])
        alive = [[1 if grid[y][x] == 1 else 0 for x in range(cols)]
                 for y in range(rows)]
        an = count_neighbours(alive)
        out = _zeros(rows, cols)
        for y in range(rows):
            for x in range(cols):
                if grid[y][x] == 1:
                    out[y][x] = 2
                elif grid[y][x] == 0 and an[y][x] == 2:
                    out[y][x] = 1
        return out


class WireworldRule(CARule):
    """Wireworld（4 态）：空→空；电子头→电子尾；电子尾→导体；

    导体→电子头 当恰有 1 或 2 个电子头邻居。
    """

    def __init__(self):
        super().__init__('wireworld', num_states=4)

    def step(self, grid, neighbours=None):
        rows, cols = len(grid), len(grid[0])
        head = [[1 if grid[y][x] == 2 else 0 for x in range(cols)]
                for y in range(rows)]
        hn = count_neighbours(head)
        out = _zeros(rows, cols)
        for y in range(rows):
            for x in range(cols):
                c = grid[y][x]
                if c == 2:
                    out[y][x] = 3
                elif c == 3:
                    out[y][x] = 1
                elif c == 1:
                    out[y][x] = 2 if hn[y][x] in (1, 2) else 1
        return out


class ForestFireRule(CARule):
    """森林火灾（3 态）：空→树（概率 p_grow）；树→燃烧（邻居有燃烧 或

    雷击概率）；燃烧→空。随机性走 ``self.rng``（可注入）。
    """

    def __init__(self, grow_prob=0.01, lightning_prob=0.001):
        super().__init__('forest_fire', num_states=3)
        self.grow_prob = grow_prob
        self.lightning_prob = lightning_prob

    def step(self, grid, neighbours=None):
        rows, cols = len(grid), len(grid[0])
        burning = [[1 if grid[y][x] == 2 else 0 for x in range(cols)]
                   for y in range(rows)]
        bn = count_neighbours(burning)
        rng = self.rng
        out = _zeros(rows, cols)
        for y in range(rows):
            for x in range(cols):
                c = grid[y][x]
                if c == 1:
                    out[y][x] = 2 if (bn[y][x] > 0 or
                                      rng.random() < self.lightning_prob) else 1
                elif c == 0 and rng.random() < self.grow_prob:
                    out[y][x] = 1
        return out


class CrystalGrowthRule(CARule):
    """晶体生长（3 态）：空→晶体（邻居晶体数 ≥ 阈值 或 冻结概率）；

    晶体保持；边界→晶体。随机性走 ``self.rng``。
    """

    def __init__(self, growth_threshold=3, freeze_prob=0.1):
        super().__init__('crystal_growth', num_states=3)
        self.growth_threshold = growth_threshold
        self.freeze_prob = freeze_prob

    def step(self, grid, neighbours=None):
        rows, cols = len(grid), len(grid[0])
        crystal = [[1 if grid[y][x] == 1 else 0 for x in range(cols)]
                   for y in range(rows)]
        cn = count_neighbours(crystal)
        rng = self.rng
        out = [row[:] for row in grid]
        for y in range(rows):
            for x in range(cols):
                if grid[y][x] == 0:
                    if cn[y][x] >= self.growth_threshold or \
                            rng.random() < self.freeze_prob:
                        out[y][x] = 1
                elif grid[y][x] == 2:
                    out[y][x] = 1
        return out


class SandpileRule(CARule):
    """阿贝尔沙堆：格点积沙到阈值即崩塌，向四个基本方向各送一粒。"""

    def __init__(self, threshold=4, num_states=4):
        super().__init__('sandpile', num_states=num_states)
        self.threshold = threshold

    def step(self, grid, neighbours=None):
        rows, cols = len(grid), len(grid[0])
        unstable = [[1 if grid[y][x] >= self.threshold - 1 else 0
                     for x in range(cols)] for y in range(rows)]
        un = count_von_neumann_neighbours(unstable)
        out = _zeros(rows, cols)
        for y in range(rows):
            for x in range(cols):
                v = grid[y][x] + un[y][x]
                if unstable[y][x]:
                    v -= self.threshold
                out[y][x] = max(0, min(self.num_states - 1, v))
        return out


# ------------------------------------------------------------------ 复杂规则 --

def _build_langton_rule_table():
    """Langton 自复制环的转移表（(center,N,S,E,W) -> new_state）。

    Chris Langton (1984) 的经典 8 态自复制 CA，经 J. Byl 细化。
    纯数据表，逐行移植自 an-engine。
    """
    R = {}

    def add(c, n, s, e, w, new):
        R[(c, n, s, e, w)] = new

    # --- 鞘维护（态 1）---
    for nbr in (0, 1, 2, 3, 4, 5, 6, 7):
        add(1, 1, nbr, 1, 1, 1)
        add(1, 1, 1, nbr, 1, 1)
        add(1, nbr, 1, 1, 1, 1)
        add(1, 1, 1, 1, nbr, 1)
        add(1, 1, 1, 1, 1, 1)

    # 鞘缺支撑且无信号 → 消散
    for nbr in (0, 2, 4, 6):
        for nbr2 in (0, 2, 4, 6):
            add(1, 1, 1, nbr, nbr2, 0)
            add(1, 1, nbr, 1, nbr2, 0)
            add(1, nbr, 1, 1, nbr2, 0)
            add(1, nbr, nbr2, 1, 1, 0)

    # 鞘遇信号 → 变信号
    add(1, 2, 1, 1, 0, 2)
    add(1, 2, 1, 0, 1, 2)
    add(1, 4, 0, 1, 1, 4)
    add(1, 0, 4, 1, 1, 4)
    add(1, 3, 1, 1, 0, 3)
    add(1, 1, 3, 1, 0, 3)
    add(1, 5, 0, 1, 1, 5)
    add(1, 1, 5, 0, 1, 5)

    # 拐角：信号转向
    add(1, 2, 0, 1, 1, 3)
    add(1, 1, 2, 0, 1, 5)
    add(1, 4, 1, 1, 0, 5)
    add(1, 0, 1, 4, 1, 3)

    # --- 信号传播（态 2-5）---
    add(2, 1, 1, 1, 2, 0)
    add(2, 1, 1, 0, 2, 2)
    add(2, 1, 1, 1, 0, 1)
    add(2, 1, 2, 1, 1, 1)
    add(2, 1, 0, 1, 1, 5)
    add(2, 1, 0, 1, 0, 5)
    add(2, 1, 0, 0, 1, 3)
    add(2, 1, 3, 1, 1, 1)

    add(3, 1, 1, 2, 1, 0)
    add(3, 0, 1, 2, 1, 3)
    add(3, 1, 1, 0, 1, 1)
    add(3, 1, 0, 1, 1, 4)
    add(3, 0, 0, 1, 1, 4)
    add(3, 1, 1, 1, 0, 2)
    add(3, 0, 1, 1, 0, 2)

    add(4, 1, 1, 4, 1, 0)
    add(4, 1, 1, 4, 0, 4)
    add(4, 1, 1, 1, 1, 1)
    add(4, 2, 1, 1, 1, 1)
    add(4, 1, 0, 0, 1, 3)
    add(4, 1, 0, 1, 1, 5)
    add(4, 1, 0, 1, 0, 5)
    add(4, 0, 0, 0, 1, 3)

    add(5, 2, 1, 1, 1, 0)
    add(5, 2, 1, 0, 1, 5)
    add(5, 1, 1, 1, 1, 1)
    add(5, 0, 1, 0, 1, 2)
    add(5, 0, 1, 1, 1, 4)
    add(5, 1, 0, 0, 1, 2)
    add(5, 0, 0, 0, 1, 2)

    # --- 吞噬细胞（态 6/7）---
    add(6, 0, 0, 0, 0, 6)
    add(6, 2, 1, 1, 1, 7)
    add(6, 1, 3, 1, 1, 7)
    add(6, 1, 1, 4, 1, 7)
    add(6, 1, 1, 1, 5, 7)

    add(7, 0, 0, 0, 0, 0)
    add(7, 1, 2, 1, 1, 1)

    return R


_LANGTON_RULES = _build_langton_rule_table()


class LangtonsLoopRule(CARule):
    """Langton 自复制环（8 态，von Neumann 邻域 + 查表）。"""

    def __init__(self):
        super().__init__('langtons_loop', num_states=8)

    def step(self, grid, neighbours=None):
        rows, cols = len(grid), len(grid[0])
        out = _zeros(rows, cols)
        for y in range(rows):
            ym, yp = (y - 1) % rows, (y + 1) % rows
            for x in range(cols):
                xm, xp = (x - 1) % cols, (x + 1) % cols
                key = (grid[y][x], grid[ym][x], grid[yp][x],
                       grid[y][xp], grid[y][xm])
                out[y][x] = _LANGTON_RULES.get(key, 0)
        return out


class SnowflakeRule(CARule):
    """雪花晶体生长（Packard 模型 + 六重各向异性）。

    态 0=蒸气，1..N-2=部分冻结，N-1=完全冻结。冻结概率**各向异性**：
    晶体邻居沿六条主轴方向时概率高 —— 自然长出六重枝晶。
    随机性走 ``self.rng``。
    """

    def __init__(self, num_states=8, base_prob=0.02, axis_prob=0.15):
        super().__init__('snowflake', num_states=num_states)
        self.base_prob = base_prob
        self.axis_prob = axis_prob
        self._max_frozen = num_states - 1

    def step(self, grid, neighbours=None):
        rows, cols = len(grid), len(grid[0])
        max_s = self._max_frozen
        rng = self.rng
        new_grid = [row[:] for row in grid]

        # 每格一个 rand（与原版一致：两趟冻结判定共用同一个随机场）
        rand = [[rng.random() for _ in range(cols)] for _ in range(rows)]

        # 8 个 Moore 邻居偏移；六重主轴近似取其中 6 个
        shifts = ((-1, -1), (-1, 0), (-1, 1), (0, -1),
                  (0, 1), (1, -1), (1, 0), (1, 1))
        hex_axes = {0, 1, 2, 5, 6, 7}  # NW, N, NE, SE, S, SW

        frozen_n = _zeros(rows, cols)
        frozen_hex = _zeros(rows, cols)
        for idx, (dr, dc) in enumerate(shifts):
            is_hex = idx in hex_axes
            for y in range(rows):
                sy = (y + dr) % rows
                srow = grid[sy]
                frow, hrow = frozen_n[y], frozen_hex[y]
                for x in range(cols):
                    if srow[(x + dc) % cols] == max_s:
                        frow[x] += 1
                        if is_hex:
                            hrow[x] += 1

        # 部分冻结格每步推进一态
        for y in range(rows):
            for x in range(cols):
                s = grid[y][x]
                if 1 <= s < max_s:
                    new_grid[y][x] = min(max_s, s + 1)

        # 蒸气冻结（两趟，与原版顺序一致）：
        #   趟 1 → 态 1（阈值 prob1*0.3，仅未冻结格）；
        #   趟 2 → max_s（阈值 prob2，**无条件覆盖** —— 原版如此，
        #         态 1 档因此被 max_s 档遮蔽，忠实保留该行为）。
        for y in range(rows):
            for x in range(cols):
                if grid[y][x] != 0 or frozen_n[y][x] == 0:
                    continue
                r = rand[y][x]
                hex_hit = frozen_hex[y][x] > 0
                prob1 = (self.axis_prob if hex_hit else self.base_prob) * 0.5
                if r < prob1 * 0.3 and new_grid[y][x] == 0:
                    new_grid[y][x] = 1
                prob2 = self.axis_prob if hex_hit else self.base_prob
                if r < prob2:
                    new_grid[y][x] = max_s
        return new_grid


class CyclicCARule(CARule):
    """循环 CA（Greenberg-Hastings 型）：N 态循环，邻居有下一态则推进。

    产出螺旋波与靶图。
    """

    def __init__(self, n_colors=14, threshold=1):
        super().__init__('cyclic', num_states=n_colors)
        self.n_colors = n_colors
        self.threshold = threshold

    def step(self, grid, neighbours=None):
        rows, cols = len(grid), len(grid[0])
        new_grid = [row[:] for row in grid]
        N = self.n_colors
        for s in range(N):
            next_s = (s + 1) % N
            nxt = [[1 if grid[y][x] == next_s else 0 for x in range(cols)]
                   for y in range(rows)]
            nn = count_neighbours(nxt)
            for y in range(rows):
                for x in range(cols):
                    if grid[y][x] == s and nn[y][x] >= self.threshold:
                        new_grid[y][x] = next_s
        return new_grid


class HodgepodgeRule(CARule):
    """Hodgepodge 机器（可激发介质反应扩散，Dewdney）。

    态 0=健康，1=感染，2..N-2=病变（每步推进），N-1=恢复。
    健康→感染：感染邻居数在 [k1, k2] 且概率 g。随机性走 ``self.rng``。
    """

    def __init__(self, num_states=20, k1=1, k2=8, g=1.0):
        super().__init__('hodgepodge', num_states=num_states)
        self.k1 = k1
        self.k2 = k2
        self.g = g

    def step(self, grid, neighbours=None):
        rows, cols = len(grid), len(grid[0])
        N = self.num_states
        rng = self.rng
        infected = [[1 if 1 <= grid[y][x] < N - 1 else 0
                     for x in range(cols)] for y in range(rows)]
        inn = count_neighbours(infected)
        out = _zeros(rows, cols)
        for y in range(rows):
            for x in range(cols):
                c = grid[y][x]
                if c == 0:
                    n = inn[y][x]
                    if self.k1 <= n <= self.k2 and rng.random() < self.g:
                        out[y][x] = 1
                elif 1 <= c < N - 1:
                    out[y][x] = c + 1
                # c == N-1（恢复）→ 0（out 已初始化为 0）
        return out


# ------------------------------------------------------------------ 连续规则 --

def _laplacian(field):
    """9 点 stencil 离散拉普拉斯（环移边界）。权重与原版一致。"""
    rows, cols = len(field), len(field[0])
    weights = ((-1, 0, 0.2), (1, 0, 0.2), (0, -1, 0.2), (0, 1, 0.2),
               (-1, -1, 0.05), (-1, 1, 0.05), (1, -1, 0.05), (1, 1, 0.05))
    out = _zeros(rows, cols, 0.0)
    for y in range(rows):
        for x in range(cols):
            c = field[y][x]
            acc = 0.0
            for dr, dc, w in weights:
                acc += w * (field[(y + dr) % rows][(x + dc) % cols] - c)
            out[y][x] = acc
    return out


_GRAY_SCOTT_PRESETS = {
    'spots': (0.035, 0.065), 'stripes': (0.030, 0.062),
    'maze': (0.025, 0.060), 'waves': (0.040, 0.060),
    'pulsating': (0.050, 0.065), 'chaos': (0.020, 0.055),
    'bubbles': (0.012, 0.050), 'tendrils': (0.027, 0.058),
    'solitons': (0.030, 0.057), 'mitosis': (0.028, 0.062),
    'coral': (0.038, 0.058), 'worms_gs': (0.040, 0.064),
    'fingerprints': (0.034, 0.061), 'flicker': (0.046, 0.059),
    'u_skate': (0.025, 0.060),
    'pearson_alpha': (0.010, 0.047), 'pearson_beta': (0.020, 0.048),
    'pearson_gamma': (0.025, 0.048), 'pearson_delta': (0.020, 0.050),
    'pearson_epsilon': (0.030, 0.055),
}


class GrayScottRule(CARule):
    """Gray-Scott 反应扩散：Turing 斑点/条纹/迷宫/螺旋。

    自持 U/V 双浮点场；网格输出是 U 场的 uint8 可视化。
    种子区随机性走 ``self.rng``（可注入）。
    """

    def __init__(self, preset='spots', Du=0.16, Dv=0.08, dt=1.0):
        super().__init__('gray_scott', num_states=256)
        f, k = _GRAY_SCOTT_PRESETS.get(preset, (0.035, 0.065))
        self.preset = preset
        self.f = f
        self.k = k
        self.Du = Du
        self.Dv = Dv
        self.dt = dt
        self._U = None
        self._V = None
        self._initialized = False

    def _ensure_state(self, shape):
        if not self._initialized or self._U is None:
            rows, cols = shape
            rng = self.rng
            self._U = _zeros(rows, cols, 1.0)
            self._V = _zeros(rows, cols, 0.0)
            r0, r1 = rows // 2 - rows // 8, rows // 2 + rows // 8
            c0, c1 = cols // 2 - cols // 8, cols // 2 + cols // 8
            for y in range(r0, r1):
                for x in range(c0, c1):
                    self._V[y][x] = rng.random() * 0.5 + 0.25
                    self._U[y][x] = rng.random() * 0.2 + 0.3
            self._initialized = True

    def reset(self, shape):
        self._initialized = False

    def step(self, grid, neighbours=None):
        self._ensure_state((len(grid), len(grid[0])))
        rows, cols = len(grid), len(grid[0])
        lap_u = _laplacian(self._U)
        lap_v = _laplacian(self._V)
        U, V = self._U, self._V
        f, k, dt = self.f, self.k, self.dt
        for y in range(rows):
            ur, vr, lur, lvr = U[y], V[y], lap_u[y], lap_v[y]
            for x in range(cols):
                u, v = ur[x], vr[x]
                uvv = u * v * v
                ur[x] = max(0.0, min(1.0, u + (self.Du * lur[x] - uvv
                                               + f * (1.0 - u)) * dt))
                vr[x] = max(0.0, min(1.0, v + (self.Dv * lvr[x] + uvv
                                               - (f + k) * v) * dt))
        return [[int(U[y][x] * 255) for x in range(cols)]
                for y in range(rows)]

    @property
    def has_float_state(self):
        return True


def _build_lenia_kernel(R, mu, sigma):
    """Lenia 环形核（半径 R，峰在 mu，宽 sigma，归一化）。"""
    size = 2 * R + 1
    kernel = _zeros(size, size, 0.0)
    for dy in range(size):
        for dx in range(size):
            dist = math.sqrt((dy - R) ** 2 + (dx - R) ** 2) / R
            if dist <= 1.0:
                kernel[dy][dx] = math.exp(-((dist - mu) ** 2) / (2 * sigma ** 2))
    s = sum(sum(row) for row in kernel)
    if s > 0:
        kernel = [[v / s for v in row] for row in kernel]
    return kernel


class LeniaRule(CARule):
    """Lenia 连续 CA：环形核卷积 + 生长函数 G(x)=2exp(-(x-μ)²/2σ²)-1。

    产出滑翔机/宇宙船/有机生命体。场初始化走 ``self.rng``。
    """

    _SPECIES_PARAMS = {
        'orbium': (10, 0.15, 0.015, 0.1),
        'hydro': (20, 0.12, 0.018, 0.15),
        'parasite': (15, 0.18, 0.012, 0.08),
        'frother': (8, 0.20, 0.014, 0.12),
    }

    def __init__(self, kernel_radius=13, mu=0.15, sigma=0.015, dt=0.1,
                 species='orbium'):
        super().__init__('lenia', num_states=256)
        if species in self._SPECIES_PARAMS:
            kernel_radius, mu, sigma, dt = self._SPECIES_PARAMS[species]
        self.species = species
        self.kernel_radius = kernel_radius
        self.mu = mu
        self.sigma = sigma
        self.dt = dt
        self._kernel = None
        self._field = None
        self._initialized = False

    def _ensure_state(self, shape):
        if self._kernel is None:
            self._kernel = _build_lenia_kernel(
                self.kernel_radius, self.mu, self.sigma)
        if not self._initialized or self._field is None:
            rows, cols = shape
            rng = self.rng
            self._field = _zeros(rows, cols, 0.0)
            cy, cx = rows // 2, cols // 2
            R = min(rows, cols) // 6
            for y in range(rows):
                for x in range(cols):
                    dist = math.sqrt((y - cy) ** 2 + (x - cx) ** 2)
                    if R / 2 <= dist <= R:
                        self._field[y][x] = rng.random() * 0.8 + 0.2
            self._initialized = True

    def reset(self, shape):
        self._initialized = False
        self._kernel = None

    def step(self, grid, neighbours=None):
        self._ensure_state((len(grid), len(grid[0])))
        rows, cols = len(grid), len(grid[0])
        kernel = self._kernel
        kh = len(kernel)
        R = kh // 2
        field = self._field
        mu, sigma, dt = self.mu, self.sigma, self.dt
        for y in range(rows):
            for x in range(cols):
                potential = 0.0
                for dy in range(kh):
                    ky = kernel[dy]
                    sy = (y + dy - R) % rows
                    frow = field[sy]
                    for dx in range(kh):
                        k = ky[dx]
                        if k:
                            potential += k * frow[(x + dx - R) % cols]
                growth = 2.0 * math.exp(
                    -((potential - mu) ** 2) / (2 * sigma ** 2)) - 1.0
                v = field[y][x] + growth * dt
                field[y][x] = max(0.0, min(1.0, v))
        return [[int(field[y][x] * 255) for x in range(cols)]
                for y in range(rows)]

    @property
    def has_float_state(self):
        return True


class LargerThanLifeRule(CARule):
    """Larger than Life：扩展半径的圆形邻域外 totalistic 规则。

    产出宏观行为：大滑翔机、扩张的虫群等。
    """

    _PRESETS = {
        'bugs': (5, 34, 45, 34, 58),
        'bugs2': (3, 21, 31, 10, 21),
        'brain': (4, 33, 45, 32, 62),
        'sand': (2, 10, 16, 10, 16),
        'cities': (3, 18, 26, 18, 26),
        'worms': (7, 74, 87, 70, 98),
        'bubbles': (4, 20, 28, 40, 60),
    }

    def __init__(self, radius=5, born_min=34, born_max=45,
                 survive_min=34, survive_max=58, preset='bugs'):
        super().__init__('larger_than_life', num_states=2)
        if preset in self._PRESETS:
            radius, born_min, born_max, survive_min, survive_max = \
                self._PRESETS[preset]
        self.radius = radius
        self.born_min = born_min
        self.born_max = born_max
        self.survive_min = survive_min
        self.survive_max = survive_max
        self.preset = preset
        self._offsets = self._build_offsets(radius)

    @staticmethod
    def _build_offsets(R):
        """圆形邻域的 (dy, dx) 偏移表（不含中心）。"""
        offs = []
        for dy in range(-R, R + 1):
            for dx in range(-R, R + 1):
                if (dy or dx) and dy * dy + dx * dx <= R * R:
                    offs.append((dy, dx))
        return offs

    def step(self, grid, neighbours=None):
        rows, cols = len(grid), len(grid[0])
        offs = self._offsets
        out = _zeros(rows, cols)
        for y in range(rows):
            for x in range(cols):
                total = 0
                for dy, dx in offs:
                    total += grid[(y + dy) % rows][(x + dx) % cols]
                if grid[y][x] == 0:
                    out[y][x] = 1 if self.born_min <= total <= self.born_max else 0
                else:
                    out[y][x] = 1 if self.survive_min <= total <= self.survive_max else 0
        return out


class Elementary1DRule(CARule):
    """Wolfram 基本 1D CA（0-255 规则）。

    网格是历史缓冲：每步整行下移，新行由顶行的 3 位模式查表算出。
    著名规则：30（混沌）/ 90（Sierpinski）/ 110（图灵完备）/ 184（车流）。
    """

    _FAMOUS = {
        'rule30': 30, 'rule90': 90, 'rule110': 110,
        'rule184': 184, 'rule54': 54, 'rule22': 22,
        'rule126': 126, 'rule150': 150, 'rule45': 45,
        'rule73': 73, 'rule105': 105,
    }

    def __init__(self, rule_number=30):
        super().__init__('elementary_1d', num_states=2)
        self.rule_number = rule_number % 256
        self._lookup = [(self.rule_number >> i) & 1 for i in range(8)]

    def step(self, grid, neighbours=None):
        rows, cols = len(grid), len(grid[0])
        # 整行下移（末行回卷到顶）
        new_grid = [grid[(y - 1) % rows] for y in range(rows)]
        new_grid = [row[:] for row in new_grid]
        top = grid[0]
        lookup = self._lookup
        for x in range(cols):
            left = top[(x - 1) % cols]
            right = top[(x + 1) % cols]
            pattern = left * 4 + top[x] * 2 + right
            new_grid[0][x] = lookup[pattern]
        return new_grid

    def init_grid(self, rows, cols):
        """单细胞居中的初始网格。"""
        g = _zeros(rows, cols)
        g[0][cols // 2] = 1
        return g


def _build_gaussian_kernel(R, inner=False):
    """高斯加权圆盘（inner）或圆环（outer）核，归一化。"""
    size = 2 * R + 1
    kernel = _zeros(size, size, 0.0)
    r_inner = R * 0.3
    r_outer = float(R)
    for dy in range(size):
        for dx in range(size):
            dist = math.sqrt((dy - R) ** 2 + (dx - R) ** 2)
            if inner:
                if dist <= r_inner:
                    kernel[dy][dx] = math.exp(
                        -(dist / max(r_inner, 1)) ** 2 * 2)
            else:
                if r_inner < dist <= r_outer:
                    mid = (r_inner + r_outer) / 2
                    width = (r_outer - r_inner) / 2
                    kernel[dy][dx] = math.exp(
                        -((dist - mid) / max(width, 1)) ** 2 * 2)
    s = sum(sum(row) for row in kernel)
    if s > 0:
        kernel = [[v / s for v in row] for row in kernel]
    return kernel


class SmoothLifeRule(CARule):
    """SmoothLife（Rafler 2011）：连续态 [0,1] + 高斯内外核 + sigmoid 转移。

    产出平滑流动的图案：会变形融合的滑翔机、呼吸的有机结构。
    场初始化走 ``self.rng``。
    """

    def __init__(self, radius=5, b1=0.257, b2=0.336, d1=0.365, d2=0.549,
                 sharpness=0.028, dt=0.1):
        super().__init__('smooth_life', num_states=256)
        self.radius = radius
        self.b1 = b1
        self.b2 = b2
        self.d1 = d1
        self.d2 = d2
        self.sharpness = sharpness
        self.dt = dt
        self._kernel_inner = None
        self._kernel_outer = None
        self._field = None
        self._initialized = False

    def _ensure_state(self, shape):
        if self._kernel_inner is None:
            self._kernel_inner = _build_gaussian_kernel(self.radius, inner=True)
            self._kernel_outer = _build_gaussian_kernel(self.radius, inner=False)
        if not self._initialized or self._field is None:
            rows, cols = shape
            rng = self.rng
            self._field = _zeros(rows, cols, 0.0)
            cy, cx = rows // 2, cols // 2
            r = min(rows, cols) // 4
            for y in range(cy - r, cy + r):
                for x in range(cx - r, cx + r):
                    self._field[y][x] = rng.random() * 0.8 + 0.1
            self._initialized = True

    def reset(self, shape):
        self._initialized = False
        self._kernel_inner = None
        self._kernel_outer = None

    @staticmethod
    def _convolve(field, kernel):
        rows, cols = len(field), len(field[0])
        kh = len(kernel)
        R = kh // 2
        out = _zeros(rows, cols, 0.0)
        for y in range(rows):
            for x in range(cols):
                acc = 0.0
                for dy in range(kh):
                    ky = kernel[dy]
                    sy = (y + dy - R) % rows
                    frow = field[sy]
                    for dx in range(kh):
                        k = ky[dx]
                        if k:
                            acc += k * frow[(x + dx - R) % cols]
                out[y][x] = acc
        return out

    @staticmethod
    def _sig(x):
        """标准 sigmoid（防 exp 溢出：大负数直接 0，大正数直接 1）。"""
        if x < -30.0:
            return 0.0
        if x > 30.0:
            return 1.0
        return 1.0 / (1.0 + math.exp(-x))

    def step(self, grid, neighbours=None):
        self._ensure_state((len(grid), len(grid[0])))
        rows, cols = len(grid), len(grid[0])
        inner_avg = self._convolve(self._field, self._kernel_inner)
        outer_avg = self._convolve(self._field, self._kernel_outer)
        field = self._field
        dt = self.dt
        s = self.sharpness
        b1, b2, d1, d2 = self.b1, self.b2, self.d1, self.d2
        sig = self._sig
        for y in range(rows):
            for x in range(cols):
                n = outer_avg[y][x]
                a = inner_avg[y][x]
                # 出生/存活带：双 sigmoid 乘积（区间 [b1,b2] / [d1,d2] 的平滑指示）
                birth = sig((n - b1) / s) * sig((b2 - n) / s)
                survival = sig((n - d1) / s) * sig((d2 - n) / s)
                # 按内核均值在「出生区 ↔ 存活区」之间平滑过渡
                aliveness = sig((a - 0.5) / 0.15)
                transition = birth * (1.0 - aliveness) + survival * aliveness
                v = field[y][x] + dt * (2.0 * transition - 1.0 - field[y][x])
                field[y][x] = max(0.0, min(1.0, v))
        return [[int(field[y][x] * 255) for x in range(cols)]
                for y in range(rows)]

    @property
    def has_float_state(self):
        return True


class BZReactionRule(CARule):
    """Belousov-Zhabotinsky 反应的离散 CA（螺旋波）。

    态 0=静息，1=激发，2..N-2=不应期（每步推进），N-1=恢复。
    激发判定：加权激活（激发邻居正权重、不应期邻居负权重）≥ 阈值。
    """

    def __init__(self, num_states=16, threshold=10.0, weight_excited=1.0,
                 weight_refractory=-0.5):
        super().__init__('bz_reaction', num_states=num_states)
        self.threshold = threshold
        self.weight_excited = weight_excited
        self.weight_refractory = weight_refractory
        self._excited_max = num_states // 3

    def step(self, grid, neighbours=None):
        rows, cols = len(grid), len(grid[0])
        N = self.num_states
        emax = self._excited_max
        excited = [[1.0 if 1 <= grid[y][x] <= emax else 0.0
                    for x in range(cols)] for y in range(rows)]
        refractory = [[1.0 if emax < grid[y][x] < N - 1 else 0.0
                       for x in range(cols)] for y in range(rows)]
        en = count_neighbours(excited)
        rn = count_neighbours(refractory)
        out = [row[:] for row in grid]
        for y in range(rows):
            for x in range(cols):
                c = grid[y][x]
                if c == 0:
                    activation = (self.weight_excited * en[y][x]
                                  + self.weight_refractory * rn[y][x])
                    if activation >= self.threshold:
                        out[y][x] = 1
                elif 1 <= c < N - 1:
                    out[y][x] = min(N - 1, c + 1)
                else:  # c == N-1 → 恢复
                    out[y][x] = 0
        return out


# ------------------------------------------------------------------ 自动机 --

class CellularAutomaton:
    """管理网格状态并按规则演化。

    支持二态与多态 CA；连续规则（Gray-Scott/Lenia/SmoothLife）自持
    浮点场，网格输出是 uint8 可视化。

    属性：
        grid:     二维列表（行主序），元胞状态 0..num_states-1
        age:      同形二维列表，当前状态的持续代数
        trail:    同形二维列表，死亡后的帧数（0=存活或早已死亡）
        rule:     当前 CARule
        rows/cols: 网格高/宽
        num_states: 当前规则的态数
    """

    def __init__(self, rows, cols, rule_name='game_of_life',
                 initial_density=0.25, trail_length=12, seed=0):
        self.rows = rows
        self.cols = cols
        self.trail_length = trail_length
        self.seed = seed
        self._rng = random.Random(seed)

        rule = RULES.get(rule_name) or RULES['game_of_life']
        # ⚠ 注册表里的规则实例是**模块级共享**的 —— 直接拿来用会让两个
        # Automaton 互相覆盖 set_rng、共享同一条随机流（同 seed 演化
        # 也会分叉，2026-10-19 实测踩中）。每个 Automaton 必须持**深拷贝**，
        # 再注入自己的 rng。
        rule = copy.deepcopy(rule)
        rule.set_rng(self._rng)
        self.rule = rule
        self.num_states = rule.num_states

        self.grid = _zeros(rows, cols)
        self.age = _zeros(rows, cols)
        self.trail = _zeros(rows, cols)
        self.randomize(initial_density)

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------
    def randomize(self, density=0.25):
        """随机填充网格（按规则类型分派初始化策略）。"""
        rule = self.rule
        rng = self._rng

        # 连续规则：重置内部状态并走一步
        if getattr(rule, 'has_float_state', False):
            if hasattr(rule, 'reset'):
                rule.reset((self.rows, self.cols))
            self.grid = rule.step(self.grid)
            self.age = _zeros(self.rows, self.cols)
            self.trail = _zeros(self.rows, self.cols)
            return

        # 1D 基本 CA：单细胞居中
        if rule.name == 'elementary_1d' or rule.name.startswith('wolfram_'):
            if hasattr(rule, 'init_grid'):
                self.grid = rule.init_grid(self.rows, self.cols)
            else:
                self.grid = [[1 if rng.random() < density else 0
                              for _ in range(self.cols)]
                             for _ in range(self.rows)]
            self.age = _zeros(self.rows, self.cols)
            self.trail = _zeros(self.rows, self.cols)
            return

        if self.num_states == 2:
            self.grid = [[1 if rng.random() < density else 0
                          for _ in range(self.cols)]
                         for _ in range(self.rows)]
        else:
            if rule.name in ('wireworld', 'langtons_loop'):
                pool = [0, 1, 1, 1, 2]
                self.grid = [[pool[rng.randrange(5)]
                              for _ in range(self.cols)]
                             for _ in range(self.rows)]
            elif rule.name == 'snowflake':
                max_s = self.num_states - 1
                self.grid = [[max_s if rng.randrange(10) == 9 else 0
                              for _ in range(self.cols)]
                             for _ in range(self.rows)]
            elif rule.name == 'bz_reaction':
                self.grid = _zeros(self.rows, self.cols)
                num_seeds = max(3, (self.rows * self.cols) // 500)
                for _ in range(num_seeds):
                    r = rng.randrange(self.rows)
                    c = rng.randrange(self.cols)
                    radius = rng.randrange(3, 10)
                    for dr in range(-radius, radius + 1):
                        for dc in range(-radius, radius + 1):
                            if dr * dr + dc * dc <= radius * radius:
                                rr, cc = (r + dr) % self.rows, (c + dc) % self.cols
                                self.grid[rr][cc] = rng.randrange(1, 3)
            else:
                self.grid = [[rng.randrange(self.num_states)
                              for _ in range(self.cols)]
                             for _ in range(self.rows)]

        self.age = _zeros(self.rows, self.cols)
        self.trail = _zeros(self.rows, self.cols)

    def clear(self):
        """全部归零并重置 age/trail。"""
        self.grid = _zeros(self.rows, self.cols)
        self.age = _zeros(self.rows, self.cols)
        self.trail = _zeros(self.rows, self.cols)

    # ------------------------------------------------------------------
    # 演化
    # ------------------------------------------------------------------
    def step(self):
        """推进一代。"""
        is_continuous = getattr(self.rule, 'has_float_state', False)
        new_grid = self.rule.step(self.grid)

        if not is_continuous:
            if self.num_states == 2:
                for y in range(self.rows):
                    grow, ngrow = self.grid[y], new_grid[y]
                    trow = self.trail[y]
                    for x in range(self.cols):
                        if grow[x] == 1 and ngrow[x] == 0:
                            trow[x] = self.trail_length
                        elif grow[x] == 0 and ngrow[x] == 0:
                            trow[x] = max(0, trow[x] - 1)
                        else:
                            trow[x] = 0
            for y in range(self.rows):
                grow, ngrow, arow = self.grid[y], new_grid[y], self.age[y]
                for x in range(self.cols):
                    arow[x] = arow[x] + 1 if ngrow[x] == grow[x] else 1

        self.grid = new_grid

    # ------------------------------------------------------------------
    # 元胞操作
    # ------------------------------------------------------------------
    def toggle_cell(self, row, col):
        """切换单元状态（0..num_states-1 循环）。"""
        if 0 <= row < self.rows and 0 <= col < self.cols:
            self.grid[row][col] = (self.grid[row][col] + 1) % self.num_states
            self.age[row][col] = 1

    def set_cell(self, row, col, state=1):
        """设置单元状态。"""
        if 0 <= row < self.rows and 0 <= col < self.cols:
            self.grid[row][col] = state
            self.age[row][col] = 1

    # ------------------------------------------------------------------
    # 规则切换
    # ------------------------------------------------------------------
    def set_rule(self, rule_name):
        """按名切换规则（深拷贝 + 清网格 + 重置连续规则内部态）。"""
        rule = RULES.get(rule_name)
        if rule is not None:
            rule = copy.deepcopy(rule)
            rule.set_rng(self._rng)
            self.rule = rule
            self.num_states = rule.num_states
            self.clear()
            if hasattr(rule, 'reset'):
                rule.reset((self.rows, self.cols))

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    @property
    def alive_count(self):
        """存活元胞数（状态 > 0）。"""
        return sum(1 for row in self.grid for v in row if v > 0)

    def state_count(self, state):
        """指定状态的元胞数。"""
        return sum(1 for row in self.grid for v in row if v == state)


# ------------------------------------------------------------------ 规则管理 --

_SIMPLE_FACTORIES = {
    'brians_brain': BriansBrainRule,
    'wireworld': WireworldRule,
    'langtons_loop': LangtonsLoopRule,
}


class RuleManager:
    """从 ``ca_rules_data.RULES_DATA`` 构建规则实例并维护注册表。"""

    def __init__(self):
        self._rules = {}
        self._descriptions = {}
        self._categories = {}
        self._load_all()

    def _load_all(self):
        for stem, data in sorted(RULES_DATA.items()):
            try:
                self._process(data, stem)
            except (KeyError, TypeError) as e:
                print(f'[RuleManager] Warning: 规则文件 {stem} 加载失败: {e}')

    def _process(self, data, filename):
        category = data.get('category', filename.replace('_rules', ''))
        rule_type = data.get('rule_type')
        rule_family = data.get('rule_family')

        if rule_type == 'outer_totalistic':
            for entry in data.get('rules', []):
                rule = OuterTotalisticRule(entry['name'],
                                           set(entry['born']),
                                           set(entry['survive']))
                self._register(entry['name'], rule,
                               entry.get('description', ''), category)

        elif category == 'multi_state':
            for entry in data.get('rules', []):
                self._build_multi_state(entry, category)

        elif category == 'complex':
            for entry in data.get('rules', []):
                self._build_complex(entry, category)

        elif rule_family == 'gray_scott':
            presets = data.get('presets', {})
            for entry in data.get('rules', []):
                self._build_gray_scott(entry, presets, category)

        elif rule_family == 'lenia':
            species_params = data.get('species_params', {})
            for entry in data.get('rules', []):
                self._build_lenia(entry, species_params, category)

        elif rule_family == 'larger_than_life':
            presets = data.get('presets', {})
            for entry in data.get('rules', []):
                self._build_ltl(entry, presets, category)

        elif rule_family == 'elementary_1d':
            for entry in data.get('rules', []):
                rule = Elementary1DRule(
                    rule_number=entry.get('rule_number', 30))
                rule.name = entry['name']
                self._register(entry['name'], rule,
                               entry.get('description', ''), category)

        elif rule_family == 'other_continuous':
            for entry in data.get('rules', []):
                self._build_other_continuous(entry, category)

    def _register(self, name, rule, description, category):
        self._rules[name] = rule
        self._descriptions[name] = description
        self._categories[name] = category

    def _build_multi_state(self, entry, category):
        name = entry['name']
        rule_type = entry.get('rule_type', name)
        params = entry.get('params', {})
        factory = _SIMPLE_FACTORIES.get(rule_type)
        if rule_type == 'forest_fire':
            rule = ForestFireRule(**params)
            rule.name = name
        elif rule_type == 'crystal_growth':
            rule = CrystalGrowthRule(**params)
            rule.name = name
        elif rule_type == 'sandpile':
            rule = SandpileRule(**params)
            rule.name = name
        elif factory is not None:
            rule = factory()
            rule.name = name
        else:
            print(f'[RuleManager] 未知 multi_state 规则类型: {rule_type}')
            return
        self._register(name, rule, entry.get('description', ''), category)

    def _build_complex(self, entry, category):
        name = entry['name']
        rule_type = entry.get('rule_type', name)
        params = entry.get('params', {})
        if rule_type == 'langtons_loop':
            rule = LangtonsLoopRule()
            rule.name = name
        elif rule_type == 'snowflake':
            rule = SnowflakeRule(**params)
            rule.name = name
        elif rule_type == 'cyclic':
            rule = CyclicCARule(**params)
            rule.name = name
        elif rule_type == 'hodgepodge':
            rule = HodgepodgeRule(**params)
            rule.name = name
        else:
            print(f'[RuleManager] 未知 complex 规则类型: {rule_type}')
            return
        self._register(name, rule, entry.get('description', ''), category)

    def _build_gray_scott(self, entry, presets, category):
        name = entry['name']
        preset_name = entry.get('preset', 'spots')
        preset_params = presets.get(preset_name, {'f': 0.035, 'k': 0.065})
        rule = GrayScottRule(
            preset=preset_name,
            Du=preset_params.get('Du', 0.16),
            Dv=preset_params.get('Dv', 0.08))
        rule.name = name
        rule.f = preset_params.get('f', 0.035)
        rule.k = preset_params.get('k', 0.065)
        self._register(name, rule, entry.get('description', ''), category)

    def _build_lenia(self, entry, species_params, category):
        name = entry['name']
        species = entry.get('species', 'orbium')
        sp = species_params.get(species, {})
        rule = LeniaRule(
            kernel_radius=sp.get('kernel_radius', 13),
            mu=sp.get('mu', 0.15),
            sigma=sp.get('sigma', 0.015),
            dt=sp.get('dt', 0.1),
            species=species)
        rule.name = name
        self._register(name, rule, entry.get('description', ''), category)

    def _build_ltl(self, entry, presets, category):
        name = entry['name']
        preset_name = entry.get('preset', 'bugs')
        ps = presets.get(preset_name, {})
        rule = LargerThanLifeRule(
            radius=ps.get('radius', 5),
            born_min=ps.get('born_min', 34),
            born_max=ps.get('born_max', 45),
            survive_min=ps.get('survive_min', 34),
            survive_max=ps.get('survive_max', 58),
            preset=preset_name)
        rule.name = name
        self._register(name, rule, entry.get('description', ''), category)

    def _build_other_continuous(self, entry, category):
        name = entry['name']
        rule_type = entry.get('rule_type', name)
        params = entry.get('params', {})
        if rule_type == 'smooth_life':
            rule = SmoothLifeRule(**params)
            rule.name = name
        elif rule_type == 'bz_reaction':
            rule = BZReactionRule(**params)
            rule.name = name
        else:
            print(f'[RuleManager] 未知连续规则类型: {rule_type}')
            return
        self._register(name, rule, entry.get('description', ''), category)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def get_rule(self, name):
        return self._rules.get(name)

    def list_rules(self):
        return sorted(self._rules.keys())

    def list_rules_by_category(self):
        result = {}
        for name, cat in self._categories.items():
            result.setdefault(cat, []).append(name)
        for cat in result:
            result[cat].sort()
        return result

    def get_description(self, name):
        return self._descriptions.get(name, '')

    def get_category(self, name):
        return self._categories.get(name, '')

    @property
    def rules(self):
        return self._rules

    @property
    def total_count(self):
        return len(self._rules)

    def reload(self):
        self._rules.clear()
        self._descriptions.clear()
        self._categories.clear()
        self._load_all()


# 全局注册表 —— 全部规则的唯一真相
_manager = RuleManager()
RULES = _manager.rules
total_count = _manager.total_count


def get_rule(name):
    return RULES.get(name)


def list_rules():
    return _manager.list_rules()


def get_description(name):
    return _manager.get_description(name)


def get_category(name):
    return _manager.get_category(name)


def list_rules_by_category():
    return _manager.list_rules_by_category()
