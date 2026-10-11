# 复刻早期经典游戏 · 规划（3dworld × Cordis）

> 状态：规划（待全哥 review 后按五阶段逐内核推进）
> 关联：`experiments/cordis/README.md`（Cordis 试点一：场景管线）、`demos/mountain.py`（已完成）、`recordings/mountain_100s.mp4`（已录制）

---

## 0. 目标与原则

用 `3dworld` 已有的图形/音频/动画能力，复刻一批**早期单机代表作**的**玩法机制**。
三条硬原则（与 `G:\allin` 治理铁律一致）：

1. **复刻机制，不复制 IP**——原创美术、原创命名、原创世界观，做"向经典致敬的玩法复刻"。
   （前车之鉴：开源项目 Stratagus 最早叫 *FreeCraft*，意图复刻 Warcraft II 引擎，
   2003 年收到 Blizzard 的 C&D 律师函，只因名字带 "craft"；改名后才活下来。）
2. **MIT / 开源优先（铁律 0）**——本仓 `LICENSE` 已为 MIT（Copyright 2026 siwpan）。
   任何借鉴都须**记账**：来源 + 许可证 + 借用范围。
3. **五阶段协作**——每个游戏内核走「先问 → 计划 → review → 执行 → 复盘」，
   一批 ≤4 问且每问带推荐项；计划须全哥放行才动手。

---

## 1. 总体方法论

```
先抽共同核  →  用 Cordis 把所有共同核粘成"服务总线"  →  在不同内核上按五阶段开干
  (抽取)           (插件即服务 / inject / 可逆副作用 / coeffect)        (玩法)
```

三层结构：

| 层 | 是什么 | 形态 |
|----|--------|------|
| **共同核** | 所有游戏都需要的子系统 | 一个个 **Cordis 插件**，各自 `provide` 一个服务 |
| **内核** | 某一类游戏的专属玩法 | **Cordis 内核插件**，只 `inject` 它需要的共同核 |
| **表现壳** | 窗口 / 帧循环 / 录制 | 复用 `_harness.DemoApp` + `scripts/record_all.py` |

为什么用 Cordis 粘合（而非手写一个大 `Game` 基类）：

- **`inject` 依赖自动排序**：内核插件声明需要哪些核心，Cordis 按依赖图决定挂载顺序，无需手工编排。
- **可逆副作用（effect）**：每个核心 `provide` 服务时登记 `ctx.effect(cleanup)`，卸载即回滚——
  天然解决 GL 顶点缓冲 / 纹理 / 音频资源的释放（呼应试点二要验证的"GL 资源回滚"）。
- **coeffect 级联**：某个核心被 `dispose()` → 依赖它的内核/下游自动停用，不会出现"悬空服务"。
- **与主仓零耦合**：核心只暴露公开函数（试点一仅用 `engine.scene_build` 的公开 API 即跑通）。

---

## 2. 共同核清单（待 Cordis 粘合的服务）

从上一轮"候选游戏"倒推出的公共子系统，以及 `3dworld` 现在的家底。

| # | 共同核 | 职责 | 现状 | 对应现有 demo / 模块 | 提供为 Cordis 服务 |
|---|--------|------|------|----------------------|--------------------|
| 1 | **渲染核心** | GL 上下文、相机（可切正交/俯视/等距）、lit/point shader、帧循环 | 已有 | `engine/` + `_harness` + `mountain` shader | `render` |
| 2 | **地形/高度场核心** | 高度场生成、网格构建、**实时编辑**（抬山/挖海） | 已有 | `terrain_flyover` / `voxel_terrain` / `mountain` | `terrain` |
| 3 | **实体/单位核心** | 通用实体：位置/速度/网格引用/血量/状态机 | **待抽离** | （散落在 mountain 的 objects/plants） | `entity` |
| 4 | **相机/输入核心** | 鼠标拖拽/滚轮/键盘 → 平移/缩放/选取 | 已有 | `OrbitCamera` + harness `on_key/on_mouse` | `input` |
| 5 | **UI/HUD 核心** | 侧边栏、小地图、状态条、居中叠层文字 | 已有 | `_harness.Hud` + `mountain._draw_overlay` | `hud` |
| 6 | **寻路/移动核心** | 网格 A* + 群体编队（由 boids 改造） | **待抽离** | `boids` | `pathing` |
| 7 | **资源/经济核心** | 金/木等资源点、工人采集、生产队列 | **待抽离** | （无，需新建） | `economy` |
| 8 | **音频核心** | 即时合成音效/配乐（零外部素材） | 已有 | `engine.audio`（`Track`/`render_wav`） | `audio` |
| 9 | **动画/时间轴核心** | 关键帧、骨骼/变换插值 | 已有 | `logic.animation` + `timeline` | `timeline` |
| 10 | **粒子核心** | GPU 粒子（法术/天气/碎屑/弹道） | 已有 | `particles_gpu` | `particles` |
| 11 | **状态/规则核心** | 胜负判定、波次、游戏状态机 | **待抽离** | （无，需新建） | `rules` |
| 12 | **SDF/体素核心** | 光线步进墙体、体素方块 | 已有 | `raymarch_sdf` / `voxel_terrain` / `blockworld` | `sdf` / `voxel` |

> 抽取优先级建议：先把**已具备且最稳**的 1/2/4/5/8/9/10 封装成服务（几乎零新代码），
> 再抽离 3/6/7/11 这几个"游戏逻辑"核心（真正的工作量与风险所在）。

---

## 3. Cordis 的角色：把共同核粘成服务总线

`experiments/cordis/README.md` 的**试点一**已验证（输出 `PIPELINE_OK`）：

- `checker` 插件 → 提供 `scene_check` 服务（包装 `engine.scene_build.validate`）
- `loader` 插件 → `inject: scene_check`；装载场景 JSON 并 `provide('scene')`（可逆 effect）
- `reporter` 插件 → `inject: scene`；依赖消失时**自动停用**（coeffect）

证明的四点：`inject` 自动排序 / 卸载即回滚 / coeffect 级联 / 与主仓零耦合。

**粘合姿态**：

- 每个共同核（第 2 节）写成一个 Cordis 插件，`provide` 同名服务；
- 跨核依赖用 `inject` 声明，例如 `pathing` inject `terrain`（读高度做避障）、
  `hud` inject `economy`（显示资源数）、`rules` inject `economy`+`terrain`（判胜负）；
- 资源回收用 `ctx.effect(cleanup)`——对应**试点二**要验证的"demo 插件化 + GL 资源回滚"；
- 依赖消失用 coeffect 自动级联卸载。

