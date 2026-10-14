# 3dworld — pyglet 三维世界示例

用 Python + pyglet 从零构建三维世界与**动画演出**：时间轴 / 关键帧插值 /
层级骨骼 / 程序化场景，外加一组单文件 3D 示例（`examples/`）。

当前面貌：

- `main.py`（`pyglet_3d_demo`）—— **48 秒五幕演出**：环形立方体升起 → 环形波 →
  机械臂逆运动学取放宝石 → 螺旋柱 → 归位，双相机模式 + 幕字幕 + 时间轴面板；
- `kkrieger_demo.py` —— **150 秒六幕程序化走廊漫游 + 程序化配乐**（致敬 .kkrieger）：
  走廊舱门/管道/货箱/频闪 → 大厅纪念碑/全息扫描/升降平台 → 核心过载 →
  撤离时逐段熄灯 → 重启突入（核心爆发白闪）→ 白闪传送至环面结反应堆圣殿；
- `examples/` —— **37 个独立示例与工具**：无限隧道、SDF 光线步进、GPU 实例化粒子、
  太阳系、程序化地形飞行、体素面剔除、水面波动、3D 鸟群、阴影贴图、
  方块世界、引擎综合展示、材质实验室、《诺德之境》、可漫步场景（S1 场景交互化）、
  方块世界可交互版（挖掘/放置 + 区块化重建）、KID-A WALKER 3D（无限走廊 + 能量/碎片）、
  《山》（Mountain：漂浮孤峰 + 昼夜气象 + 人格测验 + 键盘钢琴）。

## 两条 pyglet 技术路线（历史版本，仍保留）

| 文件 | 适配版本 | 渲染管线 | 适用环境 |
|------|----------|----------|----------|
| `main.py`（= `pyglet_3d_demo.py`） | pyglet 2.x | 可编程管线（Mat4 / Batch，来自 M1 分层重构） | 新项目、独立 venv |
| `pyglet_3d_demo_1x.py` | pyglet 1.x | 固定管线（gluPerspective 等） | 与 cocos2d 共存的环境 |

> 注意：pyglet 1.x 与 2.x API 不互通（2.x 已移除 GLU），教程代码不可混用。
> 1.x 版是 M1 重构前的单文件形态，仅作兼容保留。

## 项目结构

