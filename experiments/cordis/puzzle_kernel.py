# -*- coding: utf-8 -*-
"""P1-④ 方块解谜「归仓 / Boxward」内核 —— 推箱规则 + 关卡数据 + 求解/生成（Cordis 服务化）。

设计 note 见 ``docs/remake_ideas.md`` §11；先问结论（全哥 2026-10-10 定）：

- 谜题：推箱入仓（机制致敬 Sokoban 1982，**不碰原 IP 名称与素材**）+ **关卡编辑器**
- 渲染：光栅贪心合批（``SdfVoxelCore.world_mesh`` 的留白由 ``engine/greedy_mesh`` 补齐）
- 切片：最小可玩 + **程序化关卡**（生成器 + 求解器验证 + 种子复现）

与 fps/td/godhand 内核同一范式：

- ``GridService``（服务名 ``grid``）：关卡体素 + JSON schema + **BFS 求解器** + 生成器
  + ``world_mesh``（贪心网格化，实现 SdfVoxelCore 子集）。
- ``PuzzleKernel``（服务名 ``puzzle``）：step/undo/reset/auto_play、步数与推数、
  语义事件（move/push/goal/blocked/undo/solved）、编辑器摆格、GL 句柄记账。

**单一事实来源 = 关卡 JSON**：编辑器导出的 = 生成器产出的 = 内核吃的（§11.5）。
**auto-pilot = 求解器回放**：headless 验收、demo ``--flag auto``、生成器验证同源。

全部逻辑纯 Python、无 GL 依赖，headless 可跑；GL 资源经 ``RenderService`` 记账并用
``ctx.effect`` 绑内核 fiber（dispose 即回滚）。挂载统一走 ``tree_boot.mount_tree``。
"""
from __future__ import annotations

import collections
import json
import math
import os
import random
import sys
from collections import namedtuple

# ---- cordis 定位（与 td/godhand/fps 三个内核一致：vendored 源码可经 CORDIS_SRC 暴露）----
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import _cordis_locate  # noqa: E402

_cordis_locate.ensure()

from cordis import Service  # noqa: E402
from engine import greedy_mesh  # noqa: E402
from experiments.cordis.tower_defense_kernel import _unit_cube  # noqa: E402

# ===================================================================== 平衡常量
DEFAULT_SEED = 20261010
GRID_DEFAULT = 13            # 生成关边长（含外墙圈）
BOX_DEFAULT = 4              # 生成关箱子数
GEN_WALL_DENSITY = 0.16      # 内部撒墙密度
MIN_PUSHES = 6               # 生成关最少推箱次数（防"一步到位"的平凡关）
MAX_GEN_TRIES = 64           # 单种子最多重掷次数（仍败则降密度重试）
MAX_SOLVE_STATES = 200000    # 求解器状态上限（超限视为"超出能力"→ None）
AUTO_INTERVAL = 0.09         # auto-play 回放步间隔（秒，录制观感用）
HISTORY_MAX = 512            # undo 栈深
DEMO_SEEDS = (20261010, 11, 22, 33, 42, 101, 202, 777, 2024, 12345)

CS = 1.0                     # 格距（世界单位）
WALL_TOP = 1.0               # 墙高（单层）

# 移动方向（格坐标：di = 行/z，dj = 列/x）
DIRS = {'U': (-1, 0), 'D': (1, 0), 'L': (0, -1), 'R': (0, 1)}

Solution = namedtuple('Solution', 'moves pushes')


def cam_move_dirs(rot):
    """相机旋转档位（0..3，对应 yaw = 90°·rot）→ WASD 各键的格方向 (di, dj)。

    纯函数（demo 与单测共用）：相机朝向的 xz 分量为 (-cos a, -sin a)，
    W = 前方，D = 右方（right = (-fz, fx)，与 rayhold 的约定一致）。
    """
    a = math.radians(90.0 * rot)
    fx, fz = -math.cos(a), -math.sin(a)

    def g(dxx, dzz):
        return (int(round(dzz)), int(round(dxx)))

    return {'W': g(fx, fz), 'S': g(-fx, -fz),
            'D': g(-fz, fx), 'A': g(fz, -fx)}


