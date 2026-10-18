# -*- coding: utf-8 -*-
"""脚手架（P5-a）：生成**能跑通 headless 验收**的新玩法内核 + 配置树 + 单测模块。

## 为什么 `new_test.py` 不够，还要这一个

`new_test.py` 生成的是"一个测试模块"，它**不动**引擎的任何注册表。
而新增一个**玩法内核**要同步 7 处（本批实测清点）：

| # | 同步点 | 漏了会怎样 |
|---|--------|-----------|
| 1 | `world3d/games/<名>/kernel.py` + `__init__.py` | 内核本体 |
| 2 | `configs/cordis.<名>.yml` | 配置树（门禁 `readme-config-count` 数它） |
| 3 | `world3d/host/registry.py` 的 `SERVICES` | **配置树挂不起来**（且报错是"服务未就绪"——见下） |
| 4 | `world3d/host/tree.py` 的 `WANT_<名>` | 就绪判定缺服务 |
| 5 | `world3d/cli/config_boot.py` 的 `CONFIGS` | `config_boot` 里没有它 |
| 6 | `tests/test_<名>.py` + `run_all_tests.MODULES` | 单测不跑 |
| 7 | README 的内核/配置树/示例计数 + 测试清单 | 门禁 `readme-*-count` 变红 |

其中 **3 / 4 / 5 是纯机械的三张表**，正是"漏一处就静默失效"的类型：
`registry` 漏了 → cordis 的 Loader **吞掉构造期异常** → `await_ready` 8 秒超时、
只报"服务不就绪"，真实原因（`KeyError: 未知服务短名`）根本看不见（P0-3 为此查了半天）。
所以三张表交给脚本改，**改完立刻 `ast.parse` 守卫，改坏就抛、不写盘**。

## 实战检验（本批做过，不是"应该能行"）

生成探针内核 → `python -m world3d.cli.config_boot <探针>` 打印 `== <探针>_OK ==`
→ `python -m tests.test_<探针>` 全过 → `python scripts/verify.py` **变红**并精确指出
"README 写 9 个内核、实际 10"（这就是"脚手架 + 门禁"的配合）→ `--undo` 之后
`git status` 干净、门禁回绿。每一步的实际输出见
`docs/P5_内核脚手架与CI_design_note.md` 的验收表。

用法::

    python scripts/new_kernel.py scavenger            # 生成 + 登记三张表
    python scripts/new_kernel.py scavenger --undo     # 撤销（含从三张表摘除）
    python scripts/new_kernel.py scavenger --force    # 覆盖已存在的文件
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import re
import string
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

from new_test import (  # noqa: E402  复用同一套登记器与写盘工具
    register_test, unregister_test, write_preserving_eol,
)

#: 生成物在文件里留下的标记（`--undo` 靠它精确摘除；也让 diff 一眼看出是脚手架产物）
MARK = '# ← P5-a 脚手架生成'
SEED = 20261014


# ============================================================== 模板：内核本体
KERNEL_TEMPLATE = '''# -*- coding: utf-8 -*-
"""${title} 内核 —— ${blurb}（Cordis 服务化）。

（**为什么值得单独一个内核**：它证明了什么、与既有内核的区别在哪。这段是给人看的，
请替换掉 —— 它是这个文件里唯一必须由作者回答的问题。）

## 三条约定（`cli/config_boot.py` 的 `run_generic` 按它们办）

* `inject = []` —— **纯逻辑内核**：headless 可跑、不碰窗口/GL；
* `status()` → `'playing'` / `'won'`（初值必须是 `playing` 或 `prep`）；
* `auto_step(dt)` —— 确定性推进，直到 `status() == 'won'`。

