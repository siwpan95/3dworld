# P1-⑤ 玩法通用模块（背包 / 技能树 / 掉落 / 寻路强化）design note

> 状态：**✅ 已执行并交付**（2026-10-12）· 全哥选定「做得更全 / 接入 storm / 含多单位寻路队列」 · 依据 `docs/补完计划.md` §2 的 **P1-⑤**
> 参考源：**bullet-zombie（MIT，可借鉴）**的**模块划分**思路；只借结构、不搬码

---

## 1. 目标与验收

把"每个内核各写一份"的玩法零件，提成 **3 个新共同核 + 1 处寻路强化**，
进 `core/` + `core/contracts.py` 契约 + `cordis.cores.yml` 配置树 + `tests/test_cores.py`。

**验收（§2 P1-⑤ 原文）**：**模块化 + 单测；被 ≥1 内核使用**。
本 note 的具体口径：`config_boot cores` 自检通过（核数 12 → **15**）+ `test_cores` 全绿
+ 至少一个内核**真的改用它**（不是"提供但没人用"）。

---

## 2. 四个模块（对齐现有核的粒度）

| 模块 | 契约 | 关键 API | 设计要点 |
|------|------|----------|----------|
| **背包** `inventory` | `InventoryCore` | `add/remove/count/has/capacity/used/slots/total_value/clear` | **槽位 + 堆叠**（`item_id → {n, cap}`）；容量上限；`total_value(prices)` 供经济核估值 |
| **技能树** `skills` | `SkillsCore` | `define/learn/can_learn/level/has/effects/passives/respec/points` | **前置依赖图** + 等级上限 + 点数；`effects()` 汇总被动（供战斗核读） |
| **掉落** `loot` | `LootCore` | `table/roll/drop_from/expected_value` | **权重表 + 保底**（pity）；**同种子同结果**（沿用全局确定性种子惯例） |
| **寻路强化** 扩 `pathing` | （扩 `PathingCore`） | 新增 `find_path(start, goal)`（**A\***）+ `smooth(path)` | 现核只有 `formation` 编队；补 A* 供 FPS/塔防的"敌人绕障" |

**为什么放 `core/` 而不是 `logic/`**：这三个都是**跨内核复用的服务型**零件（与
`economy`/`entity`/`rules` 同级），按 P0-1 已确立的分层应进 `core/`；
`logic/` 保留"纯算法无服务"的东西（如 `logic/animation.py`）。

---

## 3. 落地步骤（每步独立验收）

| 步 | 内容 | 验收 |
|----|------|------|
| 1 | `contracts.py` 加 3 个 Protocol + `CORES` 注册（`WRAPPABLE`） | `python -c "import core.contracts as c; c.verify()"` 无 err |
| 2 | 三个 `core/xxx_core.py`（Service 实现） | 单测 |
| 3 | `pathing_core.py` 加 `find_path`(A*) + `smooth` | 单测（含**无解**与**绕障**用例） |
| 4 | `services/*.py` 包装 + `cordis.cores.yml` + `tree_boot.WANT_CORES` | `config_boot cores` 15 核全绿 |
| 5 | `tests/test_cores.py` 加项（协议结构 + 关键行为） | `test_cores` 全绿 |
| 6 | **接入一个内核**：`void`（星阑）的货舱改用 `inventory`（20 舱位 → 槽位 + 堆叠） | `config_boot void` 仍 `VOIDKERNEL_OK`；`voidward` 出图 |

> **行为不变的硬约束**：接内核时**不得改变玩法结果** —— `config_boot void` 的
> 贸易达标时间/交易次数应保持在原量级（±10%），否则说明抽象漏了语义。

---

## 4. 风险与对策