# ===================================================================== 关卡数据
def parse_level(art, name=''):
    """ASCII 关卡图 → 关卡 dict（单一事实来源的**作者侧**语法糖）。

    ``#`` 墙 / 空格 地板 / ``B`` 箱 / ``G`` 目标 / ``P`` 玩家 / ``*`` 箱在目标上。
    """
    lines = [ln.rstrip() for ln in art.strip('\n').splitlines() if ln.strip()]
    h = len(lines)
    w = max(len(ln) for ln in lines)
    walls, boxes, goals = set(), set(), set()
    player = None
    for i, ln in enumerate(lines):
        for j, ch in enumerate(ln):
            if ch == '#':
                walls.add((i, j))
            elif ch == 'B':
                boxes.add((i, j))
            elif ch == 'G':
                goals.add((i, j))
            elif ch == 'P':
                player = (i, j)
            elif ch == '*':
                boxes.add((i, j))
                goals.add((i, j))
    if player is None:
        raise ValueError(f'关卡 {name} 缺少玩家起点 P')
    if len(boxes) != len(goals) or not boxes:
        raise ValueError(f'关卡 {name} 箱数({len(boxes)})与目标数({len(goals)})不一致或为空')
    return dict(size=[h, w], walls=sorted(walls), boxes=sorted(boxes),
                goals=sorted(goals), player=list(player), meta={'name': name})


HANDMADE_LEVELS = (
    parse_level("""
#######
#P B G#
#     #
#     #
#######
""", '直推'),
    parse_level("""
########
#      #
#   B  #
# P    #
#  ##  #
#  G   #
#      #
########
""", '转向'),
    parse_level("""
#########
#       #
# PB  G #
#       #
#  B  G #
# P     #
#########
""", '双箱排队'),
)


