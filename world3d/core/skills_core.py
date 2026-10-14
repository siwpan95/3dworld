# -*- coding: utf-8 -*-
"""技能树共同核 ``skills``（实现 ``world3d.core.contracts.SkillsCore``）。

P1-⑤ 玩法通用模块之二。**前置依赖图 + 等级上限 + 点数 + 属性面板**。
`effects()` 汇总"技能 + 装备"的被动加成，供战斗/玩法核读取。

> **零漂移约定**：未学任何技能、未装备任何物品时，`effects()` 返回空 dict、
> `stat(name)` 等于基础值 —— 接入方（如 `storm`）在无人学技能时**行为完全不变**。
> 参考 bullet-zombie（MIT）的模块划分思路，实现自写。
"""
from __future__ import annotations

from world3d.core._cordis import Service


class SkillsService(Service):
    """技能树 + 属性面板。

    Args:
        points: 初始技能点。
        base_stats: 基础属性（如 ``{'dmg': 1, 'fire_cd': 0.42}``）。
    """

    def __init__(self, ctx, config):
        super().__init__(ctx, "skills")
        cfg = config or {}
        self.points = int(cfg.get('points', 0))
        self.base_stats = dict(cfg.get('base_stats', {}))
        self.tree = {}                        # id -> {name,max,requires,effects,cost}
        self.levels = {}                      # id -> 已学等级
        self.spent = 0

    # ---------------------------------------------------------- 定义
    def define(self, node_id, name='', max_level=1, requires=(), effects=None,
               cost=1):
        """定义一个技能节点。``effects`` 为 ``{'stat': 每级增量}``。"""
        self.tree[node_id] = dict(name=name or node_id, max=int(max_level),
                                  requires=tuple(requires),
                                  effects=dict(effects or {}),
                                  cost=int(cost))
        return node_id

    def define_many(self, spec):
        """批量：``{id: {...}}`` 或 ``[(id, kwargs), ...]``。"""
        it = spec.items() if isinstance(spec, dict) else spec
        for nid, kw in it:
            self.define(nid, **(kw or {}))
        return len(self.tree)

    # ---------------------------------------------------------- 查询
    def level(self, node_id):
        return int(self.levels.get(node_id, 0))

    def has(self, node_id):
        return self.level(node_id) > 0

    def can_learn(self, node_id, n=1):
        """``(ok, reason)``——逐条给出**具体**原因（便于 UI/测试断言）。"""
        node = self.tree.get(node_id)
        if node is None:
            return False, f'无此技能: {node_id}'
        if self.level(node_id) + int(n) > node['max']:
            return False, f'{node_id} 已达上限（{node["max"]}）'
        for req in node['requires']:
            if self.level(req) <= 0:
                return False, f'前置未满足: {req}'
        need = node['cost'] * int(n)
        if self.points < need:
            return False, f'点数不足（需 {need}，有 {self.points}）'
        return True, ''

    # ---------------------------------------------------------- 学习
    def learn(self, node_id, n=1):
        """学 ``n`` 级；返回 ``{ok, level, detail}``。"""
        n = int(n)
        if n <= 0:
            return dict(ok=False, detail='n 必须为正')
        ok, why = self.can_learn(node_id, n)
        if not ok:
            return dict(ok=False, detail=why)
        self.levels[node_id] = self.level(node_id) + n
        # 逐级消费点数（cost 可能随级变化，这里按节点的固定 cost 计）
        cost = self.tree[node_id]['cost'] * n
        self.points -= cost
        self.spent += cost
        return dict(ok=True, level=self.levels[node_id],
                    detail=f'{node_id} -> Lv{self.levels[node_id]}（-{cost} 点）')

    def add_points(self, n=1):
        self.points += int(n)
        return self.points

    def respec(self):
        """洗点：清空所有技能并**全额退点**。返回退还点数。"""
        back = self.spent
        self.levels = {}
        self.spent = 0
        self.points += back
        return back

    # ---------------------------------------------------------- 属性面板
    def effects(self, extra=None):
        """被动加成汇总：``{stat: 总增量}``（技能 + 可选 ``extra`` 来源如装备）。

        技能贡献 = Σ(节点 effects[stat] × 该节点等级)。
        """
        out = {}
        for nid, lv in self.levels.items():
            if lv <= 0:
                continue
            for stat, per in self.tree[nid]['effects'].items():
                out[stat] = out.get(stat, 0.0) + per * lv
        for stat, v in (extra or {}).items():
            out[stat] = out.get(stat, 0.0) + v
        return out

    def stat(self, name, extra=None):
        """某属性的**最终值** = 基础值 + 被动加成。未定义基础值时以 0 起算。"""
        return self.base_stats.get(name, 0.0) + self.effects(extra).get(name, 0.0)

    def is_neutral(self, extra=None):
        """是否处于"零加成"状态（未学技能且无额外来源）——接入方用它走**原路径**。"""
        return not self.levels and not (extra or {})

    def status_text(self):
        return (f'技能点 {self.points}（已花 {self.spent}）  '
                f'已学 {len([v for v in self.levels.values() if v > 0])}/{len(self.tree)}  '
                f'加成 {self.effects() or "-"}')


__all__ = ['SkillsService']
