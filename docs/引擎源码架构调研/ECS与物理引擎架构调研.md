# 引擎核心系统架构调研：ECS 框架与物理引擎源码剖析

## 0. 元信息

**调研时间**：2026-10-21 · **调研员**：arch-ecs-physics

### 真实抓取的 URL（>=12）

| # | URL | 用途 |
|---|---|---|
| 1 | `https://github.com/skypjack/EnTT/tree/master/src/entt` | EnTT 顶层目录 |
| 2 | `https://api.github.com/repos/skypjack/EnTT/contents/src/entt/entity` | EnTT `entity/` 精确文件清单（API 返回完整 JSON） |
| 3 | `https://github.com/skypjack/EnTT/blob/master/src/entt/entity/sparse_set.hpp` | sparse_set 源码 |
| 4 | `https://skypjack.github.io/2019-03-21-ecs-baf-part-2-insights/` | 作者亲述 group 机制与间接层代价 |
| 5 | `https://github.com/SanderMertens/flecs/tree/master/src` | flecs `src/` 精确文件清单 |
| 6 | `https://deepwiki.com/SanderMertens/flecs/2.3-tables-and-archetypes` | `ecs_table_t` 字段与 column_map |
| 7 | `https://ajmmertens.medium.com/building-an-ecs-storage-in-pictures-642b8bfd6e04` | archetype/table graph/tag 列映射图解 |
| 8 | `https://github.com/bevyengine/bevy/tree/main/crates/bevy_ecs` | bevy_ecs README 与特性 |
| 9 | `https://github.com/bevyengine/bevy/blob/71c5f1e3e49efc060de16157e30705a4477f8b91/crates/bevy_ecs` | Query/Changed/Added/SparseSet/Bundle 官方示例 |
| 10 | `https://bevyengine.org/news/bevys-first-birthday` | 混合 Archetypal/SparseSet 与 lock-free 设计说明 |
| 11 | `https://raw.githubusercontent.com/erincatto/box2d/main/src/CMakeLists.txt` | Box2D v3.2.0 全部源文件权威清单 |
| 12 | `https://raw.githubusercontent.com/erincatto/box2d/main/src/solver_set.h` | `b2SolverSet` 完整定义 |
| 13 | `https://raw.githubusercontent.com/erincatto/box2d/main/src/contact_solver.h` | `b2ContactConstraint` 与 SIMD 变体函数 |
| 14 | `https://box2d.org/documentation/md_migration.html` | v2.4→v3.0 迁移（TGS Soft、事件） |
| 15 | `https://box2d.org/posts/2024/08/determinism/` | 三级确定性与 FMA/三角函数处理 |
| 16 | `https://raw.githubusercontent.com/jrouwe/JoltPhysics/master/Jolt/Physics/Collision/NarrowPhaseQuery.h` | NarrowPhaseQuery 完整 API |
| 17 | `https://jrouwe.github.io/JoltPhysicsDocs/5.5.0/class_large_island_splitter.html` | `cLargeIslandTreshold` 与分批求解 |
| 18 | `https://deepwiki.com/jrouwe/JoltPhysics/2-core-architecture` | `PhysicsSystem::Init` 参数与子系统表 |
| 19 | `https://deepwiki.com/happyhappygame/JoltPhysics123/4-simulation-pipeline` | job 依赖图、TempAllocator、双缓冲接触缓存 |
| 20 | `https://api.github.com/repos/dimforge/rapier/contents/src/dynamics` | Rapier `dynamics/` 精确清单 |
| 21 | `https://api.github.com/repos/dimforge/rapier/contents/src/dynamics/solver` | Rapier `solver/` 精确清单 |
| 22 | `https://rapier.rs/docs/user_guides/rust/simulation_structures` | 13 参数 `PhysicsPipeline::step` 与集合清单 |
| 23 | `https://rapier.rs/docs/user_guides/rust/getting_started` | feature 开关与 `wide` SIMD |
| 24 | `https://deepwiki.com/bulletphysics/bullet3/2-physics-system-architecture` | Bullet 模块化架构与调用链 |

### 对任务简报的修正（源码实证，简报有误）

1. **Box2D v3 没有 `src/solver/` 子目录**，也不存在 `b2_velocity_solver.c` / `b2_position_solver.c` / `b2_soft_step.c`。v3 是**扁平 `src/`**，求解相关文件为 `solver.c`、`solver_set.c`、`contact_solver.c`，且 contact solver 按 SIMD 宽度分出 `contact_solver_w4.c` / `contact_solver_w8.c` / `contact_solver_wide.inl`（v3.2.0 权威 CMake 清单）。
2. **Rapier 的 `src/` 下没有 `parry/`**。parry 是独立仓库 `dimforge/parry`。`src/` 实际为 `control/ counters/ data/ dynamics/ geometry/ pipeline/ utils/ + lib.rs`。
3. **Rapier 求解器不是 "NbSim"**。实际是 `staged_island_solver/` + `velocity_solver.rs`，配合 `soft_constraint/`（小步长 soft constraints），SIMD 基于 `wide` crate 每指令 4 条 manifold。
4. **EnTT 没有 `src/entt/ecs/`**。world/view/system 全在 `entity/`（`registry.hpp` / `view.hpp` / `organizer.hpp` / `system` 内联于 `entt.hpp`）。
5. **flecs 没有 `src/entity_view.c`，也没有 `src/world/` 目录**——是 `entity.c` / `world.c` 扁平文件。

---

## 1. 对比总表

| 项目 | 语言 | 存储范式 | 求解器/迭代 | License | 适用场景 |
|---|---|---|---|---|---|
| **EnTT** | C++17/20 | 稀疏集（SoA），group 支持完美 SoA | 无（纯 ECS），并行由用户组织 | MIT | 嵌入式、极致性能、最小依赖 |
| **flecs** | C99 + C++17 薄封装 | Archetype（table）+ 可选 Sparse/DontFragment | 无；system pipeline 自动并行 | MIT | 复杂仿真、关系/层级、脚本化 |
| **Bevy ECS** | Rust | Archetype（Table）**或** SparseSet，**逐组件可选** | 无；lock-free 多线程 executor + 歧义检测 | MIT/Apache-2.0 | Rust 全栈引擎、大型工程 |
| **Unity DOTS/Entities** | C# | Chunk archetype（Unity 内部不可完全公开） | Job System + 依赖图 | 商业 | 商业 Unity 项目（大世界） |
| **Box2D v3.2** | C17 + C++17 头文件 | SoA 连续数组 + SIMD(4/8-wide) | **TGS Soft**（子步 + soft constraint），Gauss-Seidel 冲量 | MIT | 2D 刚体平台/独立游戏，需确定性 |
| **Rapier** | Rust | generational arena（SoA）+ `wide` SIMD | **分阶段 island solver**，soft constraint，SIMD 4/8 | Apache-2.0 | Rust 3D 物理、WASM 跨平台 |
| **Jolt Physics** | C++17 | SoA + `Body*` 紧凑池 + 无锁哈希表 | Gauss-Seidel 冲量 + **LargeIslandSplitter**（Chen et al. 并行化） | MIT | 3D 大量刚体、多核游戏 |
| **Bullet Physics** | C++ | 对象/AoS 混合（`btCollisionObject` + `btRigidBody`） | **PGS/Sequential Impulse**（带上下界），可选 NNCG / MultiBody | zlib | 影视特效、离线仿真、机器人 |

---

## 2. EnTT（C++，MIT）

### 目录结构（GitHub API 精确返回 `src/entt/entity/`）

```
src/entt/
├── entt.hpp  fwd.hpp  tools.hpp          # 聚合头 + 前向声明 + 工具
├── config/  container/  core/  entity/  graph/
├── locator/  meta/  natvis/  poly/  process/
├── resource/  signal/  stl/  tools/
└── entity/
    ├── registry.hpp (44KB)   sparse_set.hpp (38KB)   storage.hpp (44KB)
    ├── view.hpp (43KB)      group.hpp (40KB)       mixin.hpp (21KB)
    ├── snapshot.hpp (18KB)  organizer.hpp (18KB)    handle.hpp (13KB)
    ├── runtime_view.hpp     helper.hpp             ranges.hpp
    └── entity.hpp  fwd.hpp  component.hpp  io.hpp
```

### 核心类型

| 类型 | 作用 |
|---|---|
| `entt::registry` | 世界容器，`on_construct<T>` / `on_update<T>` 信号挂载点 |
| `entt::basic_sparse_set` | 实体集合基类，`sparse`/`packed`/`info`/`mode`/`head` 五元组 |
| `entt::basic_storage<Type>` | 在 sparse_set 上并行挂 `payload` 页数组 |
| `entt::basic_view<...>` | 只读遍历 |
| `entt::basic_multi_view<...>` | 可写遍历 |
| `entt::basic_group<...>` | owning group，拥有池并强制对齐 |
| `entt::handle` | 悬空安全句柄 |
| `entt::locator` | 服务定位器（`entt/service/`） |

### 机制剖析

**稀疏集双数组**：`sparse`（实体 ID → packed 下标）与 `packed`（连续实体数组）。`basic_storage` 的组件数据存在与 `packed` 平行的 `payload` 中，按 `element_at(pos) = payload[pos / page_size][pos % page_size]` 分页寻址。稀疏数组分页（默认 `ENTT_SPARSE_PAGE = 4096`），避免为稀疏的高位 ID 分配巨型连续内存。