```
world3d/              **唯一的顶层包**（P2-4 收口；发行名 3dworld / 导入名 world3d）
  __init__.py         库入口：把引擎公共面转发出来（公共面由 engine.__all__ 收口）
  compat.py           旧路径兼容层（P2-4 过渡版）：opt-in 的元路径别名，
                      install 之后 `import engine.csm` 与 `import world3d.engine.csm`
                      拿到**同一个模块对象**；本模块是过渡件，外界迁完即删
  _cordis_locate.py   cordis 定位：先试已安装的，再退回 vendored `vendor/cordis/src`
  platform/           **平台层骨架**（P2-1）：窗口 / DPI / 命令行 / HUD / 截图 / 主循环
    app.py            DemoApp · run · orbit_camera_input …（37 个 demo 的统一入口）
  core/               核心层：窗口 / 渲染 / 几何 / 资源（**允许** pyglet.gl）
    geometry.py        立方体/盒体/地面/静态合并盒体构建（ShaderProgram.vertex_list）
    resources.py       外部模型加载（当前 .obj）
    contracts.py       契约注册表 + 全仓可达性检查（P0-5）
  logic/              逻辑层：场景与动画（**禁止** pyglet.gl，可无窗口单测）
    animation.py       时间轴/关键帧/插值（glTF TRS 对齐 + tint/glow 材质通道）
    choreography.py    main.py 的 48s 五幕编排（含两连杆逆运动学）
    kkrieger.py        kkrieger 的 150s 六幕编排（相机脚本 + 场景动画）
    camera.py          轨道相机状态机 + 手写 look-at 矩阵
    playback.py        播放控制器：播放/暂停/循环/倍速/seek
    skeleton.py        节点级层级动画（转台→大臂→小臂→夹爪）
  engine/              引擎层（P3-2 起引擎级件陆续下沉；见 docs/引擎设计.md）
    fsm.py             确定性有限状态机（时间窗 + 事件转移，P0-5 自研）
    orchestration.py   事件总线 + 声明式 when→then 级联（P0-5 自研）
    baked_mesh.py      烘焙明暗的单位立方体/球（P0-3 从内核上提）
    greedy_mesh.py     单层体素贪心合批（P0-1 从内核上提）
    csm.py / volumetrics.py / dof.py / motionblur.py   ← ④ 轨道的四个渲染模块
    framegraph.py      帧图：声明 → 编译期校验 → 剔除 → 拓扑排序 → 别名（P3）
  presentation/        表现层：装配窗口事件 → 逻辑层 → 渲染
    app.py             WorldApp：48s 演出装配（双相机模式 + 夹持混合）
    kkrieger_app.py    KKApp：150s 漫游装配 + 跳帧截图 + 音频同步
    hud.py             幕字幕（淡入/停留/淡出）+ 状态行
    timeline_ui.py     底部时间轴面板（播放/循环/拖动/幕标记）
  games/               玩法内核层：games/<名>/kernel.py，9 个 Cordis 内核
  host/                宿主层：tree.py（配置树挂载/就绪等待）· registry.py（服务短名表）
                       pipeline.py（场景校验流水线：checker→loader→reporter）
  cli/                 引擎 CLI（`python -m world3d.cli.<名>`）
    config_boot.py     每棵配置树的 headless 验收（与 demo 共用 host.tree）
    frame_plan.py      帧图门禁：16 种效果组合的计划必须合法且顺序正确
examples/              37 个单文件示例与工具（详见 examples/README.md）
configs/               10 棵配置树（cordis.*.yml）：
                       声明本次挂哪些服务、各带什么 config
scripts/
  verify.py            机械门禁 16 条：同一事实写在两处必须相等（<1 秒，pre-commit 会跑）
  docs_index.py        docs/README.md 的生成器（门禁 docs-index-fresh 核对新鲜度）
  new_demo.py          脚手架：生成能开箱跑的新 demo / 新测试模块（模板被门禁钉住）
  new_kernel.py        脚手架：生成新玩法内核 + 配置树 + 单测，并登记三张表（--undo 可撤销）
  check_imports.py     全仓可达性门禁：world3d/ 下每个模块都 import 一遍（+ sys.path 不变量）
  verify_demos.py      demos 批量验收（冒烟 + 截图 + 退出码）
  run_all_tests.py     14 个测试模块的聚合 runner
  record_all.py        成片批量录制（ffmpeg）
  migrate_p2_4.py      P2-4 的一次性迁移工具（保留以便复查改了什么）
tests/               逻辑层无窗口单测：python -m tests.run_tests
main.py / kkrieger_demo.py / pyglet_3d_demo.py(兼容 shim)   入口
docs/                设计文档 + 渲染验证截图
```

依赖方向：presentation → logic → core，反向禁止。

## engine/ —— 核心引擎（2026-10 抽取）

零业务依赖的可复用引擎层，`core/logic/presentation` 的公共件已下沉至此：

- **渲染**：前向 PBR（金属-粗糙度）+ IBL（程序化天空烘焙：辐照度图 / GGX
  预过滤 mip 链 / BRDF LUT）+ MRT（HDR 颜色 + 视空间法线/深度）
- **后处理**：SSAO（半分辨率半球核）→ Bloom（软膝盖阈值 + Karis + 降采样/
  上采样金字塔）→ 曝光 / ACES / 暗角 / gamma
- **资源与场景**：glTF 2.0 / GLB 加载（sparse、动画、PBR 材质）、Node 场景图、
  GPU 实例化（一次 draw call 画 N 个）、视锥剔除（Gribb-Hartmann + p-顶点）
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

综合演示（把上面全部跑一遍）：

```bat
python examples/engine_showcase.py            正常运行
python examples/engine_showcase.py --bench 5  5 秒基准（实测 ~115 fps @720p）
python examples/engine_showcase.py --shot     抓帧到 docs/ 后关闭
```

按键：`F2` 性能面板 · `B` Bloom · `O` SSAO · `V` 相机切换 · `1/2/3` 调试视图 ·
`G` 实例阵列开关 · `K` 保存场景（改 JSON 可热重载） · 点击拾取物体。