想消费共同核（economy / rules / particles …）：把短名加进 `inject`，同时补上
`configs/cordis.${name}.yml` 的 entry 与 `world3d/host/tree.py` 的 `WANT_${upper}`。
**要取 GL 句柄**（`render`）则必须另写专属 runner（`cli/config_boot.py` 的 `RUNNERS`）
并把 `CONFIGS` 那一行的第三项改成 `True` —— 否则 `await_ready` 会一直等
`render.live_count() > 0`，8 秒后报"服务不就绪"（真实原因被 cordis 吞掉）。
"""
from __future__ import annotations

import os
import sys

# ⚠ 自举块必须算到**仓库根**：内核住 games/<名>/，比仓库根深三层
# （kernel.py -> <名>/ -> games/ -> world3d/ -> 仓库根）。
# 少算一层会把 `world3d/` 本身塞进 sys.path，于是 `world3d/platform/` 遮蔽
# **标准库 platform**（pyglet 自己 `import platform`）——
# 症状是 `AttributeError: module 'platform' has no attribute 'system'`，
# 而报错位置离原因十万八千里。`scripts/check_imports.py` 会把这条钉住。
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import world3d._cordis_locate as _cordis_locate  # noqa: E402

_cordis_locate.ensure()

from cordis import Service  # noqa: E402

__all__ = ['${cls}', 'TRACK', 'GOAL', 'STEP_HZ', 'DEFAULT_SEED', 'MAX_PER_CELL',
           'fragment_table', 'make_offline']

# ===================================================================== 常量
#: 走廊格数（位置 0 .. TRACK-1）
TRACK = 32
#: 挖到这么多碎片即胜。**必须 <= TRACK × 每格下限**，否则关卡永不可胜
#: （`fragment_table` 保证每格至少 1 枚 ⇒ 下界 = TRACK = 32 > 24）。
GOAL = 24
#: 每秒走几格（时间步长 = 1 / STEP_HZ）。`auto_step` 按它把 dt 量化成整数步。
STEP_HZ = 8.0
#: 每格碎片上限（下限是 1）
MAX_PER_CELL = 2
#: 默认种子（配置树的 `config.seed` 会覆盖它 —— 见 cordis.${name}.yml）
DEFAULT_SEED = ${seed}


# ===================================================================== 纯函数
def fragment_table(seed: int):
    """每格碎片数（1..MAX_PER_CELL），**确定性**。

    刻意**不用 `random`**：那要赌"跨平台/跨 Python 版本逐位一致"，
    而录制、断言、回放全靠这条。自己写一个 LCG，成本 4 行。
    """
    out = []
    h = int(seed) & 0x7FFFFFFF
    for _ in range(TRACK):
        h = (h * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(1 + ((h >> 7) & (MAX_PER_CELL - 1)))
    return out


# ===================================================================== 内核
class ${cls}(Service):
    """一格一步的确定性拾荒模拟（零 GL、零外部依赖）。

    `inject` 为空 ⇒ 玩法语义自足，headless 验收不需要任何句柄；
    也正因为如此，`config_boot.CONFIGS` 里它的 `require_gl` 是 `False`。
    """

    inject = []

    def __init__(self, ctx, config=None):
        super().__init__(ctx, '${name}')
        cfg = config or {}
        self._init_state(int(cfg.get('seed', DEFAULT_SEED)))

    # --------------------------------------------------------- 状态
    def _init_state(self, seed: int):
        self.seed = int(seed)
        self.track = fragment_table(self.seed)   # 每格剩余碎片
        self.pos = 0                              # 当前位置
        self.dir = 1                              # +1 向右 / -1 向左
        self.collected = 0
        self.steps = 0
        self.acc = 0.0                            # 时间累加器（量化用）
        self.state = 'playing'

    def reset(self, seed=None):
        self._init_state(self.seed if seed is None else int(seed))
        return self

    def status(self) -> str:
        return self.state

    def remaining(self) -> int:
        return sum(self.track)

    # --------------------------------------------------------- 推进
    def step(self):
        """走一格：捡走当前格的碎片，到端点就掉头。终局后再调是幂等的。"""
        if self.state != 'playing':
            return self.state
        self.collected += self.track[self.pos]
        self.track[self.pos] = 0
        self.steps += 1
        nxt = self.pos + self.dir
        if not (0 <= nxt < TRACK):               # 撞端点：掉头，再走一格
            self.dir = -self.dir
            nxt = self.pos + self.dir
        self.pos = nxt
        if self.collected >= GOAL:
            self.state = 'won'
        return self.state

    def auto_step(self, dt: float):
        """按 `STEP_HZ` 把 `dt` 量化成整数步 —— **步数只由累计时间决定**，
        与"一帧多少毫秒"无关（否则同样 3 秒在 60Hz 与 240Hz 下走出不同结局）。"""
        if self.state != 'playing':
            return self.state
        self.acc += float(dt)
        quantum = 1.0 / STEP_HZ
        while self.acc >= quantum and self.state == 'playing':
            self.acc -= quantum
            self.step()
        return self.state


# ===================================================================== 离线装配
def make_offline(seed: int = DEFAULT_SEED, ctx=None) -> '${cls}':
    """**离线内核**（不挂 ctx）：单测 / 求解器 / 回放共用这一条装配路径。

    用 `__new__` 绕过 `Service.__init__`（它要 `ctx.reflect.provide` 真正挂服务）。
    **只留一个入口** —— 早先 brickward 的 `solve_level` 与 `generate` 各写一份装配，
    漏一个字段就静默解出**错的**答案。
    """
    k = ${cls}.__new__(${cls})
    k.ctx = ctx
    k._init_state(int(seed))
    return k
'''


# ============================================================== 模板：包 __init__
GAMES_INIT_TEMPLATE = '''# -*- coding: utf-8 -*-
"""玩法内核：${name}。"""
'''


# ============================================================== 模板：配置树
CONFIG_TEMPLATE = '''# ${title} · cordis.yml 配置树（P5-a 脚手架生成）
#
# 本文件同时是 demo 的**实际挂载声明**：demo 的 `_mount_kernel` 走
#   world3d.host.tree.mount_tree(config_path('cordis.${name}.yml'), want=WANT_${upper})
# ——「demo 怎么挂的 = headless 验证怎么挂的」，接线只有这一份。
#
# 挂两个服务：
#   * `${name}`  —— 内核自身。`inject = []`（纯逻辑、零 GL）⇒ `cli/config_boot`
#     对它的 `require_gl` 是 False（见 config_boot.CONFIGS 那一行的第三项）。
#   * `render`   —— 给将来的 demo 记账 GL 资源用；**内核自己不取句柄**。
#     不需要渲染就删掉这三行，并把 host/tree.py 的 WANT_${upper} 一并改掉。
#
# `config` 会透传给服务构造 —— 这里用它覆盖内核默认种子，用来证明
# "配置树的声明真的到达了服务"（内核默认 DEFAULT_SEED=${seed}）。
#
# 运行（headless 验证）：python -m world3d.cli.config_boot ${name}
- name: ${name}
  id: ${name}_kernel
  config:
    seed: ${seed}
- name: render
  id: render
'''


# ============================================================== 模板：单测
TEST_TEMPLATE = '''# -*- coding: utf-8 -*-
"""${title} 内核单测（纯逻辑，**零 GL** ⇒ headless 可断）。