**三种删除策略**（`deletion_policy` 枚举，决定 trade-off）：

| 策略 | `size()` 变化 | 迭代顺序 | 墓碑 | 场景 |
|---|---|---|---|---|
| `swap_and_pop` | 减小 | **不稳定** | 无 | 默认，删除最快 |
| `in_place` | 不变 | 稳定 | 有 | 需 `compact()` |
| `swap_only` | 不变 | 部分稳定 | 校验式 | **group / observer 必需**（version bump） |

**swap-and-pop 的代价**：删除 E 时把 packed 末尾元素换到 E 的位置再 pop，`size()` 立即减小但**迭代顺序被打乱**——官方文档明确警告"Do not make assumption on the order in any case"。需要渲染排序等稳定顺序时得调 `sort()` / `sort_n()` / `respect(other)`。

**group 引入的间接层代价**（skypjack 本人解释）：多 sparse set 联合遍历的朴素做法是"取最短的 packed 数组，逐个探测其他池是否有该实体"——复杂度 O(最短池长度)，且两个池顺序未必一致。group 的做法是**owning group 持有池的所有权，把匹配实体的数据在每个池的头部紧密对齐且同序**，group 迭代退化为并行数组的等长 walk（"perfect SoA"）。

代价有三条：
- **打包维护发生在组件增删时，不是迭代时**——收益被前移到写入侧，读侧零分支；
- **一个数组只能有一种排序**，所以一个组件只能属于一个 group（"One component, one group"）；
- 嵌套 group 只在"字面包含"关系时成立，`<A,B>` 与 `<A,B,C>` 合法，`<A,B>` 与 `<A,C>` 被拒。

**迭代器失效**：`swap_and_pop` 下任何增删都会使全部迭代器失效（因为涉及 swap）；`swap_only` 不改 `size()`，仅 bump version，迭代器存活但需重新校验。

---

## 3. flecs（C，MIT）

### 目录结构（GitHub tree 精确返回 `src/`）

```
src/
├── addons/  datastructures/  query/  storage/
├── bootstrap.c   world.c/.h        stage.c/.h      entity.c/.h
├── entity_name.c/.h   observable.c/.h   observer.c
├── commands.c/.h    each.c   iter.c/.h    search.c
├── id.c    on_delete.c    os_api.c    poly.c/.h    ref.c
├── component_actions.c/.h    type_info.c
└── private_api.h    ignored_warnings.h
```

### 核心类型

| 类型 | 作用 |
|---|---|
| `ecs_world_t` | 顶层容器 |
| `ecs_entity_t` | **64 位 ID**，高 32 位含 generation |
| `ecs_table_t` | archetype 存储单元 |
| `ecs_record_t` | 实体 ID → (table, row) 映射；row 还编码 `EcsEntityIsTraversable` 等 flag |
| `ecs_component_record_t` | 组件 ID → 所有含它的 table 的索引 |
| `ecs_iter_t` | 迭代器（`each`/`id` 双模式） |
| `ecs_query_t` / `ecs_filter_t` | 查询与过滤器 |
| `ecs_system_t` / `ecs_pipeline_t` | 系统与流水线 |
| `ecs_observable_t` | observer 注册表 |

### 机制剖析

**`ecs_table_t` 字段**（源码结构）：`id`、`flags`、`column_count`（**不含 tag 的列数**）、`version`、`bloom_filter`（查询快速匹配）、`trait_flags`、`keep`（保活引用计数）、`childof_index`、`type`（排序后的 component ID 数组）、`data`、`node`、`component_map`、`dirty_state`、`column_map`。

**full type vs storage table 的关键区分**：tag（零尺寸组件，如 `Npc`、`IsEnemy`）**不占列**，只存在于 `type` 中。因此 `column_map` 出现**负值**表示"该 type 下标无对应列"。`column_map` 的编码是 `0..count(T)` 段为 type index → column，`count(T)..count(C)` 段为 column → type index。极端情况下"全 tag 表"移动退化为**常数时间操作**——这对关系密集的场景非常关键。

**table graph**（`src/storage/table_graph.c`）：每个 archetype 是图节点，边代表"增删某组件"。首次遇到无边的转移做一次昂贵查找，之后建边，越用越快。边上可编码不变式：`With` trait（Power 总伴随 Responsibility）让一次 archetype 移动带出多个组件；Exclusive 关系沿边移动是**原子操作**（应用永远观察不到实体暂时有两个或零个父节点）。

**存储演进**：flecs v4.1.6+ 默认 archetype，同时支持 `Sparse` 与 `EcsIdDontFragment` 标记——`flecs_table_search` 对非分片组件走独立稀疏集。这是向 Bevy 混合模型靠拢（Bevy 作者在博客中确认 flecs 作者 Sander 是该想法的原始提出者）。

**关系（Relationships）是核心卖点**：`(Relation, Target)` 对，如 `(ChildOf, parent)`、`(IsA, Base)`。存储层看关系就是"大号 component id"；寻址靠 `flecs_table_search` 沿 `IsA` 边做组件继承搜索。

---

## 4. Bevy ECS（Rust，MIT / Apache-2.0）

### 关键类型与机制（据官方 README 与示例）

| 类型 | 作用 |
|---|---|
| `World` / `EntityWorldMut` | 世界；`EntityWorldMut` 是 `World` 的独占借用视图 |
| `Schedule` / `Schedules` | 调度器，内置并行 executor |
| `System` | 任何参数实现 `SystemParam` 的函数 |
| `SystemParam` | `Query` / `Res` / `ResMut` / `Local` / `Commands` / `MessageReader` |
| `Query` / `QueryState` / `QueryFilter` | 声明式查询 |
| `Bundle` | 组件组合（如 `PlayerBundle`），派生宏 |
| `Tables` / `Table` / `TableId` | archetype 存储（默认） |
| `SparseSets` / `SparseSet` | 稀疏存储，逐组件 `#[component(storage = "SparseSet")]` |
| `ComponentId` / `ComponentTicks` / `Tick` | 变更检测 |
| `EntityHashMap` / `EntityHashSet` | 以 `Entity` 为键的哈希容器 |

### 机制剖析

**混合存储是最大创新**。Bevy 官方声明自己是"the first hybrid Archetypal / Sparse Set ECS"：此前选型是二选一——稀疏集增删快但迭代慢，archetype 迭代快但增删慢。Bevy 让开发者**逐组件**选存储，查询时自动挑选最优迭代策略（`Table` 走表内数组顺序遍历，`SparseSet` 走 packed 数组）。

**变更检测（Change Detection）**：`ComponentTicks` 为每个组件记录 `added` 与 `changed` tick。`Changed<T>` / `Added<T>` 过滤器与 archetype 过滤器**不同**——查询仍会遍历所有匹配其余签名的实体，只是跳过了逐实体的工作量，省的是 per-entity 开销而非迭代本身。资源侧对应 `Res::is_changed()`。

**无锁并行**：Bevy **不给 storage 加锁**。System 运行时对拉取到的数据有完整、无检查但安全的访问权限，默认全部并行。调度器从每个 System 的 `SystemParam` 声明推出读写模式，构建冲突图，自动并行无冲突系统；需要顺序时用 `.before()` / `.after()` / `.chain()` 或 system set。

**延迟结构性变更**：`Commands` 把 spawn/despawn/增删组件排队到同步点应用，避免结构编辑与迭代竞争。

**事件演进（版本敏感，值得注意）**：
- **Bevy 0.17 起**：缓冲事件改名为 `MessageWriter` / `MessageReader`（`#[derive(Message)]`），旧的 `EventWriter`/`EventReader` 在 0.18.1 已从 crate 移除。
- **Observer** 的 handler 参数是 `On<T>`，不再是旧的 `Trigger`。Observer 是 push-based 立即触发、可链式触发；Message 是 pull-based 在调度固定点读取，每个 reader 有独立 `MessageCursor`，保证每条消息只被读一次。

**确定性物理**：官方建议把物理放 `FixedUpdate` 调度（定步长）而非 `Update`（每帧一次），这是做确定性/回滚的前提之一。

---

## 5. Unity DOTS/Entities（商业）

Unity 未开源 core，架构信息来自公开文档与 `com.unity.entities` 包。定位是 **Chunk-based archetype**：实体按 archetype 分组打包进 chunk，每 chunk 存同一类型的组件数组（实质是 archetype/table 的分块变体，chunk 是分配与网络传输单位）。Job System + 依赖图提供并行，`IJobEntity` 提供并行 for-each 语义。变更检测有 `IEnableableComponent` 与版本号机制，但相比 Bevy 的"全局自动"是**较弱的**——Bevy 官方博客明确点名 "Compare Unity DOTs change detection to Bevy ECS change detection to get a feel for the massive gulf we have crossed here"。由于无法抓取源码，本节机制细节不做进一步断言（推测部分已标注）。

---

## 6. Box2D v3.2（C17，MIT）

### 目录结构（`src/CMakeLists.txt` 权威清单，扁平无子目录）