设计与踩坑记录见 **docs/引擎设计.md**；引擎测试：`python -m tests.test_engine`。

## 快速开始

```bat
:: 方式 A（推荐，当作项目开发 / 跑示例）：项目级 venv
python -m venv .venv
.venv\Scripts\activate
pip install -e .            :: 依赖与元数据以 pyproject.toml 为唯一来源
                            :: （requirements.txt 已改为转发它，等价于旧用法）
python main.py

:: 方式 B（当作**库**装进任意环境）
pip install .               :: 或 pip install "git+https://gitee.com/siwpan/3dworld.git"
python -c "import world3d; print(world3d.__version__)"
world3d-info                :: 自检：版本 / 依赖 / GL 能力 / cordis / 配置树位置
```

**命名**（P1 库化定）：**发行名 `3dworld` / 导入包名 `world3d`** —— Python 的导入包名不能以
数字开头（`import 3dworld` 是语法错，`'3dworld'.isidentifier()` 为 `False`），而 PEP 508
发行名允许。`world3d` 目前是**薄门面**（把引擎公共面从 `engine` 转发出来），
`engine` / `core` / `host` / `games` 等仍是独立顶层包（旧导入路径零破坏）；
P2「分层归位」会把它们收进 `world3d.*` 命名空间。

**库与仓库的边界**：`pip install` 装的是**引擎库**（engine/core/host/cli/games + vendored cordis）。
`examples/`（示例）、`scripts/`（批量验收 / 离线录制 / 打包）、`configs/`（配置树）、`assets/` 属
**仓库资产**，不随 wheel 分发 —— 所以：

- 面向仓库的工具用 `python scripts/<名>.py`（如 `scripts/verify_demos.py`、`scripts/record_all.py`）；
- `world3d-config` / `world3d-pipeline` 需要 `configs/`：从仓库运行（或 editable 安装）天然可用，
  非 editable 安装下用环境变量 `WORLD3D_CONFIGS` 指向配置树目录。

- 若你的环境绑死 cocos2d（pyglet 1.x），无需安装任何东西，
  直接用系统 Python 跑 `pyglet_3d_demo_1x.py` 即可。

## main.py —— 48 秒五幕演出

```bat
python main.py                    正常运行（循环播放）
python main.py --smoke            冒烟测试：2 秒后自动关闭
python main.py --shot             渲染四张验证截图到 docs/ 后自动关闭
python main.py --shot --only 2    只截第 3 张（调试单帧）
```

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

## kkrieger 致敬 Demo —— 150 秒六幕程序化走廊漫游（含程序化配乐）

参考 2004 年 Breakpoint 96k 冠军作品 .kkrieger（Farbrausch/.theprodukkt）的
"程序化生成"精神：**零外部素材**——金属板、灯带、舱门、管道、货箱、立柱、
五环纪念碑、全息扫描框、升降平台、能量束、风扇、无人机、数据流光带、环面结
反应堆、爆发碎片全部由代码即时生成，面板颜色用确定性哈希抖动模拟"程序化纹理"；
**配乐同样是程序生成的**（`logic/kkrieger_audio.py` 离线合成 150s 电子乐，
首次运行渲染缓存到 `assets/kkrieger_ost_150s.wav`，音画共用同一套时间常量）。

```bat
python kkrieger_demo.py            正常运行（SPACE 暂停 / R 重播 / N,P 切幕 / M 静音）
python kkrieger_demo.py --smoke    冒烟测试（2.5s 自动关闭）
python kkrieger_demo.py --shot     抓一组验证截图（9 张）到 docs/（跳帧采样，约 3s）
```

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

截图：`docs/kkrieger_corridor.png` / `_atrium.png` / `_core.png` / `_blackout.png`
/ `_sprint.png` / `_converge.png` / `_flash.png` / `_ignition.png` / `_sanctum.png`

> 已知平台坑（实测 pyglet 2.1.16 / Win10 150% DPI）：`dpi_scaling` 默认
> "platform" 模式下实际 framebuffer = 请求尺寸，但 viewport setter 会乘
> scale → 渲染放大裁切；本 Demo 统一设 `pyglet.options['dpi_scaling'] = 'real'`。

## examples/ —— 37 个单文件 3D 示例与工具