自带 runner：直接 `python -m tests.test_${name}`，全部通过时打印
`ALL_${upper}_TESTS_PASSED`（聚合器 `scripts/run_all_tests.py` 认这个标记）。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from world3d.games.${name}.kernel import (  # noqa: E402
    DEFAULT_SEED, GOAL, MAX_PER_CELL, STEP_HZ, TRACK,
    fragment_table, make_offline,
)

tests = []


def test(name):
    def deco(fn):
        tests.append((name, fn))
        return fn
    return deco


@test('碎片表：确定性 + 只含 1..MAX_PER_CELL')
def t_table():
    a, b = fragment_table(DEFAULT_SEED), fragment_table(DEFAULT_SEED)
    assert a == b, '同种子两次生成不一致'
    assert fragment_table(DEFAULT_SEED + 1) != a, '换种子分布应变化'
    assert len(a) == TRACK
    assert all(1 <= n <= MAX_PER_CELL for n in a), f'越界: {sorted(set(a))}'


@test('可胜性：每格至少 1 枚 ⇒ 总碎片数 >= GOAL')
def t_solvable():
    for sd in range(8):
        assert sum(fragment_table(sd)) >= GOAL, f'种子 {sd} 总量不足 GOAL'


@test('auto_step：跑到 won，且不超过 GOAL+MAX_PER_CELL-1')
def t_autoplay():
    k = make_offline(DEFAULT_SEED)
    assert k.status() == 'playing', '初值必须是 playing（run_generic 会断言）'
    for _ in range(int(60.0 * 60)):            # 最多 60 秒
        k.auto_step(1.0 / 60.0)
        if k.status() != 'playing':
            break
    assert k.status() == 'won', f'未收尾：{k.status()} steps={k.steps}'
    assert GOAL <= k.collected <= GOAL + MAX_PER_CELL - 1, f'超收: {k.collected}'