```
src/  （共 47 个 .c/.h 对）
├── 基础：core.c/h  math_functions.c  simd.c/h  types.c  container.h
│        arena_allocator.c/h  bitset.c/h  id_pool.c/h  timer.c  platform.h
├── 碰撞：aabb.c/h  geometry.c  hull.c  manifold.c  distance.c
│        dynamic_tree.c/h  broad_phase.c/h  shape.c/h
├── 约束图/岛：constraint_graph.c/h  island.c/h  table.c/h  solver_set.c/h
├── 求解：solver.c/h  contact_solver.c/h
│        contact_solver_w4.c  contact_solver_w8.c  contact_solver_wide.inl
│        joint.c/h  distance_joint.c  revolute_joint.c  prismatic_joint.c
│        weld_joint.c  wheel_joint.c  motor_joint.c  pogo_joint.c  mover_joint.c
├── 刚体：body.c/h  mover.c  sensor.c/h
├── 其它：physics_world.c/h  scheduler.c/h  parallel_for.c/h
│        snapshot.c/h  recording.c/h  replay.c/h
include/box2d/
└── box2d.h base.h collision.h config.h constants.h id.h
    math_functions.h  math_types.h  types.h
```

### 核心类型

| 类型 | 作用 |
|---|---|
| `b2WorldId` / `b2BodyId` / `b2ShapeId` / `b2JointId` | 整数值 ID（不再是对象指针） |
| `b2SolverSet` | 求解器数据集（关键创新，见下） |
| `b2ContactConstraint` / `b2ContactConstraintPoint` | 接触约束 |
| `b2Softness` | soft constraint 参数 |
| `b2Manifold` / `b2WorldManifold` | 接触流形 |
| `b2DynamicTree` | 宽相 AABB 树 |
| `b2IslandSim` | 仿真岛 |

### 机制剖析

**`b2SolverSet`——内存局部性作为一等目标**（源码注释直接引用了 GDC 演讲）：

```c
enum b2SolverSetType {
  b2_staticSet    = 0,   // 静态刚体与静态间关节
  b2_disabledSet  = 1,   // 被禁用刚体及其关节
  b2_awakeSet      = 2,   // 清醒刚体 + 非接触关节；接触与关节在 constraint graph
  b2_firstSleepingSet = 3 // 之后每个 set 对应一个睡眠岛
};
struct b2SolverSet {
  b2Array(b2BodySim)   bodySims;
  b2Array(b2BodyState) bodyStates;   // 只在 awake set 存在
  b2Array(b2JointSim)  jointSims;
  b2Array(b2ContactSim) contactSims;
  b2Array(b2IslandSim) islandSims;   // 睡眠 set 通常只有 1 个岛
  int setIndex;
};
```

**每个睡眠岛一个独立 set**，唤醒单个岛因此非常廉价——这是 Box2D 最被低估的设计。岛住在 solver set 里而非散落全局，正是为了限制"需要检查能否睡着的岛"的数量。

**接触约束结构**（`b2ContactConstraint`）：

```c
typedef struct b2ContactConstraint {
  int indexA, indexB;          // base-1，0 表示 null
  b2ContactConstraintPoint points[2];
  b2Vec2 normal;
  float invMassA, invMassB, invIA, invIB;
  float friction, restitution, tangentSpeed;
  float rollingResistance, rollingMass, rollingImpulse;
  b2Softness softness;
  int pointCount;
} b2ContactConstraint;
```

**SIMD 分层求解**：同一组求解阶段有三种实现——`_Overflow`（不在约束图着色内的溢出接触）、`_Wide`（泛型）、`_WideW4`（SSE2 4-wide）、`_WideW8`（AVX2 8-wide）。每个变体都实现完整六阶段：`b2PrepareContacts_*` → `b2WarmStartContacts_*` → `b2PushContacts_*` → `b2SolveContacts_*` → `b2ApplyRestitution_*` → `b2StoreImpulses_*`。`b2GetWideContactConstraintByteCount()` 用于把 SIMD intrinsics 藏在 .c 里以缩短编译期。MSVC 下 `contact_solver_w8.c` 单独加 `/arch:AVX2`。

**TGS Soft 取代迭代次数**：

```c
void b2World_Step(b2WorldId worldId, float timeStep, int32_t subStepCount);
```

不再指定 velocity/position iterations，改为**子步数**（建议从 4 起调）。关键语义变化：
- 接触点**每个完整时间步只计算一次**，接触事件因此按整步粒度；
- **外部施加的冲量/速度调整在第一个子步之后不再保留**——"每帧设速度"的做法会得到不可预期结果，应改为施加力/力矩（会被摊到所有子步），或用 `b2_kinematicBody` 获得完全控制；
- **v3.0 不再用碰撞子步做连续碰撞**，所有接触数据在时间步末有效（注意接触点仍对应时间步初的位置）。

**回调几乎全删，仅存 PreSolve**。理由是多线程下回调有问题：用户代码竞态、非确定性、性能不可预测。改为时间步后拉取事件数组，且**多线程下事件顺序仍确定**：

```c
typedef struct b2ContactEvents {
  b2ContactBeginTouchEvent* beginEvents;
  b2ContactEndTouchEvent*   endEvents;
  b2ContactHitEvent*        hitEvents;
  int beginCount, endCount, hitCount;
} b2ContactEvents;
```

Sensor 事件完全拆分为独立的 `b2SensorEvents`，不再与接触事件混在一起。可用 `b2ShapeDef.enableContactEvents` / `enablePreSolveEvents` 逐 shape 省内存。

**三级确定性（默认开启，无开关）**：
1. **算法确定性**——库内不用随机数（sample 用但不属于库）。
2. **多线程确定性**——v2.4 靠 free list 天然顺序，v3 因多线程变难。做法是**位数组**：每 worker 线程填自己的 bit array，主线程用**按位 OR** 合并，得到确定序的 set bit。作者指出"存在 atomic 或 mutex 就意味着确定性风险"——atomic 递增只防数据竞争，不防顺序随机化。
3. **跨平台确定性**——避开三个陷阱：**fast-math**（实测无收益且降低精度）、**FMA**（CMakeLists 明确对所有 GNU/Clang 加 `-ffp-contract=off`，clang-cl 走 `/clang:-ffp-contract=off`）、**三角函数**（`atan2f` 跨平台确实给不同结果，因此自实现了近似的 `sinf`/`cosf`/`atan2f`；`sqrtf` 在 x64 落到 `sqrtss` 指令，天然确定）。
4. **回滚确定性：尚不提供**——引擎内部状态太多且无回滚机制。作者明说这是待办。

CI 用 *Falling Hinges* 场景守护：跨 MSVC/Clang/GCC、x64/ARM，覆盖快速运动碰撞、睡眠、关节限位、初始旋转；断言两个数——**全体入睡所需步数**与**入睡后所有 transform 的哈希**。

**确定性层级的准确划分（回答"同平台跨运行 vs 跨平台"）**——这是两个独立的轴，不是一维：

| 轴 | 含义 | Box2D 是否保证 | 破坏来源 |
|---|---|---|---|
| **A. 算法确定性** | 同 binary 同输入 → 同输出 | ✅ 保证，但**需重建 world** | 对象池 free list：销毁再创建后 body 顺序可能变 |
| **B. 多线程确定性** | 跨线程数仍一致 | ✅ 保证 | 线程相对速度、OS 调度、atomic 顺序 |
| **C. 跨平台确定性** | 跨 CPU/编译器一致 | ✅ 保证 | fast-math、FMA、三角函数 |
| **D. 回滚确定性** | 网络对战重放一致 | ❌ **不支持** | 引擎内部状态过多，无回滚机制 |

**关键澄清（A 轴的隐藏条件）**：作者原文明确指出算法确定性"usually requires the world to be recreated"，因为 Box2D 用 free list 维护对象池，销毁并重建 body 后**第二批 body 的顺序可能与第一批不同**。所以 Box2D 的A 级保证是"**同一 world 对象连续 step** 一致"，不是"重建 world 后仍一致"。

**对渲染侧的推论**：A/B/C 三级 Box2D 全保，因此"同平台跨运行"这一场景是**安全的**——渲染侧 TAA/FSR2 历史缓冲递归累积的时域放大担忧，在 Box2D 上不成立。真正未覆盖的是 **D 级（回滚）**：Box2D 无法用于 lockstep 网络同步的确定性重放。这才是物理侧选型时的硬约束。

Rapier 的 `enhanced-determinism` 同理覆盖 A/B/C，但**与 `simd8` 互斥**——SIMD 通道宽度改变浮点运算顺序，属于 C 级让步。

另有 `BOX2D_DOUBLE_PRECISION` 选项（大世界），注意 CMakeLists 特意标注该宏必须 `PUBLIC`，否则消费者与库的 ABI 不一致。

---

## 7. Rapier（Rust，Apache-2.0）

### 目录结构（GitHub API 精确返回）

```
src/
├── lib.rs
├── control/    counters/    data/    geometry/    utils/
├── dynamics/
│   ├── ccd/    island_manager/    joint/    soft_body/    solver/
│   ├── coefficient_combine_rule.rs   integration_parameters.rs (21KB)
│   ├── rigid_body.rs (87KB)   rigid_body_components.rs (62KB)
│   ├── rigid_body_set.rs (17KB)    rigid_body_handle.rs
└── pipeline/
    ├── physics_pipeline/    debug_render_pipeline/
    ├── collision_pipeline.rs    query_pipeline.rs (37KB)
    ├── physics_world.rs (39KB)  user_changes.rs
    ├── event_handler.rs     physics_hooks.rs
```

