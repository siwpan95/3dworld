# 更新日志

本项目采用 CalVer（`YYYY.MM.DD`），与既有 tag `v2026.10.09` 一脉相承。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布] — 《砖境 / Brickward》B3：谜题编辑器 + 可玩 demo

B1/B2 把内核立住了（11 类元素、连接绑定、BFS 求解器、13/13 可解），但**只有 headless
断言，没人能真的玩**。B3 补两件：**关卡 IO**（编辑器/CLI/内核同一个 schema）与
**可玩 + 可编辑的 demo**。至此这条线才算"有产品形态"。

### 新增

- **关卡 IO（`games/brickward/kernel.py`）**：`level_to_json` / `level_from_json` /
  `LEVEL_JSON_KEYS`。schema 就是内核吃的那个 dict（`name/h/w/rows/links/par`）。
  * 导出侧拒绝**非矩形** `rows` —— `parse_level` 会用 `.` 补齐，那是"静默改关卡"；
  * 导入侧把坐标**列表还原成元组**，并且导入即校验（不合法抛 `ValueError`）。
- **编辑器纯函数（同文件）**：`level_set_cell` / `level_add_link` /
  `level_clear_links` / `level_blank` / `prune_links`。
  **规则全在内核**、全部可 headless 测 —— demo 只负责把 `ValueError` 变成屏幕提示。
- **`examples/brickward.py`**（交互 demo，32 个示例之一）：
  * 渲染：墙走 `engine.greedy_mesh.layer_mesh` 一次建网（与 boxward 同一套地基），
    其余元素用共享小网格（cube / sphere / quad / 自建 pyramid）逐个 batch；
    **正交等距视角**（偏航 +45°，与 boxward 同一套看的），`Q/E` 切 90° 档、`P` 循环俯角；
  * 游玩：WASD/方向键走·推·点火，`Z` 重开、`N` 下一关、`R` 种子关；`--flag auto`
    = **求解器回放**（与 `config_boot brickward` 的 headless 验收同源）；
  * **编辑器**（`M`）：左键摆当前道具、右键擦成地板、`[`/`]`/滚轮换道具（15 格道具栏，
    HUD 高亮当前项 + 光标处道具预览）、`C` **连接模式**（先点开关、再点门）、
    `T` 换连接模式（open/toggle/close）、`SHIFT+C` 清空连接、`V` 校验+试解、
    `E` 导出 JSON 到 `levels/`、`I` 载入 `levels/*.json`、`B` 新建空白关；
  * 表现：程序化 SFX（走/推/点火/滚动/开锁/爆炸/失败/胜利）+ 通关/失败叠层 +
    **引信越短闪得越快**的炸弹 + 本步爆炸火光 + 编辑模式的连接虚线示意。
- **配置树**：`cordis.brickward.yml` 增加 `render`（demo 要用 `ctx.render` 记账），
  `WANT_BRICKWARD = ('brickward', 'render')`；`config_boot` 对 brickward 传
  `require_gl=False`（内核从不取 GL 句柄，否则那套"等 `live_count() > 0`"永远超时）。
- **9 项新单测**（`tests/test_brickward.py` 56 → **65 项**），其中 2 项是回归守卫。
- 剧照 `docs/brickward_a.png` / `b.png`（auto 回放的两个不同关卡中段）；
  `README.md` / `examples/README.md` 同步（31 → 32 个示例）。

### 修复 / 决策

| # | 内容 | 说明 |
|---|------|------|
| 11 | **导入必须把坐标列表还原成元组** | `validate_level` 用集合成员判定（`lk['from'] in (buttons \| levers)`），而 JSON 读回来的 `[1, 4] != (1, 4)` ⇒ 不还原的话**每一关都被判"连接源不是按钮/拉杆"**，带连接的关卡一个都读不进来。守护：`test_level_from_json_restores_tuples_regression` |
| 12 | **摆放不能做完整性校验** | 第一版 `level_set_cell` 末尾直接 `validate_level` ⇒ 空白板上摆第一格时"必然既没有 P 也没有 E" ⇒ **一步都摆不下去**，编辑器等于废掉。改成"摆放只拦结构性错误（越界/边框锁死/未知元素/唯一性），完整性是试解与导出的门槛"。守护：`test_editor_blank_board_is_buildable_step_by_step` |
| 13 | **剧照两张逐像素一样** | `--shot` 靠 auto 回放取两个时刻，而 auto 的"计划"只在换关路径里算 —— **不给任何 flag 时一次换关都不会发生**，计划恒为空、一步不动。这不是剧照机制坏了，是计划没算。守护：**两张剧照的 md5 必须不同**（渲染本身有像素级噪声，所以不引具体 hash，只引"两张不同"这个事实） |

### 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python -m tests.test_brickward` | **65/65** → `ALL_BRICKWARD_TESTS_PASSED` |
| `scripts/run_all_tests.py` | 8/8 模块 / **205 项** / `ALL_TESTS_PASSED`（上一批 196 项） |
| `python -m cli.config_boot brickward` | 7 手工关 + 6 种子 = **13/13** 可解且回放到胜 → `BRICKWARD_OK` |
| `python -m cli.config_boot all` | **10 个** OK 标记（零回归） |
| `python examples/brickward.py --smoke 3` | `EXIT_OK` |
| `python examples/brickward.py --shot` | `[shot] OK 2 files`（**两张 md5 不同** —— 同一张图重复输出会被这条抓住） |
| `scripts/verify_demos.py` 全量 | `ALL_DEMOS_OK (32 demos)` |