```bat
python examples\demoscene_tunnel.py --smoke     冒烟
python examples\solar_system.py                 1..8 聚焦行星
python scripts\verify_demos.py               批量验收（冒烟 + 截图 + 退出码）
```

| demo | 技术点 |
|------|--------|
| `demoscene_tunnel.py` | 纯片元着色器：极坐标隧道 + 程序化面板 + 色散/扫描线 |
| `raymarch_sdf.py` | SDF 光线步进：smooth-min 融合 + 软阴影 + AO |
| `particles_gpu.py` | 原生实例化（`vertex_list_instanced`）4.5 万粒子，全 GPU 动画 |
| `solar_system.py` | 层级变换 + 压缩轨道力学 + 土星环 + 实例化小行星带 |
| `terrain_flyover.py` | 值噪声 + fBm 高程图、分层着色、低空航线飞行 |
| `voxel_terrain.py` | 体素面剔除（实测剔除 85% 面）+ 线框对比 |
| `water_waves.py` | Gerstner 波顶点位移 + 解析法线 + 菲涅尔天空 |
| `boids.py` | 逻辑层纯数学群集 + 实例化渲染（顶点着色器按速度定向） |
| `shadow_map.py` | 两趟渲染 / render-to-texture：光源深度图 + PCF 阴影 |
| `minecraft_style.py` | 程序化 16×16 像素材质图集 + 昼夜统一光照 + 实例化下落方块；五幕 48s 短片（**方块世界风格致敬，零外部素材**） |
| `engine_showcase.py` | 引擎综合巡礼：PBR/IBL + Bloom + SSAO + 实例化 + 视锥剔除 + 拾取与物理 + 性能面板 + 场景序列化 |
| `material_lab.py` | 材质实验室：新建模（车削/管扫/环面结）× 程序化 PBR 纹理（拉丝/大理石/电路板等 5 套） |
| `nordic_saga.py` | **《诺德之境》60s 程序化动画**：上古卷轴风格雪原遗迹、巨龙与龙语古墙，含程序化配乐 |
| `scene_walker.py` | **可漫步场景（交互化）**：S1 场景 JSON → 第一人称物理漫步（固定步长）+ 马尔可夫演出变体 + 拾取/E 互动 + SFX/自适应 BGM + 场景热重载 |
| `blockworld.py` | **方块世界 · 可交互版**：第一人称漫步/跳跃 + DDA 准星拾取 + 左键挖/右键放 + hotbar + 区块化网格重建 |
| `scene_viewer.py` | 场景查看器（**工具**，非定时演出）：加载 `--file` 的 JSON 并热重载，是「LLM 场景生成工作流」的"看"这一端 |
| `mountain.py` | **《山》**（致敬 David OReilly 的 Mountain，观察式）：复用 terrain_flyover 噪声管线生成**悬浮孤峰**（顶/底两面闭合）+ 片元级动态光照昼夜 + 云雨雪气象 + 坠落物体/生长植物 + 哲学独白叠层 + **人格测验嵌进生成参数** + 键盘钢琴（即时合成）+ 流星崩解重生 |
| `godhand.py` | **《神之手》**（P1-①，致敬 Populous）：鼠标「抬山/挖海」实时改写地形高度场，淹没敌方聚落即胜（Cordis 内核插件） |
| `tower_defense.py` | **《戍卫之路》**（P1-② 塔防内核，致敬 Warcraft/Dune II）：3 类塔 + 3 类敌人 + 5 波 + 经济循环（Cordis 内核插件） |
| `rayhold.py` | **《迷堡枪影》**（P1-③ 射线 FPS，致敬 Wolfenstein 3D/Doom）：程序化迷宫 + 全屏 SDF 光线步进 + hitscan 枪 + 敌人状态机（Cordis 内核插件） |
| `skinned_creature.py` | **蒙皮骨骼动画**（P2-3）：加载程序化生成的带 skin 的 glTF「骨节蛇」，蒙皮顶点着色器 + 关节矩阵管线驱动骨骼链摆动 |
| `gpu_cull_field.py` | **GPU 驱动渲染**（P3-1）：4096 实例，剔除 + LOD + 命令生成全在 compute 里；CPU 每帧只做 1 次 dispatch + 3 次 indirect draw；F2 切 GPU/CPU、F3 切 LOD |
| `wboit_demo.py` | **顺序无关透明**（P2-2）：Weighted Blended OIT —— 4 个相交半透明球，一趟写 accum/reveal 两张 MRT 再合成，无需排序；S 切 WBOIT/排序、O 反转顺序 |
| `pointcloud_edl.py` | **点云 + EDL**（P2-4）：程序化点云（地形/双螺旋）+ `gl_PointSize` 圆盘点 + EDL 深度轮廓增强；E 开关 EDL |
| `stillhall.py` | **《静馆》**（§14 节点式漫游解谜，致敬 Myst）：固定机位点击热点换景/取物/开机关 + 物品栏 + 谜题状态机；场景 JSON 与 scene_viewer 同 schema 且热重载 |
| `voidward.py` | **《星阑》**（§15 Elite 式太空贸易）：1 星系 + 5 货确定性市场（事件冲击）+ 惯性飞行 + 海盗拦截 + 交易/升级；资产达标 10000 → won |
| `pulsefall.py` | **《落音》**（§16 下落式音击）：4 轨道（DFJK）+ 3 谱面（BPM 90/120/150 种子复现）+ 判定 3 档 + 血量/连击/结算；音乐由 audio 核按谱面程序化合成 |
| `swarmward.py` | **《弹潮》**（P1-⑤ 全向生存弹幕，致敬 Robotron 2084）：固定屏双摇杆 + 程序化编波（难度单调）+ **boss 波**（三阶段 + 血条 + 五图案弹幕生成器）+ 粒子/编队两核消费者 |
| `boxward.py` | **《归仓》**（P1-④ 方块解谜，致敬 Sokoban）：光栅贪心合批 + **正交等距视角**（45° 偏航 + 45° 俯角，Q/E 切档 / P 调俯角） + **BFS 求解器 auto-play** + 程序化关卡 + 关卡编辑器 |
| `kid_a_walker_3d.py` | **KID-A WALKER 3D**（arcade 伪 3D 重制为真 3D）：41 房间模版 → 区块无限走廊 + 能量/碎片系统 |
| `director.py` | **可视化导演台**（**工具**）：关键帧编排相机运镜 / 环境光 / 物件 TRS，导出「演出 JSON」并可回放与录制（`record_all.py` 的 `director_20s`）；`--shot-json` 载入既有演出 |

