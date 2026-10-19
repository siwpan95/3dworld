# -*- coding: utf-8 -*-
"""回放系统：事件流录制 → 浏览/导出 → 确定性自检（纯标准库）。

借鉴记账（铁律 10）
------------------
移植自 ``roleplayer``（MIT，<https://gitee.com/siwpan/roleplayer>，
``troupe/replay.py``，145 行）：``Replay`` 类的浏览面（timeline / narrative /
filter / summary / actions_of）与「事件流 + 动作序列双轨」的设计。

适配（相对原版的改动，均因解除 roleplayer 绑定）
-----------------------------------------------
- **移除** ``from_simulator`` / ``from_save``：两者绑定 roleplayer 的
  ``Simulator`` / ``Event`` / ``save.migrate``，本仓无此依赖；
  构造改走 ``Replay(events, actions, meta)`` 或 ``from_dict``。
- **``verify_determinism`` 通用化**：原版内部 import roleplayer 的
  ``Simulator`` 跑两遍；本版接收**工厂函数** ``run_fn``（无参可调用，
  每次返回一个可深度比较的结果），任何「同种子跑两遍应一致」的场景都能用。
- **``save_replay`` 改收 ``Replay``**：原版收 ``Simulator``；导出格式不变
  （timeline 纯文本，便于 diff / 归档 / 分享）。
- **新增** ``to_dict`` / ``from_dict``：JSON 往返（原版 from_save 的
  json 读取逻辑提炼为通用序列化）。

设计立场（与 roleplayer 原版一致）
--------------------------------
- **双轨记录**：事件流记「世界变成了什么样」，动作序列记「当时谁提议了什么」——
  只有事件流的话，回放能看到结果却看不到玩家/AI 的意图。
- **确定性是回放可信的前提**：``verify_determinism`` 回答「这局能不能精确
  复现」；对 3dworld 而言这是「整数哈希 + 固定步长」立身之本的系统级证明。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Optional

__all__ = ['Replay', 'verify_determinism', 'save_replay']


class Replay:
    """一份档案的可重演视图：事件流 + 动作序列 + 元数据。"""

    def __init__(self, events: Optional[Iterable[Any]] = None,
                 actions: Optional[list] = None,
                 meta: Optional[dict] = None) -> None:
        self.events = list(events or [])
        self.actions = list(actions or [])
        self.meta = dict(meta or {})

    # ------------------------------------------------------------------ #
    #  序列化（JSON 往返）
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        """导出为可 JSON 序列化的 dict（事件需自带 to_dict 或本就是 dict）。"""
        return {
            'events': [e.to_dict() if hasattr(e, 'to_dict') else e
                       for e in self.events],
            'actions': list(self.actions),
            'meta': dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Replay":
        """从 dict 还原（事件保持原样 —— 反序列化策略交给使用方）。"""
        return cls(events=list(data.get('events') or []),
                   actions=list(data.get('actions') or []),
                   meta=dict(data.get('meta') or {}))

    def save_json(self, path: str) -> str:
        """整档存成 JSON（事件/动作/元数据全保留）。"""
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=1)
        return path

    @classmethod
    def load_json(cls, path: str) -> "Replay":
        """从 JSON 读回（``from_dict`` 的文件版）。"""
        with open(path, 'r', encoding='utf-8') as f:
            return cls.from_dict(json.load(f))

    # ------------------------------------------------------------------ #
    #  浏览
    # ------------------------------------------------------------------ #
    def __len__(self) -> int:
        return len(self.events)

    def __iter__(self):
        return iter(self.events)

    def timeline(self) -> list:
        """按 seq 排序的战报行（含时间/类型/文本）。

        事件需带 ``seq`` / ``ts`` / ``type`` / ``text`` 属性（dict 形态则
        用 ``e['seq']`` 等键）—— 与 roleplayer 的 Event 契约一致。
        """
        out = []
        for e in self.events:
            text = _field(e, 'text', '')
            if not text:
                continue
            seq = _field(e, 'seq', '?')
            ts = _field(e, 'ts', '?')
            etype = _field(e, 'type', '?')
            out.append(f'#{seq} [{ts}] {etype}: {text}')
        return out

    def narrative(self) -> list:
        """纯叙述文本（不含时间/类型前缀），适合直接当战报读。"""
        return [_field(e, 'text', '') for e in self.events
                if _field(e, 'text', '')]

    def filter(self, type: str = '', tag: str = '') -> list:
        """按类型 / 标签过滤事件。"""
        out = self.events
        if type:
            out = [e for e in out if _field(e, 'type', None) == type]
        if tag:
            out = [e for e in out
                   if tag in (_field(e, 'tags', None) or [])]
        return out

    def summary(self) -> dict:
        """各类型事件计数 + 动作数（一眼看出这局的重心在哪）。"""
        counts: dict = {}
        for e in self.events:
            etype = _field(e, 'type', '?')
            counts[etype] = counts.get(etype, 0) + 1
        return {'events': len(self.events), 'actions': len(self.actions),
                'by_type': dict(sorted(counts.items(), key=lambda kv: -kv[1])),
                'meta': dict(self.meta)}

    def actions_of(self, actor: str = '') -> list:
        """动作序列（可按提出者过滤）——「当时谁想做什么」。"""
        if not actor:
            return list(self.actions)
        return [a for a in self.actions
                if (a.get('actor') if isinstance(a, dict) else None) == actor]


def _field(e: Any, name: str, default: Any) -> Any:
    """取事件字段：对象属性优先，dict 键兜底（两种事件形态都认）。"""
    if isinstance(e, dict):
        return e.get(name, default)
    return getattr(e, name, default)


# ---------------------------------------------------------------------- #
#  确定性自检
# ---------------------------------------------------------------------- #
def verify_determinism(run_fn: Callable[[], Any], count: int = 2) -> dict:
    """确定性自检：``run_fn`` 跑 ``count`` 遍，结果应**逐项一致**。

    :param run_fn: 无参可调用，每次返回一个可深度比较的结果
                   （如事件 dict 列表、噪声采样列表）。
    :param count:  重复次数（默认 2）。
    :return: ``{"identical", "runs", "first_diff"}`` —— ``first_diff`` 为
             首个不一致项的序号（一致时为 ``None``）。

    典型用法::

        ok = verify_determinism(lambda: [
            simplex_noise(x, y, seed=7) for x in range(16) for y in range(16)])
        assert ok['identical']
    """
    runs = [run_fn() for _ in range(count)]
    first = runs[0]
    identical = all(r == first for r in runs[1:])
    first_diff = None
    if not identical:
        for i, (a, b) in enumerate(zip(runs[0], runs[1])):
            if a != b:
                first_diff = i
                break
        else:
            # 长度不一致但前缀相同
            first_diff = min(len(runs[0]), len(runs[1]))
    return {'identical': identical, 'runs': count, 'first_diff': first_diff}


def save_replay(replay: Replay, path: str) -> str:
    """把回放档的**战报**导出成纯文本（便于 diff / 归档 / 分享）。"""
    lines = replay.timeline()
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines))
        f.write('\n')
    return path
