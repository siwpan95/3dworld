# -*- coding: utf-8 -*-
"""P1-③ 射线 FPS「迷堡枪影 / Rayhold」内核 —— 程序化迷宫 + 第一人称玩法（Cordis 服务化）。

设计 note 见 ``docs/remake_ideas.md`` §10；先问结论（全哥 2026-10-09 定）：

- 渲染：SDF 光线步进（全屏片元着色器，见 ``demos/rayhold.py``）
- 地图：**程序化随机迷宫**（种子可复现）
- 武器：hitscan 单武器（射线枪）
- 敌人：完整状态机（巡逻 → 视线发现 → 追击 → 攻击）

与 ``tower_defense_kernel.py`` / ``godhand_kernel.py`` 同一范式：

- ``MazeService``（服务名 ``maze``）：迷宫生成 / 查询 / BFS 寻路 / 距离场（给着色器）。
- ``FpsKernel``（服务名 ``fps``）：玩家、武器 hitscan、敌人状态机、拾取、敌弹、胜负编排、
  **auto-pilot**（供录制与 headless 验收共用同一套逻辑）。

全部逻辑纯 Python、无 GL 依赖，headless 可跑；GL 资源经 ``RenderService`` 记账并
用 ``ctx.effect`` 绑到内核 fiber（dispose 即回滚）。
挂载统一走 ``tree_boot.mount_tree(cordis.fps.yml)``——本模块**不自建第二套接线**。
"""
from __future__ import annotations

import collections
import math
import random

from cordis import Service  # noqa: E402
from core.contracts import EntityCore, EconomyCore, RulesCore  # noqa: F401,E402
from experiments.cordis.tower_defense_kernel import _unit_cube, _unit_sphere  # noqa: E402

# ===================================================================== 平衡常量
CELLS = 15                  # 迷宫格数（奇数：1..n-2 为通道单元）
CELL = 3.0                  # 格距（世界单位）
WALL_H = 2.8                # 墙高（地板 y=0 → 天花板 y=WALL_H）
EYE = 1.55                  # 眼高
BRAID = 0.12                # 死胡同"编织"成环路的比例（减少憋闷）
DEFAULT_SEED = 20261010     # 默认种子（保证录制 / headless 验收可锁定）

PLAYER_R = 0.32             # 玩家碰撞半径
MOVE_SPEED = 4.2
SPRINT = 1.7
START_AMMO = 50             # 打磨轮：60→50（弹药略紧，实机不敢无脑泼弹）
GUN_DMG = 26
GUN_CD = 0.25
GUN_RANGE = 26.0

ENEMY_COUNT = 10            # 打磨轮：8→10（更热闹；着色器槽位 16 足够）
ENEMY_R = 0.5               # 命中判定半径（与着色器里的造型半径同源）
SIGHT = 13.0                # 视线距离
ALERT_T = 0.6               # 发现→追击的反应时间
LOSE_T = 3.0                # 追击中失去目标多久后回巡逻
HURT_T = 0.35               # 受击硬直
DEATH_T = 0.8               # 死亡淡出时长

# 近战型「游荡者」（打磨轮：更快更痛、出手更密——10 种子基准里多数局 HP=100 偏松）
STALKER = dict(hp=60, speed=3.2, dmg=10, atk_cd=1.05, reach=1.3)
# 远程型「哨卫」（打磨轮：光弹伤害 12→14、冷却 1.6→1.4）
SENTRY = dict(hp=45, speed=2.2, dmg=14, atk_cd=1.4, keep_lo=8.0, keep_hi=12.0)
BOLT_SPEED = 9.0
BOLT_R = 0.28
BOLT_HIT_R = 0.7            # 光弹命中玩家的判定半径
BOLT_LIFE = 3.0

PICKUP_R = 0.9              # 拾取判定半径
AMMO_PICKUP = 20            # 打磨轮：24→20（与开局弹药同向收紧）
MEDKIT_HEAL = 25