详见 [examples/README.md](examples/README.md)（含统一命令行与踩坑清单）。

## 测试与验收

```bat
python -m tests.run_tests           逻辑层无窗口单测（62 项）
python -m tests.test_cores          共同核契约（17 项，含 P1-⑤ 玩法模块）
python -m tests.test_engine         引擎层无窗口单测（30 项，含 model uniform 作用域 + 多网格连续三帧逐字节一致）
python -m tests.test_skin           蒙皮骨骼动画（8 项）
python -m tests.test_gpu_cull       GPU 驱动渲染（7 项，含"GPU 剔除 == CPU 剔除"逐实例比对）
python -m tests.test_p2_render      PCSS / WBOIT / 点云+EDL（7 项）
python -m tests.test_timeline_ui    底部时间轴面板（9 项）
python -m tests.test_brickward      《砖境》机关内核：BQM 致敬 B1~B3（65 项，零 GL）
python -m tests.test_csm            级联阴影 CSM（20 项：分割/角点/光盒/纹素吸附/GLSL 展开）
python -m tests.test_volumetrics    体积雾/光轴（12 项：闭式积分可加性/HG 归一/抖动同式/GLSL 组合）
python -m tests.test_dof            景深 DoF（12 项：CoC/超焦距/景深端点恒等式/散景面积均匀）
python -m tests.test_motionblur     运动模糊（9 项：静止零运动/1÷z 律/纯旋转与深度无关/三模块共存）
python -m tests.test_framegraph     帧图（17 项：物理顺序/剔除/直通/别名/环与死链校验）
python -m tests.test_compat         旧路径兼容层（6 项：install 前后不可用/同一性/不被劫持）
python -m world3d.cli.frame_plan --all-toggles  帧图门禁：16 种效果组合的计划都必须合法且顺序正确
python -m world3d.cli.config_boot all   9 个内核 + 13 共同核全部 headless 验收（10 棵树，逐项 OK 标记）
python scripts\check_imports.py          全仓可达性门禁：world3d/ 下 117 个模块逐个 import
                                        （单测只覆盖被引用的模块，这个是「改漏」的唯一防线）
python main.py --shot               主线演出渲染验证（4 张）
python kkrieger_demo.py --shot      漫游渲染验证（9 张）
python scripts\verify_demos.py      37 个 demo 逐个冒烟 + 截图
python scripts\verify_demos.py --bench 3   37 个 demo 的真实帧率（vsync 与重绘节流全关）
```

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
**全靠手改**，而手改每次都可能漏一处（示例数在 README 里就有 6 处）。

