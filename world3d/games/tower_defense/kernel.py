# -*- coding: utf-8 -*-
"""塔防内核的 Cordis 化试点（试点二 · v4.0.0 · 「共同核 + 内核」范式验证）。

把 P1-②「戍卫之路 / Roadwarden」的塔防切片，按 ``core/contracts.py`` 定义的
12 核服务契约为**单一事实来源**，拆成「共同核」（可复用的服务）与「内核」（本游戏
特有的编排）两层，用 Cordis 接线：

    共同核（Service，由试点三起可被任意游戏复用）
      RenderService    -> render    GL 上下文 / 着色器程序 / 网格上传 / 绘制
      EconomyService   -> economy   金币存量（add / spend / balance）
      EntityService    -> entity    实体注册表（spawn / get / remove / count）
      RulesService     -> rules     波次 / 胜负状态机（state / win / lose / on_event）

    内核（TowerDefenseKernel，声明 inject 只取所需核心）
      TowerDefenseKernel -> tower_defense   波次编排 / 建塔 / 索敌 / 弹道 / 经济流转

要点
----
* **依赖只声明不编排**：内核只写 ``inject = ['render','entity','economy','rules']``，
  Cordis 自动按依赖图排序加载，共同核之间零耦合。
* **GL 资源可逆回滚（P0 收口点）**：渲染共同核只提供 ``upload_mesh`` /
  ``compile_program`` / ``release`` 原语；游戏特有的 GL 资源（地面、塔/敌体的
  立方体/球、着色器程序）由内核在 ``__cordis_init__`` 取得，并用 ``ctx.effect``
  把每个资源的释放**绑定到内核 fiber**。内核 fiber 一 dispose，所有 GL 资源统一
  释放——证明「共同核 + 内核」组合后，资源生命周期随内核一起可逆回滚。
  （headless 下不真正开 GL 上下文，release 仅做记账；若有 window 则走真实
  glDelete*。回滚语义本身被完整执行并断言。）

运行（无需显示器 / 不依赖 GL 窗口）::

    python games/tower_defense/kernel.py
"""
from __future__ import annotations

import asyncio
import math
import os
import sys

# P0-3：本文件由 experiments/cordis/ 移到 games/<名>/，层级少一层
# ⚠ P5-a 修正：这里必须**四层**（kernel.py -> <名>/ -> games/ -> world3d/ -> 仓库根）。
# 少一层会把 `world3d/` 塞进 sys.path，于是 `world3d/platform/` 遮蔽**标准库 platform**
# （pyglet 自己 `import platform`）—— 实测 `platform.system()` 抛 AttributeError。
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

# ---- cordis 定位 ----
import world3d._cordis_locate as _cordis_locate  # noqa: E402

_cordis_locate.ensure()

from cordis import Context, Service  # noqa: E402

# 复用已确立的 12 核契约（不依赖 Cordis / GL，可安全 import）
from world3d.core.contracts import (  # noqa: E402
    RenderCore, EconomyCore, EntityCore, RulesCore,
)


# ===================================================================== 平衡常量
# （与 examples/tower_defense.py 完全一致，保证模拟结果可复现：约 t≈137s 通关）
GRID = 20
CELL = 2.0
HALF = GRID * CELL / 2.0

LANE = [
    (-HALF + 1.0, -HALF + 3.0),
    ( HALF - 5.0, -HALF + 3.0),
    ( HALF - 5.0, -HALF + 7.0),
    (-HALF + 5.0, -HALF + 7.0),
    (-HALF + 5.0,  HALF - 5.0),
    ( HALF - 3.0,  HALF - 5.0),
]
START_GOLD = 300
PREP_FIRST = 3.0
PREP_BETWEEN = 6.0
BASE_HP0 = 20

