# 3dworld — pyglet 三维世界示例

用 Python + pyglet 从零构建三维世界与**动画演出**：时间轴 / 关键帧插值 /
层级骨骼 / 程序化场景，外加一组单文件 3D 示例（`demos/`）。

当前面貌：

- `main.py`（`pyglet_3d_demo`）—— **48 秒五幕演出**：环形立方体升起 → 环形波 →
  机械臂逆运动学取放宝石 → 螺旋柱 → 归位，双相机模式 + 幕字幕 + 时间轴面板；
- `kkrieger_demo.py` —— **120 秒五幕程序化走廊漫游 + 程序化配乐**（致敬 .kkrieger）：
  走廊舱门/管道/货箱/频闪 → 大厅纪念碑/全息扫描/升降平台 → 核心过载 →
  撤离时逐段熄灯；
- `demos/` —— **10 个独立示例**：无限隧道、SDF 光线步进、GPU 实例化粒子、
  太阳系、程序化地形飞行、体素面剔除、水面波动、3D 鸟群、阴影贴图。

## 两条 pyglet 技术路线（历史版本，仍保留）

| 文件 | 适配版本 | 渲染管线 | 适用环境 |
|------|----------|----------|----------|
| `main.py`（= `pyglet_3d_demo.py`） | pyglet 2.x | 可编程管线（Mat4 / Batch，来自 M1 分层重构） | 新项目、独立 venv |
| `pyglet_3d_demo_1x.py` | pyglet 1.x | 固定管线（gluPerspective 等） | 与 cocos2d 共存的环境 |

> 注意：pyglet 1.x 与 2.x API 不互通（2.x 已移除 GLU），教程代码不可混用。
> 1.x 版是 M1 重构前的单文件形态，仅作兼容保留。

## 项目结构

```
core/                核心层：窗口/渲染/几何/资源（允许 pyglet.gl）
  geometry.py          立方体/盒体/地面/静态合并盒体构建（ShaderProgram.vertex_list）
  renderer.py          世界渲染器：地面+静态装饰 / 动画立方体(含 tint,glow) / 外部模型 / 机械臂
  resources.py         外部模型加载（当前 .obj；pyglet 升级后 .glb 即插即用）
  kkrieger_scene.py    kkrieger 场景：程序化走廊+大厅、舱门/灯带/全息框/能量束、雾效着色器
logic/               逻辑层：场景与动画（禁止 pyglet.gl，可无窗口单测）
  animation.py         时间轴/关键帧/插值（glTF TRS 对齐 + tint/glow 材质通道）
  choreography.py      main.py 的 48s 五幕编排（含两连杆逆运动学）
  kkrieger.py          kkrieger 的 120s 五幕编排（相机脚本 + 场景动画）
  kkrieger_audio.py    kkrieger 程序化配乐（离线合成 120s 电子乐，音画同源对齐）
  camera.py            轨道相机状态机 + 手写 look-at 矩阵
  playback.py          播放控制器：播放/暂停/循环/倍速/seek
  skeleton.py          节点级层级动画（转台→大臂→小臂→夹爪）
  events.py            事件总线（pyglet EventDispatcher）
  boids.py  waves.py   demos 的纯数学模块（群集 / Gerstner 波）
presentation/        表现层：装配窗口事件 → 逻辑层 → 渲染
  app.py               WorldApp：48s 演出装配（双相机模式 + 夹持混合）
  kkrieger_app.py      KKApp：120s 漫游装配 + 跳帧截图 + 音频同步
  hud.py               幕字幕（淡入/停留/淡出）+ 状态行
  timeline_ui.py       底部时间轴面板（播放/循环/拖动/幕标记）
  shot.py              离屏抓帧（glReadPixels，DPI 无关）
demos/               10 个单文件示例 + 公共骨架 _harness.py（详见 demos/README.md）
scripts/
  make_gem_obj.py      程序化生成 assets/gem.obj
  verify_demos.py      demos 批量验收（冒烟 + 截图 + 退出码）
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

综合演示（把上面全部跑一遍）：

```bat
python demos/engine_showcase.py            正常运行
python demos/engine_showcase.py --bench 5  5 秒基准（实测 ~115 fps @720p）
python demos/engine_showcase.py --shot     抓帧到 docs/ 后关闭
```

按键：`F2` 性能面板 · `B` Bloom · `O` SSAO · `V` 相机切换 · `1/2/3` 调试视图 ·
`G` 实例阵列开关 · `K` 保存场景（改 JSON 可热重载） · 点击拾取物体。

设计与踩坑记录见 **docs/引擎设计.md**；引擎测试：`python -m tests.test_engine`。

## 快速开始（推荐：项目级 venv 隔离）

```bat
:: 在项目根目录执行（路径不含盘符，随处可用）
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

- `requirements.txt` 锁定 pyglet 2.1.16。
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

## kkrieger 致敬 Demo —— 120 秒五幕程序化走廊漫游（含程序化配乐）