> `parry` 是独立仓库 `dimforge/parry`，不在本树内。

### `solver/` 精确构成

```
solver/
├── categorization.rs              solver_body.rs
├── interaction_groups.rs          manifold_store.rs
├── solver_contact_graph.rs        velocity_solver.rs (2.6KB —— 薄封装)
├── staged_island_solver/          ← 分阶段 island 求解核心
├── contact_constraint/            joint_constraint/
├── soft_constraint/               soft_fem/
```

### 机制剖析

**集合全是 generational arena**：`RigidBodySet` / `ColliderSet` / `ImpulseJointSet` / `MultibodyJointSet` / `SoftBodySet` 统一为「`u32` index + `u32` generation」的句柄，保证句柄唯一。

**`PhysicsPipeline::step` 需要 13 个参数**（这是 Rapier 架构最直白的证据——world 只是 façade）：

```rust
physics_pipeline.step(
    gravity, &integration_parameters,
    &mut island_manager, &mut broad_phase, &mut narrow_phase,
    &mut rigid_body_set, &mut collider_set,
    &mut impulse_joint_set, &mut multibody_joint_set,
    &mut soft_body_set, &mut ccd_solver,
    &physics_hooks, &event_handler,
);
```

文档明确说手工分步"正是 world 内部做的事"，适用于"结构由不同模块持有，例如 ECS 的 resource"——**这是 ECS 集成的官方推荐姿势**。

**三条 pipeline 各司其职**：`PhysicsPipeline`（全量）、`CollisionPipeline`（**只做碰撞检测，不做动力学**，与 PhysicsPipeline 同时跑是浪费）、`QueryPipeline`（场景查询，**复用宽相的 BVH**，通过 `broad_phase.as_query_pipeline(narrow_phase.query_dispatcher(), &bodies, &colliders, filter)` 借用构建）。

**IslandManager 直接服务于渲染同步**——这是 ECS 集成的关键接口：

```rust
for handle in world.islands.active_bodies() {   // 仅迭代本步真正动过的刚体
    let rb = &world.bodies[handle];
    // 同步到渲染 Transform
}
```

**feature 开关与 SIMD**：SIMD（基于 `wide` crate）**始终开启**，求解器每指令处理 4 条接触 manifold（`simd8` feature 提升到 8）。其他 feature：`parallel`（rayon）、`enhanced-determinism`、`block-solver`（把接触对的两个法向约束耦合成单个 2x2 求解；2D 默认开，3D 默认关因为收益小）、`fem`（软体替代 FEM 求解器，逐体选择）、`unsync-callbacks`（去掉 hooks/event handler 的 `Sync` 要求，副作用是失去线程池 API）、`debug-render`、`profiler`。

**确定性约束**：`enhanced-determinism` 与 `simd8` **互斥**——后者改变 SIMD 通道宽度，从而改变仿真结果。

> ⚠️ **交叉验证提醒**：Rapier 官方文档在陈述 `enhanced-determinism` 时附加了限定条件——**"assuming the rest of your code is also deterministic"**。也就是说这个 feature 只保证 Rapier 库内部确定，**调用方代码里的无序容器（Rust `HashMap` 的默认随机种子 hasher、浮点累加顺序、并行 `rayon` reduce）会独立地破坏它**。`parallel` feature 与 `enhanced-determinism` 同时开启时，`rayon` 的 work-stealing 归约顺序本身就是不确定性来源。**这正是"同平台跨运行也不一致"的典型成因**，且比跨平台差异更常见、更隐蔽。

---

## 8. Jolt Physics（C++17，MIT）

### 目录结构（据 DeepWiki 子系统表）

```
Jolt/Physics/
├── PhysicsSystem.h          Body/           Collision/
├── Constraints/             CollisionShape/  Collision/BroadPhase/
├── PhysicsSettings.h        PhysicsUpdateContext.h  StateRecorder.h
Jolt/Geometry/
Jolt/Core/                  Jolt/Math/
```

### 核心类型

| 类型 | 作用 |
|---|---|
| `BodyID` | **index + sequence**，避免在多线程仿真中传裸指针 |
| `Body` / `BodyManager` / `BodyInterface` | 刚体与访问接口 |
| `BroadPhaseQuadTree` | AABB 粗筛 |
| `NarrowPhaseQuery` | 精确形状查询 |
| `CollisionDispatch` | 形状对分派 |
| `ContactConstraintManager` | 接触约束管理 |
| `IslandBuilder` / `LargeIslandSplitter` | 岛构建与拆分 |
| `PhysicsSettings` / `PhysicsUpdateContext` | 配置与每帧上下文 |
| `JobSystem` / `TempAllocator` | 任务与临时分配 |

### 机制剖析

**`NarrowPhaseQuery` 完整 API**（源码原文）：`Init` → `CastRay`（单命中/多命中 `CastRayCollector`）、`CollidePoint`、`CollideShape`、`CollideShapeWithInternalEdgeRemoval`（用 `InternalEdgeRemovingCollector` 消除"幽灵碰撞"）、`CastShape`、`CollectTransformedShapes`。四层过滤器：`BroadPhaseLayerFilter` / `ObjectLayerFilter` / `BodyFilter` / `ShapeFilter`。

一个值得学的精度技巧：`inBaseOffset` 让所有命中结果相对于该偏移返回。**浮点在原点附近最精确**，远处测试时选一个更接近原点的基准位置（如 `inCenterOfMassTransform.GetTranslation()`）可显著提升精度。

**`PhysicsSystem::Init` 的六个构造参数**：`inMaxBodies`、`inNumBodyMutexes`（2 的幂，0 = 自动）、`inMaxBodyPairs`、`inMaxContactConstraints`、`inBroadPhaseLayerInterface`、`inObjectVsBroadPhaseLayerFilter`、`inObjectLayerPairFilter`。**所有 filter 接口必须在 PhysicsSystem 生命周期内存活**。

**锁策略是粗粒度的**：`Update()` 入口 `mBodyManager.LockAllBodies()`，出口 `UnlockAllBodies()`。仿真期间 job 读写刚体状态**不再加锁**——这简化了 job 实现、防止用户代码中途改刚体、并保证确定性。mutex 组织成哈希数组，映射为 `mutex_index = body_id.GetIndex() & (num_mutexes - 1)`，数组大小通常是 `2 * hardware_concurrency()`。`Update()` 之外可用 `BodyLockRead` / `BodyLockWrite` 做细粒度锁。

**`LargeIslandSplitter`——本次调研最有工程价值的发现之一**：

```cpp
static constexpr uint cNumSplits = sizeof(SplitMask) * 8;
static constexpr uint cNonParallelSplitIdx = cNumSplits - 1;
static constexpr uint cLargeIslandTreshold = 128;   // 源码拼写如此
```

- 一个岛的**约束 + 接触总数超过 128** 才尝试拆分；实现的是 Chen et al. *High-Performance Physical Simulations on Next-Generation Architecture with Many Cores* 的 "PARALLELIZATION METHODOLOGY"（`LargeIslandSplitter` 头注释直接引用）。
- `AssignSplit(body1, body2)` 保证**有接触的两个体被分到同一 split**——拆分不能破坏约束耦合。
- `AssignToNonParallelSplit(body)` 强制个体进不可并行的 split。
- 用 `SplitMask` 位掩码 + `FetchNextBatch` / `MarkBatchProcessed` 的**批次拉取模型**（`EStatus`: `WaitingForBatch` / `BatchRetrieved` / `AllBatchesDone`），而非简单 fork-join。
- `PrepareForSolvePositions()` 标记所有批次重新可取，支撑位置求解阶段再遍历一次拆分的岛。

**Job 数量按负载动态计算**，例如 `num_apply_gravity_jobs = max(1, min(num_active_bodies / 64, max_concurrency - 2))`，并为必须并发（如宽相 prepare）的 job 预留线程。

**接触缓存双缓冲 + 无锁哈希**：读缓存（上帧接触，用于 warm start）与写缓存（本帧生成），`Update()` 末尾通过 `FinalizeContactCacheAndCallContactPointRemovedCallbacks()` 交换。`ManifoldMap`（流形）与 `BodyPairMap`（体对元数据）用无锁哈希表，最小化并行碰撞检测的锁竞争。`PhysicsUpdateContext` 持有 `mWarmStartImpulseRatio` 等每帧状态。

**TempAllocator 是纯栈式 arena**（`Jolt/Core/TempAllocator.h` 源码实证）：基类注释明确写 "This allocator works as a stack: The blocks must always be freed in the reverse order as they are allocated."，且 `Free()` 里 `if (mBase + mTop != inAddress) Trace("Freeing in the wrong order"); std::abort();`——**顺序错误直接 abort，不做静默容错**。另注明"allocations and frees can take place from different threads, but the order is guaranteed through job dependencies, so it is not needed to use any form of locking"——**免锁的合法性来自 job 依赖图而非原子操作**，这点与 Box2D 的位数组思路异曲同工。

三个具体实现（源码类名核实）：