## [未发布] — 《砖境 / Brickward》B2b：滚石与史莱姆

机关内核的**第三个扩展包**，也是 B2 的最后一块。B2a 加的是"会自己炸"的元件，
B2b 加的是"**会自己滚 / 会自己走**"的元件 —— 它们把"世界的一步"真正变成一个
**有自己行为的东西**，而不只是玩家动作的副作用。

### 新增

- **滚石 `r`**：推动 = 朝该方向**一直滚到底**（不可停）。沿途**压死史莱姆、压灭火球、
  把尖刺压失效**，且**都不减速**。被墙 / 箱子 / 炸弹 / 另一块滚石 / 关着的门挡住，
  开着的门不挡。
  > **滚石压不到玩家 —— 这条不需要判定，是可以证明的**：它只朝"推它的人的反方向"滚，
  > 推的人一定在后方，而一次滚动是一条不走回头路的直线 ⇒ 玩家不可能出现在路径前方。
- **史莱姆 `s`**：每 `SLIME_PERIOD = 2` 步行动一次，朝玩家**贪心**挪一格（先走偏离大的
  那条轴，被挡就换轴，都挡则不动）。纯贪心、无随机 ⇒ 整局依旧确定。
  * 玩家走进它的格 → 玩家死（它不是"挡路"，是"送死"）；
  * 它走进玩家的格 → 玩家死；
  * 它自己踩尖刺 → 死（**尖刺仍在**，不是同归于尽）；
  * 它踩到炸弹 → 炸弹引爆、同归于尽（"用炸弹引怪"是正经解法）；
  * 被爆炸 / 火球 / 滚石 → 死。
- **世界时钟 `tick`**：指纹里放 **`tick % SLIME_PERIOD` 相位**而不是绝对值。
- **两个手工关**：
  - 「**滚石垫路**」：滚石挡在唯一通路上，推一下它一路滚过去，**顺手把整条尖刺走廊
    压失效**（5 个），玩家随后走过去才安全 —— 12 步；
  - 「**引怪入瓮**」：史莱姆堵住唯一下行口（1 格宽走廊里"走过去"和"等它过来"都是送死），
    只能推滚石把它压死 —— 13 步。
- **14 项新单测**（`tests/test_brickward.py` 42 → **56 项**）。

### 修复 / 决策

| # | 内容 | 说明 |
|---|------|------|
| 9 | `tick` **只进相位、不进绝对值** | 史莱姆的行动只取决于"这一步该不该动"。放绝对值 = 把同一局面拆成 `PERIOD` 份白烧内存；完全不放 = 行动时刻算错。指纹里是 `tick % SLIME_PERIOD` |
| 10 | **机制在册，但随包关卡从没触发过** | 第一版「引怪入瓮」把滚石放在玩家手边，第 2 步就把史莱姆压死了 ⇒ **史莱姆 AI 一次都没跑过**。关卡能通关、单测全绿，机制却等于没上线。改成"滚石离玩家 2 格、史莱姆堵住唯一下行口"，玩家走 3 步才够得着石头，这段时间史莱姆已经挪了一格 |

> 第 10 条是这一批最值得记的**验收**教训：**"能通关" ≠ "机制被驱动过"**。
> 前者求解器一跑就知道，后者得逐元素点名检查。
> 同类的机械门禁是 `test_elements_table_covers_art`（表里每个元素都必须在某关出现过），
> B2b 让它多了一层"要真的动起来"。

### 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python -m tests.test_brickward` | **56/56** → `ALL_BRICKWARD_TESTS_PASSED` |
| `scripts/run_all_tests.py` | 8/8 模块 / **196 项** / `ALL_TESTS_PASSED`（上一批 182 项） |
| `python -m cli.config_boot brickward` | 7 手工关 + 6 种子 = **13/13** 可解且回放到胜 → `BRICKWARD_OK` |
| `python -m cli.config_boot all` | **10 个** OK 标记（零回归） |

## [未发布] — 《砖境 / Brickward》B2a：炸弹与火球

机关内核的**第二个扩展包**。B1 已经把确定性模拟 + 连接绑定 + BFS 求解器立住，
B2a 加的是"**会自己动、会自己炸**"的元件 —— 它们把"一步"从"玩家的一步"
扩展成"玩家的一步 + 世界的一步"，也是最容易写出静默 bug 的一类。

### 新增

- **炸弹 `b`**（`games/brickward/kernel.py`）：
  - 可推；**推动即点引信**（`FUSE = 2`），世界推进里每步减 1，归零在该格爆炸；
    推着它连续走是合法技巧（每推一次重置引信）；
  - 爆炸形状 `BLAST_SHAPE` = **十字 1 格**（自身 + 上下左右），不是 3×3；
  - 爆炸**摧毁**：箱子、炸弹（**连锁**，队列展开）、火球（扑灭）；
    **不影响**：墙、门、金门、尖刺、钥匙、按钮、拉杆、出口；玩家在波及格内 → 死；
  - `blasts` 记录**本步**的波及格（含不可摧毁物）供渲染画火光，属瞬态，不进状态指纹。
