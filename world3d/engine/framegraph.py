# -*- coding: utf-8 -*-
"""帧图（Frame Graph，P3）—— 把"这一帧要跑哪些 pass"变成**可校验、可编译**的数据。

## 为什么引擎化到最后一定要有它

④ 轨道攒下四个效果（CSM / 体积雾 / 景深 / 运动模糊），它们**互相共用资源**：
三者都用同一张"线性距离"图，阴影图又给体积雾的单次散射查。于是"谁先谁后"
不再是可以随手写的 —— 顺序错了会静默出错：

* 雾必须在**景深之前**（物理上雾也该被虚化），景深必须在**运动模糊之前**（拖影要拖糊了的像）；
* 距离图必须在**所有用它的 pass 之前**产出；
* 关掉某个效果时，它的 pass 应当**整条被剔除**，而不是"跑一遍但不用结果"。

这些约束写在注释里迟早会漂。本模块把它们变成**数据结构 + 编译期校验**：
你声明"每个 pass 读什么、写什么"，`compile()` 负责

1. **校验**：有没有读一个没人写的资源？有没有环？
2. **拓扑排序**：得到一个可行的执行顺序（同层按声明顺序，保证确定性）；
3. **死 pass 剔除**：不参与最终输出的 pass 全砍掉（关掉一个效果的**收益是真的**）；
4. **瞬时资源别名**：生命周期不重叠的临时纹理可以**共用同一块内存**（帧图的经典收益）。

全部是纯逻辑，**不需要 GL 就能断言** —— 这一层的好处和 ④ 那四个模块一样：
把"会写错的部分"抽出来，让它可测。

## v2（P0-3）：生命周期、别名组、跨帧槽位池、峰值

v1 只回答"哪些资源**可以**别名"，但没给出"别名到哪个物理槽位""这一帧峰值多少
字节""跨帧怎么复用"。v2 把这些补成**可断言的数据结构**（仍然纯逻辑，不碰 GL）：

* `Resource.lifetime`（`transient` / `persistent` / `external`）在声明时确定；
  `first_use` / `last_use` 由 `compile()` 从**剔除+重定向之后**的 pass 读写推导；
* `FramePlan.aliases` 仍是"可共用槽位的资源组"（v1 兼容），新增
  `FramePlan.alias_group`（资源名 → 组号）与 `FramePlan.pool_slot`（资源名 → 槽位号）；
* `SlotPool` 按 `(fmt, width, height, samples)` 分桶跨帧复用槽位；
  `FrameGraph.release_plan(plan)` 归还 —— 只做分配策略，**不创建任何 GL 对象**；
* `FramePlan.peak_slots` / `peak_bytes`：本帧瞬时资源的**占用上界**（块数 / 字节数，
  保守地不做别名折扣）。剔除 pass 只会让它下降 —— "关掉效果后资源不泄漏"的断言口径；
  估算函数 `estimate_bytes()` 单独可测（未知格式直接报错，不猜）。

一条重要取舍：v2 的别名**只允许描述符完全相同的资源**共用槽位
（`fmt` / `size` / `samples` 一致）。v1 会仅凭生命周期把 `r32f` 与 `rgba8` 放进同一组，
那在真实后端上根本分配不出来 —— 纯分析可以含糊，落到槽位池就必须诚实。

**合规**：帧图是实时渲染的通用工程做法（Frostbite / FrameGraph 系列文章都讲过）。
**独立实现**，不复制任何具体仓库代码。
"""
from __future__ import annotations

import re

__all__ = ['FrameGraphError', 'Resource', 'Pass', 'FramePlan', 'FrameGraph',
           'SlotPool', 'standard_3d_plan', 'SEQUENCE_OF_DEPTH_EFFECTS',
           'estimate_bytes', 'resolve_size', 'LIFETIMES']


class FrameGraphError(ValueError):
    """帧图声明或编译期的问题（读不到生产者 / 有环 / 输出没声明 …）。"""


#: **深度相关三个效果的物理顺序**（这一条是有物理依据的，不是偏好）：
#: 雾是"光在介质里被散射"⇒ 它产生的像**也要被虚化**，所以雾在景深之前；
#: 运动模糊是对**最终成像**的曝光积分 ⇒ 它必须在最后（拖影要拖"已经虚化的像"）。
SEQUENCE_OF_DEPTH_EFFECTS = ('fog', 'dof', 'motionblur')