# ===================================================================== 共同核
class GridService(Service):
    """关卡网格共同核（服务名 ``grid``）。

    关卡 = 单层体素（墙在 y∈[0,1]），因此实现 ``SdfVoxelCore`` 的三条：
    ``block_at`` / ``voxel_get`` / **``world_mesh``（贪心网格化——P1-③ 留下的留白）**。
    另承载关卡数据（JSON schema 单一事实来源）、BFS 求解器与程序化关卡生成器。
    """

    def __init__(self, ctx, config):
        super().__init__(ctx, "grid")
        cfg = config or {}
        self.seed = int(cfg.get('seed', DEFAULT_SEED))
        self.n = int(cfg.get('cells', GRID_DEFAULT))
        self.box_total = int(cfg.get('boxes', BOX_DEFAULT))
        self.cs = CS
        self.walls: set = set()
        self.boxes: set = set()
        self.goals: set = set()
        self.player = None          # (i, j)；编辑器中允许暂时为 None
        self.meta: dict = {}
        self._level = None          # 当前关卡 dict（重置的依据）
        self.load_level(HANDMADE_LEVELS[0])

    # ------------------------------------------------------ 关卡数据 --
    def load_level(self, data):
        """加载关卡 dict（编辑器/生成器/手工关同 schema）；完成校验。"""
        h, w = data['size']
        walls = set(map(tuple, data['walls']))
        boxes = set(map(tuple, data['boxes']))
        goals = set(map(tuple, data['goals']))
        player = tuple(data['player']) if data.get('player') else None
        for c in boxes | goals | ({player} if player else set()):
            if c in walls or not (0 <= c[0] < h and 0 <= c[1] < w):
                raise ValueError(f"关卡非法：{c} 落在墙里或界外")
        if player is not None and len(boxes) == len(goals):
            pass                        # 编辑中间态允许箱/目标数不齐
        self.walls, self.boxes, self.goals, self.player = walls, boxes, goals, player
        self.h, self.w = int(h), int(w)
        self.meta = dict(data.get('meta') or {})
        self._level = self.to_dict()
        return self

    def to_dict(self, name=None):
        """当前实时状态 → 关卡 dict（编辑器导出走这里）。"""
        meta = dict(self.meta)
        if name:
            meta['name'] = name
        return dict(size=[self.h, self.w], walls=sorted(self.walls),
                    boxes=sorted(self.boxes), goals=sorted(self.goals),
                    player=list(self.player) if self.player else None, meta=meta)

    @property
    def level(self):
        return self._level

    def reload(self):
        """回到当前关卡的初始状态（箱归位、玩家归位）。"""
        return self.load_level(self._level)

    # ------------------------------------------------------ 查询 --
    def in_bounds(self, i, j):
        return 0 <= i < self.h and 0 <= j < self.w

    def is_wall(self, i, j):
        return (i, j) in self.walls

    def box_at(self, i, j):
        return (i, j) in self.boxes

    def on_goal(self, i, j):
        return (i, j) in self.goals

    @property
    def half_w(self):
        return self.w * self.cs * 0.5

    @property
    def half_h(self):
        return self.h * self.cs * 0.5

    def world_of(self, i, j):
        """格坐标 → 格中心世界 (x, z)。"""
        return ((j + 0.5) * self.cs - self.half_w, (i + 0.5) * self.cs - self.half_h)

    def cell_of(self, x, z):
        """世界 (x, z) → 格坐标 (i, j)（编辑器拾取用）。"""
        j = int(math.floor((x + self.half_w) / self.cs))
        i = int(math.floor((z + self.half_h) / self.cs))
        return (min(max(i, 0), self.h - 1), min(max(j, 0), self.w - 1))

    # 契约对齐（SdfVoxelCore 子集）
    def block_at(self, i, j, k=0):
        return 1 if (k == 0 and (i, j) in self.walls) else 0

    def voxel_get(self, x, y, z):
        return self.block_at(x, z)

    def world_mesh(self):
        """墙体 → 贪心合并网格 (pos, shade)（世界坐标，demo 可直接当 u_model=I 用）。"""
        return greedy_mesh.layer_mesh(self.walls, self.h, self.w,
                                      cs=self.cs, x0=-self.half_w, z0=-self.half_h,
                                      ytop=WALL_TOP)

    # ------------------------------------------------------ 求解器 --
    def _dead_squares(self, walls, goals, h, w):
        """角落死格：两个正交邻边都是墙、又不是目标的格——箱子推进去必卡死。"""
        dead = set()
        for i in range(1, h - 1):
            for j in range(1, w - 1):
                c = (i, j)
                if c in walls or c in goals:
                    continue
                up, dn = (i - 1, j) in walls, (i + 1, j) in walls
                lf, rt = (i, j - 1) in walls, (i, j + 1) in walls
                if (up or dn) and (lf or rt):
                    dead.add(c)
        return dead

    def solve(self, data=None, max_states=MAX_SOLVE_STATES):
        """BFS over (玩家, 箱集) 状态；返回 ``Solution(moves, pushes)`` 或 None。

        ``moves`` 为 (di, dj) 序列；状态含玩家位置 ⇒ 推箱可达性天然被编码；
        死格剪枝把"推进死格"的分支直接砍掉。超 ``max_states`` 视为超出能力。
        """
        data = data if data is not None else self.to_dict()
        h, w = data['size']
        walls = frozenset(map(tuple, data['walls']))
        goals = frozenset(map(tuple, data['goals']))
        if not data.get('player'):
            return None
        start_player = tuple(data['player'])
        start_boxes = frozenset(map(tuple, data['boxes']))
        if start_boxes == goals:
            return Solution((), 0)
        dead = self._dead_squares(walls, goals, h, w)

        start = (start_player, start_boxes)
        prev = {start: None}            # state -> (prev_state, move)
        dq = collections.deque([start])
        found = None
        while dq and found is None:
            cur = dq.popleft()
            (pi, pj), boxes = cur
            for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                np_ = (pi + di, pj + dj)
                if np_ in walls:
                    continue
                if np_ in boxes:
                    nb2 = (np_[0] + di, np_[1] + dj)
                    if nb2 in walls or nb2 in boxes or nb2 in dead:
                        continue
                    nboxes = (boxes - {np_}) | {nb2}
                    if nboxes == goals:
                        nxt, done = (np_, nboxes), True
                    else:
                        nxt, done = (np_, nboxes), False
                else:
                    nxt, done = (np_, boxes), False
                if nxt in prev:
                    continue
                prev[nxt] = (cur, (di, dj))
                if done:
                    found = nxt
                    break
                if len(prev) > max_states:
                    return None
                dq.append(nxt)
        if found is None:
            return None
        moves = []
        cur = found
        while prev[cur] is not None:
            cur, mv = prev[cur]
            moves.append(mv)
        moves.reverse()
        # pushes 计数（轻量回放）
        boxes = set(start_boxes)
        pos = start_player
        pushes = 0
        for di, dj in moves:
            t = (pos[0] + di, pos[1] + dj)
            if t in boxes:
                pushes += 1
                boxes.remove(t)
                boxes.add((t[0] + di, t[1] + dj))
            pos = t
        return Solution(tuple(moves), pushes)

    # ------------------------------------------------------ 生成器 --
    def _player_reach(self, walls, boxes, player, n):
        """玩家在（墙/箱阻挡下）可达的格集合（生成器拉箱时用）。"""
        seen = {player}
        dq = collections.deque([player])
        while dq:
            i, j = dq.popleft()
            for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                c = (i + di, j + dj)
                if (c in seen or c in walls or c in boxes
                        or not (0 <= c[0] < n and 0 <= c[1] < n)):
                    continue
                seen.add(c)
                dq.append(c)
        return seen

    def generate(self, seed=None, n=None, box_total=None,
                 density=GEN_WALL_DENSITY, min_pushes=MIN_PUSHES):
        """按种子生成关卡 dict：**反向拉箱打乱**（经典 Sokoban 生成法）。

        从「已解」状态（箱在目标上）出发，反复做合法**拉箱**（玩家在箱子外侧、
        向远离方向走一步、箱子跟进玩家原格）——每步拉都恰好被一次反向推撤销，
        所以**打乱后的状态必然可解**（可解性由构造保证，而非碰运气）；死格上的
        目标位不摆放、拉箱不入死格。解序列长度由拉数直接控制（≥ min_pushes）。
        """
        if seed is not None:
            self.seed = int(seed)
        n = int(n or self.n)
        nb = int(box_total or self.box_total)
        DIRS4 = ((-1, 0), (1, 0), (0, -1), (0, 1))
        tries = 0
        while True:
            tries += 1
            rng = random.Random(f'{self.seed}/{tries}')
            # 1) 撒墙（外圈必墙 + 内部按密度）
            walls = set()
            for i in range(n):
                for j in range(n):
                    if i in (0, n - 1) or j in (0, n - 1):
                        walls.add((i, j))
                    elif rng.random() < density:
                        walls.add((i, j))
            interior = [(i, j) for i in range(1, n - 1) for j in range(1, n - 1)
                        if (i, j) not in walls]
            if len(interior) < 3 * nb + 4:
                continue
            # 2) 目标位避开死格（角落），箱从目标位出发，玩家单独摆
            dead = self._dead_squares(walls, frozenset(), n, n)
            goal_ok = [c for c in interior if c not in dead]
            if len(goal_ok) < nb + 1:
                continue
            picks = rng.sample(goal_ok, nb + 1)
            goals = frozenset(picks[:nb])
            player = picks[nb]
            boxes = set(goals)
            # 3) 反向拉箱打乱：拉数 = 推数下限（每拉必可逆）
            k_pulls = nb + rng.randrange(4, 11)
            stuck = False
            for _ in range(k_pulls):
                reach = self._player_reach(walls, boxes, player, n)
                cands = []
                for (bi, bj) in boxes:
                    for di, dj in DIRS4:
                        p = (bi + di, bj + dj)            # 玩家站在箱子外侧
                        if p not in reach:
                            continue
                        nd = (bi + 2 * di, bj + 2 * dj)   # 玩家将走到的格
                        if (not (0 <= nd[0] < n and 0 <= nd[1] < n)
                                or nd in walls or nd in boxes or p in dead):
                            continue
                        cands.append(((bi, bj), p, nd))
                if not cands:
                    stuck = True
                    break
                box, p, nd = cands[rng.randrange(len(cands))]
                boxes.discard(box)
                boxes.add(p)                              # 箱子跟进玩家原格
                player = nd
            if stuck or boxes == goals:
                continue
            data = dict(size=[n, n], walls=sorted(walls), boxes=sorted(boxes),
                        goals=sorted(goals), player=list(player),
                        meta={'name': f'gen-{self.seed}', 'seed': self.seed})
            # 4) 求解器复验（par 来源 + 保险丝；构造保证可解，此断言理应恒真）
            sol = self.solve(data)
            if sol is not None and sol.pushes >= min_pushes:
                return data
            if tries >= MAX_GEN_TRIES * 8:
                raise ValueError(f'seed {self.seed}: 生成 {tries} 次仍无可用关卡')

    def __cordis_init__(self):
        return None

    # ------------------------------------------------------ 编辑器 --
    def editor_cycle(self, i, j):
        """编辑器摆格：地板→墙→箱→目标→玩家→地板 循环；返回摆后的内容名。

        外圈墙不可改（关卡必须封闭）；设玩家即移动玩家（并清掉该格箱/目标）。
        """
        if i in (0, self.h - 1) or j in (0, self.w - 1):
            return 'wall'                       # 外圈不可编辑
        if self.player == (i, j):
            self.player = None                  # 先挪走玩家
            return 'floor'
        if (i, j) in self.boxes:
            self.boxes.discard((i, j))
            return 'floor'
        if (i, j) in self.goals:
            self.goals.discard((i, j))
            return 'floor'
        if (i, j) in self.walls:
            self.walls.discard((i, j))
            return 'floor'
        # 空格 → 依次尝试补目标/箱/玩家？——单一循环由 demo 控制：此处默认补墙
        self.walls.add((i, j))
        return 'wall'

    def editor_set(self, i, j, what):
        """直接设置格内容（demo 编辑器的另一入口）：what ∈ wall/box/goal/player/floor。"""
        if i in (0, self.h - 1) or j in (0, self.w - 1):
            return 'wall'
        self.boxes.discard((i, j))
        self.goals.discard((i, j))
        self.walls.discard((i, j))
        if self.player == (i, j):
            self.player = None
        if what == 'wall':
            self.walls.add((i, j))
        elif what == 'box':
            self.boxes.add((i, j))
        elif what == 'goal':
            self.goals.add((i, j))
        elif what == 'player':
            self.player = (i, j)
        return what


