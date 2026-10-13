# -*- coding: utf-8 -*-
"""经济共同核 ``economy``（实现 ``core.contracts.EconomyCore``）。

自 ``games/tower_defense/kernel`` 上提的**跨内核共享实现**（P0-1 去重）：
资源存量 + 花费校验（金币 / 神力通用）。采集与生产队列为占位（内核按需覆写/扩展）。

> 依赖：仅 ``cordis``（vendored）。
"""
from __future__ import annotations

from core._cordis import Service

# 默认开局金币（与原 START_GOLD 一致；内核可经 config 覆盖）
DEFAULT_GOLD = 300.0


class EconomyService(Service):
    """经济共同核（实现 EconomyCore 契约）—— 金币存量。"""

    def __init__(self, ctx, config):
        super().__init__(ctx, "economy")
        cfg = config or {}
        self._res = {'gold': float(cfg.get('gold', DEFAULT_GOLD))}

    def add(self, resource: str, amount: float):
        self._res[resource] = self._res.get(resource, 0.0) + amount

    def spend(self, resource: str, amount: float) -> bool:
        if self._res.get(resource, 0.0) < amount:
            return False
        self._res[resource] -= amount
        return True

    def balance(self, resource: str) -> float:
        return self._res.get(resource, 0.0)

    def harvest(self, unit_id, node_id, rate):
        return 0.0

    def enqueue(self, item, cost):
        return False

    def tick(self, dt):
        pass

    def __cordis_init__(self):
        return None


__all__ = ['EconomyService', 'DEFAULT_GOLD']
