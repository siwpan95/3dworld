# -*- coding: utf-8 -*-
"""**《砖境 / Brickward》** —— 机关/道具内核（BQM 机制致敬 · B1）。

> 机制致敬 **BlockQuest Maker（《砖块迷宫建造者》，Wonderland Kazakiri 2018）**。
> **不是移植、不是复刻**：名称/美术/音效/关卡数据/联网经济一律自创或不做；
> 只吸收它的**玩法机制**（方块地牢 + 一格一步 + 机关组合 + 编辑器）。
> 合规边界见 `docs/BQM机制致敬_复刻可行性.md` §2。

## B1 范围（本文件）

* **一格一步的确定性模拟**（同关卡 + 同输入 ⇒ 同结果，可回放、可断言）；
* **元素表**（6 类）：推箱 / 按钮 / 拉杆 / 门 / 金门+钥匙 / 尖刺；出口为终局；
* **"连接"（开关 → 门）**：BQM 编辑器的核心机制 —— 声明式绑定，用
  `engine.orchestration.Bus` 做级联（P0-5 自研的总线在这里第一次吃到玩法）；
* **BFS 求解器**（状态空间有界）—— headless 验收靠它证明"每关可解且能回放到胜"；
* **关卡 = 纯数据**（ASCII 图 + 连接表），与编辑器/生成器/内核同 schema（三端同源）。

B2（求解器强化 + 炸弹/火球/滚石/史莱姆）、B3（编辑器 UI：道具栏 + 连接编辑）、
B4（渲染/手感/成片）见 `docs/BQM机制致敬_复刻可行性.md` §6。

## 元素表

| 码 | 元素 | 语义 |
|----|------|------|
| `#` | 墙 | 不可通过（箱子也不能推入） |
| `.` | 地板 | 可通过 |
| `P` | 玩家起点 | 唯一 |
| `C` | 箱子 | 可推、不可拉；可压按钮；**可推上尖刺并挡住它** |
| `o` | 按钮 | **持续型**：被玩家**或箱子**压住时处于按下 |
| `L` | 拉杆 | **切换型**：玩家走过时翻转（箱子压不住） |
| `D` | 门 | 关着时挡人挡箱；由连接控制开合 |
| `g` | 金门 | 有钥匙则开启并**消耗一把**（本步只开门、人不动）；无钥匙挡路 |
| `k` | 钥匙 | 拾取（计数制，不是占格物品） |
| `^` | 尖刺 | 玩家踩上即败；箱子压上后失效（经典"用箱垫路"） |
| `E` | 出口 | 玩家到达即胜 |

## 连接（links）

```python
links = [{'from': (r, c), 'to': (r, c), 'mode': 'open'}]   # 按钮/拉杆 → 门
```
* `mode='open'`：**源被触发**（按钮按下 / 拉杆 on）时门开，否则关；
* `mode='toggle'`：源**每次状态变化**时把门翻转；
* `mode='close'`：源被触发时门关（反向门）。

触发经 `engine.orchestration.Bus` 级联：`src_on → door_open`，多级连接（门再驱动别的门）
天然支持 —— 这是 B2 做复杂机关的地基。

**约束**：连接只能"开关 → 普通门"。金门只认钥匙（状态在 `gold_open`），连到金门会被
`validate_level` **明确拒绝** —— 否则就是"按下按钮毫无反应"的静默失效。
"""
from __future__ import annotations

import os
import sys

# P0-3/B1：内核住 games/<名>/，找仓库根要 dirname 三次。
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import _cordis_locate  # noqa: E402

_cordis_locate.ensure()

from cordis import Service  # noqa: E402

from engine.orchestration import Bus  # noqa: E402

__all__ = ['BrickwardKernel', 'ELEMENTS', 'LEVELS', 'LEVEL_ART', 'DEMO_SEEDS',
           'WALL', 'FLOOR', 'parse_level', 'validate_level', 'generate',
           'solve_level', 'make_offline', 'DIRS', 'KIND_NAME']

# ---- 元素码 ----
WALL, FLOOR, PLAYER = '#', '.', 'P'
CRATE, BUTTON, LEVER = 'C', 'o', 'L'
DOOR, GOLD_DOOR, KEY, SPIKE, EXIT = 'D', 'g', 'k', '^', 'E'

