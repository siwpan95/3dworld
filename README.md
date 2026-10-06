# 3dworld — pyglet 三维世界示例

用 Python + pyglet 从零构建三维世界与**动画演出**：时间轴 / 关键帧插值 /
层级骨骼 / 程序化场景，外加一组单文件 3D 示例（`demos/`）。

当前面貌：

- `main.py`（`pyglet_3d_demo`）—— **48 秒五幕演出**：环形立方体升起 → 环形波 →
  机械臂逆运动学取放宝石 → 螺旋柱 → 归位，双相机模式 + 幕字幕 + 时间轴面板；
- `kkrieger_demo.py` —— **96 秒四幕程序化走廊漫游**（致敬 .kkrieger）：
  走廊舱门/管道/货箱/频闪 → 大厅纪念碑/全息扫描/升降平台 → 核心过载 →
  撤离时逐段熄灯；
- `demos/` —— **9 个独立示例**：无限隧道、SDF 光线步进、GPU 实例化粒子、
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
  kkrieger.py          kkrieger 的 96s 四幕编排（相机脚本 + 场景动画）
  camera.py            轨道相机状态机 + 手写 look-at 矩阵
  playback.py          播放控制器：播放/暂停/循环/倍速/seek
  skeleton.py          节点级层级动画（转台→大臂→小臂→夹爪）
  events.py            事件总线（pyglet EventDispatcher）
  boids.py  waves.py   demos 的纯数学模块（群集 / Gerstner 波）
presentation/        表现层：装配窗口事件 → 逻辑层 → 渲染
  app.py               WorldApp：48s 演出装配（双相机模式 + 夹持混合）
  kkrieger_app.py      KKApp：96s 漫游装配 + 跳帧截图
  hud.py               幕字幕（淡入/停留/淡出）+ 状态行
  timeline_ui.py       底部时间轴面板（播放/循环/拖动/幕标记）
  shot.py              离屏抓帧（glReadPixels，DPI 无关）
demos/               9 个单文件示例 + 公共骨架 _harness.py（详见 demos/README.md）
scripts/
  make_gem_obj.py      程序化生成 assets/gem.obj
  verify_demos.py      demos 批量验收（冒烟 + 截图 + 退出码）
tests/               逻辑层无窗口单测：python -m tests.run_tests
main.py / kkrieger_demo.py / pyglet_3d_demo.py(兼容 shim)   入口
docs/                设计文档 + 渲染验证截图
```

依赖方向：presentation → logic → core，反向禁止。

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

## kkrieger 致敬 Demo —— 96 秒四幕程序化走廊漫游

参考 2004 年 Breakpoint 96k 冠军作品 .kkrieger（Farbrausch/.theprodukkt）的
"程序化生成"精神：**零外部素材**——金属板、灯带、舱门、管道、货箱、立柱、
五环纪念碑、全息扫描框、升降平台、能量束、风扇、无人机全部由代码即时生成，
面板颜色用确定性哈希抖动模拟"程序化纹理"。

```bat
python kkrieger_demo.py            正常运行（SPACE 暂停 / R 重播 / N,P 切幕）
python kkrieger_demo.py --smoke    冒烟测试（2.5s 自动关闭）
python kkrieger_demo.py --shot     四幕各抓一张截图到 docs/（跳帧采样，约 3s）
```

| 幕 | 时间 | 内容 |
|----|------|------|
| 一 · 走廊 | 0–24s | 俯冲进走廊，三道舱门依次开启，管道/货箱/警示频闪入画 |
| 二 · 大厅 | 24–48s | 纪念碑唤醒：五环转动、全息扫描框上下扫、升降平台升起 |
| 三 · 核心 | 48–72s | 过载：环加速、核心脉冲、能量束增亮、镜头逼近至"贴脸" |
| 四 · 撤离 | 72–96s | 后撤，走廊灯带与大厅灯环逐段熄灭，拉回开场高空帧后硬切回开头 |

截图：`docs/kkrieger_corridor.png` / `_atrium.png` / `_core.png` / `_blackout.png`

> 已知平台坑（实测 pyglet 2.1.16 / Win10 150% DPI）：`dpi_scaling` 默认
> "platform" 模式下实际 framebuffer = 请求尺寸，但 viewport setter 会乘
> scale → 渲染放大裁切；本 Demo 统一设 `pyglet.options['dpi_scaling'] = 'real'`。

## demos/ —— 9 个单文件 3D 示例

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

详见 [demos/README.md](demos/README.md)（含统一命令行与踩坑清单）。

## 测试与验收

```bat
python -m tests.run_tests           逻辑层无窗口单测（26 项）
python main.py --shot               主线演出渲染验证（4 张）
python kkrieger_demo.py --shot      漫游渲染验证（4 张）
python scripts\verify_demos.py      9 个 demo 逐个冒烟 + 截图
```

单测覆盖：相机位姿与钳制、look-at 矩阵性质、四元数 slerp、
Sampler/Timeline 语义、播放控制、UI 命中、OBJ 资产解码、骨骼层级传播、
**五幕编排的循环连续性与幕边界连续性**、**机械臂逆运动学落点**、
kkrieger 四幕的循环连续性与舱门/平台/灯带状态机、**镜头穿模检查**
（机位与立柱/门洞/顶棚/纪念碑的距离）。

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