- **火球 `f`**：
  - 可推；**推动即点燃**并朝被推方向滚，此后每步滚一格（本步先不滚，
    否则会"追着玩家走"）；
  - 撞墙 / 关着的门 / 未开金门 → **熄灭**；撞炸弹 → 引爆它并熄灭；
    撞箱子 → 烧毁箱子并熄灭；撞另一个火球 → 双双熄灭；撞玩家 → 玩家死；
  - **未被推过的火球静止不动**（方向为 `None`）。
- **两个手工教学关**：
  - 「**炸药开路**」：出口区的 1 格宽竖井被箱子堵死、箱子上面就是出口（推上去 = 自己堵死出口），
    **必须**用炸弹炸掉它 —— 9 步；
  - 「**火球烧箱**」：推火球一下就开始滚，烧掉挡路的箱子后熄灭 —— 10 步。
- **12 项新单测**（`tests/test_brickward.py` 30 → **42 项**），其中 2 项是回归守卫。

### 修复

| # | 症状 | 根因 |
|---|------|------|
| 7 | 炸弹搬走后**原格凭空爆炸**，把已走开的玩家炸死，整关被 BFS 判"不可解" | 引信写在 `fuses[格子]` 上，`_push` 只往新格写、忘 `pop` 旧格 —— **引信挂错了主体**（它是炸弹的属性，不是格子属性） |
| 8 | 静止火球一装载就"滚" → `KeyError: None`，整个求解器炸掉 | `_advance_world` 对所有火球都调 `_roll_fireball`，而"没被推过"的火球方向是 `None`，`DIRS[None]` 直接炸 |

顺带在重构里堵掉一处**同源缺陷**：`_flush_switches`（触发集对账）一步里要跑两次
（玩家动作后 + 世界推进后），而拉杆翻转原先嵌在"结算"里 ⇒ 站在拉杆上会被翻两次
（净效果为 0）、撞墙不动更会反复翻。现在拉杆翻转独立成 `_step_on_lever()`，
**只在真的移动了的那一步**调用，`_flush_switches` 因此可以安全地跑任意多次。

### 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python -m tests.test_brickward` | **42/42** → `ALL_BRICKWARD_TESTS_PASSED` |
| `scripts/run_all_tests.py` | 8/8 模块 / **182 项** / `ALL_TESTS_PASSED`（上一批 170 项） |
| `python -m cli.config_boot brickward` | 5 手工关 + 6 种子 = **11/11** 可解且回放到胜 → `BRICKWARD_OK` |
| `python -m cli.config_boot all` | **10 个** OK 标记（零回归） |

> 数值是**实测定**的：`FUSE=2` + 十字 1 格。1 步等于自杀，3 步会让"1 格宽走廊"
> 失去设计空间，3×3 在 1 格宽走廊里必然连推炸弹的玩家一起炸。
> 第一版「炸药开路」把炸弹放在侧边走廊，要"右推两下再绕到下方上推"，中途必然多花 2 步、
> 引信正好烧完、玩家被炸死 —— BFS 直接判"不可解"。**数值与关卡互相约束，
> 必须先定数值 → 再画关 → 用求解器证明可解。**

## [未发布] — 《砖境 / Brickward》B1：机关内核 + 连接绑定 + 求解器

BQM（《砖块迷宫建造者》）**机制致敬**的第二阶段落地。合规边界见
`docs/BQM机制致敬_复刻可行性.md` §2 —— 名称/美术/音效/关卡/联网经济一律自创或不做，
只吸收玩法机制（方块地牢 + 一格一步 + 机关组合 + 编辑器）。

### 新增

- **`games/brickward/kernel.py`**（纯逻辑，`inject = []`，零 GL、零外部依赖）：
  - **元素表 11 类**：`#` 墙 / `.` 地板 / `P` 玩家 / `C` 箱子 / `o` 按钮 / `L` 拉杆 /
    `D` 门 / `g` 金门 / `k` 钥匙 / `^` 尖刺 / `E` 出口；`ELEMENTS` 表带
    `block` / `pushable` 标志，供编辑器（B3）与渲染（B4）共用；
  - **一格一步的确定性模拟**：同关卡 + 同输入 ⇒ 同结果（可回放、可断言）；
  - **「连接」绑定**（BQM 编辑器的核心机制）：用 P0-5 自研的
    `engine.orchestration.Bus` 做级联，`mode = open | close | toggle`，
    事件 `src_on` / `src_off` / `src_flip`；
  - **BFS 最短解求解器**：状态指纹去重 + `DIR_ORDER` 固定 ⇒ **同关同解**
    （方向串可直接当断言用）；`make_offline(level)` 是 headless 求解/试解的唯一入口；
  - **`validate_level`**：装载前拒绝缺 `P` / 缺 `E` / 坏连接源 / 坏连接目标 /
    连到金门 / 未知模式 —— 全部给**明确原因**（编辑器要用）；
  - **3 个手工教学关**（直推教学 / 钥匙金门 / 拉杆切门）+ **`generate(seed)`**
    种子生成器（模板 + 换手 + 撒尖刺，**由求解器把关可解**，同种子 byte 级一致）；
  - **越界即挡**：`step` 不再假设"画图的人写了边框"（此前会走出地图）。