**Cordis 自身来源（合规记账）**：当前用的是其 **Python 移植**
[`geohotstan/cordis-py`](https://github.com/geohotstan/cordis-py)（MIT），
DeepSeek Harness 的运行时内核。
转正路径（见 README「后续线索」）：
**vendor 进仓库并锁定 commit**（不裸 `pip install` 飘在外部），记账：来源 + MIT 许可 + 借用范围
（仅 `Context`/`provide`/`get`/`effect`/`inject`/`dispose` 这一层 API）。
试点二（单 demo 插件化 + GL 回滚）、试点三（`cordis.yml` 式声明"本次挂哪些插件"）完成后转正。

---

## 4. 游戏内核候选（在各共同核之上构建）

上一轮联网核实的"早期单机代表作"，按类型映射其依赖的共同核。

| 内核 | 代表游戏（年份/机制已核实） | 主要依赖的核心 | 自研难点 | 难度 |
|------|----------------------------|----------------|----------|------|
| **神之手 (Sim)** | `Populous` (1989, Bullfrog)：实时改地形逼敌 | terrain(可编辑) / input / hud / render | 文明 AI、洪水判定 | ★★☆ |
| **迷你 RTS** | `Warcraft: Orcs & Humans` (1994)：双阵营、采金+木、4 单位编队；`Dune II`(1992) RTS 鼻祖 | economy / pathing / entity / rules / hud / terrain | **寻路 A* + 阵营 AI（硬骨头）** | ★★★★ |
| **射线 FPS** | `Wolfenstein 3D`(1992) / `Doom`(1993)：raycasting/SDF 墙体 + 精灵敌人 | sdf / particles / audio / entity | 精灵碰撞、武器 | ★★☆ |
| **太空** | `Elite`(1984) → 开源 `Oolite`/`Pioneer`：恒星系航行/贸易/战斗 | timeline(solar_system) / boids / particles | 经济/程序化星图 | ★★★ |
| **3D 解谜/方块** | `Tetris`(3D) / `Boulder Dash`(1984) / `Lemmings`(1991) | voxel / entity / rules | 下落/消除/行为状态机 | ★★~★★★ |
| **漫游解谜** | `Myst`(1993)：预渲染静谧世界（已多次重制含 VR） | raymarch_sdf / water_waves / mountain 氛围 | 谜题状态机、叙事 | ★★★ |

> 一个关键提醒：`Warcraft` 首作有"建筑要用路连起来""仅 4 单位编队""320×200 小地图"等时代约束——
> 复刻**机制**时取其"经济→兵力→战斗"循环即可，不必复刻这些限制。

---

## 5. 五阶段协作节奏（每个内核一套）

每个内核单独走一轮「先问 → 计划 → review → 执行 → 复盘」：

1. **先问**：≤4 个问题，每问**带推荐项**（全哥时间不耗在盘问式问卷上）。
2. **计划**：写成该内核的 design note（依赖哪些共同核服务、切片范围、验收标准），**须全哥放行**才动手。
3. **review**：全哥 review 计划；共识越充分，执行越不跑偏。
4. **执行**：在 Cordis 上挂好所需核心 + 写玩法；本地提交即时做。
5. **复盘**：教训沉淀进 `MEMORY`；自主决定标注「待复核」。

高风险动作（推送 / 建仓 / 删除 / 密钥 / 仓库结构变更）**逐项确认**；推送只在全哥在场并放行时做，
推后 `git ls-remote` 核对 IN_SYNC（铁律）。

---

## 6. 建议落地顺序（路线图）

**P0 · 共同核抽取 + Cordis 转正**
- vendor cordis（锁 commit，MIT，记账）；
- 试点二（单 demo 插件化 + GL 资源回滚）、试点三（`cordis.yml` 配置树）；
- 先把第 2 节里"已具备"的 1/2/4/5/8/9/10 封装成 Cordis 服务。

**P1 · 游戏内核（按性价比排序，先做切片不做完整战役）**
1. **神之手 (Populous)**——技术最现成：复用 `terrain` 可编辑高度场 + `input` + coeffect 演示，
   一个鼠标"抬山/挖海"就能跑出独特玩法；
2. **迷你 RTS（魔兽风塔防切片）**——最贴合全哥点名：一片地形 + 金/木 + 工人 + 2~3 兵种 + 1 个敌方 AI，
   跑通"经济→兵力→战斗"循环；寻路用 `boids` 改造 + 网格 A*（几百行可控）；
3. **射线 FPS (Wolfenstein/Doom 风)**——直接复用 `raymarch_sdf` + `kkrieger_demo` 已验证的 FPS 能力；
4. **延伸**：Myst 漫游 / Elite 太空 / 3D 方块解谜（按兴趣与反馈再排）。

---

## 7. 借鉴记账与合规清单

- [ ] 机制致敬、原创美术/命名/世界观（不碰原 IP 名称与素材）；
- [ ] 命名规避：任何作品名**不含 `craft` / `warcraft` / `starcraft` 等受保护词**（Stratagus 教训）；
- [ ] 每个内核 design note 顶部写「借鉴来源 + 许可证 + 借用范围」；
- [ ] cordis 依赖：vendor 并锁 commit，登记 MIT 与借用 API 范围；
- [ ] 本仓统一 MIT（已就位，仓库根 `LICENSE`）。

---

## 8. 现状与下一步

**已完成**
- `demos/mountain.py`：复刻 David O'Reilly 的《山》，已推送远端（commit `e66df0c`，IN_SYNC 核对通过）；
- `recordings/mountain_100s.mp4`：100s 离线录制（H.264 720p30，11.5MB）已产出并验证；
- `experiments/cordis` 试点一：场景管线 Cordis 化跑通（`PIPELINE_OK`），验证 inject/可逆副作用/coeffect。

**P0 状态（已闭环并推送远端，2026-10-09）**
- `vendor(cordis)`：引入 `geohotstan/cordis-py` v4.0.0 源码（MIT），`.pth` 全局暴露 + `VENDOR.md` 记账；commit `4691df0`（来源 `cordis-py-main.zip`，源码 ZIP 留作本地 provenance，不入库）。
- `core/contracts.py`：12 个共同核服务接口契约（`typing.Protocol`，脱离 cordis/GL 可 import），`CORES` 注册表 + `status_table()` + `verify()`；commit `f81ea97`。
- 上述两提交 + `28c5514`(record_all) + `6fba39e`(本规划) 已 **push 远端并 `git ls-remote` 核对 IN_SYNC**（local HEAD `4691df0` == remote `refs/heads/master`）。

**P0 收尾 + P1-①/② 状态（2026-10-09 晚）**
- 试点二（共同核 + 内核 Cordis 化 + GL 回滚）：`tower_defense_kernel.py`，commit `0887185`。
- 试点三（`cordis.yml` 配置树）：`experiments/cordis/services/*` 适配层 + `cordis.td.yml` / `cordis.godhand.yml`；`tree_boot.mount_tree` 为唯一挂载入口——`demos/tower_defense.py`、`demos/godhand.py` 的 `_mount_kernel` 与 `config_boot.py` 的 headless 验证**走同一函数、同一 `WANT_*` 列表**（验证输出 `TDKERNEL_OK` / `GODHAND_OK` + 配置传递断言 + GL 回滚 `4→0` / `3→0`）。
- Cordis 转正：`vendor/cordis.lock` 锁定上游 commit `a3d0c171571c6bbb465d04c06eab2de53d270814`（zip SHA-256 `b216a2b8…2a31616`）；`VENDOR.md` 记账同步，commit `47a7336`。
- P1-① 神之手：`godhand_kernel.py` + `demos/godhand.py` 交互 demo（`godhand_40s` 录制入口就位；本沙箱 pyglet 原生 GL 受限，实录待本机补跑）。
- P1-② 塔防切片：commit `f0ad2c2`（直连）→ `4642aa1`（委托内核）。

**P1-③ + 打磨轮状态（2026-10-10）**
- P1-③ 射线 FPS「迷堡枪影 / Rayhold」已交付并推送（commit `edbf8ed` + 设计 note `9246e85`，IN_SYNC=0 0）：
  `fps_kernel.py`（MazeService + FpsKernel）、`cordis.fps.yml`、`demos/rayhold.py`（SDF 光线步进）、
  10/10 种子 auto-pilot 通关，三内核回归全绿；执行结果见 §10.9。
- **手感与帧率打磨一轮已执行**（全哥指令 + 「参考 Aimlabs」）：hitmarker / 灵敏度 cm/360 标定 /
  量化结算 / 拾取可见化 / 出口高亮 / 失焦清键 / 步进调优 / 平衡复调（敌 10、更凶、弹药收紧）；
  新增 `config_boot fps --seeds` 多种子回归入口（10/10 绿）。详见 §10.10。
  实机录制 / 截图 / 帧率实测仍待本机补跑（沙箱 GL 受限）。

**P1-④ 归仓 / Boxward（2026-10-10）**
- **P1-④「归仓 / Boxward」已执行完毕**（设计 note `0db37cc` → 全哥放行「按 note 执行」→ 执行，§11.9）：
  内核 `puzzle_kernel.py`（GridService + PuzzleKernel + 反向拉箱生成器 + BFS 求解器 +
  贪心网格化补齐 `world_mesh`）、`cordis.puzzle.yml`、`demos/boxward.py`（编辑器 + auto-play）、
  `greedy_mesh.py`（共同核算法件）+ 6 项单测；`config_boot all` **五内核**全绿、
  10/10 生成种子回放全胜（`BOXWARD_OK`，GL 3→0）；与全哥 P0 共同核抽离线
  rebase 对齐后推送，最终 commit `0d22d04`（IN_SYNC=0 0）。

**P1-⑤ 弹潮 / Swarmward（2026-10-11）**
- **P1-⑤ 已执行完毕**（设计 note `8c2f091` → 全哥放行「按 note 执行」→ 执行，§12.9）：
  内核 `storm_kernel.py`（程序化编波 + 3 敌型 + 闪避 AI auto-pilot + **particles/
  pathing 首个内核消费者**）、`cordis.storm.yml`、`demos/swarmward.py`（双摇杆 + 粒子层
  + Aimlabs 手感件）、`core/particles_core.py` 加 `snapshot()`（共同核最小扩展）；
  `config_boot all` **六项**全绿、10/10 种子 10 波全通关（`STORMKERNEL_OK`，GL 3→0）。

**下一步**：P1-⑤ 弹潮 v1.1（boss 波 + 弹幕图案生成器，§13）/ P1-⑥ 候选四 note 全部
就绪（§13-16：弹潮 v1.1 / 静馆 Stillhall / 星阑 Voidward / 落音 Pulsefall）——
**待全哥点菜排期**；Rayhold/Boxward/Swarmward 实机手感数据复调。

---

## 9. P1-② 塔防切片 · 设计 note（首个内核）

> 状态：规划（待全哥 review 放行后执行）
> 机制致敬：Warcraft / Dune II 的「经济 → 兵力 → 战斗」循环；**不碰任何原 IP 名称与素材**（合规见 §7）。

### 9.1 工作标题（原创，规避 `craft`）
- 中文：**「戍卫之路」**；英文 codename：**Roadwarden**。
- 命名合规：不含 `craft` / `warcraft` / `starcraft` 等受保护词（Stratagus 教训，§7）。

### 9.2 切片范围（bounded slice）
- **地图**：24×24 格（世界 48×48，格距 2），近平面地形（平或极缓起伏，用 `height_at` 低幅度）。
- **车道**：固定折线路径（8~12 个 waypoint），从一侧边缘到基地；地面画成土路条带。
- **塔（3 类，from-scratch EntityCore）**：
  - 箭塔 Arrow：便宜、攻速快、低伤、单体。
  - 炮塔 Cannon：贵、慢、溅射（AoE）。
  - 冰塔 Frost：减速敌人。
  - 仅可建在**非车道**格；点击格子 → 选 1/2/3 → 放置，扣金。
- **敌人（2~3 类，from-scratch EntityCore）**：grunt（肉）/ swarm（快脆多）/ bruiser（高血）；沿车道 waypoint 前进；到达基地扣基地 HP。
- **波次（from-scratch RulesCore）**：5 波，每波按时序 spawn 一批敌人；击杀给金；清波有奖励金。
- **胜负**：撑过 5 波 = 胜利；基地 HP ≤ 0 = 失败。
- **控制**：拖拽/滚轮 = 转视角/缩放（复用 `orbit_camera_input`）；鼠标点击 = 选格/建塔；SPACE 暂停；自动或按键开波。
- **HUD（复用 HudCore）**：金币 / 波次 x/5 / 基地 HP / 选中塔 / 状态提示（CJK 经 `resolve_cjk_font` 正常显示；`print` 仅用 ASCII，遵循 `_harness` 约定）。

### 9.3 复用 vs 自建（对照 core/contracts.py）
| 核 | 来源 | 备注 |
|----|------|------|
| render | `engine.renderer` / `ShaderProgram` | 直连；顶点色 / 实例 |
| terrain | `demos.terrain_flyover.height_at` | 地面高度（低幅） |
| input | `logic.camera.OrbitCamera` + `orbit_camera_input` | 视角；建塔点击用 `engine.picking.ray_from_screen` + **解析射线-地面求交**（不依赖 `scene`/`drawables`） |
| hud | `demos._harness.Hud` | `set_status` |
| audio | `engine.audio.SfxPlayer` | 建塔/射击/击杀音效（合成） |
| particles | `demos.particles_gpu`（可选） | 弹道/命中特效，二期 |
| **entity** | **from-scratch** | `EnemySystem` + `TowerSystem`（后续包成 `entity` 服务） |
| **economy** | **from-scratch** | 金币计数 + 花费校验（→ `economy` 服务） |
| **rules** | **from-scratch** | 波次调度 + 胜负状态机（→ `rules` 服务） |
| pathing | 车道 waypoint 跟随；**A\* 暂不做** | 见 §9.5 |

> ⚠️ 修正：`logic.boids.Flock` 是**群集飞行**（有界立方内 flocking），**不是寻路**；塔防敌人走固定车道 waypoint，不需要 boids。网格 A\*（`find_path`）若日后做「迷宫塔防」再新建 `logic/pathfind.py`，不改造 boids。

### 9.4 落地架构（两阶段）
- **阶段 A（本内核先做）直连引擎切片**：`demos/tower_defense.py` 作为 `DemoApp` 子类，直接复用上面各核；系统类（`Grid`/`EnemySystem`/`TowerSystem`/`Economy`/`Rules`/`Waves`）先内联或放 `games/tower_defense/` 子包。**先交付可运行 + 可录制 demo**，证明玩法与引擎复用。
- **阶段 B（pilot-2 跟进项）Cordis 服务化**：先按 cordis v4.0.0 **fiber 属性式 API** 重写 `experiments/cordis/scene_pipeline.py`，再把本内核的系统抽成 Cordis 插件（`provide` 各核服务 + `effect` 回收 GL 资源），用 Cordis 重新驱动 demo，验证「共同核 + 内核」范式与 GL 回滚。这才是 P0「Cordis 转正」的终验。

> 为什么阶段 A 不直接上 Cordis：v4.0.0 的 API 与试点一不兼容，pilot-2 尚未做；先切片可最快拿到可玩结果，Cordis 化作为独立跟进项，互不阻塞。

### 9.5 文件布局
- `demos/tower_defense.py` —— 主交付（DemoApp 内核）；或 `games/tower_defense/{app,grid,systems,towers,enemies,render_helpers}.py`（更干净、便于 Cordis 化）。**推荐先单文件快速跑通，再视情况拆分。**
- 复用：`demos/_harness.py`(`run`)、`logic/camera.py`、`engine.picking`、`engine.audio`、`core/contracts.py`(形状参考)。
- `scripts/record_all.py`：新增 `tower_defense_60s` 录制入口（仿 `mountain_100s`）。

### 9.6 验收标准
- `python demos/tower_defense.py --smoke 3` 无报错，地图 + 第 1 波敌人出现。
- 点击空地可建塔；塔对范围内敌人开火；敌人死亡金币增加。
- 撑过 5 波 → 「胜利」叠层；基地 HP=0 → 「失败」叠层。
- 经 `scripts/record_all.py` 录一段短 demo 落盘。
- 零外部素材（几何 + 音频全合成），MIT 干净。

### 9.7 执行期待核实的 API（列清，避免拍脑袋）
- `OrbitCamera` 是否提供 `inverse_view_projection(aspect)`（picker 用）；否则用 `view_projection` 手动求逆。
- `engine.audio.SfxPlayer.play` / `sfx(name)` 的确切签名。
- `DemoApp` 基类**未转发 `on_mouse_press`**（只转发 drag/scroll/key）→ 在 `setup()` 内 `self.window.push_handlers(on_mouse_press=self.on_mouse_press)` 自行挂接，不动 `_harness`。
- 控制台 `print` 仅 ASCII；CJK 只进 HUD（`resolve_cjk_font` 已处理）。

---

## 10. P1-③ 射线 FPS 切片 · 设计 note（待 review 放行后执行）

> 状态：**已执行**（2026-10-10，commit `edbf8ed`；先问 4 问由全哥答定）
> 机制致敬：`Wolfenstein 3D`(1992) / `Doom`(1993) 的「走廊迷宫 + 第一人称射击」循环；
> **不碰任何原 IP 名称、美术、音效、关卡**（合规见 §7）。技术骨架复用本项目
> `raymarch_sdf`（SDF 全屏管线）、`blockworld`（DDA 射线思路）、`engine.FlyCamera`（第一人称相机）。
> 借用范围：仅本仓自研模块；零第三方代码、零外部素材。

### 10.1 工作标题（原创，规避受保护词）
- 中文：**「迷堡枪影」**；英文 codename：**Rayhold**（ray = 光线步进渲染的致敬；hold = 要塞）。
- 备选：「幽堡迷径 / Gloamway」「迷堡突围 / Keepbreak」——终名以全哥定为准，不影响架构（内核服务名用机制词 `fps`/`maze`，与产品名解耦）。
- 命名核验（2026-10-09，7 轮检索）：已排除撞名候选 GloomKeep（C64 同人 roguelike）、RayCastle（2020 jam 游戏）、Mazecast（C 语言 raycasting 迷宫）、Ashkeep（Steam 上 FPS）、GrimKeep（《格林堡》2026）、Mazegun（itch/Fortnite 地图）；**Rayhold 在检索范围内无精确占用**。不含 `craft` / `warcraft` / `starcraft` 等受保护词。

### 10.2 先问结论（全哥已答，2026-10-09）
| # | 问题 | 结论 |
|---|------|------|
| 1 | 渲染路线 | **SDF 光线步进**：全屏片元着色器，复用 raymarch_sdf 的软阴影 / AO / 雾 / ACES 管线 |
| 2 | 地图规模 | **程序化随机迷宫**：种子可复现（`--flag seedN` / `R` 换局），保证录制与 headless 验收锁定 |
| 3 | 武器手感 | **hitscan 单武器**：射线枪即时命中 = 网格 DDA 求墙距 + 敌人圆柱求交取最近 |
| 4 | 敌人 AI | **状态机完整版**：巡逻 → 视线发现 → 追击 → 攻击；含受击 / 死亡反馈 |

### 10.3 切片范围（bounded slice）
- **地图**：递归回溯（recursive backtracker）生成"完美迷宫"；15×15 单元（世界 45×45、格距 3.0、墙高 2.8），对角布置起点/出口；约 12% 死胡同编织（braid）成环路减少憋闷；种子可复现（默认固定种子）。
- **视野渲染**：全屏片元 SDF 光线步进。墙体 = 迷宫格（2D 网格 SDF，**单一数据源**上 GPU）；地板/天花板程序化材质；雾 + 简版阴影。**敌人 = SDF 图元**（胶囊/球组合），经 uniform 数组传入（≤16：位置/朝向/受击闪烁/死亡进度），融进同一 SDF 场景。
- **武器**：射线枪——左键射击（0.25s 冷却）；命中判定：视线网格 DDA 求 `t_wall`，再对敌人圆柱求交取 `t < t_wall` 的最近者；命中反馈（白闪 + 击退 + 火花 + 音效）；弹药 60 发，场内弹药箱 +24。
- **敌人（2 种，状态机）**：
  - **游荡者**（近战）：随机航点巡逻 → 视线发现（12m 锥形 + 0.6s 警觉）→ 追击 → 接触攻击（8 HP、1.2s 冷却）。
  - **哨卫**（远程）：发现后保持 8~12m 距离，投掷**可躲的慢速光弹**（12 HP）。
  - 通用状态：PATROL / ALERT / CHASE / ATTACK / HURT（0.35s 硬直 + 击退）/ DEAD（0.8s 淡出）。
  - 追击移动：网格 BFS 最短路（在 `fps_kernel` 内私有实现；通用化留待 `pathing` 服务抽离时迁 `logic/pathfind.py`，与 §9.5 的注记一致）。
- **玩家**：HP 100；受击红闪 + 抖动 + 音效；HP≤0 = 失败；**抵达出口 = 胜利**。
- **拾取**：弹药箱 / 医疗包（SDF 图元 + 浮动旋转，靠近自动拾取）。
- **HUD**：HP / 弹药 / 击杀 / 计时 / 种子 / 出口距离；准星 + 命中标记。
- **控制**：WASD 移动、鼠标拖动视角（FlyCamera）、左键射击、`R` 换种子重开、SPACE 暂停、ESC 退出；`--flag auto` **自动驾驶**（BFS 直奔出口 + 遇敌自动射击）——与 kid_a 的 `go`、塔防的自动布防同思路，供录制与 headless 验收共用。
- **音效**：射击/命中/击杀/受击/拾取/胜负，`engine.audio` 合成，零外部素材。

### 10.4 复用 vs 自建（对照 core/contracts.py）
| 核 | 来源 | 备注 |
|----|------|------|
| render | 复用 `tower_defense_kernel.RenderService` | GL 资源记账 + 回滚语义；demo 侧仍由宿主 pyglet 直接绘制（惯例） |
| economy | 复用 | 弹药库存（`resource='ammo'`，初始 60） |
| entity | 复用 | 敌人/拾取登记（kind = stalker / sentry / ammo / medkit） |
| rules | 复用 | 玩家 HP + 胜负状态机（新增事件：`player_hit` / `player_dead` / `reached_exit`） |
| **maze**（新服务） | **from-scratch** | 迷宫生成/查询（grid、is_wall、find_path、seed、出口）；纯逻辑 headless 可测；后续可升格为正式共同核 |
| **fps**（新内核） | **from-scratch** | 玩家/武器/敌人 AI/拾取/胜负编排；`inject=[render, entity, rules, economy, maze]` |

### 10.5 落地架构（Phase D 范式，无两套接线）
- `experiments/cordis/fps_kernel.py`：`MazeService` + `FpsKernel`（含 headless 验证入口）
- `experiments/cordis/services/{fps_kernel,maze}.py`：`default=` 适配层（配置树经 importlib 解析）
- `experiments/cordis/cordis.fps.yml`：声明本次挂载（render / economy / entity / rules / maze / fps）
- `tree_boot.py`：新增 `WANT_FPS`；`config_boot.py`：新增 `fps` 项（headless：auto-pilot 通关 + 弹药/击杀断言 + GL 回滚）
- `demos/rayhold.py`：DemoApp 子类（SDF shader + FlyCamera + HUD + auto 驾驶）；`_mount_kernel` 经 `mount_tree(config_path('cordis.fps.yml'), want=WANT_FPS)`
- `scripts/record_all.py`：新增 `rayhold_60s` 录制入口 + SHOTS 截图时刻（复用 720p 规格）

### 10.6 验收标准
- `python experiments/cordis/config_boot.py fps` → `FPSKERNEL_OK`（auto-pilot 抵达出口 `state=won`；GL live → 0）
- `python demos/rayhold.py --smoke 3` 无报错；`--shot` 出图；`--flag auto` 无人值守通关
- `scripts/record_all.py --only rayhold` 录制落盘
- 零外部素材、MIT 干净；命名核验记录在案（§10.1）

### 10.7 执行期待核实的 API（列清，避免拍脑袋）
- **迷宫数据上 GPU 的通道**：首选 `pyglet.image.ImageData` 单通道纹理（GL_RED / GL_NEAREST + `texelFetch`）；备选 uniform 数组（225 格）——需核实 pyglet 数组 uniform 设置方式与 GLSL 数组遍历限制。
- **敌人 uniform 数组**：`u_enemy[16]`（vec4 位置+状态 / 附加受击参数）在 pyglet ShaderProgram 的正确写法。
- **指针锁定**（可选增强）：`window.set_exclusive_mouse(True)` 在 pyglet 2.1.16 的行为；录制/无头下必须禁用——默认关闭、`M` 键切换。
- **FlyCamera**：`look(dx,dy)` / `move()` / `update(dt)` 已在位（engine/camera.py:148）；拖动视角走 `on_mouse_drag`（harness 已转发）。
- **DDA 命中**：移植 `blockworld._raycast`（Amanatides & Woo）到 2D 网格 + 敌人圆柱求交。
- **性能**：720p + MAX_STEPS 96 + 16 敌人图元在核显的帧率；掉帧时 `--size 960x540`（沿用 raymarch_sdf 的降级手段）。

### 10.8 风险与对策
- **数据一致性**（最大）：CPU 命中判定与 GPU 渲染必须共用**同一数据源**（MazeService.grid 上传 GPU；敌人数组由内核每帧统一导出，不各写一份）。
- **性能**：敌人求交加重每步成本 → 敌人先用包围球粗剔除再精确形；必要时 SHADOW_STEPS 降 12。
- **手感**：慢速光弹的"可躲性"要求弹体视觉半径与判定半径分调并用同一常量表。
- **无头验证**：环境无 GL/driver 时 `RenderService` 仅记账（既有范式），逻辑与命中判定全部 headless 可测。

### 10.9 执行结果（2026-10-10，commit `edbf8ed`）
- `config_boot.py fps` → `FPSKERNEL_OK`：seed=20261010 / 15×15 / hp=100 / ammo=60 / 敌 8；
  auto-pilot **19.8s** 抵达出口（`kills=7`、`hp=88`、`ammo=63`），GL 回滚 `3 → 0`。
- **种子鲁棒性**：10 个不同种子全部通关（12.9~34.8s，击杀 4~8，HP 88~100）——
  专门验证"程序化随机迷宫"这条选择（迷宫必连通 + auto-pilot 必可解）。
- 三内核回归：`TDKERNEL_OK` / `GODHAND_OK` / `FPSKERNEL_OK` 全绿，既有内核未受影响
  （`RulesService` 只加了两条新事件分支）。
- 踩到并记录**第 5 条 Cordis 坑**：Service 里**不能用 `@staticmethod`**——`Traceable`
  代理对任何方法都注入 self，静态辅助方法会多收一个参数而报
  `TypeError: takes 3 positional arguments but 4 were given`；改成实例方法即可
  （已沉淀进 `~/.workbuddy/skills/cordis-v4-integration`）。
- 待办：demo 实机录制（`record_all.py --only rayhold`）与 `--shot` 截图需本机补跑——
  沙箱 pyglet 原生 GL 受限（既往已两次遇到）；逻辑与 headless 验收已全绿。

### 10.10 手感与帧率打磨（2026-10-10，全哥指令「参考 Aimlabs」后执行）

**Aimlabs 借鉴（命中确认是 aim trainer 手感的核心）**
- **hitmarker**：命中 = 白 X 一闪、击杀 = 琥珀大 X（内径随衰减外扩的"弹开"观感），
  与既有 hit/kill 双音效构成完整命中确认链路（`u_hitmark` uniform + 着色器 45° 刻线）。
- **灵敏度标定**：`−`/`=` 实时调 `LOOK_SPEED`（0.9/1.1 步进），打印 rad/px 与
  **cm/360 估算**（按 800 DPI 折算）——Aimlabs 的 cm/360 通用标定理念。
- **量化结算**：内核加 `shots/hits` 计数；胜负叠层下加小结行
  （用时 / 击杀 / 命中率 / 余血）——Aimlabs Results 思路。
- **准星反馈**：开火瞬间准星微扩（`u_muzzle` 驱动）+ 开火水平随机后坐微 kick。

**可玩性补洞（实机最大缺口）**
- **拾取物可见化**：此前弹药/医疗完全不可见（只靠 HUD）——着色器加 `u_pick[8]`
  悬浮球（弹药=琥珀、医疗=翠绿，sin 浮动），与墙/敌人同一场景求交。
- **出口格高亮**：地板金色呼吸光（`u_exit` + `u_time` 脉冲）——找路的灯塔。

**帧率（全屏 raymarch 的步进成本）**
- 命中阈值随距离放宽 `0.0018t+0.0008 → 0.0024t+0.0010`、步长松弛 0.85→0.90
  （SDF 精确，松弛安全）；`T_MAX 30→24`（敌 AI 视距 SIGHT=13、雾在 24m ≈0.88
  与兜底背景几乎同色，硬边不可见）；`MAX_STEPS 96→88`。贴墙慢步进是热点，
  放宽阈值收益最大。GLSL 无法在沙箱编译验证，已逐段人工复查。

**手感工程**
- **失焦清键**（ALT-TAB 回来不再"角色自己一直走"）+ 失焦掉锁 / 回焦重锁
  （`on_deactivate`/`on_activate`）；HUD 加 `[LOCKED]` 指示。

**平衡复调（依据 §10.9 十种子"多数 HP=100 偏松"）**
- 敌 8→10；STALKER `dmg 8→10`、`atk_cd 1.2→1.05`、速度 `3.0→3.2`；
  SENTRY `dmg 12→14`、`atk_cd 1.6→1.4`；开局弹药 60→50、弹药包 24→20。
- **验收**：`config_boot all` 三内核绿；**新增 `config_boot.py fps --seeds`
  多种子回归入口**（把一次性十种子验证固化成正式命令），10/10 通关
  （14.9~31.5s；aimbot 命中率 100% → HP 无损属预期，**人工难度上探留本机
  实测后复调**）；GL 回滚 `3→0`。demo 平衡参数依旧集中在 `cordis.fps.yml`。

---

## 11. P1-④ 方块解谜切片 · 设计 note（「归仓 / Boxward」）

> 状态：规划（待全哥 review 放行后执行）
> 机制致敬：**Sokoban(1982, Thinking Rabbit)** 的「推箱入仓」规则内核——
> **不碰原 IP 名称与素材**（Sokoban / Boxworld / 仓庫番等既有名一律避开，合规见 §7）。

### 11.1 命名与撞名核验
- **「归仓 / Boxward」**：中文取「颗粒归仓」意象（把箱子推回仓格 = 玩法本体）；
  英文 box + -ward（与 Roadwarden / Rayhold 的 -ward 家族呼应）。
- 核验记录（2026-10-10，WebSearch）：①`"Boxward" steam` → **无精确占用**
  （近似占位 Boxworld 1989/2024 复刻、Box Wrangler 2024，均不同名；
  且确认 **Boxworld 是既有系列名，禁用**）；②`"Boxward" itch.io/indie` → 仍无精确命中；
  ③「归仓 游戏」→ 无同名游戏。执行前再补一轮核验；产品名与代码名（grid/puzzle）解耦，
  撞名可改名不伤架构（§10.1 教训沿用）。

### 11.2 先问结论（全哥 2026-10-10 定）
| 问题 | 结论 |
| --- | --- |
| P1-④ 方向 | **3D 方块解谜**（fps_kernel 里 `world_mesh` 留白 + 体素基建呼应 + 范围可控） |
| 谜题形态 | **带关卡编辑器**（鼠标摆格 → 导出 JSON；压力板/门等机关档本轮不做） |
| 渲染路线 | **光栅贪心合批**（把 `SdfVoxelCore.world_mesh` 落成「贪心网格化」共同核） |
| 切片范围 | 最小切片 + **程序化关卡**（生成器 + 求解器验证 + 种子复现） |

### 11.3 切片范围
- **做**：推箱规则内核（4 向推、撞墙/撞箱不可推、全箱入仓过关）、**撤销（undo）**、
  步数统计、**3 个手工教学关**（直推→转向→多箱排序）+ **10 种子生成关**、
  **求解器 auto-play**、**一屏式关卡编辑器**（摆墙/箱/目标/起点，导出/加载 JSON）、
  贪心网格化共同核、正交 45° 四向旋转视角、合成音效（推/入仓/受阻/过关）、录制入口。
- **不做**（下轮延伸素材）：机关（压力板/门/钥匙）、冰面/洞等地形变体、
  存档系统、关卡分享、多主题美术、超大关卡。

### 11.4 复用 vs 自建
| 复用 | 自建 |
| --- | --- |
| `SdfVoxelCore` 契约（block_at/voxel_get/**world_mesh 留白本次补齐**） | **贪心网格化** `experiments/cordis/greedy_mesh.py`（纯函数：同色共面格合并成大矩形，可单测） |
| Cordis 范式（配置树挂载 / inject 排序 / `ctx.effect` GL 回滚） | **GridService**（服务名 `grid`）：关卡体素 + JSON schema + 生成器 + **BFS 求解器** |
| `RulesService` 事件流（加 `puzzle_solved` → won） | **PuzzleKernel**（服务名 `puzzle`）：step/undo/reset/auto_play/步数，事件 push/solved/blocked |
| `RenderService` GL 记账、`EntityCore`、合成音效（SfxPlayer） | **编辑器**（demo 层）：鼠标格子拾取 + 摆放 + 导出 |
| `Mat4.orthogonal_projection`、blockworld 的 DDA 拾取先例 | 正交 45° 四向旋转相机（demo 层，方向映射纯函数可单测） |

### 11.5 落地架构
- **单一事实来源 = 关卡 JSON**：`{"size":[w,h], "walls":[[i,j]..], "boxes":[[i,j]..],
  "goals":[[i,j]..], "player":[i,j], "meta":{"name","seed","par"}}`——
  **编辑器导出的 = 生成器产出的 = 内核吃的**，三端同 schema。
- **求解器**：BFS over `(player_pos, boxes_frozenset)`；角落死格（dead square）剪枝；
  状态上限 20 万（超限报「超出能力」）。生成器：撒墙（密度 ~0.18）→ 摆 3~5 箱与目标 →
  `solve()` 验证**可解且最少推数 ≥ 阈值**（防平凡），败则重掷（≤64 次，仍败降密度重试）。
- **auto-pilot = 求解器回放**：headless 验收、`--flag auto` 录制、生成器验证**同源**——
  「验证怎么解的 = demo 怎么玩的 = 关卡怎么验证可解的」（延续 Phase D 理念）。
- 渲染：静态墙 → 贪心合并**单个网格**（一次 upload_mesh）；动态（箱/玩家）小 cube 逐个
  batch；目标 = 地板色块；尽量复用引擎既有 voxel 着色器（执行时勘查 blockworld 路径）。
- GL 资源照旧 `ctx.effect` 绑内核 fiber，dispose 回滚断言 live→0。
- 文件清单：`greedy_mesh.py`、`puzzle_kernel.py`（GridService+PuzzleKernel）、
  `services/puzzle.py` 适配、`cordis.puzzle.yml`、`tree_boot.WANT_PUZZLE`、
  `config_boot.py puzzle 分支（+all 四项）`、`demos/boxward.py`、
  `record_all.py boxward_60s`、tests 单测、README 清单 19→20。

### 11.6 验收标准
1. `config_boot all` 四内核全绿（td/godhand/fps/puzzle）；
2. `config_boot.py puzzle --seeds …`：10 种子生成关全部「可解且回放 won」+ GL 回滚 `3→0`；
3. `greedy_mesh` 单测：十字/T/L/角全过，合并后面数 < 逐块面数；
4. 求解器单测：已知最小解关卡通回放序列正确；
5. demo `py_compile` + 手工关 smoke；实机截图/录制本机补跑（沙箱 GL 受限，惯例）。

### 11.7 待核实 API
- 引擎 voxel 着色器可复用面（blockworld 的网格/图集路径）；
- 正交相机下鼠标 → 格子拾取（逆投影 + DDA，参考 blockworld）；
- `EntityCore` 对静态网格 vs 动态小 cube 的批量画法（见 tower_defense demo render）。

### 11.8 风险
- **求解器状态爆炸**（箱多格大）→ 箱 ≤5、死格剪枝、状态上限，超限降规模重掷；
- **贪心合并边界情形**（十字/T 形接缝视觉裂缝）→ 单测覆盖 + 执行时首验；
- **视角旋转的方向映射**（屏幕方向 ↔ 格方向）→ 4 状态映射纯函数 + 单测；
- **GLSL 沙箱不可编译**→ 优先复用已验证的引擎着色器，新写则逐段复查（§10.10 惯例）；
- **命名**→ 执行前补一轮撞名核验，解耦保改名自由。

### 11.9 执行结果（2026-10-10，commit 见 §8）

- **内核**：`puzzle_kernel.py`（约 560 行）——`GridService`（grid：JSON schema 单一事实来源 +
  BFS 求解器 `(玩家,箱集)` 状态 + 角落死格剪枝 + 20 万状态上限 + **反向拉箱生成器** +
  `world_mesh` 贪心网格化，SdfVoxelCore 留白补齐）+ `PuzzleKernel`（puzzle：step/undo/reset/
  next_level/auto_play、步数与推数、事件 move/push/goal/blocked/undo/solved、编辑器摆格、
  GL 句柄 `ctx.effect` 回滚）。3 个手工关以 ASCII 内嵌（直推 / 转向 / 双箱排队）。
  `RulesService` 加 `puzzle_solved` 事件分支。
- **执行中修的一个真 bug**：生成器第一版把"拉箱"写成了玩家与箱子**交换位置**
  （非法动作）→ 打乱状态根本不可解，448 次尝试全灭。改为标准**反向拉箱**
  （玩家在箱外侧走一步、箱跟进原格——每步拉恰好被一次反向推撤销，可解性由构造保证），
  10/10 种子立即全过。
- **demo**：`demos/boxward.py`——正交 45° 视角（`OrbitCamera` 复用，Q/E 90° 步进），
  WASD 方向随相机映射（`cam_move_dirs` 纯函数 + 单测）；静态墙一次贪心建网（换关重建），
  箱/玩家/目标动态小 cube；目标金色呼吸光、箱入仓变绿；编辑器（M 摆格 / X 试解 /
  E 导出 `experiments/cordis/levels/*.json` / C 清内墙）；音效 6 合成；
  `--flag auto / seedN / level=路径`；record 入口 `boxward_60s`。
- **验收（§11.6 全过）**：
  1. `config_boot all` 四内核全绿（td/godhand/fps/puzzle）；
  2. `config_boot puzzle`：3 手工关（par 3/12/12 步）+ **10 种子生成关全部求解回放到胜**
     （解长 15~31 步，推数 6~10），`steps == len(sol.moves)` 回放无漂移断言过，GL `3→0`
     → `BOXWARD_OK`；
  3. `greedy_mesh` 单测：3×3 墙块 21→5 quad、矩形合并/L 形/空墙；求解器最小解 + 死角判死；
     生成器 3 种子构造性可解；`cam_move_dirs` 四档映射——**6 项单测全绿**
     （并入 `tests/run_tests.py`，`ALL_LOGIC_TESTS_PASSED`）。
- 教训入档：**单测期望值要独立重算**——相机映射测试首版把 rot=1 的 D 方向写反
  （屏幕右应为 +x），实现正确、期望错误，测试失败反而抓的是测试自己的错。
- 待办：实机录制/截图/帧率实测需本机补跑（沙箱 pyglet 原生 GL 受限，惯例）；
  命名执行前核验已完成（Boxward 无精确占用）。
- **与 P0 线的并行对齐**：执行期间远端进了 5 个 P0 commit（共同核抽离到 `core/` +
  配置树统一 + `cores` 自检），push 被拒后 rebase 对齐——`RulesService` 已是
  `core/rules_core.py` 的共享实现，`puzzle_solved` 分支随之落进共同核新家
  （tower_defense_kernel 只留 re-export）；`config_boot all` 扩为**五项回归**
  （td/godhand/fps/puzzle/cores）全绿。最终 commit `0d22d04`（rebase 前为 `21a63ce`）。

---

## 12. P1-⑤ 弹幕固定屏射击 · 设计 note（「弹潮 / Swarmward」）

> 状态：规划（待全哥 review 放行后执行）
> 机制致敬：**Robotron 2084**(1982, Williams) 的「固定屏全向生存 + 双摇杆」品类——
> **不碰原 IP 名称与素材**（Robotron / Geometry Wars 等既有名一律避开，合规见 §7）。

### 12.1 命名与撞名核验
- **「弹潮 / Swarmward」**：swarm（敌潮/弹潮）+ -ward（家族第五作：Roadwarden / Rayhold /
  Boxward / Swarmward）；中文取「弹幕如潮、守屏求生」。
- 核验记录（2026-10-11，WebSearch）：①`"Swarmward" game` → **无精确占用**
  （近似：Swarm 2011 平台游戏、SWARMED 2026 arena roguelite——均不同名）；
  ②备选 Stormward → **有占用**（Minecraft mod 1.3K 下载活跃更新 + TTS 卡牌 Ghaiaz:
  Stormward）→ 弃用。执行前再补一轮核验；产品名与代码名（storm）解耦。

### 12.2 先问结论（全哥 2026-10-11 定）
| 问题 | 结论 |
| --- | --- |
| P1-⑤ 方向 | **弹幕固定屏射击**（四选一胜出——particles/pathing 的**首个内核消费者**，范围最小，街机手感延续 Rayhold 的 Aimlabs 打磨经验） |
| 玩法形态 | **全向生存**（固定屏全向刷敌，非横排清版/非规避弹幕 boss） |
| 波次来源 | **程序化编波**（延续 Boxward 生成器理念；手工只做教学波） |
| 操作布局 | **双摇杆**（WASD 移动 + 方向键/鼠标射击） |

### 12.3 切片范围
- **做**：固定屏全向生存（10 波通关制 + `--flag endless` 无尽）、敌型 3 种
  （直冲 chaser / 游走射击 weaver / 编队蜂群 drone）、程序化编波（种子+波号 →
  数量/混比/编队图案/速度曲线）、`pathing.formation` 编队进场、双摇杆、
  连击窗口 + 计分（economy 存 score）、Aimlabs 手感件（hitmarker / 连击数字 /
  **量化结算**：命中率/最长连击/波数/击杀/得分）、粒子反馈（爆炸/命中火花/尾焰，
  particles 核）、auto-pilot 闪避 AI、6 合成音效、录制入口。
- **不做**（下轮延伸素材）：boss 波与弹幕图案生成器（螺旋/花瓣——§12.2 形态问里
  的规避弹幕素材）、升级/roguelite 构建、排行榜、手柄支持、多场景/主题。

### 12.4 复用 vs 自建
| 复用（含两个"首个内核消费者"） | 自建 |
| --- | --- |
| **particles**（emit/update，CPU 状态 headless 安全）——**首个内核消费者**：爆炸/命中/尾焰 | StormKernel（storm）：波次生成器 + 碰撞（三组圆测试 O(n²)，屏内实体量级可控） |
| **pathing.formation**（ring/line 编队）——**首个内核消费者**：敌群进场图案（诚实评估：find_path 在固定屏无用，消费面=编队+游走） | 敌 AI 状态机（进场→阵位→散开攻击） |
| rules **原样复用**：lives=3 复用 base_hp、`wave_cleared`（total_waves=10 → won）、`player_hit`（→lost）——零改动 | 连击窗口计时（3s 内连杀累积） |
| economy 存 score 计分；entity 实体注册；render GL 记账 + `ctx.effect` 回滚 | auto-pilot 闪避 AI（威胁度采样转向 + 最近敌锁定） |
| Aimlabs 手感件（§10.10 的 hitmarker/结算思路平移）；boxward 的正交相机/vertex_list 骨架 | 无 |

### 12.5 落地架构
- **固定屏世界** 24×15 世界单位，正交相机全屏（boxward 同款骨架）；粒子用 y=0 平面
  （gravity=0），把粒子核的 3D 状态映射到屏面。
- **波次生成** `wave_plan(seed, wave_i)` → dict(count, mix{chaser/weaver/drone},
  formation{ring/line/v}, speed_mult)，难度单调递增；教学波硬编码 1-2 个。
- **auto-pilot = 闪避 AI**：敌弹威胁度采样转向 + 最近敌锁定射击——headless 验收、
  `--flag auto` 录制同源（§10/§11 理念延续）。
- **10 波通关制**：headless 断言 won 干净利落；`--flag endless` 实机无尽模式。
- 文件清单：`storm_kernel.py`、`services/storm.py`、`cordis.storm.yml`、
  `tree_boot.WANT_STORM = ('storm','render','rules','economy','entity','particles','pathing')`、
  `config_boot.py storm 分支（all 六项）`、`demos/swarmward.py`、`record_all.py stormward_60s`、
  tests 增补、README 20→21、remake_ideas §12.9。

### 12.6 验收标准
1. `config_boot all` 六项全绿（td/godhand/fps/puzzle/cores/storm）；
2. `config_boot storm --seeds …`：10 种子 × 10 波 auto-pilot 全通关（won）+ 结算统计打印
   + GL 回滚 → `STORMKERNEL_OK`；
3. 单测：波次难度单调性（波 i 各参数 ≥ 波 i-1）、编队图案几何（ring/line 位置与数量）、
   碰撞判定（已知布局命中/未命中）、连击窗口计时；
4. demo `py_compile` + smoke；实机录制/截图/手感验证本机补跑（惯例）。

### 12.7 待核实 API
- `pathing.formation` 的 pattern 取值范围（ring 已确认；line/arc 读源码后定，缺则自算不扩核）；
- particles preset dict 直传（emit 支持自定义 speed/life/gravity ✓ 已读源码确认）——观感待实机；
- 正交固定屏 × harness 截图管线（boxward 已验证同款，低风险）。

### 12.8 风险
- **高波次碰撞 CPU 压力**（50+ 敌 × 100+ 弹 O(n²)）→ 先实测，不达标上网格分桶；
- **auto-pilot 闪避在密集弹幕下不稳** → 威胁度阈值调参 + 10 种子回归把关；
- **particles 观感**（CPU 侧简单步进）→ 实机先看，不够再谈 GPU 实例化（P2 素材）；
- **命名**→ 执行前补一轮撞名核验（Swarmward 目前干净）。

### 12.9 执行结果（2026-10-11，commit 见 §8）

- **内核**：`storm_kernel.py`（约 470 行）——`StormKernel`（storm）：`wave_plan`
  程序化编波（count/speed/weaver 占比随波号单调，单测把关）、敌型 3 状态机
  （chaser 限转向率追击 / weaver 距离带+正弦横移+3 连发散射 / drone 编队环绕）、
  三组圆碰撞（模块级 `_circle_hit` 纯函数）、连击窗口 + 连击倍率计分（economy 存 score）、
  auto-pilot 闪避 AI（16 方向威胁度采样 + **方向连续性奖励** + 最近敌锁定）；
  **pathing.formation 消费**：ring/grid 委托共同核，line/v 内核自算（`_layout_points`
  支持注入 formation_fn，单测裸调用）。
- **共同核最小扩展**：`core/particles_core.py` + `contracts.py` 加 **`snapshot()`**
  只读快照（`[(x,y,z,life_frac)]`）——粒子核首个内核消费者的渲染端需要读状态，
  不暴露 `_parts` 内部结构；`StormKernel.update` 每帧驱动 `particles.update` +
  三事件 emit（击杀碎屑/命中火花/受击冲击，自定义 preset gravity=0 压到屏面）。
- **demo**：`demos/swarmward.py`——正交固定屏（竞技场 letterbox 完整可见）、
  双摇杆（WASD + 方向键/鼠标指向射击）、受击白闪（Aimlabs hitmarker 的 3D 等价物）、
  粒子层预分配 512 槽 vlist 逐帧整块写入、量化结算叠层（波数/击杀/命中率/最长连击/得分）、
  6 合成音效、失焦清键；`--flag auto/endless/seedN`；record 入口 `stormward_60s`。
- **执行中踩到并记录第 6 条 Cordis 坑**：**staticmethod 二次现形**——`_bump_combo`
  写成 `@staticmethod`，Traceable wrapper 给所有方法注入 self → 形参整体错位
  （combo 收到 ShadowView，`combo + 1` 报 `TypeError`）。与 §10.9 第 5 坑同源，
  但这次连 `int()` 都救不了（包装在方法边界），修法依旧：改实例方法。
  附带教训：Traceable 下 int 属性算术前显式 `int()` 解包更稳。
- **平衡迭代**：首跑 auto-pilot 第 7 波阵亡（3 命耗尽）→ 两轮调参：
  敌压力微降（chaser 2.3→2.0、弹速 6.5→6.0、weaver 射程 12→10.5、波间 1.2→1.6s、
  密度 cap 26→24）+ 闪避 AI 增强（8→16 方向、前瞻 0.35→0.5s、chaser 贴身惩罚加重、
  **方向连续性奖励**抑制帧间抖动）→ **10/10 种子全通关**，命中率还从 ~50% 升到 55-66%
  （抖动消除的连带收益）。全程 3 命未耗尽，实机紧张度留本机验证后复调。
- **验收（§12.6 全过）**：`config_boot all` 六项全绿；`storm --seeds` 10 种子全通关
  （62.6~71.4s，击杀 136~150，`STORMKERNEL_OK`，GL `3→0`）；单测 4 项
  （编波单调/编队图案/圆碰撞/连击窗口）并入 run_tests 全绿。
- 待办：实机录制/截图/手感（双摇杆+粒子观感）本机补跑（沙箱 GL 受限，惯例）。

---

## 13. 弹潮 v1.1 · boss 波与弹幕图案生成器（Swarmward 延伸）

> 状态：规划（note 就绪，待全哥点菜执行）——§12.3 遗留素材转正，设计档位取推荐项。

### 13.1 命名
不换名：Swarmward 就地升级（版本 v1.1），无新增核验。

### 13.2 切片范围
- **做**：**弹幕图案库**（参数化生成器：spiral 螺旋 / flower 花瓣 / fan 扇形 /
  ring-burst 环爆 / sweep 扫射——图案 = ``f(t, 参数) → [(angle, speed), ...]`` 发射计划）；
  **boss 敌型**（hp 60+10·波号、半径 1.6、**3 阶段**按血量 66%/33% 切换：
  P1 螺旋+缓移 → P2 花瓣+召唤 drone → P3 狂暴加速全图案混合）；波次编排
  （10 波制的波 5/10 为 boss 波；无尽模式每 5 波）；boss 血条（HUD）；auto-pilot
  boss 应对（boss 弹速慢利于闪避，威胁度采样加密）。
- **不做**：多 boss 图鉴（一个 boss 模型多配色）、弹幕编辑器、成就系统。

### 13.3 复用 vs 自建
复用 rules（boss 波也是一波，`wave_cleared` 不变）、economy（boss 大额分）、
particles（boss 死亡大爆炸）、entity/render 记账；自建 **PatternEmitter**
（纯函数，同 t 同参数同计划——确定性可单测）与 BossState 阶段机。

### 13.4 落地架构
`storm_kernel.py` 就地扩展（不改文件结构）：pattern 发射函数放模块级
（``emit_plan(pattern, t, phase)``）；boss = 特殊敌 dict；``ebullets`` cap 300
（超限拒发 + 优先回收最老弹）；demo 加顶部血条（2D 条 vertex_list）。

### 13.5 验收标准
1. `config_boot storm --seeds`（含 boss 波的完整 10 波）10/10 通关；
2. 图案单测：确定性、密度上限、角度分布覆盖；boss 阶段切换单测；
3. 实机弹幕观感与 auto-pilot boss 通过率本机验证。

### 13.6 风险
碰撞峰值（boss 弹幕 + 小兵同屏 O(n²)）→ 网格分桶备选；auto-pilot 在花瓣弹幕下
通过率 → 10 种子回归把关，不达标先降 boss 弹速。

---

## 14. 静馆 / Stillhall · Myst 漫游解谜

> 状态：规划（note 就绪，待全哥点菜执行）——致敬 `Myst`(1993, Cyan) 的
> 「节点式漫游 + 点击热点 + 谜题状态机」；**不碰原 IP 名称与素材**（合规见 §7）。

### 14.1 命名与撞名核验
**Stillhall**（still + hall，静默馆邸）。核验（2026-10-11 组合查询）：无精确命中
（候选 Hushwing 因 MTG 名卡 Hushwing Gryff 弃用）。**执行前补单轮核验**。

### 14.2 设计前提（未经先问，档位取推荐项，review 可调）
- 漫游形态：**节点式**（经典 Myst 式：固定机位实时渲染 + 点击画面边缘/热点换景），
  而非自由 WASD——省关卡设计、保"凝视旧地"氛围。
- 谜题形态：顺序解锁链（1 馆 3 景 3 谜：找钥匙 → 开机关 → 终局门）。
- **项目特色卖点**：场景数据 JSON 与 `scene_viewer` / 「LLM 场景生成工作流」同 schema——
  LLM 生成场景 → 内核消费（§14.5）。

### 14.3 切片范围
- **做**：节点图（3 景 + 邻接边）、热点点击 + 光标反馈、物品栏（3-4 件）、
  谜题状态机（顺序解锁）、场景 JSON 加载（含热重载）、headless 点击序列验收、
  环境音（风/钟摆，合成）。
- **不做**：多馆、自由视角、NPC 对话、预渲染帧管线、存档。

### 14.4 复用 vs 自建
| 复用 | 自建 |
| --- | --- |
| `scene_viewer` 场景 JSON 管线（加载/热重载）；engine 场景组装（kkrieger/nordic 先例） | `hall_kernel.py`（hall）：节点图 + 热点表 + 谜题状态机 + **`click(hotspot_id)` API**（headless 可驱动） |
| rules：终局谜题 → `puzzle_solved` 事件（P1-④ 已加，**零改动**） | 物品栏（entity 存即可）；场景 JSON schema（nodes/edges/hotspots） |
| render 记账 + `ctx.effect` 回滚；合成环境音（SfxPlayer） | 固定机位渲染的景切换（淡入淡出） |

### 14.5 落地架构
场景 JSON：``{"nodes": [{"id", "scene_ref", "hotspots": [{"id", "area", "action"}]}],
"edges": [...], "puzzle": {...}}``——scene_viewer 的场景描述 + 交互层的最小并集。
demo：当前景固定机位渲染（kkrieger 场景组件复用）+ 屏面射线拾取热点
（正交/透视两用）+ 热点悬停高亮 + 物品栏 HUD。

### 14.6 验收标准
1. `config_boot hall`：加载场景 JSON → 模拟点击序列 → 3 谜全解 → `puzzle_solved` → won；
2. 场景 JSON 热重载（改文件生效，坏 JSON 拒绝保旧——scene_viewer 惯例）；
3. 单测：热点命中、谜题状态机、节点图连通；
4. 实机漫游/解谜体验本机验证。

### 14.7 风险
**内容量是主风险**（谜题与美术设计是内容活）→ 切片铁律 3 景 3 谜；
LLM 场景 JSON ↔ hall schema 映射层工作量 → 先手写一份样例场景定 schema；
热点/物品栏是否升共同核 → 执行时评估，先内核内置。

---

## 15. 星阑 / Voidward · Elite 太空贸易

> 状态：规划（note 就绪，待全哥点菜执行）——致敬 `Elite`(1984, Acornsoft) 的
> 「惯性飞行 + 贸易 + 战斗」；**不碰原 IP 名称与素材**（合规见 §7）。

### 15.1 命名与撞名核验
**Voidward**（void + -ward，家族第六作：…/Boxward/Swarmward/Voidward）。核验
（2026-10-11 组合查询）：无精确命中（VoidFall 桌游/游戏、Pulse://Void 均不同名）。
**执行前补单轮核验**。

### 15.2 设计前提（未经先问，档位取推荐项，review 可调）
惯性飞行 + 单星系贸易 + 海盗拦截；**economy 核第二个重度消费者**（市场模拟是工程亮点）；
范围最大 → 狠砍清单见 §15.3。

### 15.3 切片范围
- **做**：1 星系（恒星 + 3 行星 + 1 空间站）、5 种货（价格 = 基准 × 供需随机游走
  + 事件冲击，确定性可复现）、船 1 艘（货舱 20 / 炮 1）、海盗 2-3 艘拦截战、
  2 级升级（舱位/炮）、胜负 = 资产达标（credits ≥ 10000 → won）。
- **不做**：着陆/行星表面、多星系跳转、派系与 NPC 对话、稀有货、自动驾驶仪。

### 15.4 复用 vs 自建
| 复用 | 自建 |
| --- | --- |
| **economy**：credits + 5 种货物库存全走 KV 计账 | `void_kernel.py`（void）：星系图 + 市场模块（`price(good, t)` 确定性函数 + 事件冲击）+ 船状态 |
| rules：加一条 `asset_reached` 分支（第 6 条事件，RulesService 小扩） | 惯性物理（推力 + 软阻尼——terrain_flyover 相机先例）；空间战斗（追击/射击/命中） |
| entity（海盗/货站实体）；render 记账 + `ctx.effect` 回滚 | 海盗 AI（接近→拦截→射击）；星图 HUD（M 键） |
| engine FlyCamera（惯性飞行相机现成） | 交易面板（数字键买/卖/升级） |

### 15.5 落地架构
市场数值：``price = base * (1 + 0.3*sin(t/period + phase) + shock)``——确定性可复现，
事件冲击由种子驱动（引力紊乱 → 某货涨价）；headless 贸易脚本 = 「低买高卖 +
海盗击退」的策略回放（与 demo 手玩同 API：`buy/sell/upgrade`）。

### 15.6 验收标准
1. `config_boot void`：贸易脚本达标 won（含至少一次海盗战胜利）；
2. 市场单测：价格确定性、事件冲击方向、供需回归均值；海盗战斗单测；
3. 惯性飞行手感与市场节奏本机验证（赚钱速率曲线调参）。

### 15.7 风险
范围最大（四选一时的评估仍成立）→ 铁律砍单；市场数值平衡 → headless 脚本
验证赚钱速率；惯性手感实机调参。

---

## 16. 落音 / Pulsefall · 节奏音击

> 状态：规划（note 就绪，待全哥点菜执行）——致敬下落式音击品类
> （Beatmania IIDX 1999 等）；**不碰原 IP 名称与素材**（合规见 §7）。

### 16.1 命名与撞名核验
**Pulsefall**（pulse 脉冲 + fall 下落）。核验（2026-10-11 组合查询）：无精确命中
（Pulse://Void 不同名）。**执行前补单轮核验**。

### 16.2 设计前提（未经先问，档位取推荐项，review 可调）
4 轨道下落 + 3 档判定窗；**谱面与音乐同源**——程序化生成器同一组参数
（BPM/密度/种子）同时产出 note 时间轴与合成音乐，零对轴误差（工程亮点）；
**audio 核首个内核消费者**（Track/tone 合成：鼓点/贝斯/主旋律三层）。

### 16.3 切片范围
- **做**：4 轨道（DFJK 四键）、3 谱面（BPM 90/120/150 各一，种子复现）、
  判定 3 档（PERFECT ±40ms / GOOD ±90ms / MISS）、血量（miss 扣 → lost）、
  连击/得分（economy）、结算（P/G/M 计数 + 准确率 + 最大连击）、
  实机 offset 校准（F4 ±20ms）。
- **不做**：长按/滑键 note、自制谱面导入、多人、曲目商店。

### 16.4 复用 vs 自建
| 复用 | 自建 |
| --- | --- |
| **audio**：`Track/tone` 合成音乐（内核按谱面参数生成三层曲子）——**首个内核消费者** | `pulse_kernel.py`（pulse）：`chart(seed, bpm, bars)` → notes + synth_events（同源核心） |
| rules：base_hp 复用血量（player_hit → lost 已有） | 判定窗（逻辑时钟）；`hit(track, t_now)` API（headless 自动演奏 = 到点即 hit） |
| economy 存 score；render 记账 + `ctx.effect` 回滚 | 下落渲染（轨道 + note 方块 + 判定线，正交 2D——boxward/swarmward 骨架） |

### 16.5 落地架构
谱面可打性约束（生成器内置）：同轨最小间隔 ≥ 1/8 拍、全局密度上限、
时长 = bars 精确；音乐合成事件与 note 一一对应（同 t）。

### 16.6 验收标准
1. `config_boot pulse`：3 谱面自动演奏全连 won；
2. 谱面单测：可打性约束、时长准确；判定窗单测；音频合成 smoke（wav 落盘）；
3. 实机音画同步（offset 校准后）与听感验证——**本机验证要求最高**（§12 四选一时的评估）。

### 16.7 风险
实机音画同步（pyglet 播放延迟）→ offset 校准 + 判定窗放宽兜底；程序化音乐听感
（三层合成器单薄）→ 实机评估，P2 再谈采样器；60Hz 逻辑步进 16.7ms 粒度
< 40ms PERFECT 窗 ✓ 判定可行。



