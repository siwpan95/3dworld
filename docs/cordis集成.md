# Cordis 集成（配置树 · 服务宿主）

> 状态：**正式集成**（2026-10-13 · P0 引擎化改造后）
> 前身：`experiments/cordis/README.md`（试点一/二/三记录）——试点目录已退役，本文为其正式版。
> cordis 本体 vendor 在 `vendor/cordis/`（MIT；锁 `vendor/cordis.lock`，记账 `vendor/cordis/VENDOR.md`）。

## 1. 它是什么

用 [Cordis](https://github.com/cordiverse/cordis)（「时空可组合性」元框架——插件即服务、
`inject` 依赖、**可逆副作用**、coeffect 响应）的 Python 移植，把 3dworld 的**玩法与服务**
组织成「服务 + 插件」：**改一个 yml = 换一套玩法装配**，不改代码。

| 角色 | 住哪 | 说明 |
|------|------|------|
| 配置树 | `configs/*.yml`（9 棵） | 声明"本次挂哪些服务、各带什么 config"（单一事实来源） |
| 宿主 | `host/tree.py` | `mount_tree(path, want)`：唯一挂载入口 + 就绪等待 + entry 白名单 |
| 服务表 | `host/registry.py` | **服务短名 → '模块路径:实现类'**（23 条：13 共同核 + 10 内核） |
| 验收 CLI | `cli/config_boot.py` | 每棵树的 headless 验收（demo 与它**共用** `mount_tree` 与 `WANT_*`） |
| 实现 | `core/*_core.py` / `games/<名>/kernel.py` | 共同核与玩法内核 |

「**demo 怎么挂的 = 头less 验收怎么挂的**」是本设计最重要的一条：不存在两套接线，
所以 demo 里能跑通的东西，headless 验收也一定覆盖。

## 2. 运行

```bat
:: 全部配置树（每棵树一个 OK 标记）
.venv\Scripts\python.exe -m cli.config_boot all

:: 单棵 + 多种子回归
.venv\Scripts\python.exe -m cli.config_boot td
.venv\Scripts\python.exe -m cli.config_boot fpss --seeds 20261010,11,22
.venv\Scripts\python.exe -m cli.config_boot storm --seeds 20261011,7

:: 场景校验流水线（Cordis 化的三服务：checker → loader → reporter）
.venv\Scripts\python.exe -m host.pipeline
.venv\Scripts\python.exe -m host.pipeline --scene docs\llm_scene_demo.json
```

## 3. 服务短名表（`host/registry.py`）

yml 里直接写短名，例如::

    - name: economy
      id: economy
      config: { gold: 300 }

- **13 个共同核**：`render` `economy` `entity` `rules` `terrain` `pathing` `particles`
  `sdf` `audio` `timeline` `inventory` `skills` `loot`
- **10 个玩法内核服务**：`storm` `fps` `maze` `puzzle` `grid` `tower_defense`
  `hall` `void` `pulse` `godhand`

实现方式：每个短名在 `sys.modules` 里生成一个**只含 `default` 的宿主模块**
（`_3dwsrv.<短名>`）—— 因为 `games/fps/kernel.py` 同时提供 `FpsKernel` 与 `MazeService`，
一个真模块挂不下两个 `default`。

**安全边界**：entry 的 `name` 会被 cordis 直接 `import`（"配置即代码"）。因此短名**必须
命中注册表**（封闭白名单）才可能挂载；手写模块路径另受前缀白名单约束
（`core.` / `games.` / `_3dwsrv.`，外加兼容期的 `experiments.cordis.`）。校验发生在
**短名解析之后**。

## 4. 八个玩法内核

同一范式（`games/<名>/kernel.py` + `configs/cordis.<名>.yml` + `cli/config_boot.py` 的 kind）
下共 8 个内核：

| kind | 内核 | demo | 一句话 |
|------|------|------|--------|
| `td` | 塔防「戍卫之路」 | `examples/tower_defense.py` | 经济 → 兵力 → 战斗循环 |
| `godhand` | 神之手 | `examples/godhand.py` | 抬高/挖低地形改写高度场 |
| `fps` | 迷堡枪影 | `examples/rayhold.py` | 射线 FPS + 全屏 SDF 光线步进 |
| `puzzle` | 归仓 | `examples/boxward.py` | 推箱 + BFS 求解器 + 程序化关卡 |
| `storm` | 弹潮 | `examples/swarmward.py` | 全向生存 + boss 波 + 弹幕图案生成器 |
| `hall` | 静馆 | `examples/stillhall.py` | 节点式漫游解谜 + 场景 JSON 热重载 |
| `void` | 星阑 | `examples/voidward.py` | 惯性飞行 + 确定性市场 + 海盗拦截 |
| `pulse` | 落音 | `examples/pulsefall.py` | 下落式音击（audio 核的首个消费者） |

外加 `cores`（纯共同核自检，`require_gl=False`）。

**实测**：`python -m cli.config_boot all` → **9 个 OK 标记**
（TDKERNEL / GODHAND / FPSKERNEL / BOXWARD / STORMKERNEL / HALLKERNEL / VOIDKERNEL /
PULSEKERNEL / CORES）。

## 5. 它证明了什么（设计收益）

1. **inject 依赖自动排序**：内核等共同核就绪才初始化，无需手工编排；
2. **可逆副作用**：`ctx.effect` 把 GL 资源绑到内核 fiber，`dispose` 时自动回滚
   （验收里断言 `GL live → 0`）；
3. **coeffect 响应**：依赖的服务消失 → 消费者自动停用（`host/pipeline.py` 的 reporter）；
4. **与引擎零耦合**：内核只用 `core.*` 服务与 `engine.*` 公开函数。

## 6. 已知坑位

- **cordis 的 Loader 会吞掉插件构造期异常**：某个 entry 的模块 import 成功、但
  `Service.__init__` / `setup` 抛错时，`mount_tree` 只能看到"服务不就绪"（像配置树超时）。
  定位办法：手工 `import <该 entry 的 name>` 一次看真实异常。已写进 `host/tree.py` 的报错提示。
- **Loader 需要 drain**：`ctx.plugin(Loader)` 之后要把事件循环空转几轮，等 `isolate` 等子插件
  注册完再 `root.update(entries)`，否则首条 entry 会因内部服务缺失抛 `KeyError`。