- **`configs/cordis.brickward.yml`** + `host/registry.py` 注册短名 `brickward`
  + `host/tree.py` 的 `WANT_BRICKWARD`（**不列 `render`** —— B1 压根不用 GL）。
- **`cli/config_boot.py`** 新增 `run_brickward`：手工关 + 6 个种子全部
  「求解器解出 → **回放到胜** → 再解一次逐字一致」；`--seeds` 对
  `brickward` 生效。
- **`tests/test_brickward.py`**（**30 项**，零 GL）：元素表完备性、校验器正反例、
  墙/边界、门与拉杆、三种连接模式真值表、金门与钥匙、箱子/按钮/尖刺、
  状态指纹完备性、克隆独立性、求解器最短性/确定性/不可解、生成器、服务面与快照。

### 修复（都是"静默失效"，不报错、测试还是绿的）

| # | 症状 | 根因 |
|---|------|------|
| 1 | 三关全报"可解"，回放却全是**穿模** | `doors` 一个集合既表示"哪里有门"又表示"哪扇门开着" ⇒ 关着的门能穿、开着的门挡人 |
| 2 | 拉杆关是纯装饰 | `_settle` 查 `self.levers`（本意是"on 的集合"，装载后恒为空）⇒ 拉杆从不翻转 |
| 3 | BFS 解全是错的 | `clone_from` 里带 `_settle` ⇒ "人正站在拉杆上"的节点被克隆时把拉杆**扳回去** |
| 4 | 拉杆关"能用"纯属巧合 | `_bind_links` 三种事件全绑，flip 处理函数**写死 `mode='toggle'`**、无视连接声明的 mode ⇒ `open` 连接每步"先开再翻回"、靠回调顺序抵销 |
| 5 | `mode='close'` 的门永远堵死 | `src_on`/`src_off` 只在**变化**时发，"装载时本来就没触发"不算变化 ⇒ 等不到第一发 `src_off`（新增 `_resync_links` 装载后算到位） |
| 6 | 求解器回溯 `KeyError` | 终态从不入队，父指针表里没有它的条目 ⇒ 发现胜利时**先补写父指针**再回溯 |

现在门/金门各有两套数据（`*_cells` = "哪里有"、`doors`/`gold_open` = "此刻是否开着"），
`_bind_links` **按 mode 只订阅一类事件**；`_settle` 三类事件一次算清并**排序**后遍历
（集合迭代序不进玩法语义）。这六条已全部写成回归守卫。

### 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python -m tests.test_brickward` | **30/30** → `ALL_BRICKWARD_TESTS_PASSED` |
| `scripts/run_all_tests.py` | 8/8 模块 / **170 项** / `ALL_TESTS_PASSED`（原 140 项 +30） |
| `python -m cli.config_boot brickward` | 3 手工关 + 6 种子 = 9/9 可解且回放到胜 → `BRICKWARD_OK` |
| `python -m cli.config_boot all` | **10 个** OK 标记（新增 brickward） |
| `scripts/verify_demos.py` 全量 | `ALL_DEMOS_OK`（零回归） |

## [未发布] — 引擎化改造 P2-3：`demos/` → `examples/`

引擎项目里那些单文件示例与仓库工具是**示例**，不是产品本体；目录名改回它该有的语义。
`git mv` 保历史，全仓引用**一次改完（68 个文件）**：

- 30 个示例自身的用法行（`python examples/<名>.py`）；
- `scripts/verify_demos.py`（`DEMOS_DIR` → `EXAMPLES_DIR`）、
  **`scripts/record_all.py` 的 22 条 ENTRIES 路径**、`scripts/build_exe.py` 的 13 条打包条目
  （这两处最脆：录制/打包按路径 import 模块，改错就直接跑不起来）；
- `pyproject.toml` 的 `exclude = ["examples*"]`；9 棵配置树的注释；
  `core` / `engine` / `host` / `cli` 里的文档串；活文档与 `examples/README.md`。

> **迁移映射规则（长期有效）**：**2026-10-14 起 `demos/` ⇒ `examples/`**。
> 有日期的 design note 与 CHANGELOG 历史条目里的 `demos/` 是当时的记录，**不追改**。

### 验收（实测，未设 `CORDIS_SRC`）

| 门禁 | 结果 |
|------|------|
| 示例直跑 | `python examples/boids.py --smoke 1` → `EXIT_OK` |
| **录制脚本**（改名后最脆的一环） | `record_all.py --only boids --dur-override 1` → `ALL_RECORDED`；随后恢复完整 45s 成片 |
| `scripts/run_all_tests.py` | 7/7 模块 / **140 项** / `ALL_TESTS_PASSED` |
| `python main.py --shot` / `kkrieger_demo.py --shot` | `EXIT_OK` |
| `python -m cli.config_boot all` | 9 个 OK 标记 |
| `scripts/verify_demos.py` 全量 | **ALL_DEMOS_OK (31 examples)** |