TOWER_TYPES = {
    '1': dict(name='箭塔', cost=50,  range=9.0, damage=8.0,  rate=2.0, splash=0.0, slow=0.0,
              color=(0.30, 0.85, 0.95), height=1.4),
    '2': dict(name='炮塔', cost=120, range=7.5, damage=24.0, rate=0.6, splash=3.0, slow=0.0,
              color=(0.98, 0.62, 0.22), height=1.6),
    '3': dict(name='冰塔', cost=80,  range=8.0, damage=5.0,  rate=1.2, splash=0.0, slow=0.5,
              color=(0.55, 0.80, 1.00), height=1.5),
}
ENEMY_TYPES = {
    'swarm':   dict(hp=28,  speed=5.0, reward=7,  radius=0.5, leak=1, color=(0.95, 0.80, 0.30)),
    'grunt':   dict(hp=60,  speed=3.0, reward=12, radius=0.7, leak=2, color=(0.85, 0.35, 0.30)),
    'bruiser': dict(hp=180, speed=2.0, reward=30, radius=1.0, leak=3, color=(0.55, 0.30, 0.55)),
}
WAVES = [
    [('swarm', 8, 0.8)],
    [('grunt', 6, 1.0)],
    [('swarm', 10, 0.5), ('grunt', 4, 1.2)],
    [('grunt', 10, 0.8), ('bruiser', 2, 2.0)],
    [('swarm', 14, 0.4), ('grunt', 8, 0.9), ('bruiser', 4, 1.5)],
]
AUTO_TOWERS = [((5, 2), '1'), ((15, 2), '1'), ((10, 2), '2'), ((16, 4), '3')]


# ===================================================================== 纯函数几何
def _seg_dist(px, pz, ax, az, bx, bz):
    dx, dz = bx - ax, bz - az
    l2 = dx * dx + dz * dz
    if l2 < 1e-9:
        return math.hypot(px - ax, pz - az)
    t = ((px - ax) * dx + (pz - az) * dz) / l2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + dx * t), pz - (az + dz * t))


def lane_length():
    L = 0.0
    for k in range(len(LANE) - 1):
        L += math.hypot(LANE[k + 1][0] - LANE[k][0], LANE[k + 1][1] - LANE[k][1])
    return L


def lane_point(dist):
    d = dist
    for k in range(len(LANE) - 1):
        x0, z0 = LANE[k]
        x1, z1 = LANE[k + 1]
        seg = math.hypot(x1 - x0, z1 - z0)
        if d <= seg or k == len(LANE) - 2:
            t = d / seg if seg > 1e-6 else 0.0
            return (x0 + (x1 - x0) * t, z0 + (z1 - z0) * t)
        d -= seg
    return LANE[-1]


def cell_center(i, j):
    return (-HALF + (i + 0.5) * CELL, -HALF + (j + 0.5) * CELL)


# P0-3：`unit_cube` / `unit_sphere` 已上提 `engine/baked_mesh.py`——
# 本内核与 fps/puzzle/storm/hall/pulse/void 此前都从本模块横向 import，
# 属"游戏之间互相依赖"，违反"依赖只允许向下"。共享几何一律走引擎层。


