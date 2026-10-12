# -*- coding: utf-8 -*-
"""背包共同核 ``inventory``（实现 ``core.contracts.InventoryCore``）。

P1-⑤ 玩法通用模块之一。**槽位 + 堆叠 + 装备槽**，供任意内核复用
（首个消费者：`void` 星阑的货舱）。

> 参考 bullet-zombie（MIT）的**模块划分**思路（背包独立成服务、装备槽与背包分离）；
> 实现全为本项目自写。
"""
from __future__ import annotations

from core._cordis import Service


class InventoryService(Service):
    """背包服务：`item_id` 的堆叠计数 + 容量上限 + 装备槽。

    Args:
        capacity: 总格数上限（默认 20，与星阑货舱一致）。
        equip_slots: 装备槽名列表（默认 ``['weapon', 'armor']``；
                     §2 P1-⑤「做得更全」含装备槽）。
    """

    def __init__(self, ctx, config):
        super().__init__(ctx, "inventory")
        cfg = config or {}
        self.capacity = int(cfg.get('capacity', 20))
        self.stack_default = int(cfg.get('stack', 99))
        self.equip_slots = list(cfg.get('equip_slots', ['weapon', 'armor']))
        self.slots = []                       # [[item_id, n], ...]（保序）
        self.stack_cap = {}                   # item_id -> 该物的单格上限
        self.meta = {}                        # item_id -> {'value','tags','slot'}
        self.equipped = {s: None for s in self.equip_slots}

    # ---------------------------------------------------------- 定义
    def register(self, item_id, stack=None, value=1.0, tags=(), slot=None):
        """登记物品元数据（``slot`` 非空表示可装备到该槽）。"""
        self.stack_cap[item_id] = int(stack if stack is not None else self.stack_default)
        self.meta[item_id] = dict(value=float(value), tags=tuple(tags), slot=slot)
        return item_id

    def _cap(self, item_id):
        return self.stack_cap.get(item_id, self.stack_default)

    # ---------------------------------------------------------- 增删查
    def add(self, item_id, n=1):
        """加入 ``n`` 个；返回**实际加入量**（受容量/堆叠上限限制）。"""
        n = int(n)
        if n <= 0:
            return 0
        cap = self._cap(item_id)
        added = 0
        for slot in self.slots:                       # 先补已有堆
            if slot[0] == item_id and slot[1] < cap:
                take = min(n - added, cap - slot[1])
                slot[1] += take
                added += take
                if added >= n:
                    return added
        while added < n and len(self.slots) < self.capacity:   # 再开新格
            take = min(n - added, cap)
            self.slots.append([item_id, take])
            added += take
        return added

    def remove(self, item_id, n=1):
        """移除 ``n`` 个；返回**实际移除量**。"""
        n = int(n)
        removed = 0
        for slot in list(self.slots):
            if slot[0] != item_id:
                continue
            take = min(n - removed, slot[1])
            slot[1] -= take
            removed += take
            if slot[1] <= 0:
                self.slots.remove(slot)
            if removed >= n:
                break
        return removed

    def count(self, item_id):
        return sum(s[1] for s in self.slots if s[0] == item_id)

    def has(self, item_id, n=1):
        return self.count(item_id) >= int(n)

    def used(self):
        """已用格数。"""
        return len(self.slots)

    def free(self):
        return max(0, self.capacity - self.used())

    def items(self):
        """``{item_id: n}`` 快照。"""
        out = {}
        for iid, n in self.slots:
            out[iid] = out.get(iid, 0) + n
        return out

    def total_value(self, prices=None):
        """总估值；``prices`` 可给 ``{item_id: 单价}`` 覆盖元数据里的 value。"""
        out = 0.0
        for iid, n in self.slots:
            unit = (prices or {}).get(iid, self.meta.get(iid, {}).get('value', 1.0))
            out += unit * n
        return out

    # ---------------------------------------------------------- 装备
    def equip(self, item_id, slot=None):
        """装备（自动找槽；原装备退回背包）。返回 ``{ok, slot, detail}``。"""
        if not self.has(item_id):
            return dict(ok=False, detail=f'背包里没有 {item_id}')
        slot = slot or self.meta.get(item_id, {}).get('slot')
        if slot is None or slot not in self.equipped:
            return dict(ok=False, detail=f'{item_id} 不可装备（无匹配槽）')
        old = self.equipped[slot]
        if old is not None:
            self.add(old, 1)                      # 换下的退回背包
        self.remove(item_id, 1)
        self.equipped[slot] = item_id
        return dict(ok=True, slot=slot, detail=f'{item_id} -> {slot}')

    def unequip(self, slot):
        """卸下某槽（退回背包）；返回被卸下的 item_id 或 None。"""
        item = self.equipped.get(slot)
        if item is None:
            return None
        if self.add(item, 1) <= 0:                # 背包满 -> 卸不下来
            return None
        self.equipped[slot] = None
        return item

    def equipped_items(self):
        return {s: i for s, i in self.equipped.items() if i is not None}

    def clear(self):
        self.slots = []
        self.equipped = {s: None for s in self.equip_slots}

    # ---------------------------------------------------------- 展示
    def status_text(self):
        eq = ','.join(f'{k}={v}' for k, v in self.equipped.items() if v) or '-'
        return (f'背包 {self.used()}/{self.capacity} 格  '
                f'物品 {len(self.items())} 种  装备 [{eq}]')


__all__ = ['InventoryService']