@test('时间量化：步数只由累计时间决定（帧率只带来 <=1 步的量化差）')
def t_time_quantized():
    steps = []
    for hz in (60, 240, 1000):                   # 各跑 1 秒
        k = make_offline(DEFAULT_SEED)
        for _ in range(hz):
            k.auto_step(1.0 / hz)
        steps.append(k.steps)
    assert max(steps) - min(steps) <= 1, f'帧率影响了步数：{steps}（应只差 <=1）'
    assert all(s <= int(STEP_HZ) + 1 for s in steps), f'步数超出量化上界：{steps}'


@test('边界：走廊两端掉头，位置永不越界，且走遍整条走廊')
def t_bounds():
    # 把碎片清零 + 把已捡数压成负数 ⇒ 永远达不到 GOAL，才能把"走遍整条走廊"
    # 的边界行为测完（否则 24 枚一到就 won，后半个走廊根本没走到）。
    k = make_offline(DEFAULT_SEED)
    k.track = [0] * TRACK
    k.collected = -10 ** 6
    seen = set()
    for _ in range(4 * TRACK):
        k.step()
        assert 0 <= k.pos < TRACK, f'越界 pos={k.pos}'
        seen.add(k.pos)
    assert seen == set(range(TRACK)), \
        f'没有走遍整条走廊，缺 {sorted(set(range(TRACK)) - seen)}'
    assert k.status() == 'playing', '人为压负后竟然还是终局'


@test('确定性：同种子 + 同样 dt 序列 ⇒ 轨迹逐项相同')
def t_deterministic():
    a, b = make_offline(DEFAULT_SEED), make_offline(DEFAULT_SEED)
    for _ in range(400):
        for k in (a, b):
            k.auto_step(1.0 / 60.0)
    assert (a.steps, a.pos, a.dir, a.collected, a.status()) == \\
           (b.steps, b.pos, b.dir, b.collected, b.status())
    assert a.track == b.track


@test('终局幂等：won 之后再 step 不改变任何状态')
def t_terminal_idempotent():
    k = make_offline(DEFAULT_SEED)
    while k.status() == 'playing':
        k.step()
    snap = (k.steps, k.pos, k.collected, k.track[:])
    for _ in range(10):
        k.step()
        k.auto_step(1.0)
    assert (k.steps, k.pos, k.collected, k.track[:]) == snap, '终局后状态被改动'


