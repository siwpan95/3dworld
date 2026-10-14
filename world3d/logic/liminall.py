# -*- coding: utf-8 -*-
"""**阈廊 / Liminall** —— 后室循环走廊恐怖内核（kkrieger 基座 · 纯数学无 GL）。

> 玩法机制致敬 ``P.T.``(2014, Kojima/del Toro) 的「循环走廊重装修」；
> 题材取 **后室（Backrooms）Level 0** 社区协作设定的公共要素。**不是移植、不是复刻**：
> 名称 / 美术 / 叙事文本 / 音效一律自创；P.T. 与 Backrooms 站内文本不直接使用。
> 设计 note 见 ``docs/remake_ideas.md`` §17；先问结论：三幕混合 / 分阶段威胁 /
> 收音机+涂鸦 / 8 loops 3 谜目标 30 分钟。

## 分层（kkrieger 形态：逻辑纯数学，表现自由）

本模块 = **全部玩法状态机**（loop 状态机 / 8 圈事件表 / 实体 stalk AI / 三谜题 /
非线性传送矩阵 / headless auto-pilot），**零 GL 依赖、零窗口**——与 kkrieger 逻辑层
同范式（表现层 ``presentation/liminall_*`` 自由消费）。

## 8 圈恐怖节奏表（§17.8 约定的"文字稿"——本表即落地形式）

=== ==== ======== ==========================================================
圈  幕   主事件                              微事件（环境压迫）
=== ==== ======== ==========================================================
1   走廊 安全基线：全黄壁纸正常、只有灯嗡      某灯管闪一次
2   走廊 尽头多出一把**面向玩家的椅子**        潮毯水渍扩大；一声耳语
3   走廊 **谜题1**：灯闪序列开镜柜→手电+钥匙    镜中黑影一闪
--  ---- -------- ---------------------------------------------------------
4   迷宫 noclip 坠入后室迷宫（传送矩阵首演）    直线走回原点（出口→标记点）
5   迷宫 一根灯管垂落；**实体远影首现 2s**     身后脚步声（回头无人）
6   迷宫 **谜题2**：3 涂鸦数字拉配电箱→深墙    红壁纸禁入门闪现又消失
--  ---- -------- ---------------------------------------------------------
7   走廊 红灯装修 + 走廊拉伸 ×2 + **假崩溃**    重启后只剩手电光源
8   走廊 **谜题3** 终局电话（三条件）→ 出口    "太平"得反常；实体高频 stalk
=== ==== ======== ==========================================================

强度单调上升（每圈主事件强度 ≥ 前圈）——单调性有单测把关。
"""
from __future__ import annotations

import math
import random
import collections

# ===================================================================== 常量
DEFAULT_SEED = 20261014
TOTAL_LOOPS = 8

# 走廊几何（L 形：A 段沿 -z 长 12，B 段拐角沿 -x 长 8；宽 2.4 高 3.0）
HALL_W, HALL_H = 2.4, 3.0
HALL_A_LEN, HALL_B_LEN = 12.0, 8.0
PLAYER_R = 0.32
PLAYER_SPEED = 2.6                      # P.T. 式慢步（快走 SHIFT ×1.6）
SPRINT = 1.6
ZOOM_FOV = 32.0                         # zoom 观察（P.T. 的第二个动作）
BASE_FOV = 75.0

# 迷宫（第二幕）：GRID×GRID 房间格，每格 CELL 米
MAZE_GRID, MAZE_CELL = 7, 4.0
MAZE_OUT = 3                            # 涂鸦/目标数（谜题 2 素材数）

# 实体（幻觉：后室设定"你看到的东西不是人"）
ENTITY_SPEED = 1.35
ENTITY_CATCH = 1.25                     # 贴身距离 → jump scare
ENTITY_FOV_COS = math.cos(math.radians(65.0))   # 玩家对视半角
STALK_LOOPS = (6, 7)                    # 0 基：第 7/8 圈实体 stalk（L7/L8）


