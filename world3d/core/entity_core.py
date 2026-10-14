# -*- coding: utf-8 -*-
"""实体共同核 ``entity``（实现 ``world3d.core.contracts.EntityCore``）。

自 ``games/tower_defense/kernel`` 上提的**跨内核共享实现**（P0-1 去重）：
通用实体注册表——内核把塔 / 敌人 / 弹道都登记为实体；具体行为与状态机由内核驱动
（实体核心是「存储」，不是「行为」）。

> 依赖：仅 ``cordis``（vendored）。
"""
from __future__ import annotations

from world3d.core._cordis import Service


class EntityService(Service):
    """实体共同核（实现 EntityCore 契约）—— 通用实体注册表。

    内核把塔 / 敌人 / 弹道都登记为实体；具体行为与状态机由内核驱动（实体核心是
    「存储」，不是「行为」）。
    """

    def __init__(self, ctx, config):
        super().__init__(ctx, "entity")
        self._ents: dict = {}
        self._eid = 0

    def spawn(self, kind: str, pos, **kw) -> int:
        self._eid += 1
        eid = self._eid
        ent = dict(eid=eid, kind=kind, pos=pos, **kw)
        self._ents[eid] = ent
        return eid

    def get(self, eid: int):
        return self._ents.get(eid)

    def update(self, dt):
        # 行为由内核决定；此处不推进任何状态
        pass

    def remove(self, eid: int):
        self._ents.pop(eid, None)

    def query(self, aabb):
        """返回落在给定 AABB（{'min':(x,y,z),'max':(x,y,z)}）内的实体 eid。"""
        lo, hi = aabb.get('min'), aabb.get('max')
        if lo is None or hi is None:
            return []
        out = []
        for eid, e in self._ents.items():
            p = e.get('pos')
            if p is None:
                continue
            if (lo[0] <= p[0] <= hi[0] and lo[1] <= p[1] <= hi[1]
                    and lo[2] <= p[2] <= hi[2]):
                out.append(eid)
        return out

    def count(self, kind=None) -> int:
        if kind is None:
            return len(self._ents)
        return sum(1 for e in self._ents.values() if e['kind'] == kind)

    def __cordis_init__(self):
        return None


__all__ = ['EntityService']