```bat
python scripts\verify.py              # 16 条门禁（不联网、不跑构建、不改文件）
python scripts\verify.py --list       # 只列名字与说明
python scripts\verify.py --verbose    # 每条的证据（"6 处断言 == 37 个 demo"这种）
python scripts\verify.py --fix        # 重生成生成物（docs/README.md）
python scripts\verify.py --selfcheck  # 沙箱里逐条注入破坏，证明门禁拦得住
```

| 门禁 | 断言 |
|------|------|
| `readme-demo-count` | README 里**每一处**示例数 == `examples/*.py` 真实可发现数 |
| `readme-test-modules` | README 测试命令表 ↔ `run_all_tests.MODULES`（**双向**） |
| `readme-test-counts` | README 每行的「N 项」== 该模块声明的用例数（两种真实形态都认） |
| `readme-config-count` / `-kernel-count` / `-core-count` | 配置树 / 内核 / 共同核计数 |
| `examples-index-pairs` | `examples/README.md` 表格 ↔ `examples/*.py`（**双向**） |
| `doc-links` | README / docs / examples 里的**相对链接**都指得到真实文件 |
| `docs-index-fresh` | `docs/README.md` == `scripts/docs_index.py` 重新生成的结果（新鲜度） |
| `commands-exist` | 文档里写的每条 `python -m …` / `python <文件名>.py` 目标都存在（模板占位符不算） |
| `pyrefs-exist` | 文档与 CI 里**每一处**一行式 `python -c "import <模块>"` 都指向**真实存在的模块** |
| `scaffold-templates-valid` | demo / 测试脚手架模板渲染 → `ast.parse` → **真 import** → 关键片段齐全 |
| `scaffold-kernel-valid` | 内核 / 配置树 / 测试模板渲染 → `ast.parse` → **真 import** → **按 `run_generic` 的契约跑到 won** |
| `workflows-valid` | `.github/workflows/*.yml` 可解析、结构完整（`runs-on`/`steps`/`uses` 钉版本）、里面的命令可达 |
| `cli-usage-names` | `cli/*.py` 用法 docstring 里的模块名 == 真实模块名 |
| `readme-tree-real` | README「项目结构」树里列出的路径都真实存在 |

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

## 脚手架（`scripts/new_demo.py` / `scripts/new_kernel.py`）

### 新 demo / 新测试模块

```bat
:: <名字> 要小写字母开头、只含小写字母与下划线（如 starfield / mathx）
python scripts\new_demo.py <名字>                     :: → examples/<名字>.py
python scripts\new_demo.py --test <名字> --register   :: → tests/test_<名字>.py + 登记进 runner
python scripts\new_demo.py <名字> --force             :: 覆盖已存在的文件
```

### 新玩法内核 / 新配置树

```bat
:: 生成 world3d/games/<名字>/ + configs/cordis.<名字>.yml + tests/test_<名字>.py，
:: 并自动登记 registry.SERVICES / tree.WANT_<名字> / config_boot.CONFIGS / run_all_tests.MODULES
python scripts\new_kernel.py <名字>
python scripts\new_kernel.py <名字> --undo            :: 撤销（含从三张表摘除，逐字节还原）
```