> 顺带一个操作教训：**全仓文本替换会把"描述迁移本身"的句子也改掉**
> （`demos/ → examples/` 被改成 `examples/ → examples/`）—— 这类**映射句必须人工复核**。

## [未发布] — 引擎化改造 P2-1/P2-2：分层归位

依据 `docs/5_引擎化改造规划.md` §6-P2。P1 让仓库"能装"，P2 让**分层真的成立**。

### P2-1 平台层抽出（`examples/_harness.py` → `world3d/platform/app.py`）

`_harness.py` 是与任何具体 demo 都无关的**平台层**（32 个 demo/脚本靠它开窗口、解析命令行、
抓帧、HUD、主循环），却寄生在 `examples/` 里。现在正名为 `world3d/platform/app.py`：

- 全部 20 个 demo + 2 个脚本（`verify_demos` / `record_all`）的导入统一为
  `from world3d.platform.app import ...`；顺手修掉 `examples/scene_viewer.py` 里
  `from demos._harness import ...` 这个**全仓唯一的不一致形态**（它会让同一个文件被当成两个
  模块导入、`DemoApp` 变成两个类对象，`issubclass` 判定失败）；
- `world3d/platform/app.py` 收口了 `__all__`（兼容层靠它转发，所以必须显式声明）；
- **旧路径 `examples/_harness.py` 保留为薄兼容层**（`from world3d.platform.app import *`），
  实测旧用法仍可用且是同一个类对象。

#### ⚠️ 硬约束：顶层包不能叫 `platform`

一开始按规划写成顶层 `platform/`，**立刻炸**：它**遮蔽标准库的 `platform` 模块**
（pyglet 自己就 `import platform` 取 `platform.architecture()`），单测与 `main.py` 同时报
`AttributeError: module 'platform' has no attribute 'architecture'`。
而规划 §3.2 的目标结构本来就是 `world3d/platform/` —— 于是直接落到**最终位置**，
一举免掉 P2-4 的二次搬迁。**同类命名约束**：导入包名不能以数字开头（P1 的 `world3d`）。

### P2-2 `core/` 越界清出

| 旧（越界） | 新（归位） | 理由 |
|-----------|-----------|------|
| `core/renderer.py`（`WorldRenderer`） | `presentation/world_renderer.py` | 是 `main.py` 48s 演出的渲染器 |
| `core/kkrieger_scene.py`（`KKRenderer`） | `presentation/kkrieger_scene.py` | 同理，kkrieger 漫游的表现层渲染器 |
| `core/nordic_scene.py`（`build_nordic_scene`） | `logic/nordic_scene.py` | 是场景**内容/布局**，被 `logic/nordic_saga.py` 与 demo 消费 |

同步 7 个消费方 + 3 处测试导入 + 文档对齐说明；`docs/架构设计_3D动画方向.md` 那张
2026-10-08 的分层快照加了失效注（指向当前权威文档）。
`core/geometry.py` / `core/resources.py` 是否随之迁走**留作后续评估**。

### 两个已踩的迁移坑（写进规划 §6 检查表）

1. **引用形态三种**都要 grep：`from core.<mod> import X` / **`from core import <mod> as y`** /
   `import core.<mod>` —— P2-2 漏了第二种，被 `test_nordic_scene_geometry` 抓出来；
2. **路径层级推导要同步改**：`world3d/platform/app.py` 找仓库根要 `dirname` 三次
   （`examples/_harness.py` 是两次）。"少一层就炸"这类问题在 P0-3（内核 `_ROOT`）也踩过一次。

### 验收（实测，未设 `CORDIS_SRC`）

| 门禁 | 结果 |
|------|------|
| 兼容层自检 | `examples/_harness.DemoApp is world3d.platform.app.DemoApp` → **True**；`__all__` 全部转发到位 |
| `scripts/run_all_tests.py` | 7/7 模块 / **140 项** / `ALL_TESTS_PASSED` |
| `python main.py --shot` / `python kkrieger_demo.py --shot` | `EXIT_OK`（4 张 / 9 张） |
| `scripts/verify_demos.py`（31 个全量冒烟 + 截图） | 见提交信息（本批的最强门禁：所有 demo 都改了导入） |
| `python -m cli.config_boot all` | 9 个 OK 标记 |

## [未发布] — 引擎化改造 P1：库化（可安装 + 公共 API 面 + CLI 入口）

依据 `docs/5_引擎化改造规划.md` §6-P1。P0 把试点全量转正后，P1 让仓库**能像库一样被安装使用**。

### 新增

- **`pyproject.toml`**（PEP 621，单一事实来源）：元数据 / 依赖 / extras（`dev`、`pyglet1x`）/
  库级入口点 / 打包范围 / ruff 配置。**库版本用 SemVer，从 `0.1.0` 起步**（发行版仍用 CalVer）。
- **`world3d/` 薄门面包**：把引擎公共面从 `engine` 转发出来（公共面由 `engine.__all__` 收口，
  不维护第二份清单）；含 `py.typed`。**发行名 `3dworld` / 导入名 `world3d`** —— 导入包名不能以
  数字开头（`import 3dworld` 是语法错、`'3dworld'.isidentifier()` 为 False），PEP 508 发行名允许。