参考 2004 年 Breakpoint 96k 冠军作品 .kkrieger（Farbrausch/.theprodukkt）的
"程序化生成"精神：**零外部素材**——金属板、灯带、舱门、管道、货箱、立柱、
五环纪念碑、全息扫描框、升降平台、能量束、风扇、无人机、数据流光带、爆发
碎片全部由代码即时生成，面板颜色用确定性哈希抖动模拟"程序化纹理"；
**配乐同样是程序生成的**（`logic/kkrieger_audio.py` 离线合成 120s 电子乐，
首次运行渲染缓存到 `assets/kkrieger_ost.wav`，音画共用同一套时间常量）。

```bat
python kkrieger_demo.py            正常运行（SPACE 暂停 / R 重播 / N,P 切幕 / M 静音）
python kkrieger_demo.py --smoke    冒烟测试（2.5s 自动关闭）
python kkrieger_demo.py --shot     各幕抓一张截图到 docs/（跳帧采样，约 3s）
```

| 幕 | 时间 | 内容 |
|----|------|------|
| 一 · 走廊 | 0–24s | 俯冲进走廊，三道舱门依次开启，管道/货箱/警示频闪入画 |
| 二 · 大厅 | 24–48s | 纪念碑唤醒：五环转动、全息扫描框上下扫、升降平台升起 |
| 三 · 核心 | 48–72s | 过载：环加速、核心脉冲、能量束增亮、镜头逼近至"贴脸" |
| 四 · 撤离 | 72–96s | 后撤，走廊灯带与大厅灯环逐段熄灭，拉回开场高空帧 |
| 五 · 突入 | 96–120s | 重启：心跳鼓点，高速穿越（舱门速开速闭、数据流光带跑马），全件过载，核心爆发白闪 + 碎片飞散，冲击波退出归位 |

音频：`M` 静音；seek（R/N/P/←→）时音频同步跳转；循环无缝（源时长=时间轴时长）。
配乐结构与画面对位：开门气浪在开门时刻、核心亚低频脉冲与视觉脉冲同相、
灯带熄灭的轻响逐段对应、117.5s 爆发低频坠落。

截图：`docs/kkrieger_corridor.png` / `_atrium.png` / `_core.png` / `_blackout.png`
/ `_sprint.png` / `_converge.png` / `_flash.png`

> 已知平台坑（实测 pyglet 2.1.16 / Win10 150% DPI）：`dpi_scaling` 默认
> "platform" 模式下实际 framebuffer = 请求尺寸，但 viewport setter 会乘
> scale → 渲染放大裁切；本 Demo 统一设 `pyglet.options['dpi_scaling'] = 'real'`。

## demos/ —— 10 个单文件 3D 示例

```bat
python demos\demoscene_tunnel.py --smoke     冒烟
python demos\solar_system.py                 1..8 聚焦行星
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

详见 [demos/README.md](demos/README.md)（含统一命令行与踩坑清单）。

## 测试与验收

```bat
python -m tests.run_tests           逻辑层无窗口单测（30 项）python main.py --shot               主线演出渲染验证（4 张）
python kkrieger_demo.py --shot      漫游渲染验证（4 张）
python scripts\verify_demos.py      10 个 demo 逐个冒烟 + 截图
python scripts\verify_demos.py --bench 3   10 个 demo 的真实帧率（vsync 与重绘节流全关）
```

单测覆盖：相机位姿与钳制（含**自定义范围**与非法范围拒绝）、look-at 矩阵性质、
四元数 slerp、Sampler/Timeline 语义、播放控制、UI 命中、OBJ 资产解码、
骨骼层级传播、**五幕编排的循环连续性与幕边界连续性**、**机械臂逆运动学落点**、
kkrieger 四幕的循环连续性与舱门/平台/灯带状态机、**镜头穿模检查**
（机位与立柱/门洞/顶棚/纪念碑的距离）、boids 可复现与有界、Gerstner 解析梯度、
**地形逐行装配与朴素装配逐位一致**、**体素换种子与剔除率**。

## 性能基准（真实帧率）

`--bench` 会同时关掉 vsync 与 pyglet 的窗口重绘节流
（`pyglet.app.run(interval=…)` 默认 1/60 —— 这才是"59.9 fps"的真正来源），
所以量到的是真吞吐。1280×720 实测：

| 项 | 均值 fps | p1 fps | 最慢帧 |
|----|---------:|-------:|-------:|
| demos 中最轻（无限隧道） | 1226 | 644 | 2.6 ms |
| demos 中最重（CPU 群集 boids） | 124 | 94 | 13.0 ms |
| terrain 换种子（同步重建，对照） | — | — | **1132 ms** |
| terrain 换种子（渐进重建，现状） | — | — | **62 ms** |
| voxel 换种子（同步重建，对照） | — | — | **352 ms** |
| voxel 换种子（渐进重建，现状） | — | — | **23 ms** |

最后两行是本轮修掉的"按 `R` 卡一下"：把重建拆成"每帧最多几毫秒"的渐进式，
最慢帧从 1132ms 降到 62ms；voxel 那边同理，352ms → 23ms（其中一半功劳来自
**把"全部面"对照网格改成按 `H` 懒建** —— 它有 11MB 顶点数据，之前每次重建
都要重传）。剩下的几十毫秒是换网格时的显存上传，一次性，重建期间旧地形/
旧体素照常可玩。

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