内核脚手架比 demo 那个值钱：新增一个内核要同步 **7 处**，其中**三张表**（`host/registry.py`、
`host/tree.py`、`cli/config_boot.py`）漏一处就**静默失效** —— `registry` 漏了会让 cordis
的 Loader 吞掉构造期异常，只报"服务未就绪"（P0-3 为此查了半天）。所以那三张表交给脚本改，
**改完立刻 `ast.parse` 守卫**；`config_boot` 也已改成**表驱动 + 通用 runner**，
于是生成的纯逻辑内核**零代码**接入 headless 验收。

生成成功后，脚手架会把"**怎么先确认骨架能跑**"（冒烟命令 / 测试命令，都带真实名字）
和"**还差哪几处要同步**"一起打印出来。

> 文档里的 `<名字>` / `<模块>` 是**占位符**，不是命令 —— 门禁只校验能当命令跑的字面，
> 所以占位符一律写成 `<…>`（`<` 不在校验字符集里，天然被排除）。

模板**不是伪代码**：它带着那些用血换来的行 —— `sys.path` 显式引导块
（demo 少这行会让**所有** demo 一起 `ModuleNotFoundError`；内核少算一层则会把
`world3d/` 推上 `sys.path`、遮蔽标准库 `platform`）、`TITLE/HELP/SHOTS` 与
`setup/update/render` 钩子、测试模块的 runner + `ALL_*_TESTS_PASSED` 标记。
生成之后**紧接着跑 `verify.py`**：它会列出"还差哪几处"（示例索引行 / README 的示例数 /
测试清单 / 内核与配置树计数）—— 新增东西要同步的地方由机器列，不靠记性。

模板腐掉时**没人会发现**（直到下一个用它的人踩坑），所以两条门禁把它们钉在现状上：
`scaffold-templates-valid`（demo / 测试模板）与 `scaffold-kernel-valid`
（内核 / 配置树 / 测试模板 → 渲染 → `ast.parse` → **真 import** → **按 `run_generic`
的契约真跑到 `won`**）。

## 性能基准（真实帧率）

`--bench` 会同时关掉 vsync 与 pyglet 的窗口重绘节流
（`pyglet.app.run(interval=…)` 默认 1/60 —— 这才是"59.9 fps"的真正来源），
所以量到的是真吞吐。1280×720 实测：

| 项 | 均值 fps | p1 fps | 最慢帧 |
|----|---------:|-------:|-------:|
| demos 中最轻（无限隧道） | 1226 | 644 | 2.6 ms |
| demos 中最重（CPU 群集 boids） | 124 | 94 | 13.0 ms |
| **rayhold（全屏 SDF 光线步进 FPS，`--flag auto`）** | **86.8** | **24.2** | **74.1 ms** |
| terrain 换种子（同步重建，对照） | — | — | **1132 ms** |
| terrain 换种子（渐进重建，现状） | — | — | **62 ms** |
| voxel 换种子（同步重建，对照） | — | — | **352 ms** |
| voxel 换种子（渐进重建，现状） | — | — | **23 ms** |

最后两行是本轮修掉的"按 `R` 卡一下"：把重建拆成"每帧最多几毫秒"的渐进式，
最慢帧从 1132ms 降到 62ms；voxel 那边同理，352ms → 23ms（其中一半功劳来自
**把"全部面"对照网格改成按 `H` 懒建** —— 它有 11MB 顶点数据，之前每次重建
都要重传）。剩下的几十毫秒是换网格时的显存上传，一次性，重建期间旧地形/
旧体素照常可玩。

**rayhold 的 86.8 fps** 是"全屏片元着色器逐像素走 SDF 光线步进"的成本
（迷宫距离场与命中判定同源，不开 ray marching 就没有这个品类）；`jank>20ms 5.7%`
主要来自敌人/光弹状态切换时的分支发散（核显上属预期）。录制产物见
`recordings/rayhold_60s.mp4`（`scripts/record_all.py --only rayhold_60s`）。

## 已知限制（现状）

- **主线 demo 的美术风格是"玩具感"方块**：这是"零外部素材 + 稳健优先"的自觉
  选择。要更真实需要贴图/程序化细节/软阴影，属美术投入而非缺陷修复。
- **粒子在默认 4.5 万颗时看不出单颗的软衰减**：加性混合下密度一高必然饱和，
  这是混合模式的性质而非 bug。demo 现在给了数量档（`,`/`.`）与尺寸档（`[`/`]`），
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