# ===================================================================== 迷宫共同核
class MazeService(Service):
    """迷宫共同核（服务名 ``maze``）：程序化生成 + 查询 + BFS 寻路 + 距离场。

    迷宫即"单层体素"，因此本轮同时把 ``SdfVoxelCore`` 契约里最容易对齐的两条
    （``block_at`` / ``voxel_get``）落地；``world_mesh`` 留待方块解谜内核归并时补齐。

    生成为**递归回溯**（recursive backtracker）＋ 少量死胡同编织（braid）：
    前者保证"任意两格连通"，后者把纯树状迷宫里大量死胡同连成环，避免走进去
    只能原路折返的憋闷感（§10.3）。种子可复现 → 录制与 headless 验收可锁定。
    """

    def __init__(self, ctx, config):
        super().__init__(ctx, "maze")
        cfg = config or {}
        self.seed = int(cfg.get('seed', DEFAULT_SEED))
        self.n = int(cfg.get('cells', CELLS))
        self.cell = float(cfg.get('cell_size', CELL))
        self.braid = float(cfg.get('braid', BRAID))
        self.half = self.n * self.cell * 0.5
        self.grid = []
        self.start = (1, 1)
        self.exit = (self.n - 2, self.n - 2)
        self.regenerate(self.seed)

    # ---------------------------------------------------------- 生成 --
    def regenerate(self, seed=None):
        """按种子重建迷宫（同种子 → 同迷宫）。"""
        if seed is not None:
            self.seed = int(seed)
        rng = random.Random(self.seed)
        n = self.n
        grid = [[1] * n for _ in range(n)]          # 1 = 墙，0 = 通道
        grid[1][1] = 0
        visited = {(1, 1)}
        stack = [(1, 1)]
        while stack:
            ci, cj = stack[-1]
            cand = []
            for di, dj in ((2, 0), (-2, 0), (0, 2), (0, -2)):
                ni, nj = ci + di, cj + dj
                if 1 <= ni < n - 1 and 1 <= nj < n - 1 and (ni, nj) not in visited:
                    cand.append((ni, nj, ci + di // 2, cj + dj // 2))
            if not cand:
                stack.pop()
                continue
            ni, nj, wi, wj = cand[rng.randrange(len(cand))]
            grid[wi][wj] = 0
            grid[ni][nj] = 0
            visited.add((ni, nj))
            stack.append((ni, nj))

        # 编织：死胡同（只有一个开口）按 braid 概率打通一面墙 → 形成环路
        if self.braid > 0:
            for i in range(1, n - 1):
                for j in range(1, n - 1):
                    if grid[i][j] != 0:
                        continue
                    if len(self._open_neighbors(grid, i, j)) != 1:
                        continue
                    if rng.random() > self.braid:
                        continue
                    cand = []
                    for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        wi, wj, ni, nj = i + di, j + dj, i + 2 * di, j + 2 * dj
                        if (0 <= wi < n and 0 <= wj < n and 0 <= ni < n and 0 <= nj < n
                                and grid[wi][wj] == 1 and grid[ni][nj] == 0):
                            cand.append((wi, wj))
                    if cand:
                        wi, wj = cand[rng.randrange(len(cand))]
                        grid[wi][wj] = 0
        self.grid = grid
        return grid

    def _open_neighbors(self, grid, i, j):
        # 注意：Cordis 的 Traceable 代理对**所有**方法都注入 self（连 staticmethod
        # 也会被塞一个 self 进去），所以这里必须写成实例方法，不能加 @staticmethod。
        n = len(grid)
        out = []
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ni, nj = i + di, j + dj
            if 0 <= ni < n and 0 <= nj < n and grid[ni][nj] == 0:
                out.append((ni, nj))
        return out

    # ---------------------------------------------------------- 查询 --
    def in_bounds(self, i, j):
        return 0 <= i < self.n and 0 <= j < self.n

    def is_wall_cell(self, i, j):
        if not self.in_bounds(i, j):
            return True                      # 界外当墙，玩家/敌人不会漏出去
        return self.grid[i][j] == 1

    def cell_of(self, x, z):
        """世界坐标 → 格坐标 (i=行/z, j=列/x)。"""
        j = int(math.floor((x + self.half) / self.cell))
        i = int(math.floor((z + self.half) / self.cell))
        return (min(max(i, 0), self.n - 1), min(max(j, 0), self.n - 1))

    def world_of(self, i, j):
        """格坐标 → 该格中心的世界坐标 (x, z)。"""
        return ((j + 0.5) * self.cell - self.half, (i + 0.5) * self.cell - self.half)

    def is_wall(self, x, z):
        i, j = self.cell_of(x, z)
        return self.is_wall_cell(i, j)

    def open_cells(self):
        return [(i, j) for i in range(self.n) for j in range(self.n)
                if self.grid[i][j] == 0]

    def manhattan(self, a, b):
        """格坐标曼哈顿距离（布点时用，衡量"离起点够远"）。"""
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    # 契约对齐（SdfVoxelCore 子集）
    def block_at(self, i, j, k=0):
        return 1 if self.is_wall_cell(i, j) else 0

    def voxel_get(self, x, y, z):
        return self.block_at(x, z)

    # ---------------------------------------------------------- 寻路 --
    def find_path(self, start, goal):
        """通道格 BFS 最短路，返回格列表（含首尾）；不可达返回 []。"""
        if self.is_wall_cell(*start) or self.is_wall_cell(*goal):
            return []
        prev = {start: None}
        dq = collections.deque([start])
        while dq:
            cur = dq.popleft()
            if cur == goal:
                break
            ci, cj = cur
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (ci + di, cj + dj)
                if not self.in_bounds(*nxt) or self.grid[nxt[0]][nxt[1]] != 0:
                    continue
                if nxt in prev:
                    continue
                prev[nxt] = cur
                dq.append(nxt)
        if goal not in prev:
            return []
        path = []
        cur = goal
        while cur is not None:
            path.append(cur)
            cur = prev[cur]
        path.reverse()
        return path

    # ------------------------------------------------ 射线（CPU 侧命中判定）
    def cast_ray(self, ox, oz, dx, dz, max_t):
        """2D 网格 DDA（Amanatides & Woo）求撞墙距离；未撞返回 ``max_t``。

        与 ``blockworld._raycast`` 同算法的 2D 化版本——GPU 侧用同一份迷宫数据画，
        CPU 侧用它判定命中，两侧"墙在哪"必须同源（§10.8 的最大风险项）。
        """
        n, cell, half = self.n, self.cell, self.half
        gx, gz = (ox + half) / cell, (oz + half) / cell
        ix, iz = int(math.floor(gx)), int(math.floor(gz))
        if self.is_wall_cell(ix, iz):
            return 0.0
        step_x = 1 if dx > 0 else (-1 if dx < 0 else 0)
        step_z = 1 if dz > 0 else (-1 if dz < 0 else 0)
        inf = float('inf')
        dtx = abs(cell / dx) if dx else inf
        dtz = abs(cell / dz) if dz else inf
        if dx > 0:
            tx = ((ix + 1) * cell - half - ox) / dx
        elif dx < 0:
            tx = (ix * cell - half - ox) / dx
        else:
            tx = inf
        if dz > 0:
            tz = ((iz + 1) * cell - half - oz) / dz
        elif dz < 0:
            tz = (iz * cell - half - oz) / dz
        else:
            tz = inf
        t = 0.0
        while t <= max_t:
            if tx <= tz:
                ix += step_x
                t = tx
                tx += dtx
            else:
                iz += step_z
                t = tz
                tz += dtz
            if not self.in_bounds(ix, iz):
                return min(t, max_t)
            if self.grid[ix][iz] == 1:
                return min(t, max_t)
        return max_t

    # ------------------------------------------------ 距离场（给着色器）
    def distance_field(self, max_cells=4.0):
        """到最近墙格的近似欧氏距离场（chamfer 两遍扫描），单位 = 世界距离。

        着色器拿它当 2D SDF 采样：``d3 = max(d2d, max(p.y - WALL_H, -p.y))``
        ——即把竖直棱柱拉伸到"地板到天花板"，比在片元里逐格查表便宜得多。
        """
        n, cell = self.n, self.cell
        inf = float('inf')
        d = [[0.0 if self.grid[i][j] == 1 else inf for j in range(n)]
             for i in range(n)]
        s2 = math.sqrt(2.0)
        for i in range(n):
            for j in range(n):
                best = d[i][j]
                for di, dj, w in ((0, -1, 1.0), (-1, 0, 1.0),
                                  (-1, -1, s2), (-1, 1, s2)):
                    pi, pj = i + di, j + dj
                    if 0 <= pi < n and 0 <= pj < n:
                        best = min(best, d[pi][pj] + w)
                d[i][j] = best
        for i in range(n - 1, -1, -1):
            for j in range(n - 1, -1, -1):
                best = d[i][j]
                for di, dj, w in ((0, 1, 1.0), (1, 0, 1.0),
                                  (1, 1, s2), (1, -1, s2)):
                    pi, pj = i + di, j + dj
                    if 0 <= pi < n and 0 <= pj < n:
                        best = min(best, d[pi][pj] + w)
                d[i][j] = best
        cap = max_cells
        return [[min(d[i][j], cap) * cell for j in range(n)] for i in range(n)]

    def __cordis_init__(self):
        return None


# ===================================================================== 内核
class FpsKernel(Service):
    """射线 FPS 内核：声明 inject 只取所需共同核，玩法全在此编排（逻辑在 Cordis）。"""

    inject = ['render', 'entity', 'rules', 'economy', 'maze']

    def __init__(self, ctx, config):
        super().__init__(ctx, "fps")
        cfg = config or {}
        self.seed = int(cfg.get('seed', DEFAULT_SEED))
        self.enemy_total = int(cfg.get('enemies', ENEMY_COUNT))
        self.player = dict(x=0.0, z=0.0, yaw=0.0)
        self.enemies = []
        self.bolts = []                 # 敌人光弹
        self.pickups = []
        self.kills = 0
        self.shots = 0                  # 射击次数（命中率统计，demo 结算用）
        self.hits = 0                   # 命中次数（含击杀）
        self.time = 0.0
        self.shoot_cd = 0.0
        self.muzzle = 0.0               # 枪口闪光强度（demo 用，0..1）
        self.hurt_flash = 0.0           # 受击红晕（demo 用，0..1）
        self.auto_path = []
        self._eid = 0
        self.events = []                # 语义事件（供 demo 播音效）
        self._gl_handles = []           # GL 句柄（回滚断言用）

    # ---------------------------------------------------------- 启动编排
    def __cordis_init__(self):
        maze = self.ctx.maze
        maze.regenerate(self.seed)
        sx, sz = maze.world_of(*maze.start)
        self.player['x'], self.player['z'] = sx, sz
        # 朝向出口：yaw 与 FlyCamera 同约定（forward = (cos yaw, sin yaw)）
        ex, ez = maze.world_of(*maze.exit)
        self.player['yaw'] = math.atan2(ez - sz, ex - sx)
        self._spawn_enemies()
        self._spawn_pickups()
        self.ctx.economy.add('ammo', float(START_AMMO))
        self._acquire_gl()
        return None

    def _spawn_enemies(self):
        maze = self.ctx.maze
        rng = random.Random(self.seed + 7)
        cells = [c for c in maze.open_cells()
                 if maze.manhattan(c, maze.start) > 4]
        rng.shuffle(cells)
        for k, cell in enumerate(cells[:self.enemy_total]):
            # 三分之一放哨卫（远程），其余放游荡者（近战）——两类都保证出现
            kind = 'sentry' if (k % 3 == 2) else 'stalker'
            x, z = maze.world_of(*cell)
            eid = self.ctx.entity.spawn(kind, (x, 0.0, z))
            spec = SENTRY if kind == 'sentry' else STALKER
            self.enemies.append(dict(
                eid=eid, kind=kind, x=x, z=z,
                hp=float(spec['hp']), hp0=float(spec['hp']),
                state='PATROL', t=0.0, cd=0.0, flash=0.0, dying=0.0,
                path=[], tgt=None, repath=0.0, alerted=False,
            ))

    def _spawn_pickups(self):
        maze = self.ctx.maze
        rng = random.Random(self.seed + 11)
        cells = [c for c in maze.open_cells()
                 if maze.manhattan(c, maze.start) > 2]
        rng.shuffle(cells)
        for k, cell in enumerate(cells[:6]):
            kind = 'medkit' if k % 2 else 'ammo'
            x, z = maze.world_of(*cell)
            eid = self.ctx.entity.spawn(kind, (x, 0.0, z))
            self.pickups.append(dict(eid=eid, kind=kind, x=x, z=z, taken=False))

    # -------------------------------------------------- GL 资源（可逆回滚）
    def _acquire_gl(self):
        render = self.ctx.render
        h_prog = render.compile_program('fps_ray')
        h_enemy = render.upload_mesh('fps_enemy', *_unit_sphere(8, 6))
        h_pick = render.upload_mesh('fps_pickup', *_unit_cube())
        self._gl_handles.extend([h_prog, h_enemy, h_pick])
        # 每个句柄的释放都绑到「内核 fiber」——内核 dispose 时统一回滚
        for h in self._gl_handles:
            def release(h=h):
                return lambda: render.release(h)
            self.ctx.effect(release)

    # ---------------------------------------------------------- 重开 --
    def restart(self, seed=None):
        """换种子重开一局（demo 的 R 键 / ``--flag seedN``）：迷宫、玩家、敌人、
        拾取、弹药与血量全部复位，GL 句柄不动（它们属于内核生命周期，不随局重开）。"""
        if seed is not None:
            self.seed = int(seed)
        maze = self.ctx.maze
        maze.regenerate(self.seed)
        r = self.ctx.rules
        r.reset()
        ec = self.ctx.economy
        ec.add('ammo', -ec.balance('ammo'))
        ec.add('ammo', float(START_AMMO))
        for e in list(self.enemies):
            self.ctx.entity.remove(e['eid'])
        for pk in list(self.pickups):
            self.ctx.entity.remove(pk['eid'])
        self.enemies, self.pickups, self.bolts = [], [], []
        sx, sz = maze.world_of(*maze.start)
        self.player['x'], self.player['z'] = sx, sz
        ex, ez = maze.world_of(*maze.exit)
        self.player['yaw'] = math.atan2(ez - sz, ex - sx)
        self.kills = 0
        self.shots = 0
        self.hits = 0
        self.time = 0.0
        self.shoot_cd = 0.0
        self.auto_path = []
        self._spawn_enemies()
        self._spawn_pickups()
        return self.seed

    # ---------------------------------------------------------- 事件 --
    def _emit(self, name):
        self.events.append(name)

    def drain_events(self):
        evs = self.events
        self.events = []
        return evs

    # ---------------------------------------------------------- 查询 --
    def ammo(self):
        return self.ctx.economy.balance('ammo')

    def hp(self):
        return self.ctx.rules.base_hp

    def hp_max(self):
        return self.ctx.rules.base_hp0

    def at_exit(self):
        return self.ctx.maze.cell_of(self.player['x'], self.player['z']) \
            == self.ctx.maze.exit

    def forward(self):
        y = self.player['yaw']
        return (math.cos(y), math.sin(y))

    # ---------------------------------------------------------- 操作 --
    def look(self, d_yaw):
        self.player['yaw'] += d_yaw

    def move(self, forward, right, dt, sprint=False):
        """按朝向推进（分轴撞墙滑行，贴墙走不会被卡死）。"""
        r = self.ctx.rules
        if r.state() in ('won', 'lost'):
            return
        sp = MOVE_SPEED * (SPRINT if sprint else 1.0) * dt
        fx, fz = self.forward()
        rx, rz = -fz, fx                 # right = forward × up（与 FlyCamera 同约定）
        dx = (fx * forward + rx * right) * sp
        dz = (fz * forward + rz * right) * sp
        p = self.player
        if not self._blocked(p['x'] + dx, p['z']):
            p['x'] += dx
        if not self._blocked(p['x'], p['z'] + dz):
            p['z'] += dz

    def _blocked(self, x, z):
        r = PLAYER_R
        maze = self.ctx.maze
        for sx, sz in ((x - r, z - r), (x + r, z - r), (x - r, z + r),
                       (x + r, z + r), (x, z)):
            if maze.is_wall(sx, sz):
                return True
        return False

    def shoot(self):
        """hitscan：先求墙距，再对敌人圆柱求交，取更近者（§10.3）。"""
        if self.shoot_cd > 0:
            return False
        r = self.ctx.rules
        if r.state() in ('won', 'lost'):
            return False
        ec = self.ctx.economy
        if ec.balance('ammo') < 1.0:
            self.shoot_cd = GUN_CD
            self._emit('dry')
            return False
        ec.spend('ammo', 1.0)
        self.shoot_cd = GUN_CD
        self.muzzle = 1.0
        self.shots += 1
        self._emit('shoot')

        p = self.player
        fx, fz = self.forward()
        t_wall = self.ctx.maze.cast_ray(p['x'], p['z'], fx, fz, GUN_RANGE)
        best, best_t = None, t_wall
        for e in self.enemies:
            if e['state'] == 'DEAD':
                continue
            t = _ray_circle(p['x'], p['z'], fx, fz, e['x'], e['z'], ENEMY_R)
            if t is not None and 0.0 <= t < best_t:
                best, best_t = e, t
        if best is None:
            self._emit('miss')
            return True
        self.hits += 1
        best['hp'] -= GUN_DMG
        best['flash'] = 1.0
        self._emit('hit')
        if best['hp'] <= 0:
            best['state'] = 'DEAD'
            best['dying'] = 0.0
            self.kills += 1
            self._emit('kill')
            self.ctx.rules.on_event({'type': 'enemy_killed'})
        else:
            best['state'] = 'HURT'
            best['t'] = 0.0
        return True

    # ---------------------------------------------------------- 每帧 --
    def update(self, dt):
        r = self.ctx.rules
        if r.state() in ('won', 'lost'):
            return
        self.time += dt
        self.shoot_cd = max(0.0, self.shoot_cd - dt)
        self.muzzle = max(0.0, self.muzzle - dt * 4.0)
        self.hurt_flash = max(0.0, self.hurt_flash - dt * 2.0)
        self._update_enemies(dt)
        self._update_bolts(dt)
        self._update_pickups()
        if self.at_exit():
            self.ctx.rules.on_event({'type': 'exit_reached'})
            self._emit('win')
        elif r.state() == 'lost':
            self._emit('lose')

    # ---------------------------------------------------------- 敌人 --
    def _update_enemies(self, dt):
        p = self.player
        maze = self.ctx.maze
        for e in list(self.enemies):
            if e['state'] == 'DEAD':
                e['dying'] += dt
                if e['dying'] >= DEATH_T:
                    self.ctx.entity.remove(e['eid'])
                    self.enemies.remove(e)
                continue
            e['t'] += dt
            e['cd'] = max(0.0, e['cd'] - dt)
            e['flash'] = max(0.0, e['flash'] - dt * 3.0)
            if e['state'] == 'HURT':
                if e['t'] >= HURT_T:
                    e['state'] = 'CHASE'
                    e['t'] = 0.0
                    e['alerted'] = True
                continue

            dx, dz = p['x'] - e['x'], p['z'] - e['z']
            dist = math.hypot(dx, dz)
            see = (dist <= SIGHT) and self._visible(e, dx, dz, dist)

            if e['state'] == 'PATROL':
                self._patrol(e, dt, maze)
                if see:
                    e['state'] = 'ALERT'
                    e['t'] = 0.0
                continue
            if e['state'] == 'ALERT':
                if not see:
                    e['state'] = 'PATROL'
                elif e['t'] >= ALERT_T:
                    e['state'] = 'CHASE'
                    e['t'] = 0.0
                    e['alerted'] = True
                    e['repath'] = 0.0
                continue
            # CHASE
            if not see:
                if e['t'] >= LOSE_T:
                    e['state'] = 'PATROL'
                    e['t'] = 0.0
                    e['alerted'] = False
                    e['path'] = []
                    continue
            else:
                e['t'] = 0.0
            self._chase(e, dt, maze, dist)

    def _visible(self, e, dx, dz, dist):
        """视线判定：朝向无关（简化为遮挡 + 距离），DDA 求墙距比敌距远即可见。"""
        if dist < 1e-4:
            return True
        maze = self.ctx.maze
        t = maze.cast_ray(e['x'], e['z'], dx / dist, dz / dist, dist)
        return t >= dist - 1e-3

    def _patrol(self, e, dt, maze):
        if e['tgt'] is None or not e['path']:
            cells = maze.open_cells()
            e['tgt'] = cells[(e['eid'] * 37) % len(cells)]
            e['path'] = maze.find_path(maze.cell_of(e['x'], e['z']), e['tgt'])
        self._follow(e, dt, maze, STALKER['speed'] * 0.55)

    def _chase(self, e, dt, maze, dist):
        spec = SENTRY if e['kind'] == 'sentry' else STALKER
        if e['kind'] == 'sentry':
            # 哨卫：保持 8~12m，太近后退、太远靠近，冷却到就投光弹
            if dist < spec['keep_lo']:
                self._follow(e, dt, maze, spec['speed'], away=True)
            elif dist > spec['keep_hi']:
                self._follow(e, dt, maze, spec['speed'])
            if e['cd'] <= 0.0 and dist <= SIGHT:
                self._fire_bolt(e)
                e['cd'] = spec['atk_cd']
        else:
            if dist <= spec['reach']:
                if e['cd'] <= 0.0:
                    self.ctx.rules.on_event({'type': 'player_hit',
                                             'dmg': spec['dmg']})
                    self.hurt_flash = 1.0
                    self._emit('hurt')
                    e['cd'] = spec['atk_cd']
            else:
                self._follow(e, dt, maze, spec['speed'])

    def _follow(self, e, dt, maze, speed, away=False):
        """沿 BFS 路径走向（或背离）玩家格；无路径时朝当前目标直走。"""
        if away:
            # 后撤：不寻路，直接沿"背离玩家"方向走，撞墙就算了（哨卫只需拉开距离）
            dx, dz = e['x'] - self.player['x'], e['z'] - self.player['z']
        else:
            e['repath'] -= dt
            if e['repath'] <= 0.0 or not e['path']:
                goal = maze.cell_of(self.player['x'], self.player['z'])
                e['path'] = maze.find_path(maze.cell_of(e['x'], e['z']), goal)
                e['repath'] = 0.4
            tgt = e['path'][1] if len(e['path']) > 1 else None
            if tgt is None:
                dx, dz = self.player['x'] - e['x'], self.player['z'] - e['z']
            else:
                tx, tz = maze.world_of(*tgt)
                dx, dz = tx - e['x'], tz - e['z']
                if math.hypot(dx, dz) < 0.35 and len(e['path']) > 1:
                    e['path'].pop(0)
        d = math.hypot(dx, dz)
        if d < 1e-4:
            return
        step = speed * dt
        nx, nz = e['x'] + dx / d * step, e['z'] + dz / d * step
        if not maze.is_wall(nx, nz):
            e['x'], e['z'] = nx, nz
        else:                                  # 卡墙就重算路径（下帧生效）
            e['repath'] = 0.0

    def _fire_bolt(self, e):
        dx, dz = self.player['x'] - e['x'], self.player['z'] - e['z']
        d = math.hypot(dx, dz) or 1.0
        self.bolts.append(dict(x=e['x'], z=e['z'],
                               vx=dx / d * BOLT_SPEED, vz=dz / d * BOLT_SPEED,
                               life=BOLT_LIFE))
        self._emit('bolt')

    def _update_bolts(self, dt):
        maze = self.ctx.maze
        p = self.player
        for b in list(self.bolts):
            b['life'] -= dt
            b['x'] += b['vx'] * dt
            b['z'] += b['vz'] * dt
            if b['life'] <= 0.0 or maze.is_wall(b['x'], b['z']):
                self.bolts.remove(b)
                continue
            if math.hypot(b['x'] - p['x'], b['z'] - p['z']) <= BOLT_HIT_R:
                self.ctx.rules.on_event({'type': 'player_hit', 'dmg': SENTRY['dmg']})
                self.hurt_flash = 1.0
                self._emit('hurt')
                self.bolts.remove(b)

    def _update_pickups(self):
        p = self.player
        ec = self.ctx.economy
        r = self.ctx.rules
        for pk in self.pickups:
            if pk['taken']:
                continue
            if math.hypot(pk['x'] - p['x'], pk['z'] - p['z']) > PICKUP_R:
                continue
            pk['taken'] = True
            self.ctx.entity.remove(pk['eid'])
            if pk['kind'] == 'ammo':
                ec.add('ammo', float(AMMO_PICKUP))
            else:
                r.base_hp = min(r.base_hp0, r.base_hp + MEDKIT_HEAL)
            self._emit('pickup')

    # --------------------------------------------- auto-pilot（录制/验收共用）
    def auto_step(self, dt):
        """自动驾驶：BFS 直奔出口，视线内遇敌则转向射击。

        demo 的 ``--flag auto`` 与 config_boot 的 headless 验收走**同一套**逻辑——
        "demo 怎么玩的 = 验证怎么玩的"（与 Phase D 的挂载单一入口同理念）。
        """
        r = self.ctx.rules
        if r.state() in ('won', 'lost'):
            return
        maze = self.ctx.maze
        p = self.player
        cur = maze.cell_of(p['x'], p['z'])

        # 1) 路径：目标固定是出口，丢了就重算
        if not self.auto_path or self.auto_path[-1] != maze.exit:
            self.auto_path = maze.find_path(cur, maze.exit)
        tgt = None
        if len(self.auto_path) > 1:
            tgt = maze.world_of(*self.auto_path[1])
        elif self.auto_path:
            tgt = maze.world_of(*self.auto_path[0])
        if tgt is None:
            self.auto_path = maze.find_path(cur, maze.exit)
            tgt = (maze.world_of(*self.auto_path[1])
                   if len(self.auto_path) > 1 else None)
        if tgt is not None:
            tx, tz = tgt
            if math.hypot(tx - p['x'], tz - p['z']) < 0.45 and len(self.auto_path) > 1:
                self.auto_path.pop(0)
            # 2) 有敌在视线内 → 转向它并开火；否则朝航点走
            focus = self._nearest_visible_enemy()
            if focus is not None:
                p['yaw'] = math.atan2(focus['z'] - p['z'], focus['x'] - p['x'])
                self.shoot()
                self.move(0.35, 0.0, dt)       # 边打边小步逼近
            else:
                p['yaw'] = math.atan2(tz - p['z'], tx - p['x'])
                self.move(1.0, 0.0, dt, sprint=True)

    def _nearest_visible_enemy(self):
        p = self.player
        maze = self.ctx.maze
        best, best_d = None, 1e9
        for e in self.enemies:
            if e['state'] == 'DEAD':
                continue
            dx, dz = e['x'] - p['x'], e['z'] - p['z']
            d = math.hypot(dx, dz)
            if d > SIGHT or d >= best_d or d < 1e-4:
                continue
            if maze.cast_ray(p['x'], p['z'], dx / d, dz / d, d) >= d - 1e-3:
                best, best_d = e, d
        return best

    # ---------------------------------------------------------- 展示 --
    def status_text(self):
        r = self.ctx.rules
        return (f"state={r.state()} hp={r.base_hp}/{r.base_hp0} "
                f"ammo={self.ammo():.0f} kills={self.kills} "
                f"enemies={len(self.enemies)} seed={self.seed} t={self.time:.1f}s")


# ===================================================================== 几何工具
def _ray_circle(ox, oz, dx, dz, cx, cz, r):
    """射线（原点 o、单位方向 d）与 XZ 平面圆（心 c、半径 r）的最近正交点；未命中返回 None。"""
    mx, mz = cx - ox, cz - oz
    t_ca = mx * dx + mz * dz
    if t_ca < 0.0:
        return None
    d2 = mx * mx + mz * mz - t_ca * t_ca
    r2 = r * r
    if d2 > r2:
        return None
    t = t_ca - math.sqrt(r2 - d2)
    return max(t, 0.0)