# ===================================================================== 8 圈事件表
# 每项 = 一圈的"装修变体参数集 + 主事件 + 微事件 + 广播段"——表现层按此换装，
# 逻辑层按 main/radio 驱动状态机。强度必须随圈号单调上升（单测把关）。
LOOP_EVENTS = (
    dict(title='一 · 认路', fog=(0.02, 0.02, 0.018), radio=0,
         props=dict(chair=False, droplamp=False, graffiti=False, red=0.0,
                    stretch=1.0, flicker=0.2, handlight=False),
         main='baseline', micro=('flicker',)),
    dict(title='二 · 椅子', fog=(0.02, 0.02, 0.018), radio=1,
         props=dict(chair=True, droplamp=False, graffiti=True, red=0.0,
                    stretch=1.0, flicker=0.35, handlight=False),
         main='radio', micro=('stain', 'whisper')),
    dict(title='三 · 灯语', fog=(0.018, 0.018, 0.016), radio=2,
         props=dict(chair=True, droplamp=False, graffiti=True, red=0.0,
                    stretch=1.0, flicker=0.5, handlight=False),
         main='puzzle1', micro=('mirror',)),
    dict(title='四 · 坠入', fog=(0.016, 0.016, 0.014), radio=3,
         props=dict(maze=True), main='noclip_in', micro=('loopback',)),
    dict(title='五 · 远影', fog=(0.014, 0.013, 0.012), radio=4,
         props=dict(maze=True, droplamp=True), main='silhouette',
         micro=('steps',)),
    dict(title='六 · 深墙', fog=(0.012, 0.011, 0.010), radio=5,
         props=dict(maze=True, red=0.25), main='puzzle2', micro=('reddoor',)),
    dict(title='七 · 红潮', fog=(0.10, 0.012, 0.010), radio=6,
         props=dict(chair=True, graffiti=True, red=1.0, stretch=2.0,
                    flicker=0.8, handlight=True),
         main='crash', micro=('entity',)),
    dict(title='八 · 电话', fog=(0.02, 0.02, 0.018), radio=7,
         props=dict(chair=False, graffiti=True, red=0.0, stretch=1.0,
                    flicker=0.3, handlight=True),
         main='puzzle3', micro=('calm', 'entity')),
)
RADIO_LINES = (
    '…如果 Util【信号丢失】…… Areas 区域…… 黄色…… 请不要 —',
    '…第 3 名失踪者：男性，34 岁，最后出现在停车场 B 层。他数过走廊的灯…',
    '…重复：不要进入红色壁纸的房间。不要。重复。不要…',
    '…这里有六亿平方英里的空房间。你听到的嗡鸣不是电流。它在呼吸…',
    '…如果它开始移动，不要与它对视超过一秒。它只在你看别处时走…',
    '…找到比别的墙更暗的那一面。穿过它。不要回头。不要回头。不要回…',
    '…【信号完全丢失】…（只有灯的嗡鸣，比刚才响了一倍）…',
    '…你已经被选中了。放下电话。走向门。它为你开着…',
)