#: 资源生命周期三态（v2）。`persistent` / `external` 不参与帧内槽位与峰值统计：
#: 前者跨帧常驻、后者由引擎提供，二者都不是"这一帧临时要的那块内存"。
LIFETIMES = ('transient', 'persistent', 'external')

#: 未显式给分辨率时的默认值（`size='screen'` 的估算基准）。
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080

#: 常见像素格式的**每像素字节数**。解析得出来的格式走正则，解析不出来的直接报错
#: （宁可编译期红，也不要按一个猜出来的数字算峰值 —— 那会让"资源不泄漏"的断言变成假绿）。
_PIXEL_BYTES = {
    'r8': 1, 'rg8': 2, 'rgb8': 3, 'rgba8': 4, 'srgba8': 4,
    'r16f': 2, 'rg16f': 4, 'rgb16f': 6, 'rgba16f': 8,
    'r32f': 4, 'rg32f': 8, 'rgb32f': 12, 'rgba32f': 16,
    'r11g11b10f': 4, 'rgb10a2': 4,
    'depth16': 2, 'depth24': 4, 'depth32f': 4, 'depth': 4,
}

#: 缓冲类资源的字节数（`kind='buffer'`；与像素无关）。
_BUFFER_BYTES = {'mat4': 64, 'mat3': 36, 'mat2': 16,
                 'vec4': 16, 'vec3': 12, 'vec2': 8,
                 'float': 4, 'int': 4, 'uint': 4}

_PIXEL_FMT_RE = re.compile(r'^(?:srgb)?([rgab]{1,4})(\d+)(f?)$')


