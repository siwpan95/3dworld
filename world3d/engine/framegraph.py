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

**合规**：帧图是实时渲染的通用工程做法（Frostbite / FrameGraph 系列文章都讲过）。
**独立实现**，不复制任何具体仓库代码。
"""
from __future__ import annotations

__all__ = ['FrameGraphError', 'Resource', 'Pass', 'FramePlan', 'FrameGraph',
           'standard_3d_plan', 'SEQUENCE_OF_DEPTH_EFFECTS']


class FrameGraphError(ValueError):
    """帧图声明或编译期的问题（读不到生产者 / 有环 / 输出没声明 …）。"""


#: **深度相关三个效果的物理顺序**（这一条是有物理依据的，不是偏好）：
#: 雾是"光在介质里被散射"⇒ 它产生的像**也要被虚化**，所以雾在景深之前；
#: 运动模糊是对**最终成像**的曝光积分 ⇒ 它必须在最后（拖影要拖"已经虚化的像"）。
SEQUENCE_OF_DEPTH_EFFECTS = ('fog', 'dof', 'motionblur')


class Resource:
    """帧图里的一块资源（纹理 / 缓冲）。"""

    __slots__ = ('name', 'kind', 'size', 'fmt', 'persistent', 'external')

    def __init__(self, name, kind='texture', size='screen', fmt='rgba16f',
                 persistent=False, external=False):
        if not name:
            raise FrameGraphError('资源必须有名字')
        self.name = name
        self.kind = kind
        self.size = size              # 'screen' | 'half' | (w, h)
        self.fmt = fmt
        self.persistent = bool(persistent)
        self.external = bool(external)   # 外部提供（比如深度缓冲、上一帧的矩阵）

    def __repr__(self):
        return f'<Resource {self.name} {self.kind}/{self.fmt} {self.size}>'


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
                 'resolved', 'outputs')

    def __init__(self, order, culled, aliases, lifetimes, resources,
                 resolved=None, outputs=()):
        self.order = list(order)                # pass 名字，按执行顺序
        self.culled = list(culled)              # 被剔除的 pass 名
        self.aliases = [tuple(a) for a in aliases]   # [(资源 A, 资源 B)]：可共用内存
        self.lifetimes = dict(lifetimes)        # 资源名 -> (首次写, 末次读) 的序号
        self.resources = dict(resources)
        #: **重定向之后**每个 pass 真正读/写哪些资源 —— 渲染端靠它决定绑哪张纹理。
        #: 少了它，`disable()` 的"直通"在渲染端就落不了地（读到的是已被剔除的中间资源）。
        self.resolved = {k: dict(v) for k, v in (resolved or {}).items()}
        self.outputs = list(outputs)

    def reads_of(self, pass_name):
        """该 pass 执行时**实际**要读的资源名（已按直通重定向解析过）。"""
        return tuple(self.resolved[pass_name]['reads'])

    def writes_of(self, pass_name):
        return tuple(self.resolved[pass_name]['writes'])

    def __repr__(self):
        return (f'<FramePlan {len(self.order)} passes, culled={len(self.culled)}, '
                f'aliases={self.aliases}>')

    def position(self, pass_name) -> int:
        return self.order.index(pass_name)

    def before(self, a, b) -> bool:
        """`a` 是否排在 `b` 之前（编译期断言/测试用）。"""
        return self.position(a) < self.position(b)


class FrameGraph:
    """声明式帧图。用法：``g = FrameGraph()`` → declare/add → ``plan = g.compile()``。"""

    def __init__(self):
        self.resources = {}
        self.passes = []
        self._outputs = []
        self._disabled = set()

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
        """校验 + 剔除 + 拓扑排序 + 别名。"""
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
                    raise FrameGraphError(
                        f'资源 {r!r} 没有生产者，但 pass {p.name!r} 要读它')

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
        aliases = self._alias(lifetimes, ordered)

        if not ordered:
            raise FrameGraphError(
                '编译后的计划是空的：没有任何 pass 喂到你声明的输出。'
                f'（输出 {self._outputs!r}；若它是外部资源，别忘了给"真正写屏"的 pass 标 side_effect）')
        return FramePlan([p.name for p in ordered],
                         [p.name for p in self.passes if p.name not in alive],
                         aliases, lifetimes,
                         {k: v for k, v in self.resources.items()},
                         resolved={p.name: {'reads': p.reads, 'writes': p.writes,
                                            'kind': p.kind} for p in ordered},
                         outputs=self._outputs)

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

    def _alias(self, lifetimes, ordered):
        """生命周期**不重叠**的瞬时资源可以共用同一块内存。

        只对 `persistent=False` 且 `external=False` 的资源做：常驻资源当然不能别名。
        判定用"半开区间"：A 的末次使用 < B 的首次使用（严格小于，等于就不敢别名 ——
        同一个 pass 里读 A 写 B 的情况下二者是同时活着的）。
        """
        cands = [r.name for r in self.resources.values()
                 if not r.persistent and not r.external and r.name in lifetimes]
        cands.sort(key=lambda n: lifetimes[n][0])
        groups = []                                  # [[(name, (lo, hi))], ...]
        for name in cands:
            lo, hi = lifetimes[name]
            placed = False
            for g in groups:
                if all(hi < glo or ghi < lo for _n, (glo, ghi) in g):
                    g.append((name, (lo, hi)))
                    placed = True
                    break
            if not placed:
                groups.append([(name, (lo, hi))])
        return [[n for n, _ in g] for g in groups if len(g) > 1]


# ------------------------------------------------------------------ 标准帧 --
def standard_3d_plan(shadows=True, fog=True, dof=True, motionblur=True,
                     tonemap=True) -> FrameGraph:
    """**标准 3D 帧**：深度 → 阴影 → 场景 → 雾 → 景深 → 运动模糊 → tonemap → present。

    这个函数的顺序就是 P3 要定下来的"设计决策"，而且它**被测试钉住**：
    `fog` 必须在 `dof` 之前、`dof` 必须在 `motionblur` 之前（见
    `SEQUENCE_OF_DEPTH_EFFECTS`）。开关参数则用来验证**死 pass 剔除是真的**
    （关掉景深 ⇒ 景深 pass 被砍、它独占的中间资源被别名回收）。
    """
    g = FrameGraph()
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