#: 元素表（供编辑器/文档/校验共用；`block` = 是否挡人，`pushable` = 能否被推）
ELEMENTS = {
    WALL:      dict(name='墙',     block=True,  pushable=False),
    FLOOR:     dict(name='地板',   block=False, pushable=False),
    PLAYER:    dict(name='玩家',   block=False, pushable=False),
    CRATE:     dict(name='箱子',   block=False, pushable=True),
    BUTTON:    dict(name='按钮',   block=False, pushable=True),   # 箱可压上
    LEVER:     dict(name='拉杆',   block=False, pushable=True),   # 箱可压上（但不切换）
    DOOR:      dict(name='门',     block=True,  pushable=False),  # 开着时不挡（动态）
    GOLD_DOOR: dict(name='金门',   block=True,  pushable=False),
    KEY:       dict(name='钥匙',   block=False, pushable=False),
    SPIKE:     dict(name='尖刺',   block=False, pushable=True),   # 箱可压上（挡尖刺）
    EXIT:      dict(name='出口',   block=False, pushable=False),
}
KIND_NAME = {k: v['name'] for k, v in ELEMENTS.items()}

#: 四个方向（与 puzzle 内核同约定：di 行、dj 列）
DIRS = {'U': (-1, 0), 'D': (1, 0), 'L': (0, -1), 'R': (0, 1)}
#: 求解器搜索顺序（固定 ⇒ 同关卡得同解，便于断言）
DIR_ORDER = ('U', 'R', 'D', 'L')


# ====================================================================== 关卡
def parse_level(art: str, name: str = '', links=(), par=None) -> dict:
    """ASCII 图 → 关卡 dict（`#` 墙 / `.` 地板 / 其它见元素表）。

    关卡是**纯数据**：编辑器导出、生成器产出、内核吃下的都是这一个 schema。
    """
    rows = [ln for ln in art.strip('\n').splitlines() if ln.strip()]
    h, w = len(rows), max(len(ln) for ln in rows)
    rows = [ln.ljust(w, FLOOR) for ln in rows]
    out = dict(name=name, h=h, w=w, rows=list(rows), links=[dict(l) for l in links],
               par=par)
    return out


def _cells(level):
    """把关卡拆成各类元素的坐标集合（`rows` 是唯一事实来源）。"""
    c = dict(walls=set(), crates=set(), buttons=set(), levers=set(), doors=set(),
             gold=set(), keys=set(), spikes=set(), player=None, exit=None)
    for i, row in enumerate(level['rows']):
        for j, ch in enumerate(row):
            p = (i, j)
            if ch == WALL:
                c['walls'].add(p)
            elif ch == CRATE:
                c['crates'].add(p)
            elif ch == BUTTON:
                c['buttons'].add(p)
            elif ch == LEVER:
                c['levers'].add(p)
            elif ch == DOOR:
                c['doors'].add(p)
            elif ch == GOLD_DOOR:
                c['gold'].add(p)
            elif ch == KEY:
                c['keys'].add(p)
            elif ch == SPIKE:
                c['spikes'].add(p)
            elif ch == PLAYER:
                c['player'] = p
    return c


def validate_level(level) -> tuple:
    """校验关卡结构，返回 ``(ok, reason)``（编辑器/B3 要用来挡坏数据）。"""
    if not isinstance(level, dict):
        return False, '关卡必须是 dict'
    if not level.get('rows'):
        return False, '缺 rows'
    c = _cells(level)
    if c['player'] is None:
        return False, '缺玩家起点 P'
    if not any(EXIT in row for row in level['rows']):
        return False, '缺出口 E'
    grid = {(i, j) for i in range(level['h']) for j in range(level['w'])}
    for p in c['doors'] | c['gold']:
        if p in c['walls']:
            return False, f'门与墙重叠 {p}'
    for lk in level.get('links') or []:
        if lk['from'] not in (c['buttons'] | c['levers']):
            return False, f"连接源 {lk['from']} 不是按钮/拉杆"
        if lk['to'] in c['gold']:
            # 金门的状态在 `gold_open`（钥匙开的），开关改的是 `doors` ——
            # 允许连到金门会让"按下按钮毫无反应"，而且是**静默**的。
            return False, f"连接目标 {lk['to']} 是金门（B1 金门只认钥匙，开关只能连普通门）"
        if lk['to'] not in c['doors']:
            return False, f"连接目标 {lk['to']} 不是门"
        if lk.get('mode', 'open') not in ('open', 'toggle', 'close'):
            return False, f"未知连接模式 {lk.get('mode')!r}"
    if not grid:
        return False, '空关卡'
    return True, ''