- **库级 CLI 入口**：`world3d-info`（环境自检：版本/依赖/GL/cordis/配置树，任何安装方式可用）、
  `world3d-config`（配置树 headless 验收）、`world3d-pipeline`（场景校验流水线）。
- `world3d/_info.py`：自检实现（全部带兜底，缺 GL / 缺可选件都不炸）。

### 变更

- **依赖收口为一份**：`requirements.txt` 改为**转发**（内容就是 `-e .`）—— 此前它与项目元数据是
  两份清单、必然漂移（上线前全检的 P0 发布阻断即"依赖没声明全"）。`requirements-pyglet1x.txt`
  作为 cocos2d 兼容路线备用保留。
- `host/tree.py`：配置树目录支持 **`WORLD3D_CONFIGS`** 环境变量覆盖（`configs/` 是仓库资产、
  不随 wheel 分发，非 editable 安装下需要逃生口）。
- `engine/__init__.py`：公共面补 `unit_cube` / `unit_sphere`（与 `layer_mesh` / `merge_rects` 同族）。
- `README.md`：安装段重写（方式 A 项目 venv / 方式 B 当库装）+ 说明"库 vs 仓库资产"的边界。

### 修掉的两个打包缺陷（**在干净 venv 里装一遍才发现**）

1. **顶层模块没被打包**：`_cordis_locate.py` 是**模块不是包**，`packages.find` 只找包 →
   装上后 `host.tree` 报 `ModuleNotFoundError: No module named '_cordis_locate'`。
   **editable 安装看不出来**（源码本来就在 `sys.path` 上），只有非 editable 装才暴露。
   修复：`[tool.setuptools] py-modules = ["_cordis_locate"]`。
2. `world3d-info` 取依赖版本用 `__import__(n).__version__` → `watchdog 4.x` 没有该属性，
   直接 `AttributeError`。改为 `importlib.metadata.version('<发行名>')`。

### 验收（实测：另建一个干净 venv，用 `E:\Python314` 的完整 Python）

| 步骤 | 结果 |
|------|------|
| `pip install .`（非 editable） | `import world3d` → **0.1.0**，公共面 28 项；`engine/core/logic/presentation/host/cli/games` 全部可导入（旧路径零破坏） |
| `world3d-info` | `pyglet=2.1.16 PyYAML=6.0.3 watchdog=6.0.0`；`cordis` 解析到**装出来的副本**（vendored 打包成功）；GL `(4,6) Intel UHD 630`；着色器 19 / 服务表 23；配置树缺失时给出 `WORLD3D_CONFIGS` 提示 |
| `pip install -e .` | `cordis` 解析回仓库 `vendor/cordis/src` |
| `scripts/run_all_tests.py`（用该 venv 跑） | 7/7 模块 / **140 项** / `ALL_TESTS_PASSED` |
| `world3d-config all` | **9 个 OK 标记**（TD/GODHAND/FPS/BOXWARD/STORM/HALL/VOID/PULSE/CORES） |
| `world3d-pipeline` | `PIPELINE_OK` |

## [未发布] — 引擎化改造 P0：`experiments/` 全量转正

依据 `docs/5_引擎化改造规划.md`（全哥立项）。目标：把仓库从"演示合集"改造为
**3D 引擎项目**——本批（P0）先把 `experiments/` 里的试点（6516 行 / 45 个文件）
全部**转正或退役**，做到"**没有试点目录，只有正式层**"。

### ⚠️ 破坏性变更（Breaking）

| 旧 | 新 |
|----|----|
| `experiments/cordis/<x>_kernel.py` | **`games/<x>/kernel.py`**（8 个玩法内核） |
| `experiments/cordis/tree_boot.py` | **`host/tree.py`** |
| `experiments/cordis/config_boot.py` | **`cli/config_boot.py`**，调用方式改 `python -m cli.config_boot <kind>` |
| `experiments/cordis/cordis.*.yml` | **`configs/*.yml`**（9 棵） |
| `experiments/cordis/services/*.py`（24 个 5 行壳） | **删除** → `host/registry.py` 的服务短名表（yml 里写 `- name: economy`） |
| `experiments/cordis/scene_pipeline.py` | **`host/pipeline.py`**（`python -m host.pipeline`） |
| `experiments/fixed_timestep/compare.py` | **`tests/bench_fixedstep.py`**（从对照脚本变**带断言的回归基准**） |
| `experiments/cordis/levels/`（boxward 关卡编辑器导出目录） | 仓库顶层 **`levels/`**（已加 `.gitignore`） |

`import experiments.*` 一律失效；本仓库内已全部改完（`grep` 复查无活引用）。

### 新增

- **`engine/fsm.py`**：确定性有限状态机（时间窗 + 事件转移 + 进出钩子 + 轨迹）。
  自研替代 `experiments/allin_decision/`（原依赖外部仓库 allin_engine，其源码本机已离线）。
- **`engine/orchestration.py`**：事件总线 + 声明式 `when→then` 级联 + 环检测。
  自研替代 `experiments/zhuangtaiji_orchestration/`（同上，源库已离线）。
- **`engine/baked_mesh.py`**：`unit_cube` / `unit_sphere`（烘焙明暗三角汤）
  —— 原先住在塔防内核里被**六个内核横向 import**，上提后消除"游戏依赖游戏"。