# 极简向量工具（headless，不依赖 pyglet）
def _vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _vlen(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _vadd(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _vscale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


# ===================================================================== 共同核
# P0-1（2026-10-10）：共同核实现已**上提至 core/**（跨内核复用，去重）。
# 此处 re-export 保持向后兼容——旧路径仍可用：
#     from world3d.games.tower_defense.kernel import RenderService
# P0-3：`unit_cube` / `unit_sphere` 上提到 engine/baked_mesh（原先本内核自己定义，
# 另外 6 个内核横向 import 它 → 游戏互相依赖）。
from world3d.engine.baked_mesh import unit_cube, unit_sphere  # noqa: E402
from world3d.core.render_core import RenderService      # noqa: E402,F401
from world3d.core.economy_core import EconomyService    # noqa: E402,F401
from world3d.core.entity_core import EntityService      # noqa: E402,F401
from world3d.core.rules_core import RulesService        # noqa: E402,F401


# ===================================================================== 内核
class TowerDefenseKernel(Service):
    """塔防内核：声明 inject 只取所需共同核，所有游戏逻辑在此编排。"""

    inject = ['render', 'entity', 'economy', 'rules']

    def __init__(self, ctx, config):
        super().__init__(ctx, "tower_defense")
        self.time = 0.0
        self.prep_until = PREP_FIRST
        self.wave_start = 0.0
        self.spawn_q = []
        self.towers: dict = {}            # (i,j) -> entity dict
        self.enemies: list = []           # eid 列表
        self.projectiles: list = []       # eid 列表
        self._eid = 0
        self._last_shoot_sfx = -10.0
        self._gl_handles = []             # 内核持有的 GL 资源句柄（回滚断言用）
        self.events = []                  # 内核对外发出的语义事件（供 demo 播放音效；headless 下为空闲）

    # -------------------------------------------------- GL 资源（可逆回滚）
    def _acquire_gl(self):
        render = self.ctx.render
        # 地面网格
        h_ground = render.upload_mesh('td_ground', *self._build_ground())
        self._gl_handles.append(h_ground)
        # 塔 / 敌体单元
        h_cube = render.upload_mesh('td_cube', *unit_cube())
        h_sphere = render.upload_mesh('td_sphere', *unit_sphere())
        self._gl_handles.extend([h_cube, h_sphere])
        # 主着色器程序
        h_prog = render.compile_program('td_main')
        self._gl_handles.append(h_prog)
        # 把每个资源的释放绑定到「内核 fiber」——这是 P0 收口的关键：
        # 内核 dispose 时，这些 effect 统一触发 -> 所有 GL 资源释放。
        for h in self._gl_handles:
            def release(h=h):
                return lambda: render.release(h)
            self.ctx.effect(release)

    def _build_ground(self):
        # 标记车道 / 基地 / 空地，生成地面网格（与 demo._build_ground 同形）
        cell_type = {}
        for (i, j) in self._lane_cells():
            cell_type[(i, j)] = 'lane'
        cell_type[(GRID - 1, GRID - 1)] = 'base'
        pos, shade = [], []
        for i in range(GRID):
            for j in range(GRID):
                x0 = -HALF + i * CELL
                z0 = -HALF + j * CELL
                x1, z1 = x0 + CELL, z0 + CELL
                if (i, j) in cell_type and cell_type[(i, j)] == 'lane':
                    c = (0.55, 0.45, 0.30)
                elif (i, j) in cell_type and cell_type[(i, j)] == 'base':
                    c = (0.45, 0.45, 0.52)
                else:
                    jh = ((i * 0x27D4EB2D) ^ (j * 0x165667B1)) & 0xFFFFFF
                    jh = (jh & 0xFFFFFF) / 16777216.0
                    c = (0.18 + 0.06 * jh, 0.40 + 0.08 * jh, 0.16 + 0.05 * jh)
                quad = [(x0, 0.0, z0), (x1, 0.0, z0), (x1, 0.0, z1), (x0, 0.0, z1)]
                for p in (quad[0], quad[1], quad[2], quad[0], quad[2], quad[3]):
                    pos.extend(p)
                    shade.extend(c)
        return pos, shade

    def _lane_cells(self):
        cells = set()
        for k in range(len(LANE) - 1):
            x0, z0 = LANE[k]
            x1, z1 = LANE[k + 1]
            steps = int(math.hypot(x1 - x0, z1 - z0) / CELL) + 1
            for s in range(steps + 1):
                t = s / max(1, steps)
                x = x0 + (x1 - x0) * t
                z = z0 + (z1 - z0) * t
                i = int((x + HALF) / CELL)
                j = int((z + HALF) / CELL)
                cells.add((min(GRID - 1, max(0, i)), min(GRID - 1, max(0, j))))
        # 车道两侧各留 1 格可建
        extra = set()
        for (i, j) in list(cells):
            for di in (-1, 1):
                for dj in (-1, 1):
                    ii, jj = i + di, j + dj
                    if 0 <= ii < GRID and 0 <= jj < GRID and (ii, jj) not in cells:
                        extra.add((ii, jj))
        return cells | extra

    # -------------------------------------------------- 建塔
    def _try_build(self, i, j, tk, free=False):
        if not (0 <= i < GRID and 0 <= j < GRID):
            return False
        if (i, j) in self.towers:
            return False
        spec = TOWER_TYPES[tk]
        if not free:
            if not self.ctx.economy.spend('gold', spec['cost']):
                return False
        wx, wz = cell_center(i, j)
        eid = self.ctx.entity.spawn('tower', (wx, spec['height'], wz),
                                    type=tk, wx=wx, wz=wz, cooldown=0.0)
        self.towers[(i, j)] = self.ctx.entity.get(eid)
        if not free:
            self._emit('build')
        return True

    # -------------------------------------------------- 内核对外 API（供 demo 委托）
    def build(self, i, j, tk, free=False):
        """demo 的建塔入口：委托给内核（经济扣费 / 免费）。成功返回 True。"""
        return self._try_build(i, j, tk, free=free)

    def early_wave(self):
        """N 键：立即开波 + 提前开波奖励。"""
        if self.ctx.rules.state() == 'prep':
            self.prep_until = self.time
            self.ctx.economy.add('gold', 15)

    # -------------------------------------------------- 事件通道（供 demo 音效）
    def _emit(self, name):
        """发出一个语义事件（'build'/'shoot'/'hit'/'kill'/'won'/'lose'）。"""
        self.events.append(name)

    def drain_events(self):
        """demo 每帧取走并清空本帧事件，用于触发对应音效。"""
        evs = self.events
        self.events = []
        return evs

    # -------------------------------------------------- 波次
    def _build_wave(self, idx):
        q, off = [], 0.0
        for typ, count, gap in WAVES[idx]:
            for _ in range(count):
                q.append((off, typ))
                off += gap
        return q

    def _spawn(self, typ):
        spec = ENEMY_TYPES[typ]
        eid = self.ctx.entity.spawn('enemy', (0.0, spec['radius'], 0.0),
                                    dist=0.0, hp=spec['hp'], max_hp=spec['hp'],
                                    type=typ, slow_t=0.0)
        self.enemies.append(eid)

    # -------------------------------------------------- 逐帧
    def update(self, dt):
        rs = self.ctx.rules.state()
        if rs in ('won', 'lost'):
            return
        self.time += dt
        prev = rs
        self._update_waves(dt)
        self._update_enemies(dt)
        self._update_towers(dt)
        self._update_projectiles(dt)
        now = self.ctx.rules.state()
        if now != prev:
            if now == 'won':
                self._emit('won')
            elif now == 'lost':
                self._emit('lose')

    def _update_waves(self, dt):
        r = self.ctx.rules
        if r.state() == 'prep' and self.time >= self.prep_until:
            r._state = 'wave'
            self.wave_start = self.time
        if r.state() == 'wave':
            while self.spawn_q and self.time >= self.wave_start + self.spawn_q[0][0]:
                _, typ = self.spawn_q.pop(0)
                self._spawn(typ)
        if r.state() == 'wave' and not self.spawn_q and not self.enemies:
            if r.wave_index + 1 >= r.total_waves:
                r.on_event({'type': 'wave_cleared'})   # -> won
            else:
                r.on_event({'type': 'wave_cleared'})   # -> prep + wave_index++
                self.prep_until = self.time + PREP_BETWEEN
                self.ctx.economy.add('gold', 25)
                self.spawn_q = self._build_wave(r.wave_index)

    def _update_enemies(self, dt):
        L = lane_length()
        alive = []
        for eid in self.enemies:
            e = self.ctx.entity.get(eid)
            if e is None:
                continue
            spec = ENEMY_TYPES[e['type']]
            spd = spec['speed'] * (0.45 if e['slow_t'] > 0 else 1.0)
            if e['slow_t'] > 0:
                e['slow_t'] -= dt
            e['dist'] += spd * dt
            if e['dist'] >= L:
                self.ctx.rules.on_event({'type': 'leak', 'dmg': spec['leak']})
                self.ctx.entity.remove(eid)
                continue
            alive.append(eid)
        self.enemies = alive

    def _update_towers(self, dt):
        for tw in self.towers.values():
            spec = TOWER_TYPES[tw['type']]
            tw['cooldown'] -= dt
            if tw['cooldown'] > 0:
                continue
            best, best_d = None, -1.0
            for eid in self.enemies:
                e = self.ctx.entity.get(eid)
                if e is None:
                    continue
                x, z = lane_point(e['dist'])
                if math.hypot(x - tw['wx'], z - tw['wz']) <= spec['range']:
                    if e['dist'] > best_d:
                        best, best_d = eid, e['dist']
            if best is None:
                continue
            tw['cooldown'] = 1.0 / spec['rate']
            be = self.ctx.entity.get(best)
            tx, tz = lane_point(be['dist'])
            peid = self.ctx.entity.spawn(
                'projectile', (tw['wx'], spec['height'] * 0.8, tw['wz']),
                target=best, speed=26.0, dmg=spec['damage'],
                splash=spec['splash'], slow=spec['slow'],
                color=(1.0, 0.95, 0.5))
            self.projectiles.append(peid)
            if self.time - self._last_shoot_sfx > 0.12:   # 射击音效节流
                self._emit('shoot')
                self._last_shoot_sfx = self.time

    def _update_projectiles(self, dt):
        alive = []
        for peid in self.projectiles:
            p = self.ctx.entity.get(peid)
            if p is None:
                continue
            tgt = self.ctx.entity.get(p['target'])
            if tgt is None:
                self.ctx.entity.remove(peid)
                continue
            tx, tz = lane_point(tgt['dist'])
            ty = ENEMY_TYPES[tgt['type']]['radius']
            to = (tx, ty, tz)
            d = _vsub(to, p['pos'])
            dist = _vlen(d)
            step = p['speed'] * dt
            if dist <= step + 0.4:
                self._impact(p, tx, tz)
                self.ctx.entity.remove(peid)
                continue
            p['pos'] = _vadd(p['pos'], _vscale(d, step / dist))
            alive.append(peid)
        self.projectiles = alive

    def _impact(self, p, x, z):
        self._emit('hit')
        if p['splash'] > 0:
            for eid in list(self.enemies):
                e = self.ctx.entity.get(eid)
                if e is None:
                    continue
                ex, ez = lane_point(e['dist'])
                if math.hypot(ex - x, ez - z) <= p['splash']:
                    self._damage(e, p['dmg'])
            if p['slow'] > 0:
                for eid in list(self.enemies):
                    e = self.ctx.entity.get(eid)
                    if e is None:
                        continue
                    ex, ez = lane_point(e['dist'])
                    if math.hypot(ex - x, ez - z) <= p['splash']:
                        e['slow_t'] = max(e['slow_t'], 1.5)
        else:
            tgt = self.ctx.entity.get(p['target'])
            if tgt is not None:
                self._damage(tgt, p['dmg'])
                if p['slow'] > 0:
                    tgt['slow_t'] = max(tgt['slow_t'], 1.5)

    def _damage(self, e, dmg):
        if 'dead' in e:
            return
        e['hp'] -= dmg
        if e['hp'] <= 0:
            e['dead'] = True
            self._emit('kill')
            self.ctx.economy.add('gold', ENEMY_TYPES[e['type']]['reward'])
            self.ctx.entity.remove(e['eid'])
            if e['eid'] in self.enemies:
                self.enemies.remove(e['eid'])

    # -------------------------------------------------- 启动编排
    def __cordis_init__(self):
        # 1) 自动布防（免费，保证 headless 录制/模拟可自动通关）
        for (i, j), tk in AUTO_TOWERS:
            self._try_build(i, j, tk, free=True)
        # 2) 启动首波备战
        self.spawn_q = self._build_wave(0)
        self.prep_until = PREP_FIRST
        # 3) 取得游戏专属 GL 资源，并以 effect 绑定到内核 fiber（可逆回滚）
        self._acquire_gl()
        return None

    def status_text(self):
        r = self.ctx.rules
        return (f"state={r.state()} wave={r.wave_index + 1}/{r.total_waves} "
                f"gold={self.ctx.economy.balance('gold'):.0f} "
                f"base_hp={r.base_hp} enemies={len(self.enemies)} "
                f"towers={len(self.towers)}")


# ===================================================================== 验证入口
async def run_kernel(scene_headless: bool = True) -> int:
    ctx = Context()

    print('== mount 共同核 + 内核（依赖图自动排序）==')
    render_fiber = await ctx.plugin(RenderService)
    economy_fiber = await ctx.plugin(EconomyService, {'gold': START_GOLD})
    entity_fiber = await ctx.plugin(EntityService)
    rules_fiber = await ctx.plugin(RulesService, {'total_waves': len(WAVES)})
    kernel_fiber = await ctx.plugin(TowerDefenseKernel)

    # 等待整张依赖图解析完成
    await render_fiber
    await economy_fiber
    await entity_fiber
    await rules_fiber
    await kernel_fiber

    kernel = ctx.tower_defense
    render = ctx.render

    print(f"[init] GL 资源已取得: {render.stats()}")
    # 断言：内核就绪后，GL 资源确实在线（尚未回滚）
    assert render.live_count() > 0, "GL 资源未取得"

    # 驱动模拟
    dt = 1.0 / 60.0
    t = 0.0
    print('== drive simulation (headless) ==')
    while kernel.ctx.rules.state() not in ('won', 'lost') and t < 400.0:
        kernel.update(dt)
        t += dt

    r = kernel.ctx.rules
    print(f"RESULT state={r.state()} t={t:.1f} "
          f"gold={kernel.ctx.economy.balance('gold'):.0f} "
          f"base_hp={r.base_hp} enemies_left={len(kernel.enemies)} "
          f"towers={len(kernel.towers)}")
    assert r.state() == 'won', f"预期通关，实际 {r.state()}"

    # ---- 关键：dispose 内核 fiber -> 所有 GL 资源随内核一起回滚 ----
    print('== dispose 内核（GL 资源可逆回滚）==')
    gl_before = render.live_count()
    await kernel_fiber.dispose()
    gl_after = render.live_count()
    print(f"[rollback] GL live: {gl_before} -> {gl_after}  (stats={render.stats()})")
    assert gl_after == 0, f"GL 资源未全部释放！仍存活 {gl_after}"
    assert render.stats()['released'] == render.stats()['acquired'], \
        "释放计数 != 取得计数，回滚不完整"

    print('== TDKERNEL_OK ==')
    return 0


def main(argv=None):
    return asyncio.run(run_kernel())


if __name__ == '__main__':
    sys.exit(main())