# ====================================================================== 内核
class BrickwardKernel(Service):
    """机关内核：一格一步的确定性模拟 + 求解器（零外部依赖、零 GL）。

    `inject` 为空 —— B1 的玩法语义自足（纯逻辑），这样 headless 验收不需要 GL 句柄，
    也能在任何内核未装渲染时单独跑。B2/B4 接入 particles/render 时再按需 inject。
    """

    inject = []                      # 纯逻辑内核（见类 docstring）

    def __init__(self, ctx, config):
        super().__init__(ctx, "brickward")
        cfg = config or {}
        self.seed = int(cfg.get('seed', DEMO_SEEDS[0]))
        self.start_level = int(cfg.get('start_level', 0))
        self.steps = 0
        self.pushes = 0
        self.deaths = 0
        self.level = None
        self._reset(None)
        self.load_level(generate(self.seed) if self.start_level < 0
                        else LEVELS[self.start_level % len(LEVELS)])

    # ------------------------------------------------------------- 状态初始化
    def _reset(self, level):
        # ---- 静态格（装载后不变；只在 load_level/make_offline 里赋值）----
        self.walls = set()           # 墙
        self.button_cells = set()    # 按钮（**所有**按钮格）
        self.lever_cells = set()     # 拉杆（**所有**拉杆格）
        self.door_cells = set()      # 门（**所有**门格；与"开着的门"是两件事）
        self.gold = set()            # 金门（所有）
        self.spikes = set()          # 尖刺（所有）
        self.exit = None
        self.h = self.w = 0          # 地图尺寸（越界即挡：**不假设**画图的人写了边框）
        # ---- 动态状态（每步可变；求解器的指纹就是它们）----
        self.crates = set()          # 箱子位置
        self.doors = set()           # **开着**的门
        self.levers = set()          # **扳到 on** 的拉杆
        self.gold_open = set()       # 已用钥匙开过的金门
        self.keys = set()            # 地上未拾取的钥匙
        self.spike_dead = set()      # 被箱子压住（失效）的尖刺
        self.player = None
        self.keys_held = 0
        self._pressed_prev = set()   # 上一帧的「触发集」（用于发 src_on/src_off）
        self.bus = Bus('brickward')  # 连接级联（P0-5 自研总线）
        self.log = []                # [(dir, event, note)] —— 可回放/可断言
        self._status = 'playing'

    def _load_cells(self, level):
        """按 `level['rows']` 铺静态格与初始动态状态（`load_level`/离线工厂共用）。"""
        c = _cells(level)
        self.level = level
        self.load_error = ''        # 装载成功要清掉上一次失败留下的原因
        self._reset(level)
        self.h, self.w = level['h'], level['w']
        self.walls = set(c['walls'])
        self.button_cells = set(c['buttons'])
        self.lever_cells = set(c['levers'])
        self.door_cells = set(c['doors'])
        self.gold = set(c['gold'])
        self.spikes = set(c['spikes'])
        self.crates = set(c['crates'])
        self.keys = set(c['keys'])
        self.exit = _exit_of(level)
        self.player = c['player']
        self.steps = self.pushes = self.deaths = 0
        self._bind_links()
        self._resync_links()        # 先把 'close' 门算到位，再对账事件
        self._settle(None)
        return self

    def load_level(self, level: dict) -> bool:
        """装载关卡（纯数据）。返回是否通过结构校验。"""
        ok, reason = validate_level(level)
        if not ok:
            self.load_error = reason
            return False
        self._load_cells(level)
        return True

    def load_art(self, art: str, name: str = '', links=()) -> bool:
        return self.load_level(parse_level(art, name, links))

    def _bind_links(self):
        """连接表 → 总线绑定。

        **事件语义**（三种事件由 `_settle` 统一发射，每个连接只订阅它需要的那一类）：

        * ``src_on:<坐标>``   —— 该开关**进入**触发态（按钮被压住 / 拉杆被扳到 on）；
        * ``src_off:<坐标>``  —— 该开关**离开**触发态；
        * ``src_flip:<坐标>`` —— 该开关的触发态**发生了变化**（`mode='toggle'` 用）。

        `mode='open'/'close'` 订阅 on/off；`mode='toggle'` 订阅 flip。

        ⚠ **一个连接只许订阅一类事件**。早先把三种全绑上，而且 flip 的处理函数还
        **写死 `_apply_link('toggle', ...)`、无视连接自己声明的 mode** —— 于是
        `mode='open'` 的连接在源状态变化时会"先被 src_on 打开、再被 src_flip 翻回去"，
        净效果为零，只是靠"先 on 后 flip"的回调顺序碰巧抵销：看起来能用，其实每一步
        都在自我抵消（拉杆关就是这么"通过"了早期冒烟测试的）。按 mode 订阅之后，
        这种巧合不再可能发生。
        """
        self.bus = Bus('brickward')
        for lk in self.level.get('links') or []:
            src, dst, mode = tuple(lk['from']), tuple(lk['to']), lk.get('mode', 'open')
            if mode == 'toggle':
                self.bus.on(f'src_flip:{src}',
                            lambda e, p, _d=dst: self._apply_link('toggle', _d, True))
            else:
                self.bus.on(f'src_on:{src}',
                            lambda e, p, _m=mode, _d=dst: self._apply_link(_m, _d, True))
                self.bus.on(f'src_off:{src}',
                            lambda e, p, _m=mode, _d=dst: self._apply_link(_m, _d, False))

    def _apply_link(self, mode, dst, on: bool):
        """按连接模式改门状态。``toggle`` 忽略 ``on``（每次触发翻转）。"""
        if mode == 'toggle':
            self.doors.symmetric_difference_update({dst})
        elif mode == 'open':
            (self.doors.add if on else self.doors.discard)(dst)
        else:                                   # 'close'：源触发时反而关门
            (self.doors.discard if on else self.doors.add)(dst)

    def _resync_links(self):
        """装载后按「源此刻是否触发」把每扇门**算到位**。

        为什么必须有这一步：`src_on` / `src_off` 只在**变化**时发，而装载时
        「本来就没触发」不算变化 —— 于是 `mode='close'` 的门永远等不到那第一发
        `src_off`，一直关着，"反向门"变成了"堵死的门"。

        `mode='toggle'` 跳过：翻转型没有"初值"这回事（按定义它就是靠变化驱动的）。
        """
        pressed = self._pressed_sources()
        for lk in self.level.get('links') or []:
            mode = lk.get('mode', 'open')
            if mode == 'toggle':
                continue
            self._apply_link(mode, tuple(lk['to']), tuple(lk['from']) in pressed)

    # ------------------------------------------------------------- 单步模拟
    def _solid_for_player(self, p) -> bool:
        """该格此刻是否挡人（也用于箱子"能不能被推过去"）。

        ⚠ 门/金门是**两套数据**：`*_cells` 是"这里有门"，`doors`/`gold_open` 是
        "这扇门开着"。早先把二者混成一个集合，结果**开着的门挡住人、关着的门
        能穿过去** —— 求解器于是"解出"了走穿门的分支。别再合并。
        """
        if p in self.walls:
            return True
        if p in self.crates:
            return True
        if p in self.door_cells and p not in self.doors:
            return True                          # 关着的门
        if p in self.gold and p not in self.gold_open:
            return True                          # 未开的金门
        if not (0 <= p[0] < self.h and 0 <= p[1] < self.w):
            return True                          # 越界（不该发生：随包关卡四面封闭）
        return False

    def step(self, d: str) -> str:
        """走一步（``'U'/'D'/'L'/'R'``）。返回事件名；玩法状态见 `status()`。"""
        if self._status != 'playing':
            return self._status
        assert d in DIRS, f'未知方向 {d!r}'
        di, dj = DIRS[d]
        tgt = (self.player[0] + di, self.player[1] + dj)
        ev = 'moved'

        if tgt in self.walls or not (0 <= tgt[0] < self.h and 0 <= tgt[1] < self.w):
            ev = 'blocked'
        elif tgt in self.gold and tgt not in self.gold_open:
            if self.keys_held > 0:              # 有钥匙：本步只开门，人不动
                self.keys_held -= 1
                self.gold_open.add(tgt)
                ev = 'unlock'
            else:
                ev = 'blocked'
        elif tgt in self.door_cells and tgt not in self.doors:
            ev = 'blocked'                      # 关着的门挡人挡箱
        elif tgt in self.crates:                # 推箱：只推不拉
            beyond = (tgt[0] + di, tgt[1] + dj)
            if self._solid_for_player(beyond) or beyond in self.gold - self.gold_open:
                ev = 'blocked'
            else:
                self.crates.discard(tgt)
                self.crates.add(beyond)
                if beyond in self.spikes:
                    self.spike_dead.add(beyond)     # 箱压尖刺 → 失效
                self.pushes += 1
                self.player = tgt
                ev = 'pushed'
        else:
            self.player = tgt

        if ev in ('moved', 'pushed'):
            self.steps += 1
            if self.player in self.keys:
                self.keys.discard(self.player)
                self.keys_held += 1
            if self.player in self.spikes and self.player not in self.spike_dead:
                self._status = 'lost'
                self.deaths += 1
                ev = 'died'
            elif self.player == self.exit:
                self._status = 'won'
                ev = 'won'
        self._settle(d, ev)
        return ev

    def _settle(self, d=None, ev=None):
        """走完之后统一结算：先扳拉杆（若踩到），再按「触发集变化」发连接事件。

        **三类事件一次算清**：`pressed ^ _pressed_prev` 就是"这一步里状态变了的开关"
        （拉杆被扳、按钮被压/被放开都落在这里），于是

        * `pressed - prev` → `src_on`（进入触发态）
        * `prev - pressed` → `src_off`（离开触发态）
        * `pressed ^ prev` → `src_flip`（**变化**，`mode='toggle'` 用）

        早先 `src_flip` 只在拉杆分支里直接发，导致「按钮 + toggle 连接」**永远不动作**
        —— 静默失效，不报错。统一到一处后，任何开关的 toggle 都成立，且每个开关每步
        至多发一次 flip（不会自己把自己翻两次）。

        三处都**排序**后遍历：集合迭代序不该进入玩法语义。求解器解出唯一解的前提是
        「同关同输入 ⇒ 同事件序」，而 `solve()` 的 docstring 明确承诺了这条。
        """
        if self.player in self.lever_cells:      # 拉杆：走过即翻转（切换型，箱子压不住）
            lever = self.player
            on = self.levers
            if lever in on:
                on.discard(lever)
            else:
                on.add(lever)
        pressed = self._pressed_sources()
        prev = self._pressed_prev
        for src in sorted(pressed - prev):
            self.bus.emit(f'src_on:{src}')
        for src in sorted(prev - pressed):
            self.bus.emit(f'src_off:{src}')
        for src in sorted(pressed ^ prev):
            self.bus.emit(f'src_flip:{src}')
        self._pressed_prev = pressed
        if d is not None:
            self.log.append((d, ev, self.status()))

    @property
    def lever_state(self) -> set:
        """当前扳到 on 的拉杆（渲染/HUD 用）。"""
        return set(self.levers)

    def _pressed_sources(self) -> set:
        """当前"触发"状态的开关：按钮被玩家或箱子压住，或拉杆为 on。"""
        out = set()
        for b in self.button_cells:
            if b == self.player or b in self.crates:
                out.add(b)
        out |= set(self.levers)
        return out

    # ------------------------------------------------------------- 查询
    def status(self) -> str:
        return self._status

    def snapshot(self) -> dict:
        """当前状态快照（渲染层/断言共用；纯数据，无 GL）。

        `*_cells` = "哪里有这种东西"（静态，画几何用），其余 = "此刻处于什么状态"。
        """
        return dict(level=self.level['name'] if self.level else '',
                    h=self.level['h'] if self.level else 0,
                    w=self.level['w'] if self.level else 0,
                    rows=self.level['rows'] if self.level else [],
                    links=self.level.get('links') if self.level else [],
                    player=self.player, crates=sorted(self.crates),
                    door_cells=sorted(self.door_cells), doors_open=sorted(self.doors),
                    gold_cells=sorted(self.gold), gold_open=sorted(self.gold_open),
                    keys_ground=sorted(self.keys), keys_held=self.keys_held,
                    lever_cells=sorted(self.lever_cells), levers_on=sorted(self.levers),
                    button_cells=sorted(self.button_cells),
                    spikes=sorted(self.spikes), spikes_dead=sorted(self.spike_dead),
                    exit=self.exit, steps=self.steps, pushes=self.pushes,
                    deaths=self.deaths, status=self._status)

    def key(self):
        """状态指纹（求解器去重用）。

        **不变式**：凡是能影响"从此刻往后还能怎么演化"的量，一个都不能漏；
        与之无关的（`steps`/`pushes`/`deaths`/`log`）一个都不能进 ——
        漏了会让 BFS 把两个不同的局面当成同一个而剪掉正解；
        多算了会让同一局面分成多个节点、白烧内存。
        """
        return (self.player, frozenset(self.crates), frozenset(self.doors),
                frozenset(self.gold_open), frozenset(self.keys), self.keys_held,
                frozenset(self.levers), frozenset(self.spike_dead), self._status)

    def clone_from(self, src):
        """把 ``src`` 的状态复制过来（求解器用）。

        静态格（墙/各类 `*_cells`）按**引用共享** —— 求解器要开几万个节点，
        逐个拷贝整张图会把内存和时间都烧掉；它们在关卡存续期内确实不变。

        ⚠ **不要在这里重跑 `_settle`**：玩家可能正站在拉杆上（状态里那一脚已经
        扳过了），再跑一次会把它扳回去 —— 克隆出来的节点就是错的。
        触发集差异由拷贝 `_pressed_prev` 消除，BUS 由 `_bind_links` 重绑。
        """
        self.ctx = getattr(src, 'ctx', None)
        self.seed, self.start_level = src.seed, src.start_level
        self.level = src.level
        self.h, self.w = src.h, src.w
        self.walls = src.walls
        self.button_cells = src.button_cells
        self.lever_cells = src.lever_cells
        self.door_cells = src.door_cells
        self.gold = src.gold
        self.spikes = src.spikes
        self.exit = src.exit
        self.crates = set(src.crates)
        self.doors = set(src.doors)
        self.levers = set(src.levers)
        self.gold_open = set(src.gold_open)
        self.keys = set(src.keys)
        self.spike_dead = set(src.spike_dead)
        self.player = src.player
        self.keys_held = src.keys_held
        self._pressed_prev = set(getattr(src, '_pressed_prev', ()))
        self._status = src.status()
        self.steps = self.pushes = self.deaths = 0
        self.log = []
        self._bind_links()
        return self

    # ------------------------------------------------------------- 求解器
    def solve(self, max_nodes: int = 200000):
        """BFS 求最短解（返回方向列表）。不可解/超限返回 ``None``。

        确定性：方向顺序固定（`DIR_ORDER`）、同关卡得同解 —— 录制与断言都靠它。
        """
        if self._status == 'won':
            return []
        import collections
        start = self.clone_from(self)
        seen = {start.key(): None}          # key -> (父 key, 走到它的方向)；起点为 None
        q = collections.deque([start])
        nodes = 0
        while q and nodes < max_nodes:
            cur = q.popleft()
            nodes += 1
            cur_key = cur.key()
            for d in DIR_ORDER:
                nxt = BrickwardKernel._spawn(cur)
                ev = nxt.step(d)
                if ev in ('blocked', 'died'):
                    continue
                k = nxt.key()
                if k in seen:
                    continue                # 已扩展/已入队，跳过（含回到父态）
                # 关键：终态**从不入队**，所以它的父指针必须在这里补写，
                # 否则回溯第一跳就 KeyError（曾踩）。
                seen[k] = (cur_key, d)
                if nxt.status() == 'won':
                    path, k = [], k
                    while seen[k] is not None:
                        pkey, pd = seen[k]
                        path.append(pd)
                        k = pkey
                    path.reverse()
                    return path
                q.append(nxt)
        return None