| 类 | 策略 | 特点 |
|---|---|---|
| `TempAllocatorImpl` | 一次性 `malloc` 大块，`mTop` 指针栈 | OOM 时 `Trace` + `std::abort()`；提供 `GetUsage()` / `CanAllocate()` / `OwnsMemory()` / `IsEmpty()` |
| `TempAllocatorMalloc` | 直接 `JPH::Allocate` / `Free` | 注释警告"quite slow when running in the debugger as large memory blocks need to be initialized with 0xcd" |
| `TempAllocatorImplWithMallocFallback` | 内嵌前两者，块耗尽则回落 | `CanAllocate()` 探测 + `OwnsMemory()` 分派 |

> ⚠️ **对齐常量更正**：本报告早前版本称"所有分配按 `JPH_CACHE_LINE_SIZE` 对齐以避免伪共享"——**该说法错误**，来自社区博客。源码实际对齐常量为 **`JPH_RVECTOR_ALIGNMENT`**（`AlignUp(inSize, JPH_RVECTOR_ALIGNMENT)`，且返回地址要求该对齐）。另有一条条件编译分支 `needs_aligned_allocate = JPH_RVECTOR_ALIGNMENT > JPH_DEFAULT_ALLOCATE_ALIGNMENT`，仅当平台默认对齐不足时才走 `AlignedAllocate`。**`JPH_CACHE_LINE_SIZE` 与伪共享在本文件中并未出现**——Jolt 的伪共享防护主要在 `Body`/缓存填充层，不在 TempAllocator。

**`Update()` 返回 `EPhysicsUpdateError` 标志**而非抛异常——报告 `ManifoldCacheFull` 等问题，符合游戏引擎的错误处理风格。

---

## 9. Bullet Physics（C++，zlib）

### 目录结构

```
src/
├── LinearMath/                btVector3.h  btQuaternion.h  btMatrix3x3.h
│                              btTransform.h  btQuadWord.h  btAabbUtil2.h
├── BulletCollision/
│   ├── CollisionDispatch/     btCollisionWorld  btCollisionObject  btSimulationIslandManager
│   │                          btCompoundCollisionAlgorithm
│   ├── CollisionShapes/       btCompoundShape ...
│   └── NarrowPhaseCollision/  btPersistentManifold  btRaycastCallback
├── BulletDynamics/
│   ├── Dynamics/              btDiscreteDynamicsWorld  btDynamicsWorld  btRigidBody
│   └── ConstraintSolver/      btSequentialImpulseConstraintSolver
│                              btConstraintSolver  btSolverBody  btSolverConstraint
│                              btContactSolverInfo
├── Bullet3/
└── btBulletDynamicsCommon.h
```

### 机制剖析

**模块化到可以按需拼装**（这是 Bullet 最大的架构优势）：

- `btCollisionWorld` 基类 → `btDiscreteDynamicsWorld`（刚体）/ `btSoftRigidDynamicsWorld`（软+刚）/ `btDeformableMultiBodyDynamicsWorld`（可变形体）
- `btBroadphaseInterface` → `btDbvtBroadphase`（动态 AABB 树）/ `btAxisSweep3`（扫描剪枝）/ `btSimpleBroadphase`（暴力）
- `btConstraintSolver` → `btSequentialImpulseConstraintSolver`（SI）/ `btNNCGConstraintSolver`（非负共轭梯度）/ `btMultiBodyConstraintSolver`（最小坐标）

于是可以组合出「精确仿真：NNCG + 小步长」「实时应用：SI + 大步长」「大规模：DbvtBroadphase + 并行求解器」。

**`btDiscreteDynamicsWorld` 的调用链**（源码实证）：

```
stepSimulation
  → saveKinematicState          （运动学体保存状态以算下帧速度）
  → predictUnconstrainedMotion
  → createPredictiveContacts
  → performDiscreteCollisionDetection
  → calculateSimulationIslands
  → solveConstraints(btContactSolverInfo&)
       → InplaceSolverIslandCallback（约束数据结构集合）
       → m_islandManager->buildAndProcessIslands      （并查集）
            → InplaceSolverIslandCallback::ProcessIsland
                 → btSequentialImpulseConstraintSolver::solveGroup
```

构造函数默认 `m_gravity(0, -10, 0)`（注意不是 -9.81），且**未提供外部 solver 时会自行 `btAlignedAlloc` 分配 `btSequentialImpulseConstraintSolver` 与 `btSimulationIslandManager`**，并用 `m_ownsConstraintSolver` / `m_ownsIslandManager` 标记析构时是否释放——手工内联分配的经典模式。

**求解器是 PGS（Projected Gauss-Seidel）**，即 Gauss-Seidel 加上下界投影：

```cpp
for each iteration:
    for each constraint:
        λ_old = λ
        λ = solve_single_constraint()
        λ = clamp(λ, λ_min, λ_max)      // 这正是 PGS 与 GS 的差别
        apply_impulse(λ - λ_old)
```

SI 求解器收敛速度 O(n²) 迭代但每次迭代成本低。`btContactSolverInfo` 承载迭代次数、松弛因子（ERP/CFM）、split impulse 等全部可调参数。

**存储范式偏 AoS**：`btCollisionObject` + `btRigidBody` 为对象式，`LinearMath` 提供 `btVector3` / `btQuaternion` 值类型。这是 Bullet 相比 Jolt（`Body*` 紧凑池 + SoA）在缓存局部性上的明显劣势，也是它在现代游戏引擎中常被 Jolt 取代的主因。

---

## C. 横向对比：架构取舍

### C1. 存储范式：四种路线的取舍

| 范式 | 代表 | 增删 | 迭代缓存 | 代表性代价 |
|---|---|---|---|---|
| **SoA 连续数组** | Box2D v3 | 慢 | **最优** | 增删要搬移 |
| **Archetype（table）** | flecs、Bevy 默认 | 慢（要迁移表） | **优**（整列连续） | 加组件 = 迁移全部组件 |
| **稀疏集（sparse set）** | EnTT | **快**（swap-and-pop O(1)） | 好（packed 连续） | 迭代顺序不稳定；sparse 数组内存 |
| **AoS 对象** | Bullet | 快 | 差 | 现代引擎已不选 |

**关键洞见：这是连续谱而非二元对立。**
- flecs 提供 `Sparse` / `DontFragment` 标记；
- Bevy **逐组件**选存储，查询时自动选最优策略；
- EnTT 用 **owning group** 把匹配实体在各池头部对齐，模拟出"完美 SoA"。

EnTT 作者 skypjack 那句总结值得引用：**"50% of time you loop components, 50% you access them random, 50% is other access pattern"**——所以不该用单一访问模式限制框架。

**EnTT group 的隐含代价再强调**：group 的对齐是在**组件增删时维护**而非迭代时，读侧零分支零跳转；但代价是**一个组件只能属于一个 group**（数组只有一种排序），嵌套 group 仅在字面包含时成立。

### C2. 迭代与并行：ECS 的并行 vs 物理的串行矛盾

这是**根本性的架构冲突**，四个物理引擎给出了四种解法：

**（a）Box2D v3——并行窄相 + 串行求解 + 位数组恢复确定性**
宽相/窄相并行，但求解仍按确定性顺序。确定性恢复靠**位数组按位 OR 合并**：每 worker 填自己的 bit array，主线程合并得确定序。代价是要刻意避开 `atomic_fetch_add` 的顺序随机性——atomic 只防数据竞争，不防顺序随机化。

**（b）Jolt——锁粗粒度化 + 岛内并行 + 大岛拆分**
最完整的方案：`Update()` 期间全体刚体加锁（job 内免锁）→ 岛构建用**无锁 union-find**（`IslandBuilder::LinkBodies` 原子 CAS + 路径压缩）→ 速度/位置求解**按岛并行** → 岛按约束数**降序排列**（大的先跑，减少尾部）→ 超 128 约束的大岛用 `LargeIslandSplitter` 按 Chen et al. 方法拆分成并行 split。接触缓存用**双缓冲 + 无锁哈希表**。

**（c）Rapier——rayon 并行 + `active_bodies()` 暴露给渲染**
`parallel` feature 用 rayon 并行物理流水线。ECS 集成的关键是 `IslandManager::active_bodies()`——只暴露本步真正动过的刚体，让渲染同步不必全量遍历。

**（d）Bullet——岛管理器（并查集）+ 可选并行求解器**
`btSimulationIslandManager` 用并查集建连通集，`InplaceSolverIslandCallback` 逐岛调 `solveGroup`。并行是可选的而非默认。

**结论**：ECS 鼓励「组件级并行」，物理要求「接触求解全局串行」。所有成熟引擎的收敛解法都是 **岛屿（island）**：把互相耦合的刚体绑成一个可独立求解的连通分量，**岛内串行、岛间并行**。岛既是并行的基本单位，也是睡眠的基本单位、也是「本步哪些东西变了」的天然追踪粒度。ECS 若要接物理，应当把物理引擎当作**一个独占资源的黑盒 system**，而试图把刚体拆成可并行求解的组件——这与求解器的本质冲突。

### C3. 确定性：两条轴与四个层次

Box2D 的三级划分是业界最清晰的分级（默认全部开启，无开关）：

| 层次 | 含义 | Box2D 的做法 |
|---|---|---|
| **算法确定性** | 无随机数 | 库内不用随机数 |
| **多线程确定性** | 同机多次运行一致 | **位数组 + 按位 OR 合并**，避开 atomic 顺序随机化 |
| **跨平台确定性** | 跨 CPU/编译器一致 | 关 FMA（`-ffp-contract=off`）、禁 fast-math、自实现 `atan2f` |
| **回滚确定性** | 网络对战重放一致 | **不支持**（内部状态过多，无回滚机制） |

