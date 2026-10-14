# -*- coding: utf-8 -*-
"""同步确定性事件总线 + 声明式 ``when → then`` 级联。

**P0-5 自研最小实现**：`experiments/zhuangtaiji_orchestration/run_director.py` 曾用外部仓库
zhuangtaiji 的编排层（事件总线 + 声明式绑定 ``when→then``），把两个独立范式连成演出系统：

    状态机（演出阶段）  --enter-->  级联推进一步马尔可夫链（余烬闪烁模式）
    「同一时间轴，序列随机」—— 演出变体的最简实现

那个试点需要环境变量指向外部源码、本机已离线，无法复现；按「**吸收能力、自研最小实现、
不引依赖**」处理，本模块即其替代。

设计要点：

* **同步、确定性**：``emit`` 立即回调，回调**按注册顺序**执行 —— 同一事件序列 → 同一结果，
  可断言、可复现（这是本项目的硬要求：`--seeds` 数字必须逐字不变）；
* **级联**：``bind(when, then)`` 声明"when 发生后自动 emit then"，支持多级；
  为防止环导致无界递归，``emit`` 带**深度上限**并在超限时报错（不静默丢弃）；
* **"随机变体"怎么做**：不在总线里藏随机数，而是让订阅者用一个**显式种子**的随机源
  （`core/_rng.Rng`）—— 这样"随机变体"依旧可复现；
* **可观测**：``emit`` 返回本次**连带产生**的事件元组（含首事件），便于断言级联是否发生。

典型用法::

    bus = Bus()
    bus.on('scene_ready', lambda e: print('ready'))
    bus.bind('state_enter', 'ember_step')          # 状态机进入 → 推进一步余烬
    bus.emit('state_enter', state='orbit')         # -> ('state_enter', 'ember_step')
"""
from __future__ import annotations

__all__ = ['Bus', 'Event']

#: 事件名 + 载荷
Event = tuple           # (name, payload_dict)


class Bus:
    """事件总线：订阅 + 声明式级联 + 有界递归。"""

    MAX_DEPTH = 32      # 级联深度上限（防环；超限报错而不是静默截断）

    def __init__(self, name: str = 'bus'):
        self.name = name
        self._subs = {}          # event -> [fn, ...]（按注册顺序）
        self._cascade = {}       # event -> [then_event, ...]
        self._log = []           # [(depth, event_name)]

    # ---------------------------------------------------------------- 声明
    def on(self, event: str, fn) -> 'Bus':
        """订阅事件（回调签名 ``fn(event_name, payload)``）。"""
        assert callable(fn), 'on() 需要可调用对象'
        self._subs.setdefault(event, []).append(fn)
        return self

    def bind(self, when: str, then: str) -> 'Bus':
        """声明式级联：``when`` 事件处理完后自动 emit ``then``。"""
        self._cascade.setdefault(when, []).append(then)
        return self

    # ---------------------------------------------------------------- 查询
    @property
    def log(self):
        """已分发事件（含级联），按发生顺序。"""
        return tuple(self._log)

    def events(self):
        """已订阅或已声明级联的事件名（升序）。"""
        return tuple(sorted(set(self._subs) | set(self._cascade)))

    def subscribers(self, event: str):
        return tuple(self._subs.get(event, ()))

    # ---------------------------------------------------------------- 驱动
    def emit(self, event: str, **payload):
        """分发事件；返回本次连带产生的事件元组（含首事件本身）。

        级联在**同一 depth 层结束后**推进（保证"先跑完 when 的所有订阅者，再 emit then"），
        这与"声明式编排"的直觉一致，也让回调顺序可预测。
        """
        out = []
        self._dispatch(event, dict(payload), 0, out)
        return tuple(out)

    def _dispatch(self, event, payload, depth, out):
        if depth > self.MAX_DEPTH:
            raise RecursionError(
                f'{self.name}: 事件级联超过 {self.MAX_DEPTH} 层（{event!r}）——'
                f'声明里可能有环（如 a→b→a）。已分发：{[e for _, e in self._log[-6:]]}')
        self._log.append((depth, event))
        out.append((event, payload))
        for fn in self._subs.get(event, ()):
            fn(event, payload)
        for then in self._cascade.get(event, ()):
            self._dispatch(then, {k: v for k, v in payload.items()}, depth + 1, out)

    def __repr__(self):
        return (f'Bus({self.name!r}, events={len(self.events())}, '
                f'cascades={sum(len(v) for v in self._cascade.values())})')
