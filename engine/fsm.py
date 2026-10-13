# -*- coding: utf-8 -*-
"""确定性有限状态机（FSM）—— 演出 / 玩法通用。

**P0-5 自研最小实现**：`experiments/allin_decision/perform_director.py` 曾用外部仓库
allin_engine 的决策模块，把 kkrieger 第六幕的镜头编排重表达成状态机
（状态 = 演出阶段：传送/下降/环绕/俯冲/贴脸/归位；事件 = 时间轴触发；动作 = 机位关键帧）。
那个试点需要环境变量指向外部源码且本机已离线，无法复现——按「**吸收能力、自研最小实现、
不引依赖**」处理，本模块即其替代。

设计要点：

* **两种驱动方式并存**，对应两种真实用法：
  - ``fire(event)``：**事件驱动**（交互、玩法事件、随机演出）；
  - ``at(t)``：**时间轴驱动**（按状态的时间窗口自动进出），与 `engine/timeline.py`
    的时间驱动演出合用，不需要把每个时刻编成事件；
* **进入/退出钩子**：``on_enter`` / ``on_exit`` 用于挂"切幕时做点什么"；
* **确定性**：同事件/时间序列 → 同轨迹（无随机）。要"随机变体"请显式用
  `core/_rng.Rng` 喂种子后决定事件，而不是在这里藏随机数（可复现是硬要求）；
* **可观测**：``current`` 与 ``history``（``(t, 事件, 源状态, 目标状态)``），便于断言与调试。

典型用法::

    m = Machine('act6')
    m.state('teleport', time=(118.2, 121.5))
    m.state('orbit',    time=(121.5, 136.5))
    m.state('face_off', time=(136.5, 141.8))
    m.at(130.0)            # -> 'orbit'（自动走完 teleport→orbit 的进出钩子）
    m.transition('player_interrupt', 'orbit', 'return')
    m.fire('player_interrupt')   # -> 'return'
"""
from __future__ import annotations

__all__ = ['Machine', 'machine_from_keys']


class Machine:
    """状态 + 事件转移 + 时间窗口的确定性状态机。"""

    def __init__(self, name: str = 'fsm'):
        self.name = name
        self._states = {}                # name -> {'on_enter','on_exit','time'}
        self._order = []                 # 状态声明顺序（时间窗口未覆盖时用于 at()）
        self._trans = {}                 # event -> {src(effective) -> dst}
        self._current = None
        self._history = []               # [(t, event, src, dst)]

    # ---------------------------------------------------------------- 声明
    def state(self, name: str, time=None, on_enter=None, on_exit=None) -> 'Machine':
        """声明一个状态。

        ``time``：可选时间窗口 ``(t0, t1)``，或**多个窗口** ``[(t0,t1), ...]``。
        为什么允许"多个窗口"：同一状态在时间轴上可能出现**非相邻**的多段
        （`machine_from_keys` 在 `a→b→a` 这种表上就会产生），若把两段并成一个窗口，
        ``at()`` 会在中间的 b 段里错判成 a。
        """
        if time is None:
            wins = ()
        elif (len(time) == 2 and all(isinstance(x, (int, float)) for x in time)):
            wins = (tuple(float(x) for x in time),)
        else:
            wins = tuple(tuple(float(x) for x in w) for w in time)
        for w in wins:
            assert len(w) == 2 and w[0] <= w[1], f'{name}: 时间窗需为递增的 (t0,t1)，得到 {w}'
        self._states[name] = dict(on_enter=on_enter, on_exit=on_exit, windows=wins)
        self._order.append(name)
        return self

    def transition(self, event: str, src, dst: str) -> 'Machine':
        """声明转移。``src`` 可为状态名、状态名元组，或 ``'*'``（任意状态）。"""
        assert dst in self._states, f'目标状态未声明：{dst}'
        if src != '*':
            for s in ((src,) if isinstance(src, str) else tuple(src)):
                assert s in self._states, f'源状态未声明：{s}'
        self._trans.setdefault(event, {})[src if src == '*' else tuple(
            (src,) if isinstance(src, str) else src)] = dst
        return self

    # ---------------------------------------------------------------- 查询
    @property
    def current(self):
        return self._current

    @property
    def history(self):
        return tuple(self._history)

    @property
    def states(self):
        return tuple(self._order)

    def events(self):
        """已声明的事件名（升序）。"""
        return tuple(sorted(self._trans))

    # ---------------------------------------------------------------- 驱动
    def _enter(self, dst, t, event):
        """切到 ``dst``：先退出旧状态、再进入新状态，并记轨迹。"""
        src = self._current
        if src == dst:
            return dst
        if src is not None:
            hook = self._states[src]['on_exit']
            if hook:
                hook(src, dst, t)
        self._current = dst
        hook = self._states[dst]['on_enter']
        if hook:
            hook(src, dst, t)
        self._history.append((t, event, src, dst))
        return dst

    def fire(self, event: str, t=0.0) -> str:
        """按事件转移；返回新的当前状态。无匹配转移时**保持原状态**并记轨迹。"""
        table = self._trans.get(event)
        if table is None:
            raise KeyError(f'{self.name}: 未声明的事件 {event!r}（已声明：{self.events()}）')
        dst = None
        cur = self._current
        if cur is not None:
            for src, d in table.items():
                if src != '*' and cur in src:
                    dst = d
                    break
        if dst is None:
            dst = table.get('*')
        if dst is None:
            self._history.append((t, event, cur, cur))    # 无转移：显式留痕
            return cur
        return self._enter(dst, t, event)

    def at(self, t: float) -> str:
        """按时间窗口推进到 ``t`` 对应的状态（无窗口覆盖则保持当前状态）。"""
        t = float(t)
        hit = None
        for name in self._order:
            for w in self._states[name]['windows']:
                if w[0] <= t < w[1]:
                    hit = name
                    break
            if hit is not None:
                break
        if hit is None:
            return self._current
        return self._enter(hit, t, f'at({t:g})')

    def windows(self, name: str):
        """某状态的时间窗口（供测试/工具查看）。"""
        return self._states[name]['windows']

    def __repr__(self):
        return (f'Machine({self.name!r}, states={len(self._states)}, '
                f'events={len(self._trans)}, current={self._current!r})')


def machine_from_keys(keys, name: str = 'shot') -> Machine:
    """由 ``[(t, state_name), ...]`` 关键帧表建时间轴驱动的状态机。

    相邻两个时刻构成前一个状态的一个时间窗口（末段右端为 ``+inf``）：
    这正是"导演台/演出脚本"最顺手的表达（`experiments/allin_decision` 试点里的
    ``KEYS = [(118.2, (...), (...), 'teleport'), ...]`` 同形）。

    同一状态若出现**非相邻**的段（如 ``a→b→a``），会得到**两个窗口**而不是一个
    大窗口 —— 否则中间的 b 段会被 ``at()`` 错判成 a。
    """
    keys = sorted((float(t), str(s)) for t, s in keys)
    assert keys, 'keys 不能为空'
    INF = float('inf')
    wins = {}                                   # state -> [(t0, t1), ...]
    for i, (t, s) in enumerate(keys):
        t1 = keys[i + 1][0] if i + 1 < len(keys) else INF
        wins.setdefault(s, []).append((t, t1))
    m = Machine(name)
    for t, s in keys:                           # 按首次出现顺序声明（保持可读）
        if s in m._states:
            continue
        m.state(s, time=wins[s])
    return m