**Rapier** 的等价机制：`enhanced-determinism` feature（要求严格 IEEE 754-2008 实现），但**与 `simd8` 互斥**——因为 SIMD 通道宽度改变了浮点运算顺序。这是个诚实的工程取舍：**SIMD 与严格确定性不可兼得**。

**Jolt** 提供 deterministic simulation，同样面临 SIMD 与确定性的张力。

**Bevy 的 ECS 侧确定性**：`FixedUpdate` 定步长调度是物理确定性的前提。若物理跑在每帧一次的 `Update` 里，帧率抖动会直接注入物理。

**给自研引擎的建议**：跨平台确定性的三个陷阱值得单独立测试用例——**fast-math（禁用）、FMA（`contract=off`）、三角函数（自实现近似版）**。前两个只需编译开关，第三个必须自实现。Box2D 实测 `sqrtf` 是确定的（x64 落到 `sqrtss`），但 `atan2f` 不是。

### C4. 求解器类型

| 引擎 | 求解器 | 迭代参数 | 要点 |
|---|---|---|---|
| Box2D v3 | **TGS Soft** | 子步数（建议 4） | 子步 + soft constraint；接触每步只算一次；外部冲量首个子步后失效 |
| Rapier | 分阶段 island solver + soft constraint | `IntegrationParameters` 内 solver 迭代数 | SIMD 4/8-wide（`wide` crate）；`block-solver` 把 2 法向耦合为 2x2 |
| Jolt | Gauss-Seidel 冲量 | `PhysicsSettings` 迭代数/容差 | 大岛按 128 阈值拆分并行 |
| Bullet | **PGS / Sequential Impulse** | `btContactSolverInfo` 全套 | PGS = GS + 上下界投影；可选 NNCG / MultiBody |
| （XPBD） | 位置型 | 子步 | Box2D 的 soft constraint 思路接近 XPBD 的柔化 |

**TGS Soft 的语义陷阱值得单独警示**：因为子步内保留冲量，**外部施加的速度修改在第一个子步后就失效**。每帧 `SetLinearVelocity` 的旧代码在 v3 会得到不可预期结果。正确做法是施加力/力矩（自动摊到所有子步），或改用 `b2_kinematicBody` 获取完全控制。这是升级 v2→v3 最容易踩的坑。

### C5. 触发 / 传感器 / 连续碰撞

- **Box2D v3** 把 sensor 事件**完全拆分**为独立的 `b2SensorEvents`（v2.4 中与接触事件混在一起）。回调几乎全删仅留 PreSolve，且 PreSolve 需在 `b2ShapeDef.enablePreSolveEvents` 显式开启（默认 false）。
- **Jolt** 通过 `CollideShapeWithInternalEdgeRemoval` 的 `InternalEdgeRemovingCollector` 消除**幽灵碰撞（ghost collision）**——复合形状内部相邻子形状本不该碰撞，但三角网/复合的 SAT 会报出来。这是复合碰撞体最经典的 bug 来源。
- **Rapier** 用 `PhysicsHooks` trait（过滤任意 collider 对、修改进入求解器的接触）+ `EventHandler` trait（接触开始/结束、sensor 交叠）双 trait-object 机制。`unsync-callbacks` feature 可去掉 `Sync` 要求。
- **连续碰撞**：Box2D v3 **不再用碰撞子步做 CCD**，改为时间步末统一暴露接触数据（这正是它能删掉大部分回调的前提）。Rapier 有独立的 `CCDSolver` + `IntegrationParameters` 里的 CCD 子步数。Jolt 的 CCD 走 `Body` 的运动质量（motion properties）与 TOI 事件。

### C6. 与渲染的耦合：PhysicsBody → Transform

四条路径，按耦合度递增：

**（1）纯拉取（推荐，Rapier 官方模式）**
```
physics.step()
  → world.islands.active_bodies()   // 只取本步动过的
    → for h in active: world.bodies[h].translation() / rotation()
      → 写入渲染 Transform 组件
```
关键在 `active_bodies()`——若全量遍历 10 万刚体去同步 Transform，每帧的带宽就吃掉了物理本身省下的时间。

**（2）ECS 双组件 + 后处理 system（Bevy 惯用法）**
```
FixedUpdate:
  physics_system   // 物理 world 是独占 Resource，step()
  sync_system      // Query<&RigidBody, (Without<LocalToWorld>, Changed<RigidBody>)>
                   //   → 写 LocalToWorld，渲染只读 LocalToWorld
```
`Changed<RigidBody>` 过滤器跳过未变实体。注意 `Changed` 只省 per-entity 工作量，**查询仍遍历匹配签名的所有实体**——所以物理体最好用 SparseSet 存储以缩小签名规模。

**（3）变换回滚 / 预测（Bevy `TransformState` 思路）**
交互式组件需要把物理拉回 ECS 的 `Transform`：ECS 侧记录 `previous_transform` 与权威 `transform`，物理 system 跑完后从权威值重算派生变换。适合需要"网络预测 + 回滚"的场景，但代价是必须处理物理引擎的内部状态回滚——**Box2D 明确不支持回滚确定性**，这是选型硬约束。

**（4）MotionVector（现代渲染必需）**
渲染要算 TAA/运动模糊需 `(currentPos - previousPos)`。若走上面的 ECS 组件路径，需在 ECS 里**同时维护 `prev_transform`**，否则物理步进式更新会丢失帧间速度信息。Jolt/Bullet 的 motion state 机制（Bullet 的 `saveKinematicState` / `m_latencyMotionStateInterpolation`）原生提供插值缓冲。

**实现建议**：把物理 world 建模为**一个独占 Resource 的黑盒 system**，用双组件（`PhysicsBody` 只读 / `LocalToWorld` 只写）隔开脏数据边界，让渲染只依赖 `LocalToWorld`。这样物理库可以被整体替换而不动渲染代码。

---

## D. 参考链接

**ECS 框架**
- EnTT 源码目录 — https://github.com/skypjack/EnTT/tree/master/src/entt
- EnTT `entity/` 文件清单（API） — https://api.github.com/repos/skypjack/EnTT/contents/src/entt/entity
- skypjack: ECS back and forth Part 2 — Sparse sets and grouping — https://skypjack.github.io/2019-03-21-ecs-baf-part-2-insights/
- flecs 源码目录 — https://github.com/SanderMertens/flecs/tree/master/src
- flecs Tables and Archetypes（`ecs_table_t` 字段） — https://deepwiki.com/SanderMertens/flecs/2.3-tables-and-archetypes
- Sander Mertens: Building an ECS — Storage in Pictures — https://ajmmertens.medium.com/building-an-ecs-storage-in-pictures-642b8bfd6e04
- bevy_ecs crate — https://github.com/bevyengine/bevy/tree/main/crates/bevy_ecs
- Bevy's First Birthday（混合存储 / lock-free） — https://bevyengine.org/news/bevys-first-birthday

**物理引擎**
- Box2D v3.2.0 `src/CMakeLists.txt`（权威文件清单） — https://raw.githubusercontent.com/erincatto/box2d/main/src/CMakeLists.txt
- Box2D `solver_set.h`（`b2SolverSet`） — https://raw.githubusercontent.com/erincatto/box2d/main/src/solver_set.h
- Box2D `contact_solver.h`（约束与 SIMD 变体） — https://raw.githubusercontent.com/erincatto/box2d/main/src/contact_solver.h
- Box2D v2.4 → v3.0 迁移指南 — https://box2d.org/documentation/md_migration.html
- Box2D：Determinism（三级确定性） — https://box2d.org/posts/2024/08/determinism/
- Jolt `NarrowPhaseQuery.h` — https://raw.githubusercontent.com/jrouwe/JoltPhysics/master/Jolt/Physics/Collision/NarrowPhaseQuery.h
- Jolt `LargeIslandSplitter` 类文档 — https://jrouwe.github.io/JoltPhysicsDocs/5.5.0/class_large_island_splitter.html
- Jolt Core Architecture（`PhysicsSystem::Init` 参数） — https://deepwiki.com/jrouwe/JoltPhysics/2-core-architecture
- Jolt Simulation Pipeline（job 图 / TempAllocator） — https://deepwiki.com/happyhappygame/JoltPhysics123/4-simulation-pipeline
- Rapier `dynamics/` 文件清单（API） — https://api.github.com/repos/dimforge/rapier/contents/src/dynamics
- Rapier `solver/` 文件清单（API） — https://api.github.com/repos/dimforge/rapier/contents/src/dynamics/solver
- Rapier Simulation structures — https://rapier.rs/docs/user_guides/rust/simulation_structures
- Rapier Getting started（feature 开关） — https://rapier.rs/docs/user_guides/rust/getting_started
- Bullet Physics System Architecture — https://deepwiki.com/bulletphysics/bullet3/2-physics-system-architecture