# ===================================================================== 内核
class PuzzleKernel(Service):
    """方块解谜内核：声明 inject 只取所需共同核，玩法全在此编排（逻辑在 Cordis）。"""

    inject = ['render', 'entity', 'rules', 'grid']

    def __init__(self, ctx, config):
        super().__init__(ctx, "puzzle")
        cfg = config or {}
        self.seed = int(cfg.get('seed', DEFAULT_SEED))
        self._start_level = int(cfg.get('start_level', 0))
        self.level_name = ''
        self.steps = 0
        self.pushes = 0
        self.par_moves = None             # 求解器给出的最少步数（HUD/验收用）
        self.par_pushes = None
        self._level_i = 0                 # 关卡游标（手工关 → 生成关循环）
        self._history = []                # undo 栈：(player, boxes, steps, pushes)
        self._solution = None             # auto-play 待回放序列
        self._auto_t = 0.0
        self._eids = []
        self._box_eids = {}
        self.events = []
        self._gl_handles = []

    # ---------------------------------------------------------- 启动编排
    def __cordis_init__(self):
        self.ctx.rules.reset()
        self._level_i = min(max(self._start_level, 0), len(HANDMADE_LEVELS) - 1)
        self.load_level(HANDMADE_LEVELS[self._level_i])
        self._acquire_gl()
        return None

    def _acquire_gl(self):
        render = self.ctx.render
        h_prog = render.compile_program('boxward')
        h_cube = render.upload_mesh('boxward_cube', *_unit_cube())
        h_goal = render.upload_mesh('boxward_goal', *_goal_plate())
        self._gl_handles.extend([h_prog, h_cube, h_goal])
        for h in self._gl_handles:
            def release(h=h):
                return lambda: render.release(h)
            self.ctx.effect(release)

    # ---------------------------------------------------------- 关卡编排
    def load_level(self, data):
        """换关（手工/生成/编辑器导出同一入口）：复位规则、计数、实体，并求 par。"""
        grid = self.ctx.grid
        grid.load_level(data)
        self.level_name = (data.get('meta') or {}).get('name', '')
        self._restart_state()
        self._rebuild_entities()
        self.refresh_par()
        return self

    def _restart_state(self):
        self.steps = 0
        self.pushes = 0
        self._history = []
        self._solution = None
        self._auto_t = 0.0
        self.ctx.rules.reset()

    def refresh_par(self):
        """重解当前关（求解器即 par 的来源；不可解则 par 置 None）。"""
        sol = self.ctx.grid.solve()
        if sol is None:
            self.par_moves, self.par_pushes = None, None
        else:
            self.par_moves, self.par_pushes = len(sol.moves), sol.pushes
        return self.par_moves

    def next_level(self):
        """N 键：手工关依次玩完 → 循环生成 DEMO_SEEDS 里的种子关。"""
        self._level_i += 1
        if self._level_i < len(HANDMADE_LEVELS):
            return self.load_level(HANDMADE_LEVELS[self._level_i])
        k = self._level_i - len(HANDMADE_LEVELS)
        return self.load_generated(DEMO_SEEDS[k % len(DEMO_SEEDS)])

    def load_generated(self, seed):
        """按种子生成并加载（生成器已保证可解）。"""
        data = self.ctx.grid.generate(seed)
        return self.load_level(data)

    def restart(self, seed=None):
        """R 键：换一颗种子生成新关（或无参时重玩当前关）。"""
        if seed is not None:
            self.seed = int(seed)
            return self.load_generated(self.seed)
        return self.load_level(self.ctx.grid.to_dict())

    # ---------------------------------------------------------- 实体同步
    def _rebuild_entities(self):
        ent = self.ctx.entity
        grid = self.ctx.grid
        for eid in self._eids:
            ent.remove(eid)
        self._eids, self._box_eids = [], {}
        if grid.player is not None:
            x, z = grid.world_of(*grid.player)
            self._eids.append(ent.spawn('player', (x, 0.0, z)))
        for c in sorted(grid.boxes):
            x, z = grid.world_of(*c)
            self._box_eids[c] = ent.spawn('box', (x, 0.0, z))

    def _move_player_entity(self, cell):
        if self._eids:
            e = self.ctx.entity.get(self._eids[0])
            if e is not None:
                x, z = self.ctx.grid.world_of(*cell)
                e['pos'] = (x, 0.0, z)

    def _move_box_entity(self, old, new):
        eid = self._box_eids.pop(old, None)
        if eid is None:
            return
        e = self.ctx.entity.get(eid)
        if e is not None:
            x, z = self.ctx.grid.world_of(*new)
            e['pos'] = (x, 0.0, z)
        self._box_eids[new] = eid

    # ---------------------------------------------------------- 事件 --
    def _emit(self, name):
        self.events.append(name)

    def drain_events(self):
        evs = self.events
        self.events = []
        return evs

    # ---------------------------------------------------------- 操作 --
    def step(self, d):
        """走一步（方向键或 (di,dj)）。返回 'move'/'push'/None（受阻或已胜）。"""
        r = self.ctx.rules
        if r.state() == 'won':
            return None
        if isinstance(d, str):
            d = DIRS[d]
        di, dj = d
        grid = self.ctx.grid
        if grid.player is None:
            return None
        pi, pj = grid.player
        t = (pi + di, pj + dj)
        if not grid.in_bounds(*t) or grid.is_wall(*t):
            self._emit('blocked')
            return None
        if t in grid.boxes:
            b2 = (t[0] + di, t[1] + dj)
            if not grid.in_bounds(*b2) or grid.is_wall(*b2) or b2 in grid.boxes:
                self._emit('blocked')
                return None
            self._snapshot()
            grid.boxes.discard(t)
            grid.boxes.add(b2)
            grid.player = t
            self.steps += 1
            self.pushes += 1
            self._move_box_entity(t, b2)
            self._move_player_entity(t)
            self._emit('push')
            if b2 in grid.goals:
                self._emit('goal')
            if grid.boxes == grid.goals:
                r.on_event({'type': 'puzzle_solved'})
                self._emit('solved')
            return 'push'
        self._snapshot()
        grid.player = t
        self.steps += 1
        self._move_player_entity(t)
        self._emit('move')
        return 'move'

    def _snapshot(self):
        grid = self.ctx.grid
        self._history.append((grid.player, frozenset(grid.boxes),
                              self.steps, self.pushes))
        if len(self._history) > HISTORY_MAX:
            self._history.pop(0)

    def undo(self):
        """撤销一步（已胜不可撤销——胜利是终局）。"""
        if not self._history or self.ctx.rules.state() == 'won':
            return False
        player, boxes, steps, pushes = self._history.pop()
        grid = self.ctx.grid
        grid.player = player
        grid.boxes = set(boxes)
        self.steps, self.pushes = steps, pushes
        self._rebuild_entities()
        self._emit('undo')
        return True

    def reset(self):
        """重玩当前关。"""
        self.load_level(self.ctx.grid.to_dict())

    # --------------------------------------------- auto-pilot（录制/验收共用）
    def solve_now(self):
        """立即求解当前局面（auto-play 的前置；headless 验收也用它断言可解）。"""
        sol = self.ctx.grid.solve()
        self._solution = list(sol.moves) if sol is not None else []
        self._auto_t = 0.0
        return self._solution

    def auto_step(self, dt, interval=AUTO_INTERVAL):
        """自动驾驶 = 求解器回放：demo ``--flag auto`` 与 headless 验收同一套逻辑。

        每 ``interval`` 秒从解序列里走一步（录制观感）；
        没解过就现解（解不出来 = 该关不可解，静默等待——由验收层断言）。
        """
        r = self.ctx.rules
        if r.state() == 'won':
            return
        if self._solution is None:
            self.solve_now()
        if not self._solution:
            return
        self._auto_t += dt
        while self._auto_t >= interval and self._solution:
            self._auto_t -= interval
            self.step(self._solution.pop(0))

    # ---------------------------------------------------------- 编辑器 --
    def editor_cycle(self, i, j):
        """编辑器摆格（见 GridService.editor_cycle）；同步实体，不动 undo/par。"""
        what = self.ctx.grid.editor_cycle(i, j)
        self._rebuild_entities()
        return what

    def editor_set(self, i, j, what):
        what = self.ctx.grid.editor_set(i, j, what)
        self._rebuild_entities()
        return what

    def editor_validate(self):
        """编辑结果是否构成可解关卡：有玩家、箱=目标>0、求解器可解。"""
        grid = self.ctx.grid
        if grid.player is None or not grid.boxes or len(grid.boxes) != len(grid.goals):
            return None
        return grid.solve()

    # ---------------------------------------------------------- 查询/展示 --
    def is_solved(self):
        return self.ctx.rules.state() == 'won'

    def status_text(self):
        r = self.ctx.rules
        par = f'{self.par_pushes}' if self.par_pushes is not None else '—'
        return (f"state={r.state()} level={self.level_name} "
                f"steps={self.steps} pushes={self.pushes}/par{par} "
                f"boxes={len(self.ctx.grid.boxes)} seed={self.ctx.grid.seed}")

    def update(self, dt):
        """回合制内核无连续模拟；保留 update 以对齐「demo 每帧调内核」的范式。"""
        return None


# ===================================================================== 几何工具
def _goal_plate(s=0.42, y=0.02):
    """目标格标记：贴地薄板（金色染色由 demo 的 u_tint 负责）。"""
    a, b, c, d = (-s, y, -s), (s, y, -s), (s, y, s), (-s, y, s)
    pos, shade = [], []
    for p in (a, b, c, a, c, d):
        pos.extend(p)
        shade.extend((1.0, 1.0, 1.0))
    return pos, shade


def level_to_json(data, path):
    """关卡 dict → JSON 文件（编辑器导出；三端同 schema 的落盘端）。"""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    return path


def level_from_json(path):
    """JSON 文件 → 关卡 dict（demo ``--flag level=...`` 的加载端）。"""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)