- **`engine/greedy_mesh.py`**（P0-1）：单层体素贪心合批，从内核上提。
- **`host/registry.py`**：服务短名 → 实现类（23 条 = 13 共同核 + 10 内核）。
- **`docs/cordis集成.md`**：Cordis 集成的正式文档（前身是试点目录里的 README）。
- 测试：`tests/test_engine.py` +2 项（FSM / 事件总线）→ 全套 **140 项**。

### 试点去向（18 项，逐项交代）

| 试点 | 处置 | 依据 |
|------|------|------|
| 8 个玩法内核（4381 行） | **转正** → `games/<名>/kernel.py` | 早被 8 个 demo 正式依赖，"试点"身份名不副实 |
| 20 个服务壳 + 宿主 + 9 棵 yml | **转正** → `host/` + `configs/` + `cli/` | 见上表 |
| `greedy_mesh.py` | **转正** → `engine/greedy_mesh.py` | 零依赖纯函数，属引擎算法件 |
| `scene_pipeline.py` | **转正** → `host/pipeline.py` | Cordis 三服务流水线（checker→loader→reporter），`PIPELINE_OK` |
| `fixed_timestep/compare.py` | **转正** → `tests/bench_fixedstep.py` | 两组对照结论加断言，成回归基准 |
| `allin_decision/`（FSM 决策） | **能力自研**（`engine/fsm.py`），试点退役 | 依赖外部仓库；本机 G 盘已移除，不可复现 |
| `zhuangtaiji_orchestration/`（编排） | **能力自研**（`engine/orchestration.py`），试点退役 | 同上 |
| `bloodmusic_ost/remake_act6.py` | **退役**（能力已有） | 原生 OST 已在 `logic/kkrieger_audio.py`（第六幕） |
| `scene_walker/walk.py` | **退役**（被取代） | 正式版 `examples/scene_walker.py`（652 行）⊃ 试点（422 行），且多出热重载/限时挑战/结算 |
| `scene_walker/bgm.py`、`gen_sfx.py` | **退役**（被取代） | 已转正为 `engine/adaptive_music.py` 与 `scripts/gen_sfx.py` |

### 顺手修掉的两个真缺陷

1. **玩法内核之间的横向依赖**（见 `docs/引擎设计.md` §三.12）：共享几何上提 engine。
2. **配置树挂载失败的误导性报错**：cordis 的 Loader 会吞掉插件构造期异常，
   只表现为"服务不就绪"。已在 `host/tree.py` 的超时错误里加定位提示。

### 验收（全部实测，且刻意不设 `CORDIS_SRC`）

- `scripts/run_all_tests.py` → 7/7 模块 / **140 项** / `ALL_TESTS_PASSED`；
- `python -m cli.config_boot all` → **9 个 OK 标记**（TD/GODHAND/FPS/BOXWARD/STORM/HALL/VOID/PULSE/CORES）；
- `python -m host.pipeline` → `PIPELINE_OK`（含卸载回滚与 coeffect 断言）；
- `python -m tests.bench_fixedstep` → `BENCH_OK`（固定步长帧率无关 Δx=0；变步长 Δ=0.0333m；
  0.5s 卡帧下 变步长穿墙 / 固定步长拦住）；
- `scripts/verify_demos.py`（31 个 demo 全量冒烟 + 截图）→ `ALL_DEMOS_OK`。

## [2026.10.13] — 上线前全检修复

首个显式发布版本。本次以「上线前全检」（代码审查 + 安全审计 + QA 测试）的发现为准绳，
**修复全部集中在发布工程侧**，产品代码逻辑未做改动。

对应基线提交：`03c8c71`（`feat(demos): ③ 可视化导演台`）。

### 修复（发布阻断项）

- **依赖声明不完整导致环境不可复现**（P0，原 🔴 发布阻断）
  - `requirements.txt` 原本只有 `pyglet==2.1.16`，但 vendored `cordis` 硬依赖
    **PyYAML** 与 **watchdog**（上游把二者标为 optional extra，实为硬依赖：
    `cordis/__init__.py` 无条件 `from .hmr import Hmr`，而 `hmr.py` 在模块级把
    未安装 watchdog 时会变成 `None` 的 `FileSystemEventHandler` 当基类使用）。
  - 后果：全新克隆执行 `pip install -r requirements.txt` 后，7 个发行 demo
    （boxward / godhand / tower_defense / voidward / pulsefall / stillhall /
    swarmward）与 `core/` 全部共同核均无法 import。
  - 现状：`requirements.txt` 补全两者，全新克隆无需任何手工接线即可跑通。

- **误导性报错掩盖真实根因**（P0）
  - cordis 定位兜底此前统一报「未找到 cordis 包，请设置 `CORDIS_SRC`」。而
    `ModuleNotFoundError` 是 `ImportError` 的子类，所以「cordis **已找到**、
    只是自身依赖缺失」也会被同一段兜底捕获并改写成该文案 —— 给出的是一条
    **明确但永远无效**的修复指令。
  - 现状：定位与报错收口到 `_cordis_locate`，报错区分「未找到」与「已定位但导入
    失败」，后者直接带出真实根因并提示 `pip install -r requirements.txt`；
    同时捕获 `TypeError`（缺 watchdog 时抛的是 `TypeError` 而非 `ImportError`，
    此前会完全绕过兜底）。