**§E 协同补充与更正所依据的额外 URL（本人在收到 arch-opensrc 数据后独立抓取复核）**
- Lumix `src/engine/lumix.h`（`EntityRef`/`EntityPtr`/`ComponentType` 定义、`MAX_TYPES_COUNT = 64`） — https://raw.githubusercontent.com/nem0/LumixEngine/master/src/engine/lumix.h
- Lumix `src/engine/world.h`（`EntityData` union 与 `bool valid`、`PartitionHandle`/`ArchetypeHandle = u16`、SoA 布局注释） — https://raw.githubusercontent.com/nem0/LumixEngine/master/src/engine/world.h
- Jolt `Jolt/Core/TempAllocator.h`（对齐常量更正依据） — https://raw.githubusercontent.com/jrouwe/JoltPhysics/master/Jolt/Core/TempAllocator.h

---

## E. 补充：其他 ECS 实现的两个维度

> **证据等级说明**：本节全部条目来自 arch-opensrc 的开源引擎源码调研（其报告含 23 条核验 URL），属**「clone 源码实测」**等级，与本报告其余章节（多为 GitHub API 抓取 + 官方文档）**不同源、特此标注**。§E 中每条结论均标「（arch-opensrc 实测）」。
>
> 与 arch-opensrc 的分工：他负责 clone 源码核验，我负责交叉验证与整合。以下 §E.1 的对比框架、§E.2 末尾的物理引擎挂载建议，以及**我对 Lumix 16 位句柄的 ABA 推论**均为我的分析，非arch-opensrc 原始结论——证据等级与他的实测条目区分开。

### E1. 系统/组件执行顺序：四种机制

我原先只覆盖了 Bevy 的 `Schedule`（数据访问冲突图 + 自动并行）。O3DE 与 Lumix 提供了两种更轻量的替代路线：

| 引擎 | 顺序确定方式 | 缺失依赖时| 特点 | 来源 |
|---|---|---|---|---|
| **Bevy** | `SystemParam` 读写集 → 冲突图 → 自动并行 | N/A | 并行度最高，但顺序隐式 | 本报告（官方 README） |
| **O3DE** | 声明式服务依赖 → `DependencySort` 拓扑排序 | 缺 required → 报错不激活；缺 dependent → 仍激活但顺序照排 | 顺序显式可查 | （arch-opensrc 实测） |
| **Lumix** | `IModule` 三段式 `updateParallel` → `update` → `lateUpdate` | N/A | 分段固定，无声明依赖 | （arch-opensrc 实测） |
| **Stride** | 无内建调度，变换是普通组件 | N/A | 靠用户组织 | （arch-opensrc 实测） |

**O3DE 的服务依赖模型**（`AZ::ComponentDescriptor`，与 `AZ::Component` 同文件 `AzCore/Component/Component.h`，**arch-opensrc 实测**）值得细看。四组服务数组构成有向图：

```cpp
GetProvidedServices() / GetRequiredServices()      // 依赖 / 被依赖
GetDependentServices() / GetIncompatibleServices()  // 顺序敏感 / 互斥
```

`AZ::Entity::DependencySort(ComponentArrayType&)` 做**拓扑排序**决定激活顺序，**逆序即反激活顺序**。这个设计的价值在于：依赖关系是**数据**（可被工具检视、可序列化）而非控制流，因此可以静态分析循环依赖并在启动时报错，而非运行时随机失败。

**组件间通信被强制走 EBus**（`AzCore/EBus/EBus.h`，arch-opensrc 实测）。两个预定义总线值得注意：
- `AZ::ComponentBus` — `AddressPolicy = EBusAddressPolicy::ById`，`BusIdType = EntityId`
- `AZ::EntityComponentBus` — `BusIdType = EntityComponentIdPair`，可寻址到**具体组件实例**而非仅实体

`CreateDescriptor()` 走 `ComponentDescriptorBus` 做 UUID 去重——**同名不同 UUID 会报 `AZ_Error`**（arch-opensrc 实测），这是"同名组件静默冲突"的一道防线。

**取舍**：Bevy 式自动并行收益大但顺序是隐式的（调试时需看冲突图）；O3DE 式声明依赖顺序显式、可静态检查，但用户要手写依赖声明，且拓扑排序本身有成本。**若系统间冲突少，显式依赖的调试友好性可能更值；若系统间冲突多，自动并行的收益更值。**

### E2. 流式加载：Partition 与稀疏存储

我前三项调研的引擎都不内置"世界分区/流式加载"概念。这在开放世界是个实际缺口。（本节 Lumix / O3DE 条目均为 **arch-opensrc 实测**）

**Lumix 的 Partition 机制**（`src/engine/world.h`）：
- `createPartition` / `destroyPartition` / `setActivePartition`，新实体一律建在 active partition
- **加载与卸载是加法式的**——启用一个 partition 即"加进来"，停用即"移出去"，而非反复重建

**Lumix 的存储布局有一个值得注意的细节（本人独立源码核验）**：

**`EntityData` 与 `Transform` 分成两个独立数组**（SoA），源码注释写明 "not in single array (==EntityData does not contain Transform) because of cache/performance"。**注意性质**：这是**手工挑选的冷热分离**（低频元数据 vs 高频访问的 transform），**不是 archetype 驱动的布局**——arch-opensrc 已澄清 Lumix 的 archetype 仅为类型集合哈希表，组件数据由各 `IModule` 自管。因此**不可把这条当作 EnTT owning group 的对照**（详见 §E.3❸）。

`EntityData` 的内部布局也值得一看（源码原文）：

```cpp
struct EntityData {
    i32 hierarchy;   // index into m_hierarchy, < 0 if no hierarchy
    i32 name;        // index into m_names, < 0 if no name
    union {
        struct { PartitionHandle partition; ArchetypeHandle archetype; };
        struct { int prev; int next; };   // freelist indices
    };
    bool valid = false;
};
```

**空闲槽位与分区/archetype 信息共用一个 union**——槽位被占用时解释为 (partition, archetype)，空闲时解释为 freelist 的 prev/next。没有额外的空闲数组，代价是槽位内存的多重解释。这个 `union` 也是上面 ABA 问题的根源：freelist 复用路径**只改 `valid` 一个 bit**。

2. **空闲槽位链表与 partition/archetype 做成 `union` 复用同一块内存**。`EntityRef` 是索引句柄，`prev/next` 空闲链表存在实体槽位里，槽位空闲时该内存被解释为链表指针或分区元数据。这是"内存紧凑"的极致做法——没有额外的空闲数组，但代价是槽位内存的多重解释增加了实现复杂度。

**句柄宽度与 ABA（arch-opensrc 实测 + 本人独立源码核验）**：Lumix 有**两组独立**的句柄类型，之前被我混成一条，现更正：

| 类型 | 定义 | 限制 |
|---|---|---|
| `EntityRef` / `EntityPtr` | `i32 index`（32 位） | 上限 21 亿；但**无 generation → ABA 成立**，详见 §E.3❶ |
| `PartitionHandle` / `ArchetypeHandle` | `u16`（16 位） | 上限 65536（规模限制，非 ABA 问题） |

对照本报告其他引擎：Rapier 用 `u32` index + `u32` generation（generational arena）、flecs 用 **64 位** `ecs_entity_t`（含 generation）、EnTT 用打包整型。**Lumix 是本报告唯一一个完全不设版本号的引擎**——这是明确的「紧凑内存换 ABA 防护」取舍。

⚠️ **注意**：Lumix 的 `ArchetypeHandle` **只存在于 `EntityData` 的 union 中**，与 `prev`/`next`（freelist 索引）共享内存。arch-opensrc 已更正：**archetype 在Lumix 里只是类型集合哈希表，不是存储桶**，组件数据由各 `IModule` 自管。所以它与 EnTT archetype / Bevy Table **不是同一维度**，不宜并列比较。

**组件类型表代码生成**：Lumix 的 `src/engine/component_types.h` 头部标注 `// generated by meta.cpp`，共 37 个 `extern const ComponentType`（`rigid_actor` / `camera` / `navmesh_agent` / `vehicle` / `terrain` 等，arch-opensrc 实测）。反射**用函数指针而非虚表**：`Property<T>` 的 `Setter = void(*)(IModule*, EntityRef, u32, const T&)`、`Getter = T(*)(IModule*, EntityRef, u32)`，第三参数是实例索引。相比虚表，虚函数方案在批量调用时更易被内联，且省一次间接跳转。代价是失去运行时多态——但组件类型表本来就在编译期生成，这个代价已被架构消化。

**与本报告主线的关系 —— 这条要拆成两半，性质不同**：

| 命题 | 性质 | 处置 |
|---|---|---|
| Lumix 中 per-World 隔离由架构强制：`ISystem` 全应用唯一，`IModule` 每 World 一份 | **arch-opensrc 实测（代码事实）** | ✅ 保留 |
| 「物理引擎**应该**挂 `IModule` 而非 `ISystem`」 | 我从上一条**外推**的通用原则 | ⚠️ 降级为设计建议 |

Lumix 的 World 构造函数遍历全局 `ISystem` 列表调 `createModules(*this)`，**每个 World 都从全局 ISystem 各实例化一份 IModule**（arch-opensrc 实测）。因此**仅在 Lumix 语境下**可以确定：任何需要 per-World 独立的状态只能放 `IModule`，放 `ISystem` 必然跨 World 共享。

