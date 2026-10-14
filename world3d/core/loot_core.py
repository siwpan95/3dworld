# -*- coding: utf-8 -*-
"""掉落共同核 ``loot``（实现 ``world3d.core.contracts.LootCore``）。

P1-⑤ 玩法通用模块之三。**权重表 + 保底(pity) + 词缀**，全部**确定性**：
同 ``(seed, table, roll_index)`` 必得同结果（沿用本项目"种子可复现"的全局惯例，
便于 headless 验收与录制锁定）。

> 参考 bullet-zombie（MIT）的模块划分思路（掉落独立成表 + 保底计数）；实现自写。
"""
from __future__ import annotations

from world3d.core._cordis import Service
from world3d.core._rng import Rng                          # 复用同一确定性 LCG


class LootService(Service):
    """掉落表服务。

    Args:
        seed: 基准种子（每次 ``roll`` 用 ``seed/表名/序号`` 派生子种子）。
        pity_every: 连续 N 次未出"稀有"（``rarity='rare'``）则**保底**强制出一个。
    """

    def __init__(self, ctx, config):
        super().__init__(ctx, "loot")
        cfg = config or {}
        self.seed = int(cfg.get('seed', 20261012))
        self.pity_every = int(cfg.get('pity_every', 8))
        self.tables = {}                      # name -> {'entries':[...], 'rolls':int}
        self.pity = {}                        # name -> 连续未出稀有次数
        self.affixes = {}                     # kind -> [{'name','effects','weight'}]
        self.roll_count = {}                  # name -> 已 roll 次数（种子一部分）

    # ---------------------------------------------------------- 定义
    def table(self, name, entries, rolls=1):
        """定义掉落表：``entries`` 为 ``[(item_id, weight, n_min, n_max, rarity)]``。"""
        norm = []
        for e in entries:
            iid, w = e[0], float(e[1])
            nmin = int(e[2]) if len(e) > 2 else 1
            nmax = int(e[3]) if len(e) > 3 else nmin
            rarity = e[4] if len(e) > 4 else 'common'
            norm.append((iid, w, nmin, nmax, rarity))
        self.tables[name] = dict(entries=norm, rolls=int(rolls))
        self.pity.setdefault(name, 0)
        self.roll_count.setdefault(name, 0)
        return name

    def affix_table(self, kind, entries):
        """定义词缀表：``entries`` 为 ``[(name, weight, effects_dict), ...]``。"""
        self.affixes[kind] = [(n, float(w), dict(fx)) for n, w, fx in entries]
        return kind

    # ---------------------------------------------------------- 抽取
    def _pick(self, entries, r, forced_rarity=None):
        """按权重抽一条（``forced_rarity`` 时只在同类里抽）。"""
        pool = [e for e in entries if forced_rarity is None or e[4] == forced_rarity]
        if not pool:
            pool = entries
        total = sum(e[1] for e in pool)
        if total <= 0:
            return pool[0] if pool else None
        x = r.next_f() * total
        acc = 0.0
        for e in pool:
            acc += e[1]
            if x <= acc:
                return e
        return pool[-1]

    def roll(self, table, count=1, luck=0.0):
        """抽 ``count`` 次；返回 ``[(item_id, n, rarity), ...]``（**确定性**）。

        ``luck > 0`` 把稀有项的权重放大 ``(1+luck)``（仅影响本次，不改表）。
        """
        t = self.tables.get(table)
        if t is None:
            return []
        out = []
        for _ in range(int(count)):
            idx = self.roll_count[table]
            self.roll_count[table] = idx + 1
            r = Rng(f'{self.seed}/{table}/{idx}')
            entries = t['entries']
            if luck > 0.0:
                entries = [(i, w * (1.0 + luck) if rar == 'rare' else w, a, b, rar)
                           for (i, w, a, b, rar) in entries]
            force = None
            if self.pity_every > 0 and self.pity.get(table, 0) + 1 >= self.pity_every:
                force = 'rare'                       # 保底：本次强制稀有
            e = self._pick(entries, r, forced_rarity=force)
            if e is None:
                continue
            iid, _w, nmin, nmax, rarity = e
            n = nmin if nmax <= nmin else nmin + int(r.next_f() * (nmax - nmin + 1))
            n = max(nmin, min(nmax, n))
            out.append((iid, n, rarity))
            self.pity[table] = 0 if rarity == 'rare' else self.pity.get(table, 0) + 1
        return out

    def drop_from(self, table, kind, count=1):
        """抽一次并（若有词缀表）给物品挂词缀。

        Returns: ``[(item_id, n, rarity, affixes)]``——``affixes`` 为 ``{name: effects}``。
        """
        base = self.roll(table, count)
        out = []
        for i, (iid, n, rarity) in enumerate(base):
            aff = {}
            if kind in self.affixes and self.affixes[kind]:
                r = Rng(f'{self.seed}/{table}/affix/{self.roll_count[table] - len(base) + i}')
                pool = self.affixes[kind]
                total = sum(w for _n, w, _fx in pool)
                x = r.next_f() * total
                acc = 0.0
                for name, w, fx in pool:
                    acc += w
                    if x <= acc:
                        aff = {name: dict(fx)}
                        break
            out.append((iid, n, rarity, aff))
        return out

    def expected_value(self, table, prices):
        """期望收益：Σ(权重占比 × 数量均值 × 单价)。"""
        t = self.tables.get(table)
        if not t:
            return 0.0
        total = sum(e[1] for e in t['entries']) or 1.0
        out = 0.0
        for iid, w, nmin, nmax, _rar in t['entries']:
            avg_n = (nmin + nmax) / 2.0
            out += (w / total) * avg_n * prices.get(iid, 1.0)
        return out

    def status_text(self):
        return (f'掉落表 {len(self.tables)} 张  词缀表 {len(self.affixes)} 类  '
                f'保底 {self.pity_every} 次  已 roll '
                f'{sum(self.roll_count.values())}')


__all__ = ['LootService']