def maze_layout(seed, grid=MAZE_GRID):
    """确定性后室迷宫：随机分段内墙 + BFS 可达性保证。

    返回 ``(walls, open_cells, start, far_cells)``：
    ``walls`` 是内墙段集合（格间墙：((i,j), ('N'|'W'))，表示格 (i,j) 的北/西侧有墙）；
    ``far_cells`` = 从 start 出发 BFS 最远的 ``MAZE_OUT`` 个格（谜题 2 素材位）。
    """
    rng = random.Random(f'{seed}/maze')
    walls = set()
    for i in range(grid):
        for j in range(grid):
            if j > 0 and rng.random() < 0.42:
                walls.add(((i, j), 'W'))            # (i,j) 与 (i,j-1) 之间有墙
            if i > 0 and rng.random() < 0.42:
                walls.add(((i, j), 'N'))            # (i,j) 与 (i-1,j) 之间有墙
    start = (grid // 2, grid // 2)
    # BFS 可达性（无墙即相邻可通）
    reach = {start}
    dq = collections.deque([start])
    while dq:
        cur = dq.popleft()
        for nxt in _maze_neighbors(cur, walls, grid):
            if nxt not in reach:
                reach.add(nxt)
                dq.append(nxt)
    if len(reach) < grid * grid // 2:               # 太碎：减墙重来（有界重试）
        return maze_layout(seed + 1, grid)
    far = sorted(reach, key=lambda c: -_maze_dist(start, c))[:MAZE_OUT]
    return walls, sorted(reach), start, far


def _maze_dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _maze_neighbors(cell, walls, grid):
    i, j = cell
    for di, dj, side, back in ((-1, 0, 'N', 'N'), (1, 0, 'S', 'N'),
                               (0, -1, 'W', 'W'), (0, 1, 'E', 'W')):
        ni, nj = i + di, j + dj
        if not (0 <= ni < grid and 0 <= nj < grid):
            continue
        if side in ('N', 'W') and ((i, j), side) in walls:
            continue
        if side in ('S', 'E') and ((ni, nj), back) in walls:
            continue
        yield (ni, nj)


def _maze_path(start, goal, walls, grid):
    """迷宫 BFS 最短路（格序列，含首尾）——auto-pilot 与验收共用。"""
    if start == goal:
        return [start]
    prev = {start: None}
    dq = collections.deque([start])
    while dq:
        cur = dq.popleft()
        for nxt in _maze_neighbors(cur, walls, grid):
            if nxt in prev:
                continue
            prev[nxt] = cur
            if nxt == goal:
                path = [nxt]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                path.reverse()
                return path
            dq.append(nxt)
    return []


# ===================================================================== 内核
class LiminallKernel:
    """阈廊内核：loop 状态机 + 实体 stalk + 三谜题（纯 Python，无 GL）。

    表现层（``presentation/liminall_*``）只读本模块状态与 ``variant``（当前圈
    装修变体）并调用 ``move`` / ``turn`` / ``interact`` / ``zoom``；
    headless 验收走 ``auto_step``（与 demo ``--flag auto`` 同一逻辑）。
    """

    def __init__(self, seed=DEFAULT_SEED):
        self.seed = int(seed)
        self.time = 0.0
        self.loop = 0                     # 0 基当前圈
        self.phase = 'hall'               # hall / fall / maze / ended
        self.won = False
        self.deaths = 0
        self.events = []                  # 语义事件：radio/jumpscare/unlock/door/fall/...
        self.player = dict(x=0.0, z=HALL_A_LEN - 1.2, yaw=math.pi, zoom=False)
        self.flags = {}                   # 事件/谜题旗标（每圈部分复位）
        self.handlight = False
        # 谜题 1：灯闪序列（4 段，每段次数）——镜柜密码
        self._flash_seq = [4, 1, 3, 2]
        self._flash_seen = []             # 玩家"读到"的次数（interact 灯具累积）
        self.p1_open = False
        # 谜题 2：涂鸦数字 1/3/4 → 配电箱按序拉杆
        self._switch_seq = [1, 3, 4]
        self._switches = []               # 已按下的（错误序即重置）
        self.p2_done = False
        self.maze_walls = None
        self.maze_start = None
        self.maze_far = None
        self.maze_marks = []              # 已找到的涂鸦格
        self.maze_cell = None
        # 谜题 3：三条件（顺序无关达成，全齐 → 电话响 → answer → won）
        self.p3 = dict(walked=False, stared=0.0, looped=False)
        self.p3_ring = False
        # 实体（幻觉）
        self.entity = dict(active=False, x=0.0, z=0.0, frozen=False)
        self._entity_home = (0.0, 0.0)
        # auto-pilot
        self._auto = None
        self.maze_init()

    # -------------------------------------------------- 圈与变体 --
    @property
    def variant(self):
        return LOOP_EVENTS[min(self.loop, TOTAL_LOOPS - 1)]

    @property
    def variant_title(self):
        return self.variant['title']

    def _emit(self, name):
        self.events.append(name)

    def drain_events(self):
        evs = self.events
        self.events = []
        return evs

    def respawn(self):
        """回本圈起点（实体抓杀 / noclip 落地共用）。"""
        if self.phase == 'maze':
            i, j = self.maze_start
            self.player['x'] = (j + 0.5) * MAZE_CELL - self._maze_half()
            self.player['z'] = (i + 0.5) * MAZE_CELL - self._maze_half()
        else:
            self.player['x'] = 0.0
            self.player['z'] = HALL_A_LEN - 1.2
            self.player['yaw'] = math.pi

    def _maze_half(self):
        return MAZE_GRID * MAZE_CELL / 2.0

    def maze_init(self):
        self.maze_walls, _open, self.maze_start, self.maze_far = \
            maze_layout(self.seed)
        self.maze_cell = self.maze_start

    # -------------------------------------------------- 操作（表现层驱动）
    def move(self, dx, dz, dt, sprint=False):
        """第一人称移动（世界系方向由表现层按 yaw 旋转后传入）。"""
        if self.won:
            return
        d = math.hypot(dx, dz)
        if d < 1e-6:
            return
        sp = PLAYER_SPEED * (SPRINT if sprint else 1.0)
        p = self.player
        nx, nz = p['x'] + dx / d * sp * dt, p['z'] + dz / d * sp * dt
        if self.phase == 'maze':
            self._maze_move(nx, nz)
        else:
            self._hall_move(nx, nz)
        # 谜题 3 条件一：L8 累计走满 10m（respawn 不清零——宽容制）
        if self.loop == 7 and not self.p3_ring:
            self.p3_walked = getattr(self, 'p3_walked', 0.0) + d
            if self.p3_walked >= 10.0:
                self.puzzle3_event('walked10')

    def _hall_move(self, nx, nz):
        """走廊碰撞：L 形 AABB 并集 + **分轴滑动**（整体判定会把玩家卡死在
        A/B 交界的缝隙里——实测 600s 假死的成因）。"""
        hw = HALL_W / 2 - PLAYER_R

        def ok(x, z):
            in_a = (-hw <= x <= hw and -0.2 <= z <= HALL_A_LEN)
            in_b = (-HALL_B_LEN <= x <= 0.2 and -hw <= z <= hw)
            in_room = (-hw <= x <= hw and HALL_A_LEN - 1.0 <= z <= HALL_A_LEN + 1.0)
            return in_a or in_b or in_room

        px, pz = self.player['x'], self.player['z']
        if ok(nx, pz):                               # x 轴先行（旧 z）
            px = nx
        if ok(px, nz):                               # z 轴后行（新 x）
            pz = nz
        self.player['x'], self.player['z'] = px, pz

    def _maze_move(self, nx, nz):
        """迷宫碰撞：分轴移动，跨格需无墙（传送例外）。"""
        half = self._maze_half()
        nx = min(half - 0.3, max(-half + 0.3, nx))
        nz = min(half - 0.3, max(-half + 0.3, nz))
        px, pz = self.player['x'], self.player['z']
        cur = self._cell_of(px, pz)
        cx = self._cell_of(nx, pz)                       # x 轴先行
        if cx == cur or self._maze_pass(cur, cx):
            px = nx
        cur2 = self._cell_of(px, pz)
        cz = self._cell_of(px, nz)                       # z 轴后行
        if cz == cur2 or self._maze_pass(cur2, cz):
            pz = nz
        self.player['x'], self.player['z'] = px, pz
        self.maze_cell = self._cell_of(px, pz)

    def _cell_of(self, x, z):
        half = self._maze_half()
        j = int((x + half) // MAZE_CELL)
        i = int((z + half) // MAZE_CELL)
        return (min(max(i, 0), MAZE_GRID - 1), min(max(j, 0), MAZE_GRID - 1))

    def _maze_pass(self, a, b):
        """相邻格间是否无墙（含传送边界检查）。"""
        if a == b:
            return True
        di, dj = b[0] - a[0], b[1] - a[1]
        if abs(di) + abs(dj) != 1:
            return True                              # 跨格跳跃（传送）放行
        if dj == 1:                                  # 向东
            return ((b), 'W') not in self.maze_walls
        if dj == -1:                                 # 向西
            return ((a), 'W') not in self.maze_walls
        if di == 1:                                  # 向南（i+）
            return ((b), 'N') not in self.maze_walls
        return ((a), 'N') not in self.maze_walls      # 向北（i-）

    def turn(self, d_yaw):
        if not self.won:
            self.player['yaw'] += d_yaw

    def zoom(self, on):
        self.player['zoom'] = bool(on)

    # -------------------------------------------------- 交互（hotspot API）
    def interact(self, what):
        """与热点交互（demo 光标拾取 / headless 脚本共用同一入口）。

        返回动作结果字符串（表现层提示用）；无匹配返回 None。
        """
        if self.won:
            return None
        v = self.variant
        if what == 'start_door' and self.phase == 'hall':
            # 走到 B 段尽头的门 = 完成一圈
            self.complete_loop()
            return 'loop'
        if what == 'radio':
            rid = v.get('radio')
            if rid is not None:
                self._emit('radio')
                return RADIO_LINES[min(rid, len(RADIO_LINES) - 1)]
            return None
        if what == 'lamp':
            # 谜题 1：读灯闪序列（表现层播闪烁，逻辑记次数）
            if self.loop == 2 and not self.p1_open:
                self._flash_seen = list(self._flash_seq)
                self._emit('flash')
                return 'flash'
            return None
        if what == 'mirror_cab' and self.loop == 2:
            if not self.p1_open:
                return 'locked'
            return 'open'
        if what == 'cabinet_keypad' and self.loop == 2:
            if self.p1_open:
                return 'open'
            if self._flash_seen == self._flash_seq:
                self.p1_open = True
                self.handlight = True
                self._emit('unlock')
                return 'p1_solved'
            return 'locked'
        if what == 'graffiti' and self.phase == 'maze':
            cell = self.maze_cell
            if cell in self.maze_far and cell not in self.maze_marks:
                self.maze_marks.append(cell)
                self._emit('mark')
                return f'mark {len(self.maze_marks)}/{MAZE_OUT}'
            return None
        if what.startswith('breaker') and self.phase == 'maze':
            # 配电箱有 4 根杆（编号 1-4），按涂鸦数字序拉（拉错全重置）
            idx = int(what.split(':')[1]) if ':' in what else 0
            return self.puzzle2_switch(idx)
        if what == 'deep_wall' and self.phase == 'maze' and self.p2_done:
            self._emit('fall')
            self.phase = 'hall'
            self.loop = 6
            self.respawn()
            self._sync_entity()
            return 'return'
        if what == 'phone' and self.loop == 7:
            if self.p3_ring:
                self._emit('answer')
                self.won = True
                self.phase = 'ended'
                self._emit('won')
                return 'won'
            return 'silent'
        return None

    # -------------------------------------------------- 谜题 2 / 3 --
    def puzzle2_switch(self, idx):
        """配电箱拉杆（按涂鸦数字序 1→3→4；拉错全重置）。"""
        if self.p2_done or self.phase != 'maze':
            return 'done'
        want = self._switch_seq[len(self._switches)]
        if idx == want:
            self._switches.append(idx)
            self._emit('switch')
            if len(self._switches) == len(self._switch_seq):
                self.p2_done = True
                self._emit('unlock')
                return 'p2_solved'
            return 'next'
        self._switches = []
        self._emit('reset')
        return 'reset'

    def puzzle3_event(self, kind):
        """终局电话三条件（达成即置位；全齐 → 电话响）。"""
        if self.loop != 7 or self.p3_ring:
            return None
        if kind == 'walked10':
            self.p3['walked'] = True
        elif kind == 'stared':
            self.p3['stared'] = 5.0
        elif kind == 'looped':
            self.p3['looped'] = True
        if all(self.p3.values()) and not self.p3_ring:
            self.p3_ring = True
            self._emit('ring')
            return 'ring'
        return None

    # -------------------------------------------------- 圈流转 --
    def complete_loop(self):
        """走完一圈：门关→重开（下一圈装修变体）；特殊圈触发 noclip/事件。"""
        if self.phase != 'hall':
            return
        nxt = self.loop + 1
        if nxt == 3:                                 # L3 结束：坠入迷宫
            self._emit('fall')
            self.phase = 'maze'
            self.loop = nxt
            self.respawn()
            self._sync_entity()
            return
        if nxt == 6:                                 # L6 结束：回走廊终局（红潮）
            self.phase = 'hall'
            self.loop = 6
            self.respawn()
            self._sync_entity()
            self._emit('crash')                      # L7 主事件：假崩溃界面
            self._emit('door')
            return
        if nxt >= TOTAL_LOOPS:                       # L8：电话谜题才是出口
            self.p3['looped'] = True                 # 条件三：绕门一圈不回头
            self.puzzle3_event('looped')
            self._emit('loop')
            return
        self.loop = nxt
        self.respawn()
        self._sync_entity()
        self.flags = {}                              # 圈内一次性旗标复位
        self._emit('loop')
        if self.variant.get('radio') is not None:
            self._emit('radio')

    # -------------------------------------------------- 每帧 --
    def update(self, dt, facing=None, light=False):
        """推进（``facing`` = 玩家视线单位向量；``light`` = 手电是否开着）。

        实体 AI 三态（§17 手电驱退机制）：被手电照到 → **后退**；对视但无光 →
        冻结；背对 → 追击。demo 的 F 键手电是 L7/L8 的核心自保手段。
        auto-pilot 模式下视线/手电由自动驾驶接管（它"读懂了"驱退机制）。
        """
        if self.won:
            return
        if self._auto is not None and self.loop in STALK_LOOPS \
                and self.entity['active']:
            fx, fz = (self.entity['x'] - self.player['x'],
                      self.entity['z'] - self.player['z'])
            d = math.hypot(fx, fz) or 1.0
            facing, light = (fx / d, fz / d), True
        self.time += dt
        # 谜题 3 条件二：L8 站在电话旁**累计**静止 5s（被 scare 后不清——宽容制）
        if self.loop == 7 and not self.p3_ring:
            if self._staring_phone():
                self.p3['stared'] = min(5.0, self.p3['stared'] + dt)
            if self.p3['stared'] >= 5.0:
                self.puzzle3_event('stared')
        # 实体 stalk（仅 stalk 圈）——grace 状态机：
        #   _grace=None 正常态；_jumpscare 置 1.5s 宽限（inactive 防连抓），
        #   归零帧弹回 home 重现（从远处走来的恐怖演出）。
        if self.loop in STALK_LOOPS and self.phase in ('hall', 'maze'):
            g = getattr(self, '_grace', None)
            if g is None:
                if not self.entity['active']:
                    self.entity['active'] = True
                    self.entity['x'], self.entity['z'] = self._entity_home
                self._update_entity(dt, facing, light)
            elif g > 0.0:
                self._grace = g - dt
                self.entity['active'] = False
                if self._grace <= 0.0:
                    self.entity['x'], self.entity['z'] = self._entity_home
                    self.entity['active'] = True
            else:
                self.entity['active'] = True
                self._update_entity(dt, facing, light)

    def _staring_phone(self):
        """玩家是否停在电话附近（L8 电话在 A 段中段墙上）。"""
        px, pz = self.player['x'], self.player['z']
        return math.hypot(px - 0.7, pz - 4.0) < 1.2

    # -------------------------------------------------- 实体 AI --
    def _sync_entity(self):
        """按当前圈决定实体活性（L7/L8 stalk；其余圈收起）。

        home **动态取玩家对侧端点**（玩家在 A 段/起点 → 实体守 B 段深处，反之亦然）
        ——实体永远在"身后半程"，交互瞬发即可通过；home 若固定在拐角会把必经
        之路堵死（实测 144 连抓死锁的成因）。
        """
        if self.loop in STALK_LOOPS:
            self.entity['active'] = True
            px = self.player['x']
            self._entity_home = ((0.0, 0.5) if px < -3.0 else (-6.5, 0.5))
            self.entity['x'], self.entity['z'] = self._entity_home
        else:
            self.entity['active'] = False

    def _update_entity(self, dt, facing, light=False):
        p = self.player
        e = self.entity
        dx, dz = p['x'] - e['x'], p['z'] - e['z']
        d = math.hypot(dx, dz)
        # 对视检测：视线 · (玩家→实体) > cos65° → 玩家正看着它
        seen = False
        if facing is not None and d > 1e-4:
            seen = (facing[0] * (-dx / d) + facing[1] * (-dz / d)) > ENTITY_FOV_COS
        e['frozen'] = seen and not light
        if seen and light:
            # 手电驱退：被照到就后退（后室"光照驱退幻觉"——L7/L8 核心自保手段）
            e['x'] -= dx / d * 1.0 * dt
            e['z'] -= dz / d * 1.0 * dt
        elif not seen:
            e['x'] += dx / d * ENTITY_SPEED * dt
            e['z'] += dz / d * ENTITY_SPEED * dt
        # 实体走廊夹紧（光照后退不许推出墙外——墙外会绕过玩家从背后重现）
        e['x'] = min(-HALL_B_LEN + 0.4, max(-0.4, e['x']))
        e['z'] = min(HALL_A_LEN - 0.4, max(-0.4, e['z']))
        if d < ENTITY_CATCH and not (seen and light):
            self._jumpscare()

    def _jumpscare(self):
        self.deaths += 1
        self._emit('jumpscare')
        self.respawn()
        self._sync_entity()
        self.entity['active'] = False                # 抓杀后 1.5s 宽限（防连抓死循环）
        self._grace = 1.5

    # -------------------------------------------------- auto-pilot（验收）
    def auto_step(self, dt):
        """headless 闪避→走位→交互脚本：8 loops 全解 3 谜 → won（§17.7）。"""
        if self.won:
            return
        if self._auto is None:
            self._auto = _AutoPilot(self)
        self._auto.step(dt)


class _AutoPilot:
    """headless 驱动器：按圈执行脚本（走位/交互/等待）。

    与 demo ``--flag auto`` 共用同一逻辑——「验收怎么玩的 = demo 怎么玩的」。
    走位用简单的"朝目标走 + 到达判定"；交互直接调 ``kernel.interact``。
    """

    def __init__(self, kernel):
        self.k = kernel
        self.queue = collections.deque()
        self.wait = 0.0

    def _plan(self):
        """按当前圈生成动作队列：[('move', x, z) | ('interact', what) | ('wait', s)]。"""
        k = self.k
        q = collections.deque()
        if k.phase == 'hall':
            if k.loop == 2:
                # 谜题 1 必须在 L3 圈内解（灯语 → 镜柜），start_door 会推圈
                q.append(('interact', 'lamp'))
                q.append(('interact', 'cabinet_keypad'))
            # 走到 B 段尽头门 → 完成圈（L 形走廊跨段必经拐角 (0,0)）
            for wp in self._hall_waypoints(-HALL_B_LEN + 0.7, 0.0):
                q.append(('move',) + wp)
            q.append(('interact', 'start_door'))
            if k.loop == 7:
                # 终局电话：绕门一圈（条件三）→ 走满 10m（条件一随移动累计）
                # → **hold** 电话旁 7s（条件二；被实体 scare 传走后自动重新回位，
                #   stare 由内核累计不清零——宽容制）→ 铃响接听
                for wp in self._hall_waypoints(0.7, 4.0):
                    q.append(('move',) + wp)
                q.append(('hold', 0.7, 4.0, 7.0))
                q.append(('interact', 'phone'))
        elif k.phase == 'maze':
            # 依次走涂鸦格 → 配电箱格 → 深墙格（BFS 路径）
            stops = list(k.maze_far) + [k.maze_far[0]]
            cur = k.maze_start
            for cell in stops:
                for hop in _maze_path(cur, cell, k.maze_walls, MAZE_GRID)[1:]:
                    x = (hop[1] + 0.5) * MAZE_CELL - k._maze_half()
                    z = (hop[0] + 0.5) * MAZE_CELL - k._maze_half()
                    q.append(('move', x, z))
                q.append(('wait', 0.3))
                q.append(('interact', 'graffiti'))
                cur = cell
            for num in k._switch_seq:                # 读懂涂鸦的玩家按 1→3→4 拉
                q.append(('interact', f'breaker:{num}'))
            q.append(('wait', 0.5))
            q.append(('interact', 'deep_wall'))
        return q

    def _hall_waypoints(self, tx, tz):
        """走廊 L 形走位：A 段（|x|≤1）与 B 段（x≤-1）之间必经拐角 (0,0)。"""
        px, pz = self.k.player['x'], self.k.player['z']
        cur_a = abs(px) <= 1.0
        tgt_a = abs(tx) <= 1.0
        pts = []
        if cur_a != tgt_a:
            pts.append((0.0, 0.0))
        pts.append((tx, tz))
        return pts

    def step(self, dt):
        k = self.k
        if self.wait > 0.0:
            self.wait -= dt
            return
        if not self.queue:
            self.queue = self._plan()
            if not self.queue:
                return
        act = self.queue[0]
        if act[0] == 'wait':
            self.wait = act[1]
            self.queue.popleft()
            return
        if act[0] == 'hold':
            # 驻留目标点 dur 秒（被 respawn 传走会自动重新走向目标）：
            # stare/引信类"需要持续在场"的条件用这个，而不是一次性 wait。
            _, tx, tz, dur = act
            p = k.player
            if math.hypot(p['x'] - tx, p['z'] - tz) > 0.9:
                dx, dz = tx - p['x'], tz - p['z']
                k.move(dx, dz, dt)
                self._hold_t = 0.0
                return
            if getattr(self, '_hold_id', None) != id(act):
                self._hold_id = id(act)
                self._hold_t = 0.0
            self._hold_t += dt
            if self._hold_t >= dur:
                self._hold_id = None
                self.queue.popleft()
            return
        if act[0] == 'interact':
            self.queue.popleft()
            k.interact(act[1])
            return
        # move：朝目标走（世界系）
        _, tx, tz = act
        p = k.player
        dx, dz = tx - p['x'], tz - p['z']
        d = math.hypot(dx, dz)
        if d < 0.25:
            self.queue.popleft()
            return
        k.move(dx, dz, dt)
