# experiments/cordis —— 场景管线的 Cordis 化试点（试点一）

> 状态：**试点一/二/三已完成**，且配置树已落地 demo 真实挂载路径（见文末「进展」）。
> 依据：《3dworld × Cordis 评估与试点报告》（2026-10-08）。

## 这是什么

用 [Cordis](https://github.com/cordiverse/cordis)（「时空可组合性」元框架——
插件即服务、`inject` 依赖、**可逆副作用**、coeffect 响应；DeepSeek Harness 的
运行时内核）的 **Python 移植**，把 3dworld 的**场景管线**组织成「服务 + 插件」：

| 插件 | 角色 |
|------|------|
| `checker` | 提供 `scene_check` 服务（包装 `engine.scene_build` 的校验器） |
| `loader` | `inject: scene_check`；装载场景 JSON、注册 `scene` 服务（可逆 effect） |
| `reporter` | `inject: scene`；输出统计——依赖消失时**自动停用**（coeffect） |

cordis 本体已 vendor 到仓库 `vendor/cordis/`（MIT；提交锁见 `vendor/cordis.lock`，
记账见 `vendor/cordis/VENDOR.md`）；本目录只放试点代码，换环境时再按下方「安装 cordis」兜底。

## 安装 cordis（Python 移植，MIT）

二选一：

```bat
:: 方式 1：源码 + 环境变量（推荐本地试验）
git clone https://github.com/geohotstan/cordis-py
set CORDIS_SRC=<你克隆的目录>\src

:: 方式 2：git 直装
pip install "cordis @ git+https://github.com/geohotstan/cordis-py"
```

cordis 的 `include` / `hmr` 模块依赖 **pyyaml** 与 **watchdog**，一并安装：

```bat
pip install pyyaml watchdog
```

## 运行

```bat
:: 用主项目 .venv（pyglet 已在其中）
.venv\Scripts\python.exe experiments\cordis\scene_pipeline.py
.venv\Scripts\python.exe experiments\cordis\scene_pipeline.py --scene docs\llm_scene_demo.json
```

## 示例输出

```text
== mount plugin tree (order by dependency graph) ==
[checker] service ready: scene_check
[loader] loaded llm_scene_demo.json: nodes=20 verts=24335
[reporter] llm_scene_demo.json: meshes=19 tris=15296
== dispose loader (service rollback + downstream deactivation) ==
[reporter] deactivated: scene withdrawn (coeffect reacted)
[loader] unloaded: scene service withdrawn (effect reverted)
[verify] scene service accessible: False
== PIPELINE_OK ==
```

（卸载为并发回滚，后三行的先后次序可能互换。）

## 它证明了什么

1. **inject 依赖自动排序**：loader 等 checker 的服务就绪才装载，无需手工编排；
2. **可逆副作用**：loader 卸载 → 它注册的服务与 effect 自动回滚；
3. **coeffect 响应**：依赖的 `scene` 服务消失 → `reporter` 自动停用；
4. **与主仓零耦合**：仅使用 `engine.scene_build` 的公开函数。

## 进展（原「后续线索」，均已收口）

- **试点二 · 已完成**：塔防内核 Cordis 化（`tower_defense_kernel.py`）——共同核 `provide`
  为服务、内核 `inject` 依赖自动排序；GL 资源经 `ctx.effect` 绑定内核 fiber，
  `dispose` 断言回滚（`TDKERNEL_OK`）。
- **试点三 · 已完成并落地到 demo**：`cordis.td.yml` / `cordis.godhand.yml` 声明「本次挂
  哪些插件」；`tree_boot.mount_tree` 是唯一挂载入口——`demos/tower_defense.py`、
  `demos/godhand.py` 的 `_mount_kernel` 与 `config_boot.py` 的 headless 验证走**同一函数、
  同一 `WANT_*` 列表**（`TDKERNEL_OK` / `GODHAND_OK` + 配置传递断言）。
- **转正 · 已完成**：`vendor/cordis`（MIT，`VENDOR.md` 记账）+ `vendor/cordis.lock`
  锁定上游 commit `a3d0c171571c6bbb465d04c06eab2de53d270814`（zip SHA-256 同录）。
- **内核族扩展 · 已完成**：同一范式（`xxx_kernel.py` + `services/xxx.py` + `cordis.xxx.yml`
  + `config_boot xxx`）下现共 **8 个内核**：

  | kind | 内核 | 一句话 |
  |------|------|--------|
  | `td` | 塔防「戍卫之路」 | 经济 → 兵力 → 战斗循环 |
  | `godhand` | 神之手 | 抬高/挖低地形改写高度场 |
  | `fps` | 迷堡枪影 | 射线 FPS + 全屏 SDF 光线步进 |
  | `puzzle` | 归仓 | 推箱 + BFS 求解器 + 程序化关卡 |
  | `storm` | 弹潮 | 全向生存 + **boss 波** + 弹幕图案生成器 |
  | `hall` | 静馆 | 节点式漫游解谜 + 场景 JSON **热重载** |
  | `void` | 星阑 | 惯性飞行 + **确定性市场** + 海盗拦截 |
  | `pulse` | 落音 | 下落式音击（**audio 核的首个消费者**） |

  外加 `cores`（纯共同核自检）。**`python experiments/cordis/config_boot.py all` → 12/12 OK**；
  `config_boot <kind>` 是每个内核的 headless 验收入口（demo 与它共用 `mount_tree` +
  同一 `WANT_*`，即「demo 怎么挂的 = 验收怎么挂的」）。
