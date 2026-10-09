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

**下一步**：P1-③ 候选——**射线 FPS（Wolfenstein/Doom 风）**，直接复用 `raymarch_sdf` + `kkrieger_demo` 已验证的 FPS 能力（见 §6）。按五阶段协作先「先问」（≤4 问、每问带推荐项），计划经全哥放行后执行；亦可由全哥指定其它内核。

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