| 风险 | 对策 |
|------|------|
| 抽象过度（造一堆没人用的核） | 验收要求"**被 ≥1 内核真的使用**"，且只做 §2 点名的 4 项 |
| 接入后玩法漂移 | 步 6 用同一 headless 验收对比（时间/交易数±10%） |
| A* 与现有 `pathing` 语义冲突 | **只加方法不改旧方法**；`formation` 原样保留 |
| bullet-zombie 是 MIT 但仍是外部代码 | **只读其"模块怎么分"**，实现全自写；记账进 §7 合规清单 |

---

## 5. 待确认（3 问，带推荐）

| # | 问题 | 推荐 |
|---|------|------|
| 1 | **粒度**：只做 §2 点名的 4 项，还是顺带做"装备槽/属性面板"等更细的？ | **只做 4 项** —— 验收要求"被真正使用"，做多了没人接反而成负担 |
| 2 | **接入对象**：拿哪个内核试接？ | **`void`（星阑货舱 → inventory）** —— 天然是"容量 20 + 5 种货堆叠"，改造最小、语义最贴 |
| 3 | **A\* 的占用**：需要"多单位同时寻路"（带队列/缓存）吗？ | **先不做** —— 单次 `find_path` + 调用方自己缓存；多单位寻路等真需要时再加 |

---

## 6. 执行结果（2026-10-12 ✅ 已交付）

**全哥决策**（三项均选**非推荐**，取最大工程量）：① **做得更全** ② 接入 **storm** ③ **含多单位寻路队列**。

**落地**：

| 项 | 文件 |
|----|------|
| 确定性随机工具 | `core/_rng.py`（LCG + FNV-1a hash；**不用 `random`** —— 跨 Python 版本会漂移） |
| 背包（槽位+堆叠+**装备槽**） | `core/inventory_core.py` → 契约 `InventoryCore` |
| 技能树（前置图+等级上限+**属性面板**） | `core/skills_core.py` → `SkillsCore` |
| 掉落（权重表+**保底**+**词缀**，确定性） | `core/loot_core.py` → `LootCore` |
| 寻路强化（**拉绳平滑** + **多单位队列**） | `core/pathing_core.py` 新增 `smooth` / `request_path` / `step_paths` / `cached_path` |
| 服务包装 + 配置树 | `services/{inventory,skills,loot}.py` + `cordis.cores.yml`（10 → **13 核**）+ `WANT_CORES` |
| 契约注册 | `core/contracts.py`（12 → **15 契约**）+ `__all__` |
| 测试 | `tests/test_cores.py` **+4 项**（13 → **17**） |
| **接入 storm** | `storm_kernel.shoot()` 读 `skills.effects()['fire_cd_pct']`（0~0.6 夹取） |

**零漂移（本轮最关键的约束）**：接入采用**软引用**（`getattr(self.ctx, 'skills', None)`，**不进 `inject`**），
且 `skills.is_neutral()`（未学技能、无装备）时倍率恒为 **1.0** —— 实测 `config_boot storm --seeds 20261101,7,13`
数字与接入前**逐字一致**（t=70.8/71.8/71.5、kills=126、hits=322）✅。
⇒「接入技能树」**没有**破坏刚调通的 boss 平衡。

**验收（全绿）**：`config_boot cores` → `CORES_OK`（13 核经配置树可达 / 15 契约注册）；
`test_cores` **17/17**（新增：背包堆叠容量与装备、技能树前置与属性面板与洗点、
掉落确定性与保底与词缀与期望收益、寻路平滑与队列去重分帧）；
`config_boot all` 13 项 OK；6 套测试全绿。

**踩坑**：
1. `test_cores_registry_complete` 里**硬编码 `len(CORES) == 12`** —— 加核后必须同步改（否则测试先炸）；
2. 背包的 **`capacity` 是"格数"不是"件数"**（1 格可堆 `stack` 个）—— 我第一版测试按"件数"写，断言全错；
3. 断言里**不要重复调用有副作用的 API**（`assert inv.add(x) == 1, f'...{inv.add(x)}'` 会调用两次）；
4. `loot_core` 初版自建了一个 `_Rng` 类 —— 应复用 `core/_rng.py`（统一实现，避免两套 LCG）。