def _bytes_per_pixel(fmt) -> int:
    key = str(fmt or '').lower()
    if key in _PIXEL_BYTES:
        return _PIXEL_BYTES[key]
    m = _PIXEL_FMT_RE.match(key)
    if m:
        channels, bits = len(m.group(1)), int(m.group(2))
        if bits % 8 == 0 and bits > 0:
            return channels * (bits // 8)
    raise FrameGraphError(
        f'无法估算像素格式 {fmt!r} 的字节数 —— 请在 framegraph._PIXEL_BYTES 里补一条，'
        f'不要靠猜（峰值统计要用来断言"关掉 pass 后资源不泄漏"）')


def estimate_bytes(fmt, width, height, samples=1) -> int:
    """一张 ``width × height``、格式 ``fmt`` 的纹理占多少字节（**纯估算，不开 GL**）。

    估算规则：每像素字节数 × 像素数 × 采样数（``samples<=1`` 时按 1 计）。
    MSAA 的显存开销按"像素 × 采样数"线性放大 —— 与常见后端一致到量级。
    """
    w, h = int(width), int(height)
    if w <= 0 or h <= 0:
        raise FrameGraphError(f'尺寸必须为正：{width!r} × {height!r}')
    s = max(1, int(samples or 1))
    return _bytes_per_pixel(fmt) * w * h * s


def resolve_size(size, width, height):
    """把 ``'screen'`` / ``'half'`` / ``(w, h)`` 解析成真实像素尺寸。"""
    if isinstance(size, str):
        if size == 'screen':
            return int(width), int(height)
        if size == 'half':
            return max(1, int(width) // 2), max(1, int(height) // 2)
        raise FrameGraphError(f'未知的尺寸别名 {size!r}（只认 "screen" / "half" / (w, h)）')
    try:
        w, h = size
    except (TypeError, ValueError):
        raise FrameGraphError(f'尺寸必须是 "screen" / "half" / (w, h)，实际 {size!r}')
    if int(w) <= 0 or int(h) <= 0:
        raise FrameGraphError(f'尺寸必须为正：{size!r}')
    return int(w), int(h)


class Resource:
    """帧图里的一块资源（纹理 / 缓冲）。

    ``lifetime`` 三态（v2）：

    * ``'transient'``：本帧内的临时资源 —— 编译后才有 ``first_use`` / ``last_use``，
      也只有它参与别名与槽位池；
    * ``'persistent'``：跨帧常驻（历史缓冲、LUT …），永不别名；
    * ``'external'``：引擎/外部提供（深度缓冲、上一帧矩阵），**不需要生产者**。

    ``first_use`` / ``last_use`` 是 ``compile()`` 的**推断结果**（不是声明），
    记录的是"剔除 + 直通重定向之后"的执行序号；未参与本帧时二者都是 ``None``。
    """

    __slots__ = ('name', 'kind', 'size', 'fmt', 'persistent', 'external',
                 'lifetime', 'samples', 'first_use', 'last_use')

    def __init__(self, name, kind='texture', size='screen', fmt='rgba16f',
                 persistent=False, external=False, lifetime=None, samples=None):
        if not name:
            raise FrameGraphError('资源必须有名字')
        if lifetime is None:
            lifetime = ('external' if external
                        else ('persistent' if persistent else 'transient'))
        elif lifetime not in LIFETIMES:
            raise FrameGraphError(
                f'资源 {name!r} 的 lifetime 只能是 {LIFETIMES}，实际 {lifetime!r}')
        else:
            # 显式 lifetime 是**权威**：两个旧布尔位由它派生，避免出现
            # "lifetime=transient 但 external=True" 这种两套账本互相矛盾的资源。
            persistent = lifetime in ('persistent', 'external')
            external = lifetime == 'external'
        self.name = name
        self.kind = kind
        self.size = size              # 'screen' | 'half' | (w, h)
        self.fmt = fmt
        self.persistent = bool(persistent)
        self.external = bool(external)   # 外部提供（比如深度缓冲、上一帧的矩阵）
        self.lifetime = lifetime
        self.samples = samples            # None ⇒ 用 FrameGraph 的全局采样数
        self.first_use = None             # 编译后推导（pass 序号）
        self.last_use = None

    def desc(self):
        """别名/池化的**描述符判据**：只有三项全同才允许共用物理槽位。"""
        return (self.fmt, self.size, self.samples)

    def __repr__(self):
        return (f'<Resource {self.name} {self.kind}/{self.fmt} {self.size} '
                f'{self.lifetime} [{self.first_use},{self.last_use}]>')


class Pass:
    """一次渲染/计算。``reads`` / ``writes`` 是资源名。"""

    __slots__ = ('name', 'kind', 'reads', 'writes', 'seq', 'side_effect')

    def __init__(self, name, reads=(), writes=(), kind='graphics', seq=0,
                 side_effect=False):
        if not name:
            raise FrameGraphError('pass 必须有名字')
        self.name = name
        self.kind = kind
        self.reads = tuple(reads)
        self.writes = tuple(writes)
        self.seq = seq                          # 声明顺序（同层排序用，保证确定性）
        #: 有副作用的 pass（比如"present 到屏幕"）即使不写任何资源也不能被剔除
        self.side_effect = bool(side_effect)

    def __repr__(self):
        return f'<Pass {self.name} R{list(self.reads)} W{list(self.writes)}>'


class FramePlan:
    """`compile()` 的产物：一条**可执行**的帧计划。"""

    __slots__ = ('order', 'culled', 'aliases', 'lifetimes', 'resources',
                 'resolved', 'outputs', 'alias_group', 'peak_slots', 'peak_bytes',
                 'pool_slot', 'pool_leases', 'width', 'height', 'samples')

    def __init__(self, order, culled, aliases, lifetimes, resources,
                 resolved=None, outputs=(), alias_group=None,
                 peak_slots=0, peak_bytes=0, pool_slot=None, pool_leases=(),
                 width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT, samples=1):
        self.order = list(order)                # pass 名字，按执行顺序
        self.culled = list(culled)              # 被剔除的 pass 名
        self.aliases = [tuple(a) for a in aliases]   # [(资源 A, 资源 B)]：可共用内存
        self.lifetimes = dict(lifetimes)        # 资源名 -> (首次写, 末次读) 的序号
        self.resources = dict(resources)
        #: **重定向之后**每个 pass 真正读/写哪些资源 —— 渲染端靠它决定绑哪张纹理。
        #: 少了它，`disable()` 的"直通"在渲染端就落不了地（读到的是已被剔除的中间资源）。
        self.resolved = {k: dict(v) for k, v in (resolved or {}).items()}
        self.outputs = list(outputs)
        #: 资源名 → 别名组号（同一组 = 共用同一物理槽位）。未别名的资源不在表里。
        self.alias_group = dict(alias_group or {})
        #: **峰值**（占用上界口径）：本帧瞬时资源的块数与字节总量。
        #: 用来断言"关掉某个 pass 之后资源不泄漏"（剔除只会让它下降）。
        self.peak_slots = int(peak_slots)
        self.peak_bytes = int(peak_bytes)
        #: 资源名 → 池内槽位号（`FrameGraph(pool=...)` 才有；否则为空表）。
        self.pool_slot = dict(pool_slot or {})
        #: 本次计划从池里借走的 `(key, slot)` 列表 —— `release_plan()` 按它归还。
        self.pool_leases = tuple(pool_leases)
        self.width = int(width)
        self.height = int(height)
        self.samples = int(samples)

    def reads_of(self, pass_name):
        """该 pass 执行时**实际**要读的资源名（已按直通重定向解析过）。"""
        return tuple(self.resolved[pass_name]['reads'])

    def writes_of(self, pass_name):
        return tuple(self.resolved[pass_name]['writes'])

    def lifetime_of(self, name):
        """资源名的 ``(first_use, last_use)``；不在本帧里则 ``None``。"""
        return self.lifetimes.get(name)

    def group_of(self, name):
        """资源名所属的别名组号；未共享槽位则 ``None``。"""
        return self.alias_group.get(name)

    def slot_of(self, name):
        """资源名占的池槽位号；没有池或未参与本帧则 ``None``。"""
        return self.pool_slot.get(name)

    def __repr__(self):
        return (f'<FramePlan {len(self.order)} passes, culled={len(self.culled)}, '
                f'aliases={len(self.aliases)}, peak={self.peak_slots} slots/'
                f'{self.peak_bytes} B>')

    def position(self, pass_name) -> int:
        return self.order.index(pass_name)

    def before(self, a, b) -> bool:
        """`a` 是否排在 `b` 之前（编译期断言/测试用）。"""
        return self.position(a) < self.position(b)


class SlotPool:
    """**跨帧**槽位池：按 ``(fmt, width, height, samples)`` 分桶复用物理槽位。

    只维护"哪个桶里有哪些空闲槽位号"，**不创建任何 GL 对象** —— 真实后端拿到
    `pool_slot` 之后自己去建/复用 FBO 附件；本层只保证"同一描述符的槽位可以复用、
    借用与归还一一配对"。空闲表用 **FIFO**（先归还的先用）：同一张图逐帧"编译 → 归还"
    得到**完全相同**的槽位号，帧间不抖动。
    """

    __slots__ = ('_free', '_leased', '_next')

    def __init__(self):
        self._free = {}          # key -> [slot, ...]（空闲）
        self._leased = {}        # key -> set(slot)（借出中）
        self._next = {}          # key -> 下一个新槽位号

    def acquire(self, key):
        """借一个槽位（有空闲则复用，否则新建）。返回槽位号（桶内从 0 递增）。"""
        key = tuple(key)
        free = self._free.setdefault(key, [])
        if free:
            slot = free.pop(0)
        else:
            slot = self._next.get(key, 0)
            self._next[key] = slot + 1
        self._leased.setdefault(key, set()).add(slot)
        return slot

    def release(self, key, slot) -> bool:
        """归还槽位；重复归还返回 ``False``（不重复计数、不抛异常 —— 幂等）。"""
        key = tuple(key)
        leased = self._leased.setdefault(key, set())
        if slot not in leased:
            return False
        leased.discard(slot)
        self._free.setdefault(key, []).append(slot)
        return True

    def free_slots(self, key=None) -> int:
        if key is not None:
            return len(self._free.get(tuple(key), ()))
        return sum(len(v) for v in self._free.values())

    def leased_slots(self, key=None) -> int:
        if key is not None:
            return len(self._leased.get(tuple(key), ()))
        return sum(len(v) for v in self._leased.values())

    def created_slots(self, key=None) -> int:
        """累计创建过的槽位数（跨帧复用能力：它不随借用次数增长）。"""
        if key is not None:
            return int(self._next.get(tuple(key), 0))
        return sum(int(v) for v in self._next.values())

    def stats(self, key=None) -> dict:
        return {'free': self.free_slots(key),
                'leased': self.leased_slots(key),
                'created': self.created_slots(key)}

    def __repr__(self):
        return (f'<SlotPool buckets={len(self._next)} free={self.free_slots()} '
                f'leased={self.leased_slots()} created={self.created_slots()}>')


class FrameGraph:
    """声明式帧图。用法：``g = FrameGraph()`` → declare/add → ``plan = g.compile()``。

    v2 新增三个**可选**构造参数（不传时行为与 v1 一致）：

    * ``pool``：`SlotPool`，编译时按描述符借槽位、`release_plan()` 归还；
    * ``width`` / ``height``：`size='screen'` 的像素基准（峰值字节与池 key 要用）；
    * ``samples``：全局采样数（资源可用 ``samples=`` 覆盖）。
    """

    def __init__(self, pool=None, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT,
                 samples=1):
        self.resources = {}
        self.passes = []
        self._outputs = []
        self._disabled = set()
        self.pool = pool
        self.width = int(width)
        self.height = int(height)
        self.samples = max(1, int(samples))

    # ------------------------------------------------------ 运行期开关 --
    def disable(self, *names):
        """**运行期关掉一个 pass**（帧图的经典能力）。

        关掉之后不是"跑一遍但不用结果"，而是：

        * 这个 pass 从计划里消失；
        * 它写的资源被**重定向**到它读的第一个资源（"直通"）—— 于是下游读者
          透明地拿到上一级的结果，**中间那张纹理也就不再需要了**（会被别名回收）。

        代价是：**被关掉的 pass 必须至少读一个资源**才能直通。例如把 `depth_prepass`
        关掉（它什么都不读）就会直接报错 —— 这正是"关闭它会让下游没数据"的诚实表达，
        比"跑一遍空 pass"好得多。
        """
        for n in names:
            if not any(p.name == n for p in self.passes):
                raise FrameGraphError(f'没有名为 {n!r} 的 pass')
        self._disabled |= set(names)
        return self

    def enable(self, *names):
        for n in names:
            self._disabled.discard(n)
        return self

    # ---------------------------------------------------------- 声明 --
    def resource(self, name, **kw) -> Resource:
        if name in self.resources:
            raise FrameGraphError(f'资源重名：{name}')
        r = Resource(name, **kw)
        self.resources[name] = r
        return r

    def add(self, name, reads=(), writes=(), kind='graphics', side_effect=False) -> Pass:
        if any(p.name == name for p in self.passes):
            raise FrameGraphError(f'pass 重名：{name}')
        for res in tuple(reads) + tuple(writes):
            if res not in self.resources:
                raise FrameGraphError(f'pass {name} 用了未声明的资源 {res!r}')
        p = Pass(name, reads, writes, kind=kind, seq=len(self.passes),
                 side_effect=side_effect)
        self.passes.append(p)
        return p

    def set_output(self, *names):
        for n in names:
            if n not in self.resources:
                raise FrameGraphError(f'输出用了未声明的资源 {n!r}')
        self._outputs.extend(names)
        return self

    def external(self, name, **kw) -> Resource:
        return self.resource(name, external=True, persistent=True, **kw)

    # ---------------------------------------------------------- 编译 --
    def compile(self) -> FramePlan:
        """校验 + 剔除 + 拓扑排序 + 别名/峰值/槽位（v2）。"""
        if not self._outputs:
            raise FrameGraphError('没有声明输出（set_output）—— 无法判断哪些 pass 是活着的')
        passes = self._apply_disabled()
        writers = {}
        for p in passes:
            for w in p.writes:
                writers.setdefault(w, []).append(p.name)

        # ① 每个被读的资源都得有生产者（外部资源视为已提供）
        for p in passes:
            for r in p.reads:
                if r not in writers and not self.resources[r].external:
                    res = self.resources[r]
                    raise FrameGraphError(
                        f'资源 {r!r}（lifetime={res.lifetime!r}）从未被任何 pass 写过，'
                        f'但 pass {p.name!r} 要读它 —— 要么给它加一个生产者 pass，'
                        f'要么把它声明成 external（外部提供）')

        # ② 从输出反向求"活着的 pass"（死 pass 剔除）
        alive = self._live_passes(writers, passes)
        live = [p for p in passes if p.name in alive]

        # ③ 拓扑排序（Kahn；同层按声明顺序 ⇒ 结果确定）
        ordered = self._toposort(live)

        # ④ 资源生命周期与瞬时别名（用**剔除后**的 pass 序号）
        index = {p.name: i for i, p in enumerate(ordered)}
        lifetimes = {}
        for p in ordered:
            i = index[p.name]
            for r in p.writes:
                first, _last = lifetimes.get(r, (i, -1))
                lifetimes[r] = (min(first, i), max(_last, i))
            for r in p.reads:
                first, _last = lifetimes.get(r, (i, -1))
                lifetimes[r] = (min(first, i), max(_last, i))
        # 推导结果回写到 Resource（每次 compile 先清空 —— 否则关掉效果后旧值会残留）
        for res in self.resources.values():
            res.first_use = res.last_use = None
        for name, (lo, hi) in lifetimes.items():
            self.resources[name].first_use, self.resources[name].last_use = lo, hi

        groups = self._alias(lifetimes)
        aliases = [list(g) for g in groups if len(g) > 1]
        alias_group = {name: gid
                       for gid, g in enumerate(aliases) for name in g}

        if not ordered:
            raise FrameGraphError(
                '编译后的计划是空的：没有任何 pass 喂到你声明的输出。'
                f'（输出 {self._outputs!r}；若它是外部资源，别忘了给"真正写屏"的 pass 标 side_effect）')

        # ⑤ 峰值统计（纯估算）+ 跨帧槽位分配（只借槽位号，不建 GL 对象）
        peak_slots, peak_bytes = self._peak(lifetimes)
        pool_slot, leases = self._assign_slots(groups)

        return FramePlan([p.name for p in ordered],
                         [p.name for p in self.passes if p.name not in alive],
                         aliases, lifetimes,
                         {k: v for k, v in self.resources.items()},
                         resolved={p.name: {'reads': p.reads, 'writes': p.writes,
                                            'kind': p.kind} for p in ordered},
                         outputs=self._outputs,
                         alias_group=alias_group,
                         peak_slots=peak_slots, peak_bytes=peak_bytes,
                         pool_slot=pool_slot, pool_leases=leases,
                         width=self.width, height=self.height,
                         samples=self.samples)

    # ------------------------------------------------------ 资源账本（v2）--
    def release_plan(self, plan) -> int:
        """把 ``plan`` 从池里借的槽位**全部归还**，返回实际归还数（幂等）。

        必须在**同一张图/同一个池**上调用（槽位号是桶内编号，跨池不通用）。
        没有池、或已经归还过 ⇒ 返回 0，不抛异常 —— 让"每帧末尾无条件释放"写起来安全。
        """
        leases = tuple(getattr(plan, 'pool_leases', ()) or ())
        n = 0
        if self.pool is not None:
            for key, slot in leases:
                if self.pool.release(key, slot):
                    n += 1
        if leases:
            plan.pool_leases = ()
        return n

    def _resource_bytes(self, res) -> int:
        """一个资源按声明估算的字节数（`kind='buffer'` 与纹理分开算）。"""
        if res.kind == 'buffer':
            key = str(res.fmt or '').lower()
            if key not in _BUFFER_BYTES:
                raise FrameGraphError(
                    f'无法估算缓冲格式 {res.fmt!r}（资源 {res.name!r}）—— '
                    f'请在 framegraph._BUFFER_BYTES 里补一条')
            return _BUFFER_BYTES[key]
        w, h = resolve_size(res.size, self.width, self.height)
        samples = res.samples if res.samples is not None else self.samples
        return estimate_bytes(res.fmt, w, h, samples)

    def _pool_key(self, res) -> tuple:
        w, h = resolve_size(res.size, self.width, self.height)
        samples = res.samples if res.samples is not None else self.samples
        return (res.fmt, int(w), int(h), max(1, int(samples)))

    def _peak(self, lifetimes):
        """本帧瞬时资源的**占用上界**：`peak_slots` 块、`peak_bytes` 字节。

        口径刻意取"上界"而不是"并发峰值"：别名/池化能省多少取决于真实后端的槽位
        复用方式（要不要换纹理视图、要不要 barrier），v2 不建 GL，算一个"精确并发
        峰值"只会得到**看起来精确实则依赖后端**的数字。上界口径的好处是**单调**：
        剔除一个 pass ⇒ 它写的资源从账本里消失 ⇒ 关掉效果后峰值只降不升，
        这正是"资源不泄漏"要断言的量。

        别名的收益体现在 `aliases` / `alias_group` / `pool_slot` 上，不在这里重复打折。
        只算 `lifetime='transient'`：常驻资源跨帧活着、外部资源不由我们分配。
        """
        sizes = [self._resource_bytes(self.resources[name])
                 for name in lifetimes
                 if self.resources[name].lifetime == 'transient']
        return len(sizes), sum(sizes)

    def _assign_slots(self, groups):
        """为每个物理槽位（别名组或单例）从池里借一个槽位；无池则返回空表。"""
        slot_of, leases = {}, []
        if self.pool is None:
            return slot_of, ()
        for g in groups:
            key = self._pool_key(self.resources[g[0]])
            slot = self.pool.acquire(key)
            leases.append((key, slot))
            for name in g:
                slot_of[name] = slot
        return slot_of, tuple(leases)

    def _apply_disabled(self):
        """把被关掉的 pass 从图里摘掉，并把它的输出**重定向**到它的第一个输入。

        ⚠ **重定向要传递地解析到不动点**。三个效果一起关时链路是
        ``hdr_mb → hdr_dof → hdr_fog → hdr``：只做一次映射的话，
        `tonemap_pass` 会去读一个"生产者已被剔除"的 `hdr_fog` —— 编译期直接报
        "没有生产者"。我第一版就是只映射了一层，关单个 pass 没问题、关三个才炸。
        """
        if not self._disabled:
            return list(self.passes)
        by_name = {p.name: p for p in self.passes}
        redirect = {}
        for p in self.passes:                 # **按声明顺序**建链（链的走向才确定）
            if p.name not in self._disabled:
                continue
            if not p.reads:
                raise FrameGraphError(
                    f'pass {p.name!r} 什么都不读，关掉它之后它的输出没有"上游"可以直通 '
                    f'—— 这种情况不能靠 disable 剔除（要么别声明它，要么把读它的 pass 也关掉）')
            src = p.reads[0]
            for w in p.writes:
                redirect[w] = src

        def resolve(r):
            seen = set()
            while r in redirect and r not in seen:
                seen.add(r)
                r = redirect[r]
            return r

        out = []
        for p in self.passes:
            if p.name in self._disabled:
                continue
            out.append(Pass(p.name,
                            reads=tuple(resolve(r) for r in p.reads),
                            writes=tuple(resolve(w) for w in p.writes),
                            kind=p.kind, seq=p.seq, side_effect=p.side_effect))
        return out

    def _live_passes(self, writers, passes=None):
        """从输出（含 side_effect 的 pass）反向遍历：只有真正喂到输出的 pass 才算活。"""
        passes = self.passes if passes is None else passes
        alive = set()
        stack = []
        for out in self._outputs:
            for w in writers.get(out, ()):
                stack.append(w)
        for p in passes:
            if p.side_effect:
                stack.append(p.name)
        by_name = {p.name: p for p in passes}
        while stack:
            name = stack.pop()
            if name in alive:
                continue
            alive.add(name)
            for r in by_name[name].reads:
                stack.extend(writers.get(r, ()))
        return alive


    def _toposort(self, live):
        by_name = {p.name: p for p in live}
        producers = {}
        for p in live:
            for w in p.writes:
                producers.setdefault(w, []).append(p.name)
        # 边：写者 → 读者（同一个 pass 内不算依赖）
        deps = {p.name: set() for p in live}
        for p in live:
            for r in p.reads:
                for w in producers.get(r, ()):
                    if w != p.name:
                        deps[p.name].add(w)
        out, ready = [], [p for p in live if not deps[p.name]]
        ready.sort(key=lambda p: p.seq)
        remaining = {p.name: set(deps[p.name]) for p in live}
        while ready:
            p = ready.pop(0)
            out.append(p)
            for q in live:
                if q.name in remaining and p.name in remaining[q.name]:
                    remaining[q.name].discard(p.name)
                    if not remaining[q.name] and q not in out and q not in ready:
                        ready.append(q)
            ready.sort(key=lambda p: p.seq)
        if len(out) != len(live):
            stuck = sorted(n for n, d in remaining.items() if d and n not in {p.name for p in out})
            raise FrameGraphError(f'帧图里有环，涉及：{stuck}')
        return out

    def _alias(self, lifetimes):
        """生命周期**不重叠**且**描述符相同**的瞬时资源可以共用同一块内存。

        只对 `lifetime='transient'` 的资源做：常驻资源跨帧活着、外部资源不归我们管。
        两条判据缺一不可：

        1. 半开区间不重叠：A 的末次使用 < B 的首次使用（**严格小于** —— 同一个 pass 里
           读 A 写 B 时二者共活，等于就不敢别名）；
        2. 描述符 `(fmt, size, samples)` 完全一致。v1 只看生命周期，会把 `r32f` 的
           距离图和 `rgba8` 的 LDR 图判成"可共用"——纯分析时无害，落到槽位池就是错的。

        返回**全部分组**（含单例，每组描述符相同）；只有长度 > 1 的组才是对外可见的
        `aliases`，但单例也要占一个池槽位，所以内部保留。
        """
        cands = [r.name for r in self.resources.values()
                 if r.lifetime == 'transient' and r.name in lifetimes]
        cands.sort(key=lambda n: lifetimes[n][0])
        groups = []                                # [(desc, [(name, (lo, hi))])]
        for name in cands:
            lo, hi = lifetimes[name]
            desc = self.resources[name].desc()
            placed = False
            for d, members in groups:
                if d != desc:
                    continue
                if all(hi < glo or ghi < lo for _n, (glo, ghi) in members):
                    members.append((name, (lo, hi)))
                    placed = True
                    break
            if not placed:
                groups.append((desc, [(name, (lo, hi))]))
        return [[n for n, _ in members] for _d, members in groups]


# ------------------------------------------------------------------ 标准帧 --
def standard_3d_plan(shadows=True, fog=True, dof=True, motionblur=True,
                     tonemap=True, pool=None, width=DEFAULT_WIDTH,
                     height=DEFAULT_HEIGHT, samples=1) -> FrameGraph:
    """**标准 3D 帧**：深度 → 阴影 → 场景 → 雾 → 景深 → 运动模糊 → tonemap → present。

    这个函数的顺序就是 P3 要定下来的"设计决策"，而且它**被测试钉住**：
    `fog` 必须在 `dof` 之前、`dof` 必须在 `motionblur` 之前（见
    `SEQUENCE_OF_DEPTH_EFFECTS`）。开关参数则用来验证**死 pass 剔除是真的**
    （关掉景深 ⇒ 景深 pass 被砍、它独占的中间资源被别名回收）。

    v2 追加的四个参数都是可选的（不传时与 v1 完全一致）：``pool`` 跨帧槽位池、
    ``width`` / ``height`` / ``samples`` 峰值与池 key 的估算基准。
    """
    g = FrameGraph(pool=pool, width=width, height=height, samples=samples)
    g.external('scene_color', fmt='rgba16f')       # 场景色（外部命名只为可读性）
    g.external('prev_view_proj', kind='buffer', fmt='mat4')
    g.external('backbuffer', fmt='rgba8')
    g.resource('distance', fmt='r32f')             # 线性距离：雾/景深/运动模糊共用
    g.add('depth_prepass', reads=(), writes=('distance',))
    if shadows:
        g.resource('shadow', size=(1024, 1024), fmt='r32f')
        g.add('shadow_pass', reads=(), writes=('shadow',))
    g.resource('hdr', fmt='rgba16f')
    shadow_reads = ('shadow',) if shadows else ()
    g.add('scene_pass', reads=('distance',) + shadow_reads, writes=('hdr',))
    # 三个深度相关的效果：**顺序在 `SEQUENCE_OF_DEPTH_EFFECTS` 里，不能改**
    if fog:
        g.resource('hdr_fog', fmt='rgba16f')
        g.add('fog_pass', reads=('hdr', 'distance') + shadow_reads, writes=('hdr_fog',))
    if dof:
        g.resource('hdr_dof', fmt='rgba16f')
        g.add('dof_pass', reads=('hdr_fog' if fog else 'hdr', 'distance'),
              writes=('hdr_dof',))
    if motionblur:
        g.resource('hdr_mb', fmt='rgba16f')
        g.add('motionblur_pass',
              reads=('hdr_dof' if dof else ('hdr_fog' if fog else 'hdr'),
                     'distance', 'prev_view_proj'),
              writes=('hdr_mb',))
    tail = ('hdr_mb' if motionblur else
            ('hdr_dof' if dof else ('hdr_fog' if fog else 'hdr')))
    if tonemap:
        g.resource('ldr', fmt='rgba8')
        g.add('tonemap_pass', reads=(tail,), writes=('ldr',))
        tail = 'ldr'
    g.add('present', reads=(tail, 'backbuffer'), writes=(), side_effect=True)
    g.set_output('backbuffer')
    return g
