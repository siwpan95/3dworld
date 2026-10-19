# -*- coding: utf-8 -*-
"""行为树（Behavior Tree, BT）引擎：组合 / 装饰 / 条件 / 动作 + 声明式构造。

借鉴记账（铁律 10）
------------------
移植自 ``zhuangtaiji``（MIT，<https://gitee.com/siwpan/zhuangtaiji>，
``core/behavior_tree.py``，617 行，**纯标准库零依赖**）—— 这是行为树的
**原始项目**（allin-engine 的决策套件即从 zhuangtaiji 并入，借鉴源头认
zhuangtaiji 而非 allin-engine）。借用范围：全部节点类型与调度语义。

适配（相对原版的改动）
---------------------
- **``Timeout`` 加可注入时钟**：原版用 ``time.time()`` 墙钟；本版默认
  ``time.monotonic``（测 elapsed 的正确选择），并支持 ``clock`` 参数注入
  假时钟 —— 超时逻辑因此可无等待地单测（``test_behavior_tree.py`` 的
  ``test_timeout_with_fake_clock`` 钉住）。
- 其余节点类型、``Status`` 语义、``Blackboard``、声明式 ``build_node`` /
  ``build_behavior_tree`` 与原版逐行一致（改导入路径与本文档外无改动）。

核心思想
--------
行为树是「根节点驱动」(root-driven) 的：每收到一次 tick，从根节点向下遍历，
为每个节点求得一个状态（Status）：

    SUCCESS  成功
    FAILURE  失败
    RUNNING  正在执行（下次 tick 会回到该节点继续）
    INVALID  未初始化 / 非法

节点分类
--------
* 组合节点 (Composite)
    - Sequence  : 顺序执行子节点，任一 FAILURE 则整体 FAILURE；全部 SUCCESS 才 SUCCESS。
    - Selector  : 选择执行子节点，任一 SUCCESS 则整体 SUCCESS；全部 FAILURE 才 FAILURE。
    - Parallel  : 并行执行全部子节点，按 success/failure 阈值决定整体结果。
* 装饰节点 (Decorator)：只有 1 个子节点，用于取反 / 重复 / 限流 / 超时等。
* 条件节点 (Condition)：查询 Blackboard，返回 SUCCESS/FAILURE（无副作用）。
* 动作节点 (Action)  ：执行具体逻辑，可返回 SUCCESS/FAILURE/RUNNING。

Blackboard（黑板）
-----------------
所有节点共享的一块键值存储，用于节点间通信（条件写、动作读）。
本实现支持显式的 blackboard 对象，也支持从外部 dict 注入。

数据驱动
--------
节点树可由声明式 spec 以纯声明方式描述，通过 ``build_behavior_tree(spec)``
构造，无需写代码即可组装复杂行为。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


# ==========================================
# 状态定义
# ==========================================
class Status(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    RUNNING = "RUNNING"
    INVALID = "INVALID"

    def __bool__(self) -> bool:  # 便于 if status:
        return self is not Status.FAILURE


# ==========================================
# 黑板
# ==========================================
class Blackboard:
    """节点间共享的键值存储。"""

    def __init__(self, initial: Optional[Dict[str, Any]] = None):
        self._data: Dict[str, Any] = dict(initial or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, data: Dict[str, Any]) -> None:
        self._data.update(data)

    def items(self):
        return self._data.items()

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def snapshot(self) -> Dict[str, Any]:
        return dict(self._data)


# ==========================================
# 节点基类
# ==========================================
class Node:
    def __init__(self, name: str = ""):
        self.name = name or self.__class__.__name__
        self.status: Status = Status.INVALID
        self.parent: Optional["Node"] = None
        self.blackboard: Optional[Blackboard] = None

    def attach(self, blackboard: Blackboard) -> "Node":
        self.blackboard = blackboard
        return self

    # 子类实现
    def tick(self) -> Status:
        raise NotImplementedError

    # 生命周期：进入 / 离开
    def on_enter(self) -> None:
        pass

    def on_exit(self) -> None:
        pass

    def reset(self) -> None:
        self.status = Status.INVALID

    def _children_tick(self, children: List["Node"]) -> List[Status]:
        return [c.tick() for c in children]

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.name!r} {self.status.value}>"


# ==========================================
# 组合节点
# ==========================================
class Composite(Node):
    def __init__(self, name: str = "", children: Optional[List[Node]] = None):
        super().__init__(name)
        self.children: List[Node] = list(children or [])

    def add(self, child: Node) -> "Composite":
        child.parent = self
        self.children.append(child)
        return self

    def reset(self) -> None:
        super().reset()
        for c in self.children:
            c.reset()


class Sequence(Composite):
    """顺序：子节点依次执行，遇到 FAILURE/RUNNING 立即返回。"""

    def tick(self) -> Status:
        for child in self.children:
            s = child.tick()
            if s is not Status.SUCCESS:
                self.status = s
                return s
        self.status = Status.SUCCESS
        return self.status


class Selector(Composite):
    """选择：子节点依次执行，遇到 SUCCESS/RUNNING 立即返回。"""

    def tick(self) -> Status:
        for child in self.children:
            s = child.tick()
            if s is not Status.FAILURE:
                self.status = s
                return s
        self.status = Status.FAILURE
        return self.status


class Parallel(Composite):
    """并行：同时 tick 所有子节点。

    success_threshold: 需要多少个 SUCCESS 才整体 SUCCESS（默认全部）。
    failure_threshold: 需要多少个 FAILURE 才整体 FAILURE（默认 1）。
    """

    def __init__(self, name: str = "", children: Optional[List[Node]] = None,
                 success_threshold: int = None, failure_threshold: int = 1):
        super().__init__(name, children)
        self.success_threshold = success_threshold
        self.failure_threshold = failure_threshold

    def tick(self) -> Status:
        results = self._children_tick(self.children)
        n_success = sum(1 for r in results if r is Status.SUCCESS)
        n_failure = sum(1 for r in results if r is Status.FAILURE)
        n_running = any(r is Status.RUNNING for r in results)

        need_success = self.success_threshold or len(self.children)
        if n_success >= need_success:
            self.status = Status.SUCCESS
        elif n_failure >= self.failure_threshold:
            self.status = Status.FAILURE
        elif n_running:
            self.status = Status.RUNNING
        else:
            self.status = Status.FAILURE
        return self.status


# ==========================================
# 装饰节点
# ==========================================
class Decorator(Node):
    def __init__(self, name: str = "", child: Optional[Node] = None):
        super().__init__(name)
        self.child = child

    def set_child(self, child: Node) -> "Decorator":
        child.parent = self
        self.child = child
        return self

    def reset(self) -> None:
        super().reset()
        if self.child:
            self.child.reset()


class Inverter(Decorator):
    """取反：SUCCESS<->FAILURE，RUNNING 原样返回。"""

    def tick(self) -> Status:
        if not self.child:
            self.status = Status.FAILURE
            return self.status
        s = self.child.tick()
        if s is Status.SUCCESS:
            self.status = Status.FAILURE
        elif s is Status.FAILURE:
            self.status = Status.SUCCESS
        else:
            self.status = s
        return self.status


class Succeeder(Decorator):
    """结果总是 SUCCESS（即使子节点 FAILURE）。"""

    def tick(self) -> Status:
        if self.child:
            self.child.tick()
        self.status = Status.SUCCESS
        return self.status


class Failer(Decorator):
    """结果总是 FAILURE。"""

    def tick(self) -> Status:
        if self.child:
            self.child.tick()
        self.status = Status.FAILURE
        return self.status


class Repeater(Decorator):
    """重复执行子节点 N 次（N<=0 表示无限，依赖外部停止）。"""

    def __init__(self, name: str = "", child: Optional[Node] = None, times: int = 1):
        super().__init__(name, child)
        self.times = times
        self._count = 0

    def tick(self) -> Status:
        if not self.child:
            self.status = Status.FAILURE
            return self.status
        self._count += 1
        s = self.child.tick()
        if self.times > 0 and self._count >= self.times:
            self.status = Status.SUCCESS
        else:
            self.status = Status.RUNNING if s is not Status.FAILURE else Status.SUCCESS
        return self.status

    def reset(self) -> None:
        super().reset()
        self._count = 0


class Timeout(Decorator):
    """超时：子节点在 budget_ms 内未脱离 RUNNING，则整体判 FAILURE。

    ``clock`` 为可注入时钟（默认 ``time.monotonic``）：测试可传假时钟，
    无需真实等待即可验证超时路径（本仓适配，见模块 docstring）。
    """

    def __init__(self, name: str = "", child: Optional[Node] = None,
                 budget_ms: int = 1000,
                 clock: Callable[[], float] = time.monotonic):
        super().__init__(name, child)
        self.budget_ms = budget_ms
        self.clock = clock
        self._start: Optional[float] = None

    def tick(self) -> Status:
        if not self.child:
            self.status = Status.FAILURE
            return self.status
        if self._start is None:
            self._start = self.clock()
        s = self.child.tick()
        if s is Status.RUNNING and \
                (self.clock() - self._start) * 1000 >= self.budget_ms:
            self.status = Status.FAILURE
            return self.status
        if s is not Status.RUNNING:
            self._start = None
        self.status = s
        return self.status

    def reset(self) -> None:
        super().reset()
        self._start = None


class Cooldown(Decorator):
    """冷却：子节点一旦 SUCCESS，随后 ticks 次 tick 强制返回 FAILURE。"""

    def __init__(self, name: str = "", child: Optional[Node] = None,
                 ticks: int = 3):
        super().__init__(name, child)
        self.ticks = ticks
        self._remain = 0

    def tick(self) -> Status:
        if not self.child:
            self.status = Status.FAILURE
            return self.status
        if self._remain > 0:
            self._remain -= 1
            self.status = Status.FAILURE
            return self.status
        s = self.child.tick()
        if s is Status.SUCCESS:
            self._remain = self.ticks
        self.status = s
        return self.status

    def reset(self) -> None:
        super().reset()
        self._remain = 0


class UntilSuccess(Decorator):
    """直到成功：子节点未 SUCCESS 前持续返回 RUNNING，成功后返回 SUCCESS。"""

    def tick(self) -> Status:
        if not self.child:
            self.status = Status.FAILURE
            return self.status
        s = self.child.tick()
        self.status = Status.SUCCESS if s is Status.SUCCESS else Status.RUNNING
        return self.status


class UntilFailure(Decorator):
    """直到失败：子节点未 FAILURE 前持续返回 RUNNING，失败后返回 FAILURE。"""

    def tick(self) -> Status:
        if not self.child:
            self.status = Status.FAILURE
            return self.status
        s = self.child.tick()
        self.status = Status.FAILURE if s is Status.FAILURE else Status.RUNNING
        return self.status


class Service(Node):
    """服务节点：持续返回 RUNNING，直到 predicate 满足才返回 SUCCESS。

    适合建模「长耗时任务直至完成」（如：下载中→下载完成）。
    """

    def __init__(self, name: str = "", predicate: Optional[Callable[[TickContext], bool]] = None):
        super().__init__(name)
        self.predicate = predicate

    def tick(self) -> Status:
        ok = bool(self.predicate(TickContext(blackboard=self.blackboard))) \
            if callable(self.predicate) else False
        self.status = Status.SUCCESS if ok else Status.RUNNING
        return self.status


# ==========================================
# 叶子节点：条件 / 动作
# ==========================================
@dataclass
class TickContext:
    """传递给条件/动作节点的上下文。"""
    blackboard: Blackboard
    params: Dict[str, Any] = field(default_factory=dict)
    tick_count: int = 0
    dt: float = 0.0  # 距上次 tick 的时间间隔（秒），供 timeout 等节点使用


class Condition(Node):
    """条件节点：基于 Blackboard 做判断，返回 SUCCESS / FAILURE。"""

    def __init__(self, name: str = "", predicate: Optional[Callable[[TickContext], bool]] = None):
        super().__init__(name)
        self.predicate = predicate

    def tick(self) -> Status:
        if not self.predicate:
            self.status = Status.FAILURE
            return self.status
        ctx = TickContext(blackboard=self.blackboard, tick_count=0)
        try:
            ok = self.predicate(ctx)
        except Exception:
            ok = False
        self.status = Status.SUCCESS if ok else Status.FAILURE
        return self.status


class Action(Node):
    """动作节点：执行函数，函数返回 Status 或 bool。"""

    def __init__(self, name: str = "",
                 func: Optional[Callable[[TickContext], Any]] = None,
                 running: bool = False):
        super().__init__(name)
        self.func = func
        self.running = running  # 若动作固定为 RUNNING（如持续任务）

    def tick(self) -> Status:
        if self.running:
            if self.func:
                self.func(TickContext(blackboard=self.blackboard))
            self.status = Status.RUNNING
            return self.status
        if not self.func:
            self.status = Status.FAILURE
            return self.status
        try:
            ret = self.func(TickContext(blackboard=self.blackboard))
        except Exception:
            self.status = Status.FAILURE
            return self.status
        if isinstance(ret, Status):
            self.status = ret
        elif ret is True or ret is None:
            self.status = Status.SUCCESS
        elif ret is False:
            self.status = Status.FAILURE
        else:
            self.status = Status.SUCCESS
        return self.status


# ==========================================
# 行为树：根节点 + tick 调度
# ==========================================
class BehaviorTree:
    def __init__(self, root: Node, blackboard: Optional[Blackboard] = None):
        self.root = root
        self.blackboard = blackboard or Blackboard()
        self.root.attach(self.blackboard)
        self._bind(self.root)
        self.tick_count = 0

    def _bind(self, node: Node):
        node.blackboard = self.blackboard
        for child in getattr(node, "children", []):
            self._bind(child)
        if getattr(node, "child", None):
            self._bind(node.child)

    def tick(self) -> Status:
        self.tick_count += 1
        self.status = self.root.tick()
        return self.status

    def reset(self):
        self.root.reset()
        self.tick_count = 0

    def run(self, max_ticks: int = 1000):
        """持续 tick 直到没有节点处于 RUNNING 或达到上限。"""
        for _ in range(max_ticks):
            status = self.tick()
            if status is not Status.RUNNING:
                return status
        return Status.RUNNING


# ==========================================
# 数据驱动构造（声明式）
# ==========================================
# 内置节点类型表
_NODE_TYPES = {
    "sequence": Sequence,
    "selector": Selector,
    "parallel": Parallel,
    "inverter": Inverter,
    "succeeder": Succeeder,
    "failer": Failer,
    "repeater": Repeater,
    "timeout": Timeout,
    "cooldown": Cooldown,
    "until_success": UntilSuccess,
    "until_failure": UntilFailure,
    "service": Service,
    "condition": Condition,
    "action": Action,
}


def _build_condition(key: str, op: str = "exists", value: Any = None):
    """根据声明式条件构造查询函数。"""
    ops = {
        "exists": lambda bb, k, v: k in bb,
        "not_exists": lambda bb, k, v: k not in bb,
        "eq": lambda bb, k, v: bb.get(k) == v,
        "ne": lambda bb, k, v: bb.get(k) != v,
        "gt": lambda bb, k, v: bb.get(k, -1e18) > v,
        "lt": lambda bb, k, v: bb.get(k, 1e18) < v,
        "ge": lambda bb, k, v: bb.get(k, -1e18) >= v,
        "le": lambda bb, k, v: bb.get(k, 1e18) <= v,
        "in": lambda bb, k, v: bb.get(k) in (v or []),
        "truthy": lambda bb, k, v: bool(bb.get(k)),
    }

    def predicate(ctx: TickContext) -> bool:
        fn = ops.get(op, ops["exists"])
        return bool(fn(ctx.blackboard, key, value))

    return predicate


def _build_action(action_spec: Dict[str, Any]):
    """根据声明式动作构造执行函数。"""
    kind = action_spec.get("kind", "log")

    def func(ctx: TickContext):
        bb = ctx.blackboard
        if kind == "set":
            bb.set(action_spec["key"], action_spec.get("value"))
        elif kind == "increment":
            cur = bb.get(action_spec["key"], 0)
            bb.set(action_spec["key"], cur + action_spec.get("by", 1))
        elif kind == "log":
            # 简单日志（可被外部钩子捕获）
            msg = action_spec.get("msg", f"action:{action_spec.get('name','')}")
            if callable(action_spec.get("_log")):
                action_spec["_log"](msg)
        # 默认 SUCCESS
        return Status.SUCCESS

    return func


def build_node(spec: Dict[str, Any]) -> Node:
    """由单个声明式节点构造 Node 对象。"""
    ntype = spec.get("type", "action")
    name = spec.get("name", ntype)
    node_cls = _NODE_TYPES.get(ntype)
    if node_cls is None:
        raise ValueError(f"未知行为树节点类型: {ntype}")

    if ntype in ("sequence", "selector", "parallel"):
        node = node_cls(name)
        for child_spec in spec.get("children", []):
            node.add(build_node(child_spec))
        return node

    if ntype in ("inverter", "succeeder", "failer", "repeater",
                 "timeout", "cooldown", "until_success", "until_failure"):
        if ntype == "repeater":
            node = Repeater(name, times=spec.get("times", 1))
        elif ntype == "timeout":
            node = Timeout(name, budget_ms=spec.get("budget_ms", 1000))
        elif ntype == "cooldown":
            node = Cooldown(name, ticks=spec.get("ticks", 3))
        elif ntype == "until_success":
            node = UntilSuccess(name)
        elif ntype == "until_failure":
            node = UntilFailure(name)
        else:
            node = node_cls(name)
        child_spec = spec.get("child")
        if child_spec:
            node.set_child(build_node(child_spec))
        return node

    if ntype == "service":
        pred = _build_condition(
            spec.get("key", ""),
            op=spec.get("op", "exists"),
            value=spec.get("value"),
        )
        return Service(name, predicate=pred)

    if ntype == "condition":
        pred = _build_condition(
            spec.get("key", ""),
            op=spec.get("op", "exists"),
            value=spec.get("value"),
        )
        return Condition(name, predicate=pred)

    if ntype == "action":
        func = _build_action(spec)
        return Action(name, func=func, running=spec.get("running", False))

    raise ValueError(f"无法构造节点: {spec}")


def build_behavior_tree(spec: Dict[str, Any],
                        blackboard: Optional[Blackboard] = None) -> BehaviorTree:
    """由声明式规格构造 BehaviorTree。

    spec 形如：
        {
          "name": "故障自愈",
          "root": { "type": "selector", "children": [ ... ] }
        }
    """
    root_spec = spec.get("root")
    if not root_spec:
        raise ValueError("行为树规格缺少 root")
    root = build_node(root_spec)
    return BehaviorTree(root, blackboard=blackboard)


__all__ = [
    'Status', 'Blackboard', 'Node', 'Composite', 'Sequence', 'Selector',
    'Parallel', 'Decorator', 'Inverter', 'Succeeder', 'Failer', 'Repeater',
    'Timeout', 'Cooldown', 'UntilSuccess', 'UntilFailure', 'Service',
    'TickContext', 'Condition', 'Action', 'BehaviorTree',
    'build_node', 'build_behavior_tree',
]
