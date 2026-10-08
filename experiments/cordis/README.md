# experiments/cordis —— 场景管线的 Cordis 化试点（试点一）

> 状态：**试点**（独立目录，不扰主流程；不引入主依赖）。
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

本目录**不 vendor 第三方代码**；cordis 需另行安装（见下）。

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

## 后续线索

- **试点二**：单 demo 插件化（`scene_viewer` 的 setup/render/teardown 三件套，
  验证卸载时 GL 资源回滚）
- **试点三**：loader 配置树（`cordis.yml` 式声明「本次跑哪些插件」）
- **转正路径**：vendor cordis 并锁定 commit（MIT；记账：来源 + 许可 + 借用范围）