- **cordis 定位逻辑重复 10 处且只认环境变量**（P0）
  - `core/_cordis.py` 与 `experiments/cordis/` 下 9 个内核各自复制一段兜底，
    且只查 `CORDIS_SRC`、**不查仓库自带的** `vendor/cordis/src`。
  - 现状：统一改为 `_cordis_locate.ensure()`；`CORDIS_SRC` 仍可显式覆盖，但需
    先通过 `os.path.isdir` 校验才允许进入 `sys.path`（安全审计 F-004）。

- **契约自检的误分类**（P0）
  - `core/contracts.py` 用 `if 'cordis' in str(e)` 判定，会把「依赖缺失」误记为
    `pending:cordis`，把排查方向从「装依赖」带偏到「去暴露 cordis」。
  - 现状：新增 `_root_cause()` 沿 `__cause__` 链取根因，按根因缺失的模块名分类。

### 新增

- `scripts/run_all_tests.py` —— 一键回归入口（原项目零 CI、零一键入口）。
  用子进程逐个跑 6 个测试模块，判定通过 = 退出码 0 **且**出现专属 PASSED 标记；
  以**实际执行数**为分母统计（跑挂的模块记为未执行，不混同于跳过）。
- `_cordis_locate.py` —— cordis 统一定位模块（含安全校验与可读报错）。
- `requirements.txt` 补齐 `PyYAML`、`watchdog`。

### 安全加固（P1）

- **F-004 环境变量目录注入 `sys.path[0]`**：`experiments/` 下两个试点脚本原本写成
  `os.environ.get('ZHUANGTAIJI_PATH', r'G:\repos\zhuangtaiji')` / `AN_ENGINE_PATH`
  默认 `G:\repos\an-engine`，然后模块级 `sys.path.insert(0, …)`。**带机器相关默认值
  意味着无需设置任何环境变量** —— 只要目标机器存在可写的 G 盘，放入同名包即自动
  激活，等同于随代码分发一条活性注入路径。现改为必须显式指定且先经 `os.path.isdir`
  校验，未配置时给出明确提示（不再是崩溃或隐性加载）。
- **F-002 配置树 entry 白名单**：cordis 会把 yml 里每条 entry 的 `name` 直接交给
  `importlib.import_module`，即"改配置 = 执行代码"，而 `.yml` 在评审者眼里通常是
  数据。`tree_boot.load_tree` 现强制校验 `name` 前缀，白名单外直接拒绝。

### 覆盖率（P2）

- 新增 `tests/test_timeline_ui.py`：`presentation/timeline_ui.py` 覆盖率
  **27% → 100%**（该文件是本次 HEAD 可视化导演台引入的新 UI，原为全仓最低覆盖盲区）。
  - 顺带修出一个真实边界缺陷：`_t_from_x()` 在 `duration` 为负时会返回负时间
    （`update()` 有 `duration <= 0` 守卫而它没有，两处口径不一致）。已在
    `_t_from_x` 补同款守卫。
  - 用例对「幕刻度必须存入 `self.act_ticks`」做了断言 —— pyglet `Batch` 不持有
    Shape 引用，不存会被 GC 回收而**静默不画**（M6 踩过的坑，注释里有记录）。

### 验收（本次实测）

- 单测：**138 / 138 全部通过**（7/7 模块；修复前全新克隆为 92/129，2 个模块崩溃、
  36 项从未执行；本次新增 timeline_ui 9 项）。
- Demo：**31 / 31 通过**（`ALL_DEMOS_OK`），含此前无法启动的 7 个 cordis 系 demo。
- 配置树：`config_boot` 对 puzzle / cores / td / hall / void / pulse 六棵树全部通过
  （验证 F-002 白名单未误伤合法配置）。

### 变更文件

- 新增：`_cordis_locate.py`、`scripts/run_all_tests.py`、`tests/test_timeline_ui.py`、
  `.github/workflows/ci.yml`、`VERSION`、`CHANGELOG.md`、`requirements-dev.txt`。
- 修改：`requirements.txt`、`core/_cordis.py`、`core/contracts.py`、
  `presentation/timeline_ui.py`、`experiments/cordis/` 下 9 个内核与引导模块、
  `experiments/` 下 2 个试点脚本。

### 说明

- 按决策 **`experiments/` 随包发布**：其中 `zhuangtaiji_orchestration/` 与
  `allin_decision/` 依赖本仓库之外的兄弟仓库（`core/orchestrator.py`、`allin_engine`
  均不存在），属 dev-only 死代码。未删除（按决策保留），但已改为**未配置时明确报错**
  而非隐性加载任意路径，随发布附带的风险已收敛。
- CI 目前只跑 `windows-latest`：渲染层依赖 OpenGL，Linux runner 无 GL 上下文时 GL
  用例会走 SKIP 分支（不失败但等于没测到）。接入 EGL/OSMesa 后再扩展矩阵。
- 文档截图（`docs/*.png`）由 `scripts/verify_demos.py` 重新生成，本次**未随修复提交**
  （66 个二进制文件会淹没代码改动）。需要刷新时单独执行该脚本并提交。