但「因此物理引擎在别的引擎里也该挂 IModule」是外推——**O3DE 存在 `SystemComponent` 这一独立概念，能否实例化多份未经核验**，不应拿 Lumix 的结论覆盖 O3DE 的设计。team-lead 将亲自核对分屏与服务器多房间两个场景。

**这条设计建议的价值**（若成立）：若物理引擎挂在全局唯一的 `ISystem` 上，partition 切换时**无法隔离不同 partition 的刚体状态**——同一份物理 world 装着所有 partition 的刚体，卸载 partition 就要从 world 里精确摘除。挂 `IModule` 则天然跟随 World 生命周期。此推导**依赖「物理该挂哪一级」这个未验证前提**，故仅作保留疑问。

### E3. 结论状态（正文只写最终状态，过程见附录 F）

> 按team-lead 要求单列。**含糊比说错更危险。**

**✅ ❶ Lumix `EntityRef` 无 generation → ABA 成立**

`EntityRef` / `EntityPtr` 是裸 `i32 index`，**无 generation 字段**；槽位有效性仅有 `bool valid`（**1 bit，不是计数器**），配合 freelist 复用会导致 ABA——销毁后重建的同索引实体，旧 `EntityRef` 会静默通过 `hasEntity()` 并指向新对象。

这不是推测，是代码的直接语义：布尔能表达「槽位是否被占用」，但**无法区分「这个槽位上的对象是不是当初那个对象」**。经 arch-opensrc 与本人在 `lumix.h` / `world.h` **双向独立核验一致**（引用逐字核对无差异）。

```cpp
// lumix.h
struct EntityRef { i32 index = -1; /* ... */ };
struct ComponentType { enum { MAX_TYPES_COUNT = 64 }; i32 index; };
// world.h
struct EntityData {
    i32 hierarchy; i32 name;
    union {
        struct { PartitionHandle partition; ArchetypeHandle archetype; };
        struct { int prev; int next; };   // freelist indices
    };
    bool valid = false;
};
int m_first_free_slot;   // freelist for m_entities/m_transforms
```

独立佐证：`ComponentUID`（`src/engine/component_uid.h`）同样无版本位，只有 `(EntityPtr, ComponentType, IModule*)` 三元组，`isValid()` 只判 `index >= 0`。**两处独立实现都无版本位 ⇒ 全局设计取向**（紧凑内存换 ABA 防护）。

**⚠️❷ 句柄位宽：两组类型，两条独立限制**

| 类型 | 定义 | 限制 |
|---|---|---|
| `EntityRef` / `EntityPtr` | `i32 index`（**32 位**） | 上限 21 亿；**无 generation → ABA**，见 ❶ |
| `PartitionHandle` / `ArchetypeHandle` | `u16`（**16 位**） | 上限 65536（规模限制，非 ABA 问题） |

**⚠️❸ 不可把 Lumix 的 `EntityData`/`Transform` 分离当作 EnTT owning group 的对照**

- **EnTT group**：owning group 主动维护多池实体对齐，使多组件联合遍历退化为等长并行 walk。代价是增删组件时维护对齐。
- **Lumix**：`m_entities` / `m_transforms` 两个数组 + 源码注释 "not in single array (==EntityData does not contain Transform) because of cache/performance"——这是**手工挑选的冷热分离**，**不是 group 机制**，也不由 archetype 驱动。arch-opensrc 已澄清：**Lumix 的 archetype 只是类型集合哈希表，不是存储桶**，组件数据由各 `IModule` 自管。

两者唯一共同点是「每类组件一个数组以保证迭代连续」，**机制与代价都不同**。作为通用取舍（对齐维护成本 vs 零维护）本身成立，但两者不构成同一维度的对照组。

**❹ Lumix 组件类型上限 64（本人独立核实）**

`ComponentType::MAX_TYPES_COUNT = 64`（源码原文），且 `m_component_type_map` 是定长数组 `Local<ComponentTypeEntry> m_component_type_map[ComponentType::MAX_TYPES_COUNT]`。这是第三处 scalability 硬限制，与句柄位宽、ABA 三者性质不同但同向。

**❹ Lumix 组件类型上限 64（我独立核实）**

`ComponentType::MAX_TYPES_COUNT = 64`（源码原文），`m_component_type_map` 是定长数组 `Local<ComponentTypeEntry> m_component_type_map[ComponentType::MAX_TYPES_COUNT]`。这是第三处scalability 硬限制，与句柄位宽、ABA 三者性质不同但同向。

**❺ `contactHertz` / `contactDampingRatio` / `contactPushMaxSpeed` —— 未验证**

Box2D 的接触软化参数名，我只验证了 `b2Softness` 这个**类型名**（有 `contact_solver.h` 源码支撑），这三个**参数字段名**在我抓到的源码里未出现（该次 fetch 失败）。它们在 Box2D 官方文档中确实存在，但属**文档级证据**，低于本报告其余条目的源码级。若要展开具体软化参数行为，需补抓 `core.h` 或 `constants.h`。**已同步 arch-render，其渲染报告 §4.6 若引用请标注证据等级。**
---

## 附录 F. 协作勘误记录（过程信息，不影响正文结论）

> **本附录是过程记录，不构成结论。** 正文 §E.3 只写最终状态。此处保留推理链以便追溯。
> 建议汇编时：正文拼接，附录统一附于总文件末尾。

### F1. ABA 条目的往返（§E.3❶ 的形成过程）

**最终结论：ABA 成立**（正文已定，此处仅记录过程）。

过程：
1. arch-opensrc 实测 Lumix 源码后给出结论「ABA 确认成立」，并强调有效性字段是 `bool valid`——「**1 bit，不是计数器**」。
2. **我误读该表述**，把「1 bit 不是计数器」当成「不成立」的证据，据此在正文写下「arch-opensrc 确认 ABA 不成立，已撤回」。这一步是我的接收错误，原文表述本身是清楚的。
3. arch-opensrc 指出我读反了，并给出三段决定性源码（`EntityRef` 裸 i32、`bool valid`、freelist 复用路径）。
4. arch-opensrc 进一步给出独立佐证：`ComponentUID` 亦无版本位，说明是全局取向而非疏漏。
5. **我独立抓取 `lumix.h` / `world.h` 复核**，逐字确认其引用无误，将结论恢复。

**我的错误**：把「1 bit 布尔无法区分新旧对象」这个**支持 ABA 成立**的论证，读成了「不成立」的依据。
**arch-opensrc 的纠正是对的**，我的撤回无效。

### F2. 「静态分离 vs 动态 group」类比被否掉的推理链（§E.3❸）

我最初把 Lumix 的 `m_entities` / `m_transforms` 分离说成「与 EnTT owning group 是同一结论的两种实现」，并据此推论「有物理的世界用静态分离更优」。arch-opensrc 指出两者不在同一维度——**Lumix 的 archetype 只是类型集合哈希表，不是存储桶**，组件数据由各 `IModule` 自管，那个分离是手工冷热分离而非 group 机制。该类比已撤回。

**附带影响**：我基于此前提的「partition 推导」（物理挂 `IModule` 才能隔离 partition 刚体状态）虽在 Lumix 语境下成立，但依赖一个未验证前提，故在正文降级为设计建议。

### F3. 我自己的两处独立错误（与协作无关，纯自查）

| 错误 | 错在哪 | 正确 | 发现方式 |
|---|---|---|---|
| 句柄位宽 | 把 `EntityRef`(i32) 与 `PartitionHandle`(u16) 混成「16 位上限 65536」一句 | 两条独立限制，见 §E.3❷| arch-opensrc 指出后复核源码 |
| Jolt TempAllocator 对齐常量 | 写「按 `JPH_CACHE_LINE_SIZE` 对齐以避免伪共享」 | 实为 `JPH_RVECTOR_ALIGNMENT`；伪共享防护在 Body/缓存填充层 | 落实证据分级要求时自查 |

**TempAllocator 那条的来源是社区博客（CSDN）**，我在未验证的情况下当作源码级写入。性质是「证据等级误用」而非编造。

### F4. 方法论：我从这轮往返得到的教训

**收到的结论要回原文核对，不要按自己第一次的印象处理。**

这轮里我犯的是「接收端未核对原文」——arch-opensrc 给的是明确加粗的「ABA 确认成立」，我仍按印象处理并反向传达给他。arch-opensrc 的纠正是对我原始表述的**独立抓源码复核**，这个动作才是协作里最该固化的部分：**给出结论时附证据，让接收方能自行复核，而不是让接收方信任表述**。

同一模式在别处也出现过：我给 arch-render 的类比里含未验证字段名（`contactHertz` 等），arch-opensrc 给 arch-graphics 的归纳里含主观归类。**双方都在自己给出结论时附了证据等级，问题才被及时发现。**

### F5. 已同步的跨成员结论

| 对象 | 内容 |
|---|---|
| arch-render | 物理→渲染耦合 6 条；`inBaseOffset` ↔ Nanite cluster 局部原点同构；确定性两轴（而非三级）；Box2D 三参数证据等级 |
| arch-classic | Box2D 不支持回滚确定性（影响 lockstep 选型）；A 轴「重建 world 后顺序可能变」；双跑测试判据 |
| arch-opensrc | §E.1/§E.2 协同数据整合；ABA/位宽/group 类比三处更正 |
| team-lead | 简报 5 处源码纠错；确定性两轴修正；TempAllocator 证据等级更正；§E 协同增量 |
