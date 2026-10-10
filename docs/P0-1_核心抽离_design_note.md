# P0-1 · 核心抽离到 core/（design note）

> 状态：**待全哥放行** · 2026-10-10 · 依据 `docs/补完计划.md` §5 决策①（落点 = 新建 `core/` 顶层包）
> 范围：把散在各游戏内核里的 Service 实现，抽离/新建为**跨内核复用的正式共同核**。

---

## 1. 目标

1. 让 12 个共同核（`core/contracts.py` 已定义契约）**有正式、唯一、跨内核复用**的实现；
2. 实现落在 **`core/`** 顶层包，与契约同包；
3. 每个实现对象满足对应 `Protocol`（`isinstance(obj, XxxCore)` 通过）。

## 2. 现状（实证）

| 层 | 位置 | 事实 |
|----|------|------|
| 契约 | `core/contracts.py` | 12 个 `Protocol` + `CORES` 注册表 + `verify()`；只 import typing，无 GL/cordis 依赖 |
| 适配层 | `experiments/cordis/services/*.py` | **均 5 行**（importlib 转发，非实现） |
| **真实现在哪** | `tower_defense_kernel.py`(789) / `fps_kernel.py`(778) / `godhand_kernel.py`(350) | 各内核**自带** `RenderService` / `EconomyService` / `EntityService` / `RulesService` 等 —— **重复实现、无法跨内核复用** |
| 缺口 | —— | `entity` / `economy` / `rules` **无正式实现**；`terrain` 缺 `edit_height/rebuild_mesh`；`pathing` 缺 `find_path/formation`；`particles`/`sdf` 未封装 |

> 结论：P0-1 的实质 = **去重**（把内核里的 Service 上提为共享核心）+ **补缺**（3 核从零建）。

## 3. 落点与文件清单（`core/`）

```
core/
├── contracts.py          # 已有，不动（契约 + CORES 注册表）
├── __init__.py           # 导出各核心服务（新增）
├── render_core.py        # RenderService      ← 抽（源：三内核实）
├── terrain_core.py       # TerrainService     ← 抽 + 补 edit_height/rebuild_mesh
├── entity_core.py        # EntityService      ← 从零建
├── economy_core.py       # EconomyService     ← 从零建
├── rules_core.py         # RulesService       ← 从零建
├── pathing_core.py       # PathingService     ← 补 find_path(A*) / formation
├── particles_core.py     # ParticlesService   ← 封装（源：demos/particles_gpu.py）
├── sdf_core.py           # SdfVoxelService    ← 封装（源：demos/{raymarch_sdf,voxel_terrain,blockworld}.py）
└── input_core.py         # InputService       ← 补动作映射层（可选，见 §8）
```

**依赖方向**（硬约束）：`core/*` 只允许依赖 `engine/*` 与 `logic/*`；**禁止** `engine/logic` 反向 import `core`；**禁止** `core` import `experiments/*`。

## 4. 迁移顺序（每步独立可验收，不删旧码先并行）

| 步 | 动作 | 风险 |
|----|------|:----:|
| 1 | 建 `core/` 包骨架 + `__init__.py` 导出 | 低 |
| 2 | 抽**已具备**核：`render` / `audio` / `timeline`（纯搬迁） | 低 |
| 3 | 补/抽**部分具备**核：`terrain` / `pathing` / `particles` / `sdf` | 中 |
| 4 | 从零建 3 核：`entity` → `economy` → `rules`（依依赖序） | **高** |
| 5 | 切换：`experiments/cordis/services/*.py` 改为转发 `core/*_core.py` | 中 |
| 6 | 三内核 demo 改用核心服务，跑全量回归 | 中 |

## 5. 验收标准

- `python -c "from core import contracts as c; print(c.verify())"` → 全部 `ok` / `pending`（无 `err:`）；
- 新增 `tests/test_cores.py`：每核 Protocol 结构检查 + 关键行为单测（如 economy.spend 不足返回 False、rules.state 迁移）；
- `python experiments/cordis/config_boot.py all` → `TDKERNEL_OK` / `GODHAND_OK` / `FPSKERNEL_OK`；
- `python -m tests.run_tests` → 既有 36 项 + 新增全绿；
- demo 行为不变（截图/关键数值对比）。

## 6. 风险与对策

| 风险 | 对策 |
|------|------|
| 抽离导致**行为漂移** | 抽离前后跑**同一** headless 验收；旧实现保留，切换前并行验证 |
| **循环导入** | 严守 §3 依赖方向；`core` 不 import `experiments` |
| 误抽内核**专属逻辑** | 只抽"跨内核复用"的通用面；内核特有策略（如 td 的塔位规则）留在 kernel |
| 契约方法名与底层不一致 | 契约是 **facade**（`contracts.py` 已注明不做 isinstance 结构比对），实现按契约名写 |

## 7. 依赖的现有文件

- `core/contracts.py`（契约）
- `experiments/cordis/{tower_defense,fps,godhand}_kernel.py`（抽离源）
- `experiments/cordis/services/*.py`（切换点）
- `engine/{renderer,audio,timeline,hud,camera}.py`、`logic/boids.py`、`demos/{terrain_flyover,particles_gpu,raymarch_sdf,voxel_terrain,blockworld}.py`（源）

## 8. 待确认（2 问，带推荐）

| # | 问题 | 推荐 |
|---|------|------|
| 1 | "已具备"的 4 核（`input/hud/audio/timeline`）本轮是否一并封装？ | **只抽 `audio`/`timeline`**；`hud`/`input` 延后（它们与各自 demo 耦合较紧，收益低） |
| 2 | 新增单测放哪？ | **新建 `tests/test_cores.py`**（与 `test_engine.py` 并列，按核分节） |

---

*本 design note 为规划草案；放行后按 §4 分步执行，每步跑 §5 验收。*