def _spawn(src) -> 'BrickwardKernel':
    """由 `src` 复制一个同关卡、同状态的求解器节点（不挂 ctx，纯数据用）。"""
    return BrickwardKernel.__new__(BrickwardKernel).clone_from(src)


BrickwardKernel._spawn = staticmethod(_spawn)


def make_offline(level, seed: int = 0, ctx=None) -> 'BrickwardKernel':
    """造一个**离线内核**（不挂 ctx）并装载关卡。

    headless 求解 / 验收 / 编辑器"试解"共用这一条路径 —— 早先 `solve_level` 与
    `generate` 各写了一份十几行的装配代码，漏一个字段（比如 `door_cells`）就
    静默解出**错的**答案。**只留一个入口**。
    """
    k = BrickwardKernel.__new__(BrickwardKernel)
    k.ctx = ctx
    k.seed, k.start_level = int(seed), 0
    k.load_error = ''
    return k._load_cells(level)
    return node.clone_from(src)


BrickwardKernel._spawn = staticmethod(_spawn)

# ====================================================================== 关卡集
LEVEL_ART = {
    '直推教学': """
###########
#P..C..o..#
#.....#...#
#.....#...#
####D######
#........E#
###########
""",
    '钥匙金门': """
###########
#P..C..o..#
#.........#
#..k......#
#####D#####
#.........#
#####g#####
#........E#
###########
""",
    '拉杆切门': """
#############
#P..L...#...#
#.......D...#
#.......#...#
#.......#..E#
#############
""",
}


