# 3dworld — pyglet 三维世界引擎

## 仓库角色定位（投资组合分级 ①）

> 本仓在 siwpan 投资组合（D:/projects/repos，共 133 仓）中定级为 **① 核心推进**，领域 **游戏引擎/框架**，定位 **引擎平台层**。
> **角色**：持续投入：Python Bevy 式 3D+procgen 底座，补测试/文档。
> **维护策略**：① 核心推进——持续投入、不允许冻结；随所属收敛点 / 流水线长期演进。
> 投资组合统一分级索引（多机可查）：[组合分级决策索引 · allin 事实源](https://gitee.com/siwpan/allin/blob/master/docs/事实源/组合分级决策索引.md)。

> 仓库：<https://gitee.com/siwpan/3dworld> ｜ 许可：MIT（见 [LICENSE](LICENSE)）
> 注：文中 `<PATH>` 为路径占位符，请替换为你本机实际的目录 / 可执行文件路径。

[![PyPI](https://img.shields.io/pypi/v/3dworld)](https://pypi.org/project/3dworld/)
[![Python](https://img.shields.io/pypi/pyversions/3dworld)](https://pypi.org/project/3dworld/)
[![License](https://img.shields.io/pypi/l/3dworld)](https://pypi.org/project/3dworld/)

**PyPI**：<https://pypi.org/project/3dworld/> ｜ **发行名 `3dworld` / 导入名 `world3d`**
（Python 导入包名不能以数字开头，`import 3dworld` 是语法错；PEP 508 发行名则允许。）

用 Python + pyglet 从零构建三维世界与**动画演出**：时间轴 / 关键帧插值 /
层级骨骼 / 程序化场景。**本仓是引擎库**（发行名 `3dworld` / 导入名 `world3d`）；
39 个可运行的演示程序（含成片与截图产物）已剥离至独立仓
[siwpan/3dworld-demos](https://gitee.com/siwpan/3dworld-demos) ——
演示所代表的能力全部保留在本仓引擎内（`logic/` `engine/` `games/` `presentation/`）。

当前面貌（能力均在引擎内，演示见独立仓）：

- `presentation/app.py`（WorldApp）—— **48 秒五幕演出**：环形立方体升起 → 环形波 →
  机械臂逆运动学取放宝石 → 螺旋柱 → 归位，双相机模式 + 幕字幕 + 时间轴面板；
- `presentation/kkrieger_app.py` + `logic/kkrieger.py` —— **150 秒六幕程序化走廊
  漫游 + 程序化配乐**（致敬 .kkrieger）：走廊舱门/管道/货箱/频闪 → 大厅纪念碑/
  全息扫描/升降平台 → 核心过载 → 撤离时逐段熄灯 → 重启突入（核心爆发白闪）→
  白闪传送至环面结反应堆圣殿；
- `world3d/engine/` + `world3d/logic/` + `world3d/games/` —— 引擎全量能力：
  PBR/IBL 渲染、级联阴影、体积雾、景深、运动模糊、帧图管线、GPU 驱动渲染、
  顺序无关透明、点云+EDL、蒙皮骨骼、程序化地形/体素/粒子、9 个玩法内核
  （详见下文「能力清单」）。

## 两条 pyglet 技术路线（历史版本）

| 文件 | 适配版本 | 渲染管线 | 现状 |
|------|----------|----------|------|
| `world3d`（引擎库） | pyglet 2.x | 可编程管线（Mat4 / Batch） | 现行 |
| `pyglet_3d_demo_1x.py` | pyglet 1.x | 固定管线（gluPerspective 等） | 已随 demo 迁往 3dworld-demos（与 cocos2d 共存环境的历史兼容件） |

> 注意：pyglet 1.x 与 2.x API 不互通（2.x 已移除 GLU），教程代码不可混用。

## 项目结构

```
world3d/              **唯一的顶层包**（P2-4 收口；发行名 3dworld / 导入名 world3d）
  __init__.py         库入口：把引擎公共面转发出来（公共面由 engine.__all__ 收口）
  compat.py           旧路径兼容层（P2-4 过渡版）：opt-in 的元路径别名，
                      install 之后 `import engine.csm` 与 `import world3d.engine.csm`
                      拿到**同一个模块对象**；本模块是过渡件，外界迁完即删
  _cordis_locate.py   cordis 定位：先试已安装的，再退回 vendored `vendor/cordis/src`
  platform/           **平台层骨架**（P2-1）：窗口 / DPI / 命令行 / HUD / 截图 / 主循环
    app.py            DemoApp · run · orbit_camera_input …（演示程序的统一骨架；
                      演示本体在独立仓 3dworld-demos，本模块是其接入面）
  core/               核心层：窗口 / 渲染 / 几何 / 资源（**允许** pyglet.gl）
    geometry.py        立方体/盒体/地面/静态合并盒体构建（ShaderProgram.vertex_list）
    resources.py       外部模型加载（当前 .obj）
    contracts.py       契约注册表 + 全仓可达性检查（P0-5）
    particles_core.py  粒子逻辑核（games/storm 等内核消费）
    sdf_core.py        SDF 距离场核（games/fps 迷宫距离场消费）
    terrain_core.py    可编辑高度场核（games/godhand 消费）
  logic/              逻辑层：场景与动画（**禁止** pyglet.gl，可无窗口单测）
    animation.py       时间轴/关键帧/插值（glTF TRS 对齐 + tint/glow 材质通道）
    choreography.py    48s 五幕编排（含两连杆逆运动学）
    kkrieger.py        kkrieger 的 150s 六幕编排（相机脚本 + 场景动画）
    camera.py          轨道相机状态机 + 手写 look-at 矩阵
    playback.py        播放控制器：播放/暂停/循环/倍速/seek
    skeleton.py        节点级层级动画（转台→大臂→小臂→夹爪）
    terrain.py         程序化地形：值噪声 fBm 高程场 + 分层烘焙色 + 逐行网格发射
                       （2026-10-15 自 terrain_flyover 演示上提；渐进重建的数学
                       保证由 test_terrain_row_emitter 钉住）
    voxel.py           体素世界：高度图生成 + 面剔除网格化（可分段）
                       （2026-10-15 自 voxel_terrain 演示上提；剔除率与可复现性
                       由 test_voxel_mesh_invariants 钉住）
    blocks.py          方块世界逻辑：像素材质像素 / 世界生成 / 网格化 / 生物步态 / 昼夜
    kid_a.py           KID-A 房间模版与无限走廊逻辑
  engine/              引擎层（P3-2 起引擎级件陆续下沉；见 docs/引擎设计.md）
    fsm.py             确定性有限状态机（时间窗 + 事件转移，P0-5 自研）
    orchestration.py   事件总线 + 声明式 when→then 级联（P0-5 自研）
    baked_mesh.py      烘焙明暗的单位立方体/球（P0-3 从内核上提）
    greedy_mesh.py     单层体素贪心合批（P0-1 从内核上提）
    voxel_mesh.py      体素网格化 v2：任意三维数组的三轴贪心合并 + 逐顶点 AO
                       （六向 sweep、无内部面、对角翻转消条纹；纯数据、无 GL）
    terrain_lod.py     CDLOD 地形：四叉树选层 + 顶点 morph 消缝（单张网格模板，
                       三角数随视距亚线性；纯数学、无 GL，可单测）
    csm.py / volumetrics.py / dof.py / motionblur.py   ← ④ 轨道的四个渲染模块
    framegraph.py      帧图：声明 → 编译期校验 → 剔除 → 拓扑排序 → 别名（P3）
                       v2 追加：生命周期推导 / 别名组 / 跨帧槽位池 / 峰值账本
    particles.py       GPU 实例化粒子系统（2026-10-15 自 particles_gpu 演示上提；
                       逐帧动画 100% 在顶点着色器，CPU 每帧只更新标量）
  presentation/        表现层：装配窗口事件 → 逻辑层 → 渲染
    app.py             WorldApp：48s 演出装配（双相机模式 + 夹持混合）
    kkrieger_app.py    KKApp：150s 漫游装配 + 跳帧截图 + 音频同步
    hud.py             幕字幕（淡入/停留/淡出）+ 状态行
    timeline_ui.py     底部时间轴面板（播放/循环/拖动/幕标记）
  games/               玩法内核层：games/<名>/kernel.py，9 个 Cordis 内核
  host/                宿主层：tree.py（配置树挂载/就绪等待）· registry.py（服务短名表）
                       pipeline.py（场景校验流水线：checker→loader→reporter）
  cli/                 引擎 CLI（`python -m world3d.cli.<名>`）
    config_boot.py     每棵配置树的 headless 验收（与内核共用 host.tree）
    frame_plan.py      帧图门禁：16 种效果组合的计划必须合法且顺序正确
configs/               10 棵配置树（cordis.*.yml）：
                       声明本次挂哪些服务、各带什么 config
scripts/
  _console.py          入口脚本的 stdio 编码兜底（GBK 控制台不再因 ⚠/⊆ 崩掉而吞掉结论）
  verify.py            机械门禁 15 条：同一事实写在两处必须相等（<1 秒，pre-commit 会跑）
  docs_index.py        docs/README.md 的生成器（门禁 docs-index-fresh 核对新鲜度）
  new_test.py          脚手架：生成能开箱跑的新测试模块（模板被门禁钉住；
                       demo 脚手架已随 demo 迁往 3dworld-demos）
  new_kernel.py        脚手架：生成新玩法内核 + 配置树 + 单测，并登记三张表（--undo 可撤销）
  check_imports.py     全仓可达性门禁：world3d/ 下每个模块都 import 一遍（+ sys.path 不变量）
  run_all_tests.py     45 个测试模块的聚合 runner（数字由 readme-test-modules 门禁钉住）
  migrate_p2_4.py      P2-4 的一次性迁移工具（保留以便复查改了什么）
tests/               逻辑层无窗口单测：python -m tests.run_tests
docs/                设计文档（渲染验证截图已随 demo 迁往 3dworld-demos）
```

依赖方向：presentation → logic → core，反向禁止。

## engine/ —— 核心引擎（2026-10 抽取）

零业务依赖的可复用引擎层，`core/logic/presentation` 的公共件已下沉至此：

- **渲染**：前向 PBR（金属-粗糙度）+ IBL（程序化天空烘焙：辐照度图 / GGX
  预过滤 mip 链 / BRDF LUT）+ MRT（HDR 颜色 + 视空间法线/深度）
- **后处理**：SSAO（半分辨率半球核）→ Bloom（软膝盖阈值 + Karis + 降采样/
  上采样金字塔）→ 曝光 / ACES / 暗角 / gamma
- **资源与场景**：glTF 2.0 / GLB 加载（sparse、动画、PBR 材质）、Node 场景图、
  GPU 实例化（一次 draw call 画 N 个）、视锥剔除（Gribb-Hartmann + p-顶点）、
  场景 JSON 的程序化几何生成器（当前 11 个：2026-10-20 起含 **`heightfield`
  高度场地形**：`hm` 二维矩阵驱动 y、可被 Validate 直接校验，与纯平面的
  `ground_grid` 互补；路网/河流在生成 `hm` 时把走廊压平即可）
- **交互与物理**：轨道/第一人称漫游、射线拾取（AABB 粗测 + 三角形精测）、
  AABB 碰撞 + 重力 + 沿墙滑动
- **工程化**：性能面板（p1/jank + GPU 计时查询）、场景 JSON 序列化 + 热重载、
  统一 CLI（`--smoke/--shot/--bench`）
- **进阶渲染**（2026-10 后批）：
  - **蒙皮骨骼动画**（`skin.py`）：glTF `skin` 解析 + 关节矩阵管线 + `pbr_skin` 着色器
    （`u_joints[MAX_JOINTS]`；⚠️ pyglet 的数组 uniform 只写第 0 个元素，须直调 `glUniformMatrix4fv`）
  - **GPU 驱动渲染**（`gpu_cull.py`）：compute 里做**剔除 + LOD + 命令生成**，
    CPU 每帧只 1 次 dispatch + N 次 indirect draw；含**运行时能力探测 + CPU 回退**
  - **PCSS 软阴影**（`soft_shadow.py`）：blocker search → 半影估计 → 可变半径 PCF（Poisson 盘）
  - **WBOIT 顺序无关透明**（`oit.py`）：一趟写 accum/reveal 两张 MRT 再合成，无需排序
  - **点云 + EDL**（`pointcloud.py`）：程序化点云 + imposter 圆盘 + 深度轮廓增强

综合演示（把上面全部跑一遍的演示程序）已随 demo 迁往独立仓
[siwpan/3dworld-demos](https://gitee.com/siwpan/3dworld-demos)（`examples/engine_showcase.py`，
`pip install 3dworld` 后即可运行）；引擎测试：`python -m tests.test_engine`。

## 快速开始

```bat
:: 从 PyPI 装进任意环境（库形态）
pip install 3dworld
python -c "import world3d; print(world3d.__version__)"
world3d-info                :: 自检：版本 / 依赖 / GL 能力 / cordis / 配置树位置
```

```bat
:: 当作项目开发（editable，依赖与元数据以 pyproject.toml 为唯一来源）
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

> 装完拿到的 CLI：`world3d-info`（纯库自检，任何安装方式都可用）、
> `world3d-config` / `world3d-pipeline`（需仓库的 `configs/` 配置树，非 editable 安装下用
> 环境变量 `WORLD3D_CONFIGS` 指向）。

**命名**（P1 库化定）：**发行名 `3dworld` / 导入包名 `world3d`** —— Python 的导入包名不能以
数字开头（`import 3dworld` 是语法错，`'3dworld'.isidentifier()` 为 `False`），而 PEP 508
发行名允许。`world3d` 目前是**薄门面**（把引擎公共面从 `engine` 转发出来），
`engine` / `core` / `host` / `games` 等仍是独立顶层包（旧导入路径零破坏）；
P2「分层归位」会把它们收进 `world3d.*` 命名空间。

**库与仓库的边界**：`pip install` 装的是**引擎库**（engine/core/host/cli/games + vendored cordis）。
`configs/`（配置树）、`assets/` 属**仓库资产**，不随 wheel 分发 —— 所以：

- `world3d-config` / `world3d-pipeline` 需要 `configs/`：从仓库运行（或 editable 安装）天然可用，
  非 editable 安装下用环境变量 `WORLD3D_CONFIGS` 指向配置树目录；
- 演示程序（39 个）在独立仓 [siwpan/3dworld-demos](https://gitee.com/siwpan/3dworld-demos)，
  `pip install 3dworld` 后即可运行（成片录制/批量验收等工具随仓附带）。

## 48 秒五幕演出（能力：`presentation/app.py` + `logic/choreography.py`）

环形立方体升起 → 环形波 → 机械臂逆运动学取放宝石 → 螺旋柱 → 归位，
双相机模式 + 幕字幕 + 时间轴面板。演示程序已迁往独立仓 3dworld-demos
（`examples/` 内的入口脚本）；**能力本体在本仓**：编排在 `logic/choreography.py`，
窗口装配在 `presentation/app.py`（WorldApp）。

| 幕 | 时间 | 内容 |
|----|------|------|
| 一 · 升起 | 0–10s | 10 个环形立方体错峰从地面升起，随高度点亮 |
| 二 · 环形波 | 10–20s | 相位错开的行波（径向 + 垂直）扫过环带 |
| 三 · 机械臂 | 20–30s | 转台 + 两段臂**逆运动学**解到取件位，夹起宝石送上台座 |
| 四 · 螺旋 | 30–40s | 立方体收拢成螺旋柱，核心脉冲放大，轨道球退让 |
| 五 · 归位 | 40–48s | 螺旋展开、回落、沉入地面，姿态精确回到 t=0 帧 |

按键：`SPACE` 播放/暂停　`L` 循环　`C` 相机模式（电影机位/自由轨道）　
`N`/`P` 下一幕/上一幕　`R` 重播　`-`/`=` 速度　鼠标拖动/滚轮（自动切自由视角）　`ESC` 退出

> 循环不是"大概接上"：`tests/run_tests.py` 会断言所有姿态函数在
> `t=0` 与 `t=48` 逐分量相等、幕边界无跳变，以及夹爪在 t=22/t=26.5
> 分别落在宝石与台座 0.3 单位内。

## kkrieger 致敬 · 150 秒六幕程序化走廊漫游（能力：`presentation/kkrieger_app.py` + `logic/kkrieger.py`）

参考 2004 年 Breakpoint 96k 冠军作品 .kkrieger（Farbrausch/.theprodukkt）的
"程序化生成"精神：**零外部素材**——金属板、灯带、舱门、管道、货箱、立柱、
五环纪念碑、全息扫描框、升降平台、能量束、风扇、无人机、数据流光带、环面结
反应堆、爆发碎片全部由代码即时生成，面板颜色用确定性哈希抖动模拟"程序化纹理"；
**配乐同样是程序生成的**（`logic/kkrieger_audio.py` 离线合成 150s 电子乐，
首次运行渲染缓存到 `assets/kkrieger_ost_150s.wav`，音画共用同一套时间常量）。
演示程序已迁往独立仓 3dworld-demos。

| 幕 | 时间 | 内容 |
|----|------|------|
| 一 · 走廊 | 0–24s | 俯冲进走廊，三道舱门依次开启，管道/货箱/警示频闪入画 |
| 二 · 大厅 | 24–48s | 纪念碑唤醒：五环转动、全息扫描框上下扫、升降平台升起 |
| 三 · 核心 | 48–72s | 过载：环加速、核心脉冲、能量束增亮、镜头逼近至"贴脸" |
| 四 · 撤离 | 72–96s | 后撤，走廊灯带与大厅灯环逐段熄灭，拉回开场高空帧 |
| 五 · 突入 | 96–118s | 重启：心跳鼓点，高速穿越（舱门速开速闭、数据流光带跑马），全件过载，核心爆发白闪 + 碎片飞散，冲击波退出归位 |
| 六 · 圣殿 | 118–150s | 白闪中传送过门洞：环面结反应堆常态低鸣 → 点火过载（141.5s 二次白闪），尾声归位 |

音频：`M` 静音；seek（R/N/P/←→）时音频同步跳转；循环无缝（源时长=时间轴时长）。
配乐结构与画面对位：开门气浪在开门时刻、核心亚低频脉冲与视觉脉冲同相、
灯带熄灭的轻响逐段对应、117.5s 爆发低频坠落、141.5s 点火二次爆发。

> 已知平台坑（实测 pyglet 2.1.16 / Win10 150% DPI）：`dpi_scaling` 默认
> "platform" 模式下实际 framebuffer = 请求尺寸，但 viewport setter 会乘
> scale → 渲染放大裁切；本演出统一设 `pyglet.options['dpi_scaling'] = 'real'`。

## 能力清单（原 39 个演示的能力落点）

2026-10-15 demo 剥离：39 个演示程序迁往独立仓
[siwpan/3dworld-demos](https://gitee.com/siwpan/3dworld-demos)（含成片、截图与
批量验收工具）；**每个演示代表的能力全部保留在本仓引擎内**，落点如下：

| 能力 | 引擎落点 |
|------|----------|
| 极坐标隧道（纯片元） | `engine/shaders.py` → `tunnel` |
| SDF 光线步进（smooth-min 融合 + 软阴影 + AO） | `engine/shaders.py` → `raymarch`（距离场数学同源：`core/sdf_core.py`） |
| GPU 实例化粒子（4.5 万颗全 GPU 动画） | `engine/particles.py` + `shaders.py` → `particles`（逻辑核：`core/particles_core.py`） |
| 程序化地形（值噪声 fBm + 分层烘焙 + 渐进重建） | `logic/terrain.py` |
| 体素面剔除 | `logic/voxel.py` |
| Gerstner 波顶点位移 | `logic/waves.py` |
| Reynolds 群集（boids） | `logic/boids.py` |
| 层级变换 + 轨道力学（太阳系） | `logic/skeleton.py` + `logic/animation.py` |
| 阴影贴图 / PCF | `engine/soft_shadow.py` |
| 程序化像素材质 + 昼夜（方块世界） | `logic/blocks.py` |
| PBR/IBL + Bloom + SSAO + 实例化 + 视锥剔除 + 拾取 + 物理 + 序列化 | `engine/renderer.py` `engine/scene.py` `engine/serialize.py` `engine/frustum.py` `engine/picking.py` `engine/physics.py` |
| 新建模 × 程序化 PBR 纹理（材质实验室） | `engine/proctex.py` + `engine/material.py` |
| 《诺德之境》程序化动画 + 配乐 | `logic/nordic_saga.py` `logic/nordic_scene.py` `logic/nordic_audio.py` |
| 可漫步场景（第一人称 + 自适应音乐） | `engine/adaptive_music.py` `engine/audio.py` `engine/fixedstep.py` |
| 方块世界可交互版（DDA 拾取 + 区块重建） | `logic/blocks.py` + `engine/fixedstep.py` |
| 场景 JSON 查看器 + 热重载 | `engine/scene.py` `engine/scene_build.py`（查看器形态随演示迁出） |
| 《山》悬浮孤峰 + 气象 | 孤峰生成复用 `logic/terrain.py` 噪声管线（艺术向部分随演示迁出） |
| 《迷堡枪影》射线 FPS | `games/fps/kernel.py` + `configs/cordis.fps.yml` |
| 《归仓》方块解谜 | `games/puzzle/kernel.py` + `configs/cordis.puzzle.yml` |
| 《砖境》机关解谜 | `games/brickward/kernel.py` + `configs/cordis.brickward.yml` |
| 《弹潮》弹幕生存 | `games/storm/kernel.py` + `configs/cordis.storm.yml` |
| 《神之手》地形改写 | `games/godhand/kernel.py` + `configs/cordis.godhand.yml`（高度场：`core/terrain_core.py`） |
| 《戍卫之路》塔防 | `games/tower_defense/kernel.py` + `configs/cordis.td.yml` |
| 《星阑》太空贸易 | `games/void/kernel.py` + `configs/cordis.void.yml` |
| 《落音》下落式音击 | `games/pulse/kernel.py` + `configs/cordis.pulse.yml` |
| 《静馆》节点式漫游解谜 | `games/hall/kernel.py` + `configs/cordis.hall.yml` |
| 《阈廊》后室循环走廊 | `logic/liminall.py` `logic/liminall_audio.py` `presentation/liminall_scene.py` |
| 《沙垒》UV 投影规则演示场 | `logic/sandward.py` `presentation/sandward_scene.py` `engine/texproj` `engine/proctex.py` |
| KID-A WALKER 3D | `logic/kid_a.py` |
| 蒙皮骨骼动画 | `engine/skin.py` + `engine/gltf.py` |
| GPU 驱动渲染（剔除 + LOD + 命令生成） | `engine/gpu_cull.py` |
| 顺序无关透明（WBOIT） | `engine/oit.py` |
| 点云 + EDL | `engine/pointcloud.py` + `engine/postfx.py` |
| 级联阴影 CSM | `engine/csm.py` |
| 体积雾与光轴 | `engine/volumetrics.py` |
| 景深 DoF | `engine/dof.py` |
| 运动模糊（相机重投影） | `engine/motionblur.py` |
| 帧图驱动渲染 | `engine/framegraph.py` |
| 可视化导演台（关键帧编排） | `engine/shot.py` `engine/timeline.py` `presentation/timeline_ui.py` |

演示程序的运行方式、统一命令行（`--smoke/--shot/--bench/--flag`）与踩坑清单见
独立仓 3dworld-demos 的 `examples/README.md`。

## 测试与验收

```bat
python -m tests.run_tests           逻辑层无窗口单测（65 项）
python -m tests.test_cores          共同核契约（17 项，含 P1-⑤ 玩法模块）
python -m tests.test_engine         引擎层无窗口单测（30 项，含 model uniform 作用域 + 多网格连续三帧逐字节一致）
python -m tests.test_shaders       着色器库：注册表静态核对 + 契约核对 + 全量编译（5 项，GL 不可用记 SKIP）
python -m tests.test_noise          噪声库：golden 交叉验证（与 procgen numpy 逐位一致）+ 确定性 + 值域 + 算法性质（8 项）
python -m tests.test_replay         回放系统：浏览面 + 确定性自检（含反向用例）（9 项）
python -m tests.test_behavior_tree  行为树：组合/装饰/叶子语义 + 声明式构造 + 假时钟超时（10 项）
python -m tests.test_dungeon         地牢/洞穴：连通性洪水填充验证 + 确定性 + 结构约束（6 项）
python -m tests.test_town            Voronoi 城镇：ward/建筑多边形有效 + 坐标级确定性（5 项）
python -m tests.test_ca              元胞自动机：141 规则注册表 + GoL 经典 + 连续规则 + 交错隔离（9 项）
python -m tests.test_curves           曲线库：Bézier 任意阶 + Catmull-Rom 过控制点 + 长度/切线（9 项，零 GL）
python -m tests.test_physics3d        3D 物理：向量/碰撞/扫掠 TOI/单球积分/多球世界（12 项，零 GL）
python -m tests.test_lsystem          L-Systems：重写（确定/随机）+ 2D/3D 龟图（10 项，零 GL）
python -m tests.test_wfc              WFC 波函数坍缩：瓦片平铺求解 + 邻接不变量 + 矛盾处理（8 项，零 GL）
python -m tests.test_skin           蒙皮骨骼动画（8 项）
python -m tests.test_gpu_cull       GPU 驱动渲染（7 项，含"GPU 剔除 == CPU 剔除"逐实例比对）
python -m tests.test_p2_render      PCSS / WBOIT / 点云+EDL（7 项）
python -m tests.test_timeline_ui    底部时间轴面板（9 项）
python -m tests.test_brickward      《砖境》机关内核：BQM 致敬 B1~B3（65 项，零 GL）
python -m tests.test_liminall       阈廊内核：后室 × P.T. 循环走廊（9 项，零 GL）
python -m tests.test_texproj        UV 投影与贴图规则：投影/逆投影/反解/图集/密度（12 项，零 GL）
python -m tests.test_source2        Source 2 解析：VPK / KV3 v5 / LZ4 block（10 项，零依赖）
python -m tests.test_meshopt        meshopt 顶点/索引解码：golden 对齐官方 WASM（5 项，零依赖）
python -m tests.test_source2_model  vmdl_c → MeshData 转换：合成容器 + 压缩路径（5 项）
python -m tests.test_sandward       沙垒演示场：路线/巡游/碰撞/UV 密度/切线/POM（7 项，零 GL）
python -m tests.test_proctex        程序化纹理 × PBR 图集：通道打包逐像素（4 项，零 GL）
python -m tests.test_heightfield    heightfield 地形生成器：XZ 高度场拓扑/范围/UV/高程线性映射/法线朝向 + validate 放行与拦截二维 hm（7 项，零 GL）
python -m tests.test_csm            级联阴影 CSM（20 项：分割/角点/光盒/纹素吸附/GLSL 展开）
python -m tests.test_volumetrics    体积雾/光轴（12 项：闭式积分可加性/HG 归一/抖动同式/GLSL 组合）
python -m tests.test_dof            景深 DoF（12 项：CoC/超焦距/景深端点恒等式/散景面积均匀）
python -m tests.test_motionblur     运动模糊（9 项：静止零运动/1÷z 律/纯旋转与深度无关/三模块共存）
python -m tests.test_framegraph     帧图（17 项：物理顺序/剔除/直通/别名/环与死链校验）
python -m tests.test_framegraph_v2  帧图 v2（17 项：生命周期推导/别名组/槽位池借还/峰值下降/自动屏障计划 RAW·WAR 推导与确定性）
python -m tests.test_terrain_lod    CDLOD 地形（18 项：选层无缝无重叠/相邻层差≤1/morph 消缝/三角数亚线性/模板网格）
python -m tests.test_compat         旧路径兼容层（6 项：install 前后不可用/同一性/不被劫持）
python -m tests.test_voxel_mesh     体素网格化 v2：三轴贪心合并/无内部面/逐顶点 AO/对角翻转（12 项，零 GL）
python -m tests.test_binary_greedy  体素二进制贪心网格化：uint64 位掩码 + quadtree 全覆盖合并/精确覆盖/三轴贪心对照（13 项，零 GL）
python -m tests.test_terrain_clipmap 几何 clipmap：嵌套 ring 网格/刚性吸附/顶点位移/层数与 annulus 拓扑（15 项，零 GL）
python -m tests.test_jolt_lite      3D 刚体世界：GJK-EPA 穿透（四面体种子严格含原点 + 有向边 horizon 扩张）+ 顺序冲量 + warm start + Baumgarte + Coulomb 摩擦 + 回弹；胶囊/旋转盒经 GJK-EPA 兜底；**sweep-and-prune 宽相 + 岛管理 + 按 body id 定序遍历（确定性）**（核心零 GL，headless 可验证；GL 系消费方事）（17 项）
python -m tests.test_box2d_lite     2D 序列冲量 solver：warm start + Baumgarte 偏置 + Coulomb 摩擦 + OBB-OBB SAT 裁剪流形（6 项，零 GL）
python -m tests.test_ozz_lite       骨架 FK / clip 采样 lerp+slerp / 加权混合 / 加法 clip / joint-mask 分层 / root motion / 真实蒙皮网格回放（构建骨架+clip+蒙皮矩阵+线性蒙皮，消费 gltf_lite）（12 项，零 GL）
python -m tests.test_matcache_lite  材质变体键 JSON 确定性 → 产物 define 注入 + SHA-256 摘要 → 内存 LRU + 磁盘 pickle；变体实际编译为 GLSL 顶点/片段着色器源串（SKINNING/FOG/ALPHA_TEST/NUM_LIGHTS 驱动，可注入 GL，不调编译器）（11 项，解码核心零 GL）
python -m tests.test_gltf_lite      glTF 2.0 真实 loader：数据模型/FK/扩展检测/校验 + accessor→numpy 解码/primitive 装配/.glb 二进制加载/skin+animation 提取（11 项，解码核心零 GL）
python -m tests.test_rid           RID 不透明句柄表：代际失效/槽位复用/跨表拒绝/悬垂句柄可断言/free 幂等（11 项，零 GL）
python -m tests.test_reflect       反射注册表（一物多用）：一份属性元数据同时驱动序列化 / 编辑器检视 / 脚本绑定 / 范围与枚举校验 + 继承链展开（11 项，零 GL）
python -m world3d.cli.frame_plan --all-toggles  帧图门禁：16 种效果组合的计划都必须合法且顺序正确
python -m world3d.cli.config_boot all   9 个内核 + 13 共同核全部 headless 验收（10 棵树，逐项 OK 标记）
python scripts\check_imports.py          全仓可达性门禁：world3d/ 下 160 个模块逐个 import
                                        （单测只覆盖被引用的模块，这个是「改漏」的唯一防线）
```

> 演示程序的批量验收（冒烟 + 截图 + 退出码）与成片录制已随 demo 迁往独立仓
> 3dworld-demos（其 `scripts/verify_demos.py` / `scripts/record_all.py`）。

单测覆盖：相机位姿与钳制（含**自定义范围**与非法范围拒绝）、look-at 矩阵性质、
四元数 slerp、Sampler/Timeline 语义、播放控制、UI 命中、OBJ 资产解码、
骨骼层级传播、**五幕编排的循环连续性与幕边界连续性**、**机械臂逆运动学落点**、
kkrieger 六幕的循环连续性与舱门/平台/灯带状态机、**镜头穿模检查**
（机位与立柱/门洞/顶棚/纪念碑的距离）、boids 可复现与有界、Gerstner 解析梯度、
**地形逐行装配与朴素装配逐位一致**、**体素换种子与剔除率**；
后批追加：**蒙皮绑定姿态 == 单位矩阵**、**热点命中/节点图连通/谜题顺序解锁/场景校验**、
**市场确定性/事件冲击/均值回归**、**谱面可打性约束/判定窗**、
**弹幕图案确定性与角度覆盖/boss 阶段切换/编波单调**。

## 机械门禁（`scripts/verify.py`，<1 秒）

把"同一事实写在两处必须相等"从散文变成**可跑的检查** —— 因为本项目的这些数字
**全靠手改**，而手改每次都可能漏一处。

```bat
python scripts\verify.py              # 15 条门禁（不联网、不跑构建、不改文件）
python scripts\verify.py --list       # 只列名字与说明
python scripts\verify.py --verbose    # 每条的证据
python scripts\verify.py --fix        # 重生成生成物（docs/README.md）
python scripts\verify.py --selfcheck  # 沙箱里逐条注入破坏，证明门禁拦得住
```

| 门禁 | 断言 |
|------|------|
| `readme-test-modules` | README 测试命令表 ↔ `run_all_tests.MODULES`（**双向**） |
| `readme-test-counts` | README 每行的「N 项」== 该模块声明的用例数（两种真实形态都认） |
| `readme-config-count` / `-kernel-count` / `-core-count` | 配置树 / 内核 / 共同核计数 |
| `cores-doc-pairs` | 契约手册总览表 ↔ `WANT_CORES`（**双向**，2026-10-19 加） |
| `doc-links` | README / docs 里的**相对链接**都指得到真实文件 |
| `docs-index-fresh` | `docs/README.md` == `scripts/docs_index.py` 重新生成的结果（新鲜度） |
| `commands-exist` | 文档里写的每条 `python -m …` / `python <文件名>.py` 目标都存在（模板占位符不算） |
| `pyrefs-exist` | 文档与 CI 里**每一处**一行式 `python -c "import <模块>"` 都指向**真实存在的模块** |
| `scaffold-templates-valid` | 测试脚手架模板渲染 → `ast.parse` → **真 import** → 关键片段齐全 |
| `scaffold-kernel-valid` | 内核 / 配置树 / 测试模板渲染 → `ast.parse` → **真 import** → **按 `run_generic` 的契约跑到 won** |
| `workflows-valid` | `.github/workflows/*.yml` 可解析、结构完整（`runs-on`/`steps`/`uses` 钉版本）、里面的命令可达 |
| `cli-usage-names` | `cli/*.py` 用法 docstring 里的模块名 == 真实模块名 |
| `readme-tree-real` | README「项目结构」树里列出的路径都真实存在 |

> 2026-10-15 demo 剥离时移除两条门禁：`readme-demo-count` 与 `examples-index-pairs`
> （示例数/示例索引的断言对象已迁往独立仓 3dworld-demos）。

**它上线当天就抓到过一个真漂移**：`tests/test_timeline_ui` 注册在
`run_all_tests.py` 里（14 个模块）却没进 README 的测试清单（只列了 13 个）。

后来加 `pyrefs-exist` 时又抓到**两处我根本不知道已经坏了的**：
`.github/workflows/ci.yml` 里 `import _cordis_locate`（P2-4 改名前搬走了，只在 GitHub 上跑所以本机看不见）、
以及 `docs/P0-1_核心抽离_design_note.md` 里的 `from core import contracts`。
**门禁不只防"未来会漂"，还会揭"现在就已经错了"。**

加 `scaffold-kernel-valid`（P5-a）时，同样的机制又逼出一个**全仓隐患**：8 个
`games/<名>/kernel.py` 的自举块少算一层，把 `world3d/` 推上了 `sys.path`，
于是 `world3d/platform/` **遮蔽标准库 `platform`**（pyglet 自己 `import platform`）——
`platform.system()` 直接 `AttributeError`。已修，并在 `scripts/check_imports.py` 落了
一条不变量把这类问题钉死：**仓库内自带 `__init__.py` 的目录不许出现在 `sys.path` 上**。

`--selfcheck` 会给每条门禁注入一个**针对性破坏**并断言它变红（16/16 全拦得住）——
"永远为绿的门禁等于装饰品"，所以这一步不能省。

**pre-commit 钩子**（可选，已在本仓库启用）：

```bat
git config core.hooksPath .githooks     # 装（已启用；--unset 即卸载）
```

钩子只跑 `verify.py`（<1 秒），**找不到 Python 就跳过而不是拦下**；
失败时打印下一步动作（`--fix` / `--verbose` / `--no-verify`）。
CI（`.github/workflows/ci.yml`）里挂了 `verify.py` + `check_imports.py` ——
pre-commit 只在装了钩子的那台机器上跑，CI 才是"无论如何都会跑"的那一份。

CI 现在是**两个 job**：`windows-latest`（发布口径：真窗口、真驱动，GL 用例真跑）与
`ubuntu-latest`（别人 clone 下来第一件事：没有显示器、没有 GPU 时还能不能跑）。
Linux 那份用 `PYGLET_HEADLESS=1` 让 pyglet 走 **EGL**（Mesa llvmpipe 软件渲染），
系统依赖清单是**从本仓锁定的 pyglet 源码里的 `load_library` 调用读出来的**，不是照抄博客。
⚠ 前提：workflow 只有 **GitHub** 会执行，而本仓当前 remote 只有 Gitee ⇒
要让它真跑，需要加一个 GitHub 镜像 remote（或改用 Gitee Go）。

## 脚手架（`scripts/new_test.py` / `scripts/new_kernel.py`）

### 新测试模块

```bat
:: <名字> 要小写字母开头、只含小写字母与下划线（如 mathx）
python scripts\new_test.py <名字>                  :: → tests/test_<名字>.py
python scripts\new_test.py <名字> --register       :: 顺便登记进 run_all_tests.py
python scripts\new_test.py <名字> --force          :: 覆盖已存在的文件
```

> demo 脚手架（`new_demo.py`）已随 demo 迁往独立仓 3dworld-demos。

### 新玩法内核 / 新配置树

```bat
:: 生成 world3d/games/<名字>/ + configs/cordis.<名字>.yml + tests/test_<名字>.py，
:: 并自动登记 registry.SERVICES / tree.WANT_<名字> / config_boot.CONFIGS / run_all_tests.MODULES
python scripts\new_kernel.py <名字>
python scripts\new_kernel.py <名字> --undo            :: 撤销（含从三张表摘除，逐字节还原）
```

内核脚手架比测试那个值钱：新增一个内核要同步 **7 处**，其中**三张表**（`host/registry.py`、
`host/tree.py`、`cli/config_boot.py`）漏一处就**静默失效** —— `registry` 漏了会让 cordis
的 Loader 吞掉构造期异常，只报"服务未就绪"（P0-3 为此查了半天）。所以那三张表交给脚本改，
**改完立刻 `ast.parse` 守卫**；`config_boot` 也已改成**表驱动 + 通用 runner**，
于是生成的纯逻辑内核**零代码**接入 headless 验收。

生成成功后，脚手架会把"**怎么先确认骨架能跑**"（测试命令，带真实名字）
和"**还差哪几处要同步**"一起打印出来。

> 文档里的 `<名字>` / `<模块>` 是**占位符**，不是命令 —— 门禁只校验能当命令跑的字面，
> 所以占位符一律写成 `<…>`（`<` 不在校验字符集里，天然被排除）。

模板**不是伪代码**：它带着那些用血换来的行 —— `sys.path` 显式引导块
（内核少算一层则会把 `world3d/` 推上 `sys.path`、遮蔽标准库 `platform`）、
测试模块的 runner + `ALL_*_TESTS_PASSED` 标记。
生成之后**紧接着跑 `verify.py`**：它会列出"还差哪几处"（测试清单 /
内核与配置树计数）—— 新增东西要同步的地方由机器列，不靠记性。

模板腐掉时**没人会发现**（直到下一个用它的人踩坑），所以两条门禁把它们钉在现状上：
`scaffold-templates-valid`（测试模板）与 `scaffold-kernel-valid`
（内核 / 配置树 / 测试模板 → 渲染 → `ast.parse` → **真 import** → **按 `run_generic`
的契约真跑到 `won`**）。

## 性能基准（真实帧率，demo 期实测留存）

`--bench` 会同时关掉 vsync 与 pyglet 的窗口重绘节流
（`pyglet.app.run(interval=…)` 默认 1/60 —— 这才是"59.9 fps"的真正来源），
所以量到的是真吞吐。1280×720 实测（演示程序已迁往独立仓，数据作为能力证据留存）：

| 项 | 均值 fps | p1 fps | 最慢帧 |
|----|---------:|-------:|-------:|
| demos 中最轻（无限隧道） | 1226 | 644 | 2.6 ms |
| demos 中最重（CPU 群集 boids） | 124 | 94 | 13.0 ms |
| **rayhold（全屏 SDF 光线步进 FPS，`--flag auto`）** | **86.8** | **24.2** | **74.1 ms** |
| terrain 换种子（同步重建，对照） | — | — | **1132 ms** |
| terrain 换种子（渐进重建，现状） | — | — | **62 ms** |
| voxel 换种子（同步重建，对照） | — | — | **352 ms** |
| voxel 换种子（渐进重建，现状） | — | — | **23 ms** |

渐进重建（`logic/terrain.py` 的 `emit_row` + `logic/voxel.py` 的分段 `build_mesh`）
把重建拆成"每帧最多几毫秒"：最慢帧从 1132ms 降到 62ms、352ms → 23ms，
重建期间旧地形/旧体素照常可玩；逐位一致性由 `test_terrain_row_emitter` 钉住。

**rayhold 的 86.8 fps** 是"全屏片元着色器逐像素走 SDF 光线步进"的成本
（迷宫距离场与命中判定同源，不开 ray marching 就没有这个品类）；`jank>20ms 5.7%`
主要来自敌人/光弹状态切换时的分支发散（核显上属预期）。

## 已知限制（现状）

- **演出美术风格是"玩具感"方块**：这是"零外部素材 + 稳健优先"的自觉
  选择。要更真实需要贴图/程序化细节/软阴影，属美术投入而非缺陷修复。
- **粒子在默认 4.5 万颗时看不出单颗的软衰减**（`engine/particles.py`）：加性混合下
  密度一高必然饱和，这是混合模式的性质而非 bug。演示给了数量档与尺寸档，
  降到几千颗即可看清单颗的圆形软边。
- **光线步进在弱核显上需要降分辨率**：本机 1280×720 均值 757fps（每像素最多
  96 步），更弱的集显建议 `--size 960x540`。
- **boids 的邻居按格子扫描顺序截断到 20 个**，不是严格 k-近邻：当前密度下
  角向中位 2.0°，实测无可见影响；改成严格 k-NN 要排序，收益近零、每帧多几毫秒。
- **地形近景是平滑顶点着色**（无细节纹理）、**体素世界的 H 切换在画面上几乎
  一样**（内部面本来就被挡住，实证靠 HUD 的面数）：前者是稳健取舍，后者是
  面剔除的必然表现。
- 抓帧时刻与粒子相位不逐位可复现（时间累加所致），不影响任何验收口径。

## 隔离原则

- 本项目依赖只装进 `.venv/`（已 gitignore），不污染全局环境；
- 升级 / 回滚只影响本项目：`pip install pyglet==<版本>`；
- cocos2d 等其他项目各建各的 venv，互不干扰。

## 素材来源

- pyglet 官方示例 `examples/opengl/opengl.py`（旋转圆环）与官方文档
  （`pyglet.image.buffer` 的 Framebuffer 用法、`ShaderProgram` 实例化接口）；
- pyglet 编程指南（math / graphics / rendering 章节）；
- .kkrieger 背景（Farbrausch/.theprodukkt，2004 Breakpoint 96k 冠军，
  .werkkzeug3 程序化生成、运行时约 300MB 内存、约 10 分钟流程——
  用于场景与分幕的内容设计参考，未使用其任何代码或素材）；
- Stack Overflow「How can I create 3D with pyglet?」（黑屏 = 默认正交投影裁剪）。

## 许可

MIT，见 [LICENSE](LICENSE)。

---

## 仓库索引标注（2026-10-19 · siwpan 统一治理）

> 3dworld 程序化 3D 场景/动画引擎（现行，独立仓 siwpan/3dworld，PyPI 包名 `3dworld`）；
> 本地对应 `<PATH>`。演示程序（39 个 + 成片/截图/验收工具）在独立仓
> siwpan/3dworld-demos；本标注为发布面统一追加，不改变原有内容。