def main() -> int:
    print(f'[test_${name}] {len(tests)} 项（STEP_HZ={STEP_HZ} TRACK={TRACK} GOAL={GOAL}）')
    bad = 0
    for nm, fn in tests:
        try:
            fn()
            print(f'  ok: {nm}')
        except AssertionError as e:              # noqa: PERF203
            bad += 1
            print(f'  FAIL: {nm}\\n        {e}')
    if bad:
        print(f'ALL_${upper}_TESTS_FAILED（{bad}/{len(tests)}）')
        return 1
    print(f'ALL_${upper}_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
'''


def _cls_name(name):
    return ''.join(part.capitalize() for part in re.split(r'[_-]', name)) + 'Kernel'


def render_kernel(name):
    return string.Template(KERNEL_TEMPLATE).substitute(
        name=name, cls=_cls_name(name), title=name, upper=name.upper(),
        seed=SEED, blurb='一句话说清它模拟什么')


def render_games_init(name):
    return string.Template(GAMES_INIT_TEMPLATE).substitute(name=name)


def render_config(name):
    return string.Template(CONFIG_TEMPLATE).substitute(
        name=name, upper=name.upper(), title=name, seed=SEED)


def render_test(name):
    return string.Template(TEST_TEMPLATE).substitute(
        name=name, upper=name.upper(), title=name)


# ================================================================= 三张表的登记
def _rewrite(path, transform, quiet=False):
    """读 → 变换 → **`ast.parse` 守卫** → 写。返回 (是否改动, 说明)。

    `ast.parse` 在写盘**之前**：这三张表被改坏的话，整个仓库的导入都会炸，
    而症状（"服务不就绪"/"配置树加载失败"）离改动点很远 —— 所以宁可当场抛。
    """
    text = io.open(path, encoding='utf-8').read()
    new, note = transform(text)
    if new == text:
        return False, note
    ast.parse(new)
    write_preserving_eol(path, new)      # 保行尾：见 new_test.write_preserving_eol
    if not quiet:
        print(f'[new] {note}')
    return True, note


def _registry_edit(name, cls):
    def f(text):
        if f"'{name}':" in text:
            return text, f'registry.SERVICES 已有 {name!r}，跳过'
        m = re.search(r'^(SERVICES = \{\n)(.*?)^\}$', text, re.S | re.M)
        if not m:
            raise RuntimeError('找不到 registry.SERVICES —— 请手工加一行')
        line = (f"    '{name}': 'world3d.games.{name}.kernel:{cls}',"
                f"   {MARK}\n")
        new = text[:m.end(2)] + line + text[m.end(2):]
        return new, f'登记 registry.SERVICES：{name} -> games.{name}.kernel:{cls}'
    return f


def _tree_edit(name, cls=None):
    up = name.upper()

    def f(text):
        if f'WANT_{up} =' in text:
            return text, f'host/tree.py 已有 WANT_{up}，跳过'
        # ⚠ 插入必须是"**逐行可摘**"的：`--undo` 按行删掉标记行之后，文件要与
        # 生成前**逐字节相同**。所以不能顺手加空行垫高（第一版就踩了：
        # 加了 `\n\n\n` 垫底，撤销后留下 2 个空行，`git status` 里 tree.py 仍然 M）。
        # 匹配"WANT_* 常量区末尾到 `def config_path` 之间的空行"，把新常量**紧贴**
        # 在原常量下面插进去：删掉那两行就精确回到原样。
        m = re.search(r'\n\n\ndef config_path\(', text)
        if not m:
            raise RuntimeError('找不到 host/tree.py 的 config_path 定义（或它前面的空行不符）')
        block = (f"\n{MARK}：纯逻辑内核（`inject = []`），`render` 只给 demo 用\n"
                 f"WANT_{up} = ('{name}', 'render')\n\n\ndef config_path(")
        new = text[:m.start()] + block + text[m.end():]
        # `__all__` 里也**单独加一行**（不要塞进已有那行：撤销是按行摘的，
        # 塞进 'config_path' 那行会把相邻名字一起带走）。
        al = '__all__ = [\n'
        if al not in new:
            raise RuntimeError('找不到 host/tree.py 的 __all__')
        new = new.replace(al, al + f"    'WANT_{up}',   {MARK}\n", 1)
        return new, f'登记 host/tree.py：WANT_{up}（并加进 __all__）'
    return f


def _config_boot_edit(name, cls=None):
    up = name.upper()

    def f(text):
        if f"'{name}': (" in text:
            return text, f'config_boot.CONFIGS 已有 {name!r}，跳过'
        imp = 'from world3d.host.tree import (  # noqa: E402\n'
        if imp not in text:
            raise RuntimeError('找不到 config_boot 的 host.tree import 块')
        text2 = text.replace(imp, imp + f'    WANT_{up},   {MARK}\n', 1)
        anchor = "    'cores': ("
        if anchor not in text2:
            raise RuntimeError("找不到 CONFIGS 里的 'cores' 条目")
        line = (f"    '{name}': ('cordis.{name}.yml', WANT_{up}, False),"
                f"   {MARK}\n")
        new = text2.replace(anchor, line + anchor, 1)
        return new, f'登记 config_boot.CONFIGS：{name} -> cordis.{name}.yml'
    return f


REGISTRARS = (
    ('world3d/host/registry.py', _registry_edit),
    ('world3d/host/tree.py', _tree_edit),
    ('world3d/cli/config_boot.py', _config_boot_edit),
)


def _drop_lines(path, patterns, quiet=False):
    """按正则**逐行**摘除（`--undo` 用）。返回摘掉的条数。"""
    text = io.open(path, encoding='utf-8').read()
    out, n = [], 0
    for ln in text.split('\n'):
        if any(p.search(ln) for p in patterns):
            n += 1
            continue
        out.append(ln)
    if n:
        new = '\n'.join(out)
        ast.parse(new)
        write_preserving_eol(path, new)
        if not quiet:
            print(f'[undo] {path}：摘掉 {n} 行')
    return n


def undo(name, quiet=False):
    up = name.upper()
    dropped = 0
    dropped += _drop_lines(os.path.join(ROOT, 'world3d/host/registry.py'),
                           [re.compile(rf"^    '{name}': 'world3d\.games\.{name}\.kernel:")],
                           quiet)
    dropped += _drop_lines(os.path.join(ROOT, 'world3d/host/tree.py'),
                           [re.compile(rf'^WANT_{up} = '),
                            re.compile(rf"^{re.escape(MARK)}：纯逻辑内核"),
                            re.compile(rf"^    'WANT_{up}',")], quiet)
    dropped += _drop_lines(os.path.join(ROOT, 'world3d/cli/config_boot.py'),
                           [re.compile(rf"^    '{name}': \('cordis\.{name}\.yml'"),
                            re.compile(rf"^    WANT_{up},   {re.escape(MARK)}$")], quiet)
    dropped += unregister_test(name, quiet)
    removed = []
    for rel in (f'configs/cordis.{name}.yml', f'tests/test_{name}.py',
                f'world3d/games/{name}/kernel.py', f'world3d/games/{name}/__init__.py'):
        p = os.path.join(ROOT, rel)
        if os.path.isfile(p):
            os.remove(p)
            removed.append(rel)
    d = os.path.join(ROOT, 'world3d', 'games', name)
    if os.path.isdir(d) and not os.listdir(d):
        os.rmdir(d)
    if not quiet:
        for rel in removed:
            print(f'[undo] 已删除 {rel}')
    print(f'[undo] 共摘除 {dropped} 行登记、删除 {len(removed)} 个文件')
    print('[undo] 接下来：python scripts/verify.py 应回到 VERIFY_OK；'
          'git status 应只剩你自己的改动')
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='脚手架：新玩法内核 + 配置树 + 单测')
    ap.add_argument('name', help='内核名（小写字母/下划线；如 scavenger）')
    ap.add_argument('--undo', action='store_true', help='撤销：删文件 + 从三张表摘除')
    ap.add_argument('--force', action='store_true', help='覆盖已存在的文件')
    args = ap.parse_args(argv)

    name = args.name.strip()
    if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
        print(f'[new] 名字要小写字母开头、只含小写与下划线：{name!r}')
        return 2
    if args.undo:
        return undo(name)

    files = {
        f'world3d/games/{name}/__init__.py': render_games_init(name),
        f'world3d/games/{name}/kernel.py': render_kernel(name),
        f'configs/cordis.{name}.yml': render_config(name),
        f'tests/test_{name}.py': render_test(name),
    }
    for rel, text in files.items():
        path = os.path.join(ROOT, rel)
        if os.path.exists(path) and not args.force:
            print(f'[new] {rel} 已存在 —— 要覆盖请加 --force')
            return 2
        if rel.endswith('.py'):
            ast.parse(text)                      # 模板自身先过一遍语法
    for rel, text in files.items():
        path = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        io.open(path, 'w', encoding='utf-8', newline='\n').write(text)
        print(f'[new] 已生成 {rel}（{len(text.splitlines())} 行）')

    print()
    cls = _cls_name(name)
    for rel, maker in REGISTRARS:
        _rewrite(os.path.join(ROOT, rel), maker(name, cls))
    register_test(name)

    print()
    print('[new] 接下来（**跑一次门禁，它会列出还差哪几处**）：')
    print(f'      python -m world3d.cli.config_boot {name}   # 期望 == {name.upper()}_OK ==')
    print(f'      python -m tests.test_{name}                # 期望 ALL_{name.upper()}_TESTS_PASSED')
    print('      python scripts/verify.py                  # 它会红：README 的计数还没同步')
    print('      → README 的「内核数」「配置树数」各 +1（门禁 readme-kernel-count / -config-count）')
    print(f'      → README「测试与验收」加一行 python -m tests.test_{name}（readme-test-modules 会核对）')
    print('      → 若要配演示：demo 已迁往独立仓 siwpan/3dworld-demos，'
          '在那里用其 scripts/new_demo.py 生成并补索引行')
    return 0


if __name__ == '__main__':
    sys.exit(main())
