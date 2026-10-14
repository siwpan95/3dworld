# -*- coding: utf-8 -*-
"""神之手内核的 Cordis 化试点（试点三 · P1-① 神之手 · Populous 式实时地形编辑）。

把 P1-①「神之手」的玩法——玩家以「抬山 / 挖海」之力实时改写地形，淹没或掩埋敌方
聚落——按 ``core/contracts.py`` 的 12 核契约为单一事实来源，拆成「共同核 + 内核」：

    共同核（复用 tower_defense_kernel.py 已确立的实现）
      RenderService    -> render    GL 资源原语 + 绘制（headless 记账 + 回滚）
      EconomyService   -> economy   神力（mana）存量
      EntityService    -> entity    聚落实体注册表
      RulesService     -> rules     胜负状态机（state / win / on_event）
    本试点新增
      TerrainService   -> terrain   可编辑高度场（edit_height 抬山 + / 挖海 −，headless 安全）

    内核
      GodHandKernel    -> godhand   神力动作（raise/lower）+ 聚落淹没/掩埋结算 + 胜负

要点
----
* **地形即玩法**：``TerrainCore.edit_height`` 是神之手的命门（见 core/contracts.py
  注释）；本试点把它做成**自包含、可 headless 验证**的高度场网格，不依赖任何 GL /
  噪声库，因而离线模拟即可证明「挖海淹没全部聚落 → 通关」且 GL 资源随内核 fiber
  dispose 可逆回滚。
* **依赖只声明不编排**：内核只写 ``inject = ['render','entity','terrain','rules',
  'economy']``，Cordis 自动按依赖图排序，共同核之间零耦合。
* **GL 资源可逆回滚（P0 收口点，同 tower_defense_kernel）**：游戏专属 GL 资源
  （地表网格 / 水面网格 / 着色器程序）由内核在 ``__cordis_init__`` 取得，并以
  ``ctx.effect`` 绑定到内核 fiber；内核 fiber 一 dispose，全部释放。

运行（无需显示器 / 不依赖 GL 窗口）::

    python games/godhand/kernel.py
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

# P0-1（2026-10-10）：共同核实现已上提至 core/，此处直接从 core 取（跨内核复用）。
from world3d.core.render_core import RenderService      # noqa: E402
from world3d.core.economy_core import EconomyService    # noqa: E402
from world3d.core.entity_core import EntityService      # noqa: E402
from world3d.core.rules_core import RulesService        # noqa: E402
# 12 核契约（仅作类型参考，不强制）
from world3d.core.contracts import TerrainCore  # noqa: E402  (F401 保留语义引用)


# ===================================================================== 平衡常量
GRID_T = 24                      # 高度场格数（世界 48×48）
CELL_T = 2.0
HALF_T = GRID_T * CELL_T / 2.0   # 24.0
SEA = 0.0                        # 海平面
BURY = 4.0                       # 高于此高度 = 被掩埋（压垮）
FLOOD_RATE = 12.0                # 每低于海平面 1 单位、每秒掉血
BURY_RATE = 8.0                  # 每高于掩埋线 1 单位、每秒掉血
START_MANA = 100000              # 开局神力存量（足够淹没全部聚落）
SETTLEMENT_HP = 100.0
POWER_COST = {'raise': 8.0, 'lower': 6.0}   # 每次神力消耗 mana

# 敌方聚落世界坐标（避开边界，确保落在高度场有效区内）
SETTLEMENT_POS = [
    (-16.0, -12.0), (10.0, -16.0), (18.0, 6.0), (-8.0, 14.0), (4.0, 2.0),
]


# ===================================================================== 地形共同核
# P0-1（2026-10-10）：TerrainService 已上提至 core/terrain_core.py（跨内核复用）。
# 此处 re-export 保持向后兼容。
from world3d.core.terrain_core import TerrainService  # noqa: E402,F401


def _water_quad():
    """海平面整片水面网格（一个平铺四边形），供渲染与 GL 记账。"""
    pos = [-HALF_T, SEA, -HALF_T, HALF_T, SEA, -HALF_T,
           HALF_T, SEA, -HALF_T, -HALF_T, SEA, -HALF_T,
           HALF_T, SEA, -HALF_T, -HALF_T, SEA, HALF_T]
    shade = [0.30, 0.55, 0.80] * (len(pos) // 3)
    return pos, shade


# ===================================================================== 内核
class GodHandKernel(Service):
    """神之手内核：声明 inject 只取所需共同核，神力与胜负逻辑在此编排。"""

    inject = ['render', 'entity', 'terrain', 'rules', 'economy']

    def __init__(self, ctx, config):
        super().__init__(ctx, "godhand")
        self.time = 0.0
        self.settlements = []           # 聚落实体 eid 列表
        self._gl_handles = []           # 内核持有的 GL 资源句柄（回滚断言用）
        self.events = []                # 语义事件（供 demo 播放音效）

    # -------------------------------------------------- 事件通道
    def _emit(self, name):
        self.events.append(name)

    def drain_events(self):
        evs = self.events
        self.events = []
        return evs

    # -------------------------------------------------- 聚落
    def _spawn_settlements(self):
        for (x, z) in SETTLEMENT_POS:
            y = self.ctx.terrain.height_at(x, z)
            eid = self.ctx.entity.spawn('settlement', (x, y, z),
                                       hp=SETTLEMENT_HP, max_hp=SETTLEMENT_HP,
                                       x=x, z=z)
            self.settlements.append(eid)

    # -------------------------------------------------- 神力（抬山/挖海）
    def raise_at(self, x, z, amount=1.0):
        """神之手：抬山。消耗 mana，成功返回 True。"""
        if not self.ctx.economy.spend('mana', POWER_COST['raise']):
            return False
        self.ctx.terrain.edit_height(x, z, +amount)
        self._emit('raise')
        return True

    def lower_at(self, x, z, amount=1.0):
        """神之手：挖海。消耗 mana，成功返回 True。"""
        if not self.ctx.economy.spend('mana', POWER_COST['lower']):
            return False
        self.ctx.terrain.edit_height(x, z, -amount)
        self._emit('lower')
        return True

    # -------------------------------------------------- 逐帧结算
    def update(self, dt):
        if self.ctx.rules.state() in ('won', 'lost'):
            return
        self.time += dt
        terrain = self.ctx.terrain
        alive = []
        for eid in self.settlements:
            e = self.ctx.entity.get(eid)
            if e is None:
                continue
            h = terrain.height_at(e['x'], e['z'])
            dmg = 0.0
            if h < terrain.sea:
                dmg = (terrain.sea - h) * FLOOD_RATE * dt      # 淹没掉血
            elif h > terrain.bury:
                dmg = (h - terrain.bury) * BURY_RATE * dt      # 掩埋压垮
            if dmg > 0:
                e['hp'] -= dmg
                if e['hp'] <= 0:
                    self.ctx.entity.remove(eid)
                    self._emit('destroy')
                    continue
            alive.append(eid)
        self.settlements = alive
        if not self.settlements:
            self.ctx.rules.on_event({'type': 'settlements_cleared'})  # -> won
            self._emit('won')

    # -------------------------------------------------- GL 资源（可逆回滚）
    def _acquire_gl(self):
        render = self.ctx.render
        h_surface = render.upload_mesh('gh_surface', *self.ctx.terrain.rebuild_mesh())
        self._gl_handles.append(h_surface)
        h_water = render.upload_mesh('gh_water', *_water_quad())
        self._gl_handles.append(h_water)
        h_prog = render.compile_program('gh_main')
        self._gl_handles.append(h_prog)
        for h in self._gl_handles:
            def release(h=h):
                return lambda: render.release(h)
            self.ctx.effect(release)

    def __cordis_init__(self):
        # 1) 注入开局神力
        self.ctx.economy.add('mana', START_MANA)
        # 2) 放置敌方聚落
        self._spawn_settlements()
        # 3) 取得游戏专属 GL 资源，并以 effect 绑定到内核 fiber（可逆回滚）
        self._acquire_gl()
        return None

    def status_text(self):
        r = self.ctx.rules
        mana = self.ctx.economy.balance('mana')
        return (f"state={r.state()} settlements={len(self.settlements)} "
                f"mana={mana:.0f} t={self.time:.1f}")


# ===================================================================== 验证入口
async def run_kernel() -> int:
    ctx = Context()

    print('== mount 共同核 + 内核（依赖图自动排序）==')
    render_fiber = await ctx.plugin(RenderService)
    economy_fiber = await ctx.plugin(EconomyService, {'mana': 0})
    entity_fiber = await ctx.plugin(EntityService)
    terrain_fiber = await ctx.plugin(TerrainService)
    rules_fiber = await ctx.plugin(RulesService)
    kernel_fiber = await ctx.plugin(GodHandKernel)

    await render_fiber
    await economy_fiber
    await entity_fiber
    await terrain_fiber
    await rules_fiber
    await kernel_fiber

    kernel = ctx.godhand
    render = ctx.render
    terrain = ctx.terrain

    print(f"[init] GL 资源已取得: {render.stats()}")
    assert render.live_count() > 0, "GL 资源未取得"

    # ---- 驱动：对每个聚落持续挖海，直到全部淹没 ----
    dt = 1.0 / 60.0
    t = 0.0
    print('== 神之手：挖海淹没全部聚落（headless）==')
    while kernel.ctx.rules.state() not in ('won', 'lost') and t < 120.0:
        for eid in list(kernel.settlements):
            e = kernel.ctx.entity.get(eid)
            if e is None:
                continue
            if terrain.height_at(e['x'], e['z']) >= terrain.sea:
                kernel.lower_at(e['x'], e['z'], 1.5)
        kernel.update(dt)
        t += dt

    r = kernel.ctx.rules
    print(f"RESULT state={r.state()} t={t:.1f} "
          f"remaining_settlements={len(kernel.settlements)} "
          f"mana_left={kernel.ctx.economy.balance('mana'):.0f}")
    assert r.state() == 'won', f"预期通关（淹没全部聚落），实际 {r.state()}"

    # ---- 关键：dispose 内核 fiber -> 所有 GL 资源随内核一起回滚 ----
    print('== dispose 内核（GL 资源可逆回滚）==')
    gl_before = render.live_count()
    await kernel_fiber.dispose()
    gl_after = render.live_count()
    print(f"[rollback] GL live: {gl_before} -> {gl_after}  (stats={render.stats()})")
    assert gl_after == 0, f"GL 资源未全部释放！仍存活 {gl_after}"
    assert render.stats()['released'] == render.stats()['acquired'], \
        "释放计数 != 取得计数，回滚不完整"

    print('== GODHAND_OK ==')
    return 0


def main(argv=None):
    return asyncio.run(run_kernel())


if __name__ == '__main__':
    sys.exit(main())
