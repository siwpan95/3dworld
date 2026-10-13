# -*- coding: utf-8 -*-
"""**《砖境 / Brickward》** —— 机关/道具内核（BQM 机制致敬 · B1）。

> 机制致敬 **BlockQuest Maker（《砖块迷宫建造者》，Wonderland Kazakiri 2018）**。
> **不是移植、不是复刻**：名称/美术/音效/关卡数据/联网经济一律自创或不做；
> 只吸收它的**玩法机制**（方块地牢 + 一格一步 + 机关组合 + 编辑器）。
> 合规边界见 `docs/BQM机制致敬_复刻可行性.md` §2。

## B1 范围（本文件）

* **一格一步的确定性模拟**（同关卡 + 同输入 ⇒ 同结果，可回放、可断言）；
* **元素表**：推箱 / 按钮 / 拉杆 / 门 / 金门+钥匙 / 尖刺 / **炸弹** / **火球**；
  出口为终局；
* **"连接"（开关 → 门）**：BQM 编辑器的核心机制 —— 声明式绑定，用
  `engine.orchestration.Bus` 做级联（P0-5 自研的总线在这里第一次吃到玩法）；
* **BFS 求解器**（状态空间有界）—— headless 验收靠它证明"每关可解且能回放到胜"；
* **关卡 = 纯数据**（ASCII 图 + 连接表），与编辑器/生成器/内核同 schema（三端同源）。

B2b（滚石/史莱姆）、B3（编辑器 UI：道具栏 + 连接编辑）、
B4（渲染/手感/成片）见 `docs/BQM机制致敬_复刻可行性.md` §6。

## 元素表

| 码 | 元素 | 语义 |
|----|------|------|
| `#` | 墙 | 不可通过（实体也不能推入） |
| `.` | 地板 | 可通过 |
| `P` | 玩家起点 | 唯一 |
| `C` | 箱子 | 可推、不可拉；可压按钮；**可推上尖刺并挡住它**；可被炸掉/烧掉 |
| `o` | 按钮 | **持续型**：被玩家**或箱子**压住时处于按下 |
| `L` | 拉杆 | **切换型**：玩家**走进**时翻转（箱子压不住，站着不动也不会反复翻） |
| `D` | 门 | 关着时挡人挡实体；由连接控制开合 |
| `g` | 金门 | 有钥匙则开启并**消耗一把**（本步只开门、人不动）；无钥匙挡路 |
| `k` | 钥匙 | 拾取（计数制，不是占格物品） |
| `^` | 尖刺 | 玩家踩上即败；箱子压上后失效（经典"用箱垫路"） |
| `b` | 炸弹 | 可推；**推动即点引信**（`FUSE` 步），归零爆炸 |
| `f` | 火球 | 可推；**推动即点燃**，此后每步朝该方向滚一格，撞硬障碍熄灭 |
| `E` | 出口 | 玩家到达即胜 |

## 炸弹与火球（B2a）

* **引信 `FUSE = 2`**：被推动即重置。世界推进里每步减 1，归零则在该格爆炸。
  在 1 格宽走廊里，2 步刚好够玩家逃出波及范围 —— 3 步会让"推完没处躲"，
  1 步又等于自杀。**推着炸弹连续走**是合法技巧（每推一次就重置）。
* **爆炸形状 `BLAST_SHAPE` = 十字 1 格**（自身 + 上下左右），不是 3×3：
  3×3 在 1 格宽走廊里必然连推炸弹的玩家一起炸，关卡设计空间会被堵死。
* 爆炸**摧毁**：箱子、炸弹（**连锁**，用队列展开）、火球（扑灭）；
  **不影响**：墙、门、金门、尖刺、钥匙、按钮、拉杆、出口。
  玩家在波及格内 → 死。
* 火球滚动：撞墙 / 关着的门 / 未开的金门 → **熄灭**；撞炸弹 → 引爆它并熄灭；
  撞箱子 → 烧毁箱子并熄灭；撞另一个火球 → 两个都熄灭；撞玩家 → 玩家死。
  **火球不烧史莱姆以外的东西还会继续滚**这条留到 B2b 与史莱姆一起定。

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
           'WALL', 'FLOOR', 'BOMB', 'FIREBALL', 'FUSE', 'BLAST_SHAPE', 'PUSHABLE',
           'parse_level', 'validate_level', 'generate',
           'solve_level', 'make_offline', 'DIRS', 'KIND_NAME']

# ---- 元素码 ----
WALL, FLOOR, PLAYER = '#', '.', 'P'
CRATE, BUTTON, LEVER = 'C', 'o', 'L'
DOOR, GOLD_DOOR, KEY, SPIKE, EXIT = 'D', 'g', 'k', '^', 'E'
BOMB, FIREBALL = 'b', 'f'                       # B2a

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
    BOMB:      dict(name='炸弹',   block=False, pushable=True),   # 推动即点引信
    FIREBALL:  dict(name='火球',   block=False, pushable=True),   # 推动即朝该方向滚
}
KIND_NAME = {k: v['name'] for k, v in ELEMENTS.items()}

#: 能推的实体（"推"对它们各有不同后果，见 `_push`）
PUSHABLE = (CRATE, BOMB, FIREBALL)

#: 炸弹引信（步）。被推动即重置为这个值；世界推进里每步减 1，归零则爆炸。
#: 取 2 而不是 3：一格宽走廊里玩家必须来得及逃出"十字 1 格"的波及范围。
FUSE = 2
#: 爆炸波及形状：自身 + 上下左右（**十字 1 格**，不是 3×3）。
#: 3×3 在 1 格宽走廊里必然连玩家一起炸，"推完没处躲" ⇒ 关卡设计空间被堵死。
BLAST_SHAPE = ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1))

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
             gold=set(), keys=set(), spikes=set(), bombs=set(), fireballs=set(),
             player=None, exit=None)
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
            elif ch == BOMB:
                c['bombs'].add(p)
            elif ch == FIREBALL:
                c['fireballs'].add(p)
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
        self.bombs = set()           # 炸弹位置（未被引燃的）—— B2a
        self.fireballs = {}          # 火球位置 -> 方向；方向 None = 静止未点燃 —— B2a
        self.fuses = {}              # 已引燃炸弹 -> 剩余引信步数 —— B2a
        self.blasts = set()          # **本步**的爆炸波及格（渲染用；瞬态，不进指纹）
        self.doors = set()           # **开着**的门
        self.levers = set()          # **扳到 on** 的拉杆
        self.gold_open = set()       # 已用钥匙开过的金门
        self.keys = set()            # 地上未拾取的钥匙
        self.spike_dead = set()      # 被箱子压住（失效）的尖刺
        self.player = None
        self.keys_held = 0
        self._ignited = set()        # **本步**刚被推动的炸弹/火球（瞬态，不进指纹）
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
        self.bombs = set(c['bombs'])
        self.fireballs = {p: None for p in c['fireballs']}   # 初始静止，要被推才会滚
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
        if p in self.crates or p in self.bombs or p in self.fireballs:
            return True                          # 可推实体：挡人（但能被推走）
        if p in self.door_cells and p not in self.doors:
            return True                          # 关着的门
        if p in self.gold and p not in self.gold_open:
            return True                          # 未开的金门
        if not (0 <= p[0] < self.h and 0 <= p[1] < self.w):
            return True                          # 越界（不该发生：随包关卡四面封闭）
        return False

    # ------------------------------------------------------------- 单步模拟
    def step(self, d: str) -> str:
        """走一步（``'U'/'D'/'L'/'R'``）。返回事件名；玩法状态见 `status()`。

        **一步 = 玩家动作 → 连接对账 → 世界推进 → 触发集再对账 → 终局判定**。
        世界推进放在玩家动作之后，是因为"火球滚一格""引信减一格"都是**世界的时间**，
        要落在玩家这一步之内（否则同一局面会算出两种结果）。
        终局判定放在最后：走到出口的同时被火球烧到，算死（一步之内的赌注必须承认）。
        """
        if self._status != 'playing':
            return self._status
        assert d in DIRS, f'未知方向 {d!r}'
        self._ignited.clear()
        self.blasts.clear()
        ev = self._player_action(d)
        if ev in ('moved', 'pushed', 'ignited'):
            self.steps += 1
            self._pickup_key()
            self._step_on_lever()            # 走过拉杆即翻转（切换型）
        self._flush_switches()               # 玩家动作改变了触发集
        self._advance_world()                # 火球滚动 / 引信倒计时 / 爆炸
        self._flush_switches()               # 世界推进也可能改变触发集
        ev = self._judge_end(ev)
        self.log.append((d, ev, self.status()))
        return ev

    def _pickup_key(self):
        if self.player in self.keys:
            self.keys.discard(self.player)
            self.keys_held += 1

    def _step_on_lever(self):
        """玩家**走进**拉杆格 → 翻转。

        ⚠ 只在这里翻，且只在真的移动了的那一步翻。早先这段嵌在"结算"里，
        而 `step` 每步要结算两次（玩家动作后 + 世界推进后）⇒ 站在拉杆上不动
        也会被翻两次（净效果为 0），撞墙不动更会反复翻。**翻转属于"走进"这个动作，
        不属于"结算"**，所以它必须独立成一个只在移动后调用的函数。
        """
        if self.player not in self.lever_cells:
            return
        on = self.levers
        if self.player in on:
            on.discard(self.player)
        else:
            on.add(self.player)

    def _player_action(self, d: str) -> str:
        """玩家的那一下：走 / 推 / 开金门 / 被挡。**不做**世界推进与终局判定。"""
        di, dj = DIRS[d]
        tgt = (self.player[0] + di, self.player[1] + dj)
        if tgt in self.walls or not (0 <= tgt[0] < self.h and 0 <= tgt[1] < self.w):
            return 'blocked'
        if tgt in self.gold and tgt not in self.gold_open:
            if self.keys_held <= 0:
                return 'blocked'
            self.keys_held -= 1              # 有钥匙：本步只开门、人不动
            self.gold_open.add(tgt)
            return 'unlock'
        if tgt in self.door_cells and tgt not in self.doors:
            return 'blocked'                 # 关着的门挡人挡实体
        if tgt in self.crates or tgt in self.bombs or tgt in self.fireballs:
            beyond = (tgt[0] + di, tgt[1] + dj)
            if self._solid_for_player(beyond):
                return 'blocked'
            ev = self._push(tgt, beyond, d)  # 推：只推不拉
            self.pushes += 1
            self.player = tgt
            return ev
        self.player = tgt
        return 'moved'

    def _push(self, frm, to, d: str) -> str:
        """把 `frm` 上的可推实体推到 `to`。三种实体的**后果各不相同**。"""
        if frm in self.crates:
            self.crates.discard(frm)
            self.crates.add(to)
            if to in self.spikes:
                self.spike_dead.add(to)          # 箱压尖刺 → 失效
            return 'pushed'
        if frm in self.bombs:
            self.bombs.discard(frm)
            self.bombs.add(to)
            # ⚠ 引信是**炸弹的属性**，不是"格子属性"：旧格必须一起清掉。
            # 漏了这一行，炸弹搬走后原格仍留着倒计时 ⇒ 到点原地凭空爆炸
            # （实踩：玩家被一颗已经不存在的炸弹炸死，求解器判定"不可解"）。
            self.fuses.pop(frm, None)
            self.fuses[to] = FUSE
            self._ignited.add(to)                # 本步不倒计时（玩家才有 2 步可逃）
            return 'pushed'
        # 火球：推动 = 点燃，朝被推方向滚（本步先不滚，否则会追着玩家走）
        self.fireballs.pop(frm, None)
        self.fireballs[to] = d
        self._ignited.add(to)
        return 'ignited'

    # ------------------------------------------------------------- 世界推进
    def _advance_world(self):
        """本步的"世界时间"：引信倒计时 → 爆炸 → 火球滚动。"""
        for pos in sorted(self.fuses):
            if pos in self._ignited:
                continue
            self.fuses[pos] -= 1
        boom = sorted(p for p, n in self.fuses.items() if n <= 0)
        for pos in boom:
            self.fuses.pop(pos, None)
            self.bombs.discard(pos)
            self._explode(pos)
        for pos in sorted(self.fireballs):       # 排序：同一步多个火球，顺序必须确定
            if pos in self._ignited:
                continue
            if pos not in self.fireballs:        # 可能已被本步的爆炸/别的火球清掉
                continue
            if self.fireballs[pos] is None:      # **静止**火球（从没被推过）不滚
                continue
            self._roll_fireball(pos)
        self._ignited.clear()

    def _roll_fireball(self, pos):
        """火球朝自身方向滚一格。撞硬障碍即熄灭；烧掉挡路的软目标。"""
        d = self.fireballs[pos]
        di, dj = DIRS[d]
        nxt = (pos[0] + di, pos[1] + dj)
        if (not (0 <= nxt[0] < self.h and 0 <= nxt[1] < self.w)
                or nxt in self.walls
                or (nxt in self.door_cells and nxt not in self.doors)
                or (nxt in self.gold and nxt not in self.gold_open)):
            self.fireballs.pop(pos, None)        # 撞墙/关门/未开金门 → 熄灭
            return
        if nxt in self.bombs:                    # 撞炸弹 → 引爆自己消失
            self.bombs.discard(nxt)
            self.fireballs.pop(pos, None)
            self._explode(nxt)
            return
        if nxt in self.crates:                   # 烧毁箱子，火球也熄（否则火球太强）
            self.crates.discard(nxt)
            self.fireballs.pop(pos, None)
            return
        if nxt in self.fireballs:                # 火球对撞 → 都熄
            self.fireballs.pop(nxt, None)
            self.fireballs.pop(pos, None)
            return
        self.fireballs.pop(pos, None)
        self.fireballs[nxt] = d
        if nxt == self.player:
            self._kill_player()

    def _explode(self, center):
        """炸弹爆炸：十字 1 格波及；范围内的炸弹**连锁**引燃；软目标被摧毁。"""
        queue, seen, guard = [center], set(), 0
        while queue:
            guard += 1
            assert guard < 4096, f'爆炸连锁失控（{guard} 次）—— 引信/连锁写成了环'
            c = queue.pop(0)
            if c in seen:
                continue
            seen.add(c)
            self.blasts.add(c)
            for dr, dc in BLAST_SHAPE:
                p = (c[0] + dr, c[1] + dc)
                if not (0 <= p[0] < self.h and 0 <= p[1] < self.w):
                    continue
                self.blasts.add(p)               # 波及格先全记下（渲染要画整片火光）
                if p in self.walls or p in self.door_cells or p in self.gold:
                    continue                     # 墙/门/金门**不可摧毁**
                self.fireballs.pop(p, None)      # 火球被扑灭
                self.crates.discard(p)           # 炸掉箱子 ← 炸弹的主要用途
                if p in self.bombs and p not in seen:
                    self.bombs.discard(p)
                    self.fuses.pop(p, None)
                    queue.append(p)              # 连锁
                if p == self.player:
                    self._kill_player()

    def _kill_player(self):
        if self._status == 'playing':
            self._status = 'lost'
            self.deaths += 1

    def _judge_end(self, ev: str) -> str:
        """终局判定（世界推进**之后**跑一次）。返回这一步对外的事件名。"""
        if self._status == 'playing':
            if self.player in self.spikes and self.player not in self.spike_dead:
                self._kill_player()
            elif self.player == self.exit:
                self._status = 'won'
        if self._status == 'won':
            return 'won'
        if self._status == 'lost':
            return 'died'
        return ev

    def _flush_switches(self):
        """按「触发集变化」发连接事件。三类事件一次算清：

        * `pressed - prev` → `src_on`（进入触发态）
        * `prev - pressed` → `src_off`（离开触发态）
        * `pressed ^ prev` → `src_flip`（**变化**，`mode='toggle'` 用）

        三处都**排序**后遍历：集合迭代序不该进入玩法语义。求解器解出唯一解的前提是
        「同关同输入 ⇒ 同事件序」，而 `solve()` 的 docstring 明确承诺了这条。

        本函数**不碰拉杆**（那属于"走进"这个动作，见 `_step_on_lever`），
        因此可以安全地在一步里被调用多次（玩家动作后、世界推进后各一次）。
        """
        pressed = self._pressed_sources()
        prev = self._pressed_prev
        for src in sorted(pressed - prev):
            self.bus.emit(f'src_on:{src}')
        for src in sorted(prev - pressed):
            self.bus.emit(f'src_off:{src}')
        for src in sorted(pressed ^ prev):
            self.bus.emit(f'src_flip:{src}')
        self._pressed_prev = pressed

    def _settle(self, d=None, ev=None):
        """触发集对账 + 记录（装载路径与 `step` 共用的收口点）。"""
        self._flush_switches()
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
                    bombs=sorted(self.bombs), fuses=sorted(self.fuses.items()),
                    fireballs=sorted(self.fireballs.items()),
                    blasts=sorted(self.blasts),
                    exit=self.exit, steps=self.steps, pushes=self.pushes,
                    deaths=self.deaths, status=self._status)

    def key(self):
        """状态指纹（求解器去重用）。

        **不变式**：凡是能影响"从此刻往后还能怎么演化"的量，一个都不能漏；
        与之无关的（`steps`/`pushes`/`deaths`/`log`/`blasts`/`_ignited`）一个都不能进 ——
        漏了会让 BFS 把两个不同的局面当成同一个而剪掉正解；
        多算了会让同一局面分成多个节点、白烧内存。

        `fireballs` 是 dict（位置 → 方向）：**方向必须进指纹** —— 两个"火球在同一格"
        的局面，朝上和朝下往后滚的方向完全不同。`fuses` 同理（引信剩几步决定何时爆）。
        """
        return (self.player, frozenset(self.crates), frozenset(self.doors),
                frozenset(self.gold_open), frozenset(self.keys), self.keys_held,
                frozenset(self.levers), frozenset(self.spike_dead), self._status,
                frozenset(self.bombs), frozenset(self.fuses.items()),
                frozenset(self.fireballs.items()))

    def clone_from(self, src):
        """把 ``src`` 的状态复制过来（求解器用）。

        静态格（墙/各类 `*_cells`）按**引用共享** —— 求解器要开几万个节点，
        逐个拷贝整张图会把内存和时间都烧掉；它们在关卡存续期内确实不变。

        ⚠ **不要在这里重跑 `_settle`**：玩家可能正站在拉杆上（状态里那一脚已经
        扳过了），再跑一次会把它扳回去 —— 克隆出来的节点就是错的。
        触发集差异由拷贝 `_pressed_prev` 消除，BUS 由 `_bind_links` 重绑。

        `blasts` / `_ignited` 是**本步**的瞬态（只服务于渲染与"本步不倒计时"），
        克隆总是发生在步与步之间 ⇒ 一律清空，不进指纹。
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
        self.bombs = set(src.bombs)
        self.fireballs = dict(src.fireballs)
        self.fuses = dict(src.fuses)
        self.blasts = set()
        self.doors = set(src.doors)
        self.levers = set(src.levers)
        self.gold_open = set(src.gold_open)
        self.keys = set(src.keys)
        self.spike_dead = set(src.spike_dead)
        self.player = src.player
        self.keys_held = src.keys_held
        self._ignited = set()
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
    # ---- B2a：炸弹（引信 2 步、十字 1 格）与火球（推动即朝该方向滚）----
    # 出口（row1）只有 (2,5)/(3,5)/(4,5)/(5,5) 一条 1 格宽竖井通上去，
    # 箱子卡在竖井顶且**上面就是出口** —— 把它推上去 = 出口被自己堵死（再也推不动），
    # 所以**必须用炸弹炸掉它**。
    # 布局要点：炸弹就放在玩家正上方，连推两次到位（中途不换方向），
    # 之后玩家还有 2 步退到 (6,5) 逃出"十字 1 格"。
    # 早先那版把炸弹放在侧边走廊、要"右推两下再绕到下方上推"，中途必然多花 2 步 ——
    # 引信正好在那 2 步里烧完，玩家被炸死（BFS 直接判"不可解"，这版是它的修正）。
    '炸药开路': """
###########
#....E....#
#####C#####
#####.#####
#####.#####
#####b#####
#####P#####
###########
""",
    # 火球教学：推它一下就开始滚，烧掉挡路的箱子后熄灭。
    '火球烧箱': """
###########
#P.f..C..E#
###########
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