def _mk_links(name: str, level) -> list:
    """按关名给出连接表（"开关 → 门"，BQM 编辑器的核心机制）。"""
    c = _cells(level)
    if name == '直推教学':
        b = next(iter(c['buttons']))
        d = next(iter(c['doors']))
        return [{'from': b, 'to': d, 'mode': 'open'}]
    if name == '钥匙金门':
        # 按钮开的是**普通门**（通往金门的路），金门必须用钥匙 ——
        # 早先把按钮连到金门上，钥匙就成了摆设（关卡"能解"但玩法是假的）。
        b = next(iter(c['buttons']))
        d = next(iter(c['doors']))
        return [{'from': b, 'to': d, 'mode': 'open'}]
    if name == '拉杆切门':
        lv = next(iter(c['levers']))
        d = next(iter(c['doors']))
        return [{'from': lv, 'to': d, 'mode': 'open'}]
    return []


def _build_levels():
    out = []
    for i, (name, art) in enumerate(LEVEL_ART.items()):
        base = parse_level(art, name)
        lv = parse_level(art, name, _mk_links(name, base))
        out.append(lv)
    return tuple(out)


LEVELS = _build_levels()

DEMO_SEEDS = (20261014, 7, 13, 42, 101, 777)


def generate(seed: int) -> dict:
    """确定性生成关：模板 + 种子驱动的元素微调，**并由求解器保证可解**。

    做法：在模板的自由格上按种子换手（挪一格箱子）+ 撒 0~2 个尖刺，跑求解器。
    有解才收；无解就换下一个随机抽（最多 24 次）→ **同种子结果唯一**。
    """
    from core._rng import Rng
    names = list(LEVEL_ART)
    base_name = names[seed % len(names)]
    art = LEVEL_ART[base_name]
    base = parse_level(art, f'gen-{seed}')

    for attempt in range(24):
        rng = Rng(seed * 1000 + attempt)
        rows = [list(r) for r in base['rows']]
        free = [(i, j) for i in range(base['h']) for j in range(base['w'])
                if rows[i][j] == FLOOR]
        for k in range(len(free) - 1, 0, -1):        # Fisher-Yates（Rng 只给原始接口）
            m = rng.next_int(0, k)
            free[k], free[m] = free[m], free[k]
        crates = [(i, j) for i in range(base['h']) for j in range(base['w'])
                  if rows[i][j] == CRATE]
        if crates and free:                          # 挪一格箱子（换手）
            old = crates[0]
            new = free.pop()
            rows[old[0]][old[1]] = FLOOR
            rows[new[0]][new[1]] = CRATE
        for p in free[: rng.next_int(0, 2)]:          # 撒 0~2 个尖刺
            rows[p[0]][p[1]] = SPIKE
        cand = parse_level('\n'.join(''.join(r) for r in rows),
                           f'gen-{seed}', _mk_links(base_name, base))
        if not validate_level(cand)[0]:
            continue
        if make_offline(cand, seed).solve(max_nodes=60000) is not None:
            return cand
    return parse_level(art, f'gen-{seed}', _mk_links(base_name, base))


def _exit_of(level):
    for i, row in enumerate(level['rows']):
        if EXIT in row:
            return (i, row.index(EXIT))
    return None


def solve_level(level) -> tuple:
    """求解一个关卡 dict，返回 ``(可解?, 方向串)``（验收/编辑器"试解"共用）。"""
    ok, _reason = validate_level(level)
    if not ok:
        return False, ''
    path = make_offline(level).solve()
    return (path is not None), (''.join(path) if path else '')
