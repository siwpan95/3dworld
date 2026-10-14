# -*- coding: utf-8 -*-
"""规则共同核 ``rules``（实现 ``world3d.core.contracts.RulesCore``）。

自 ``games/tower_defense/kernel`` 上提的**跨内核共享实现**（P0-1 去重）：
波次 / 胜负状态机。事件驱动（``on_event``），覆盖四类内核的胜负口径：

* 塔防：``leak``（漏怪扣基地血）/ ``wave_cleared``（过波）
* 神之手：``settlements_cleared``（敌方聚落全清）
* 射线 FPS：``player_hit`` / ``exit_reached``
* 归仓（方块解谜）：``puzzle_solved``（全部箱子入仓）
* 星阑（太空贸易）：``asset_reached``（资产达标）

> 依赖：仅 ``cordis``（vendored）。默认值与原塔防常量一致，保证行为不变。
"""
from __future__ import annotations

from world3d.core._cordis import Service

# 默认总波数 / 基地血（与原 WAVES 长度、BASE_HP0 一致；内核可经 config 覆盖）
DEFAULT_TOTAL_WAVES = 5
DEFAULT_BASE_HP = 20


class RulesService(Service):
    """规则共同核（实现 RulesCore 契约）—— 波次 / 胜负状态机。"""

    def __init__(self, ctx, config):
        super().__init__(ctx, "rules")
        cfg = config or {}
        self.total_waves = int(cfg.get('total_waves', DEFAULT_TOTAL_WAVES))
        self.base_hp0 = int(cfg.get('base_hp', DEFAULT_BASE_HP))
        self.reset()

    def state(self) -> str:
        return self._state

    def win(self) -> bool:
        return self.won

    def lose(self) -> bool:
        return self.lost

    def on_event(self, event: dict):
        t = event.get('type')
        if t == 'leak':
            self.base_hp -= event.get('dmg', 1)
            if self.base_hp <= 0:
                self.base_hp = 0
                self._state, self.lost = 'lost', True
        elif t == 'wave_cleared':
            self.wave_index += 1
            if self.wave_index >= self.total_waves:
                self._state, self.won = 'won', True
            else:
                self._state = 'prep'
        elif t == 'enemy_killed':
            pass
        elif t == 'settlements_cleared':
            # 神之手：全部敌方聚落被淹没/掩埋 -> 胜
            self._state, self.won = 'won', True
        elif t == 'player_hit':
            # 射线 FPS：玩家受伤（base_hp 在此复用为玩家血）
            self.base_hp -= event.get('dmg', 1)
            if self.base_hp <= 0:
                self.base_hp = 0
                self._state, self.lost = 'lost', True
        elif t == 'exit_reached':
            # 射线 FPS：抵达出口 -> 胜
            self._state, self.won = 'won', True
        elif t == 'puzzle_solved':
            # 归仓（P1-④ 方块解谜）：全部箱子入仓 -> 胜
            self._state, self.won = 'won', True
        elif t == 'asset_reached':
            # 星阑（§15 太空贸易）：资产（credits + 货值）达标 -> 胜
            self._state, self.won = 'won', True

    def reset(self):
        self.wave_index = 0
        self.base_hp = self.base_hp0
        self._state = 'prep'
        self.won = False
        self.lost = False

    def __cordis_init__(self):
        return None


__all__ = ['RulesService', 'DEFAULT_TOTAL_WAVES', 'DEFAULT_BASE_HP']
