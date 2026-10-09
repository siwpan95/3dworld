# demos — 单文件 3D 示例集

每个 demo 都是**独立可跑的单文件脚本**，共用 `_harness.py` 里的 `DemoApp`
骨架（窗口 / DPI / 命令行 / HUD / 截图 / 暂停），不改变主工程
`core → logic → presentation` 的分层约定。

## 统一用法

```bat
python demos\<名字>.py                  正常运行
python demos\<名字>.py --smoke [秒]     冒烟：N 秒后自动关闭（默认 2.0）
python demos\<名字>.py --shot           按时抓帧写 docs/ 后自动关闭
python demos\<名字>.py --size 960x540   指定窗口尺寸
python demos\<名字>.py --fps 30         指定逻辑刷新率
python demos\<名字>.py --duration 12    覆盖演示循环时长
python demos\<名字>.py --bench 5        基准：跑 5 秒，打印真实帧率统计后退出
python demos\<名字>.py --flag NAME      演示专属开关（可逗号并列，见下表）
```

**关于 `--bench`**：它会自动关掉 vsync，并且把 pyglet 的窗口重绘节流
（`pyglet.app.run(interval=...)`，默认 1/60）也关掉 —— **后者才是"59.9 fps"
的真正来源**。不关它，量到的永远是 60。输出形如：

```
[bench] frames=2327 span=4.00s fps mean=582.1 median=609.2 p1=369.6 worst_frame_ms=3.78 jank_gt20ms=0.0%
```

均值会被偶发快帧拉高，**看 p1（1% 慢帧下界）和最慢帧**才知道卡不卡。

批量验收（冒烟 + 截图 + 退出码，逐个串行跑真实 GL 窗口）：

```bat
python scripts\verify_demos.py                     全部
python scripts\verify_demos.py --smoke-only        只跑冒烟
python scripts\verify_demos.py --bench 3           真实帧率（每个 demo 3 秒）
python scripts\verify_demos.py --only boids,water  只验指定 demo
```

## 清单

| demo | 时长 | 演示的技术点 | 截图 |
|------|------|--------------|------|
| `demoscene_tunnel.py` | 24s | **纯片元着色器**：极坐标隧道、程序化面板、行进光带、色散/扫描线/暗角 | `demo_tunnel_a/b.png` |
| `raymarch_sdf.py` | 32s | **SDF 光线步进**：球/盒/环 smooth-min 融合、软阴影、AO、地面反射雾 | `demo_raymarch_a/b.png` |
| `particles_gpu.py` | 30s | **原生实例化**（`vertex_list_instanced`）：数万粒子的位置/颜色全在顶点着色器里算 | `demo_particles_a/b.png` |
| `solar_system.py` | 60s | **层级变换 + 轨道力学 + 相位视角**：UV 球体、土星环、月球绕地、实例化小行星带 | `demo_solar_a/b/c.png` |
| `terrain_flyover.py` | 48s | **程序化几何 + 渐进重建**：值噪声 fBm、分层着色、烘焙光照、换种子不冻画面 | `demo_terrain_a/b.png` |
| `voxel_terrain.py` | 36s | **体素面剔除**：只生成暴露面、`H` 对照全局面数、`R` 换种子重建 | `demo_voxel_a/b.png` |
| `water_waves.py` | 40s | **顶点着色器动画**：Gerstner 波位移 + 解析法线、菲涅尔天空、CPU/GPU 共用一套波函数 | `demo_water_a/b.png` |
| `boids.py` | 45s | **逻辑层纯数学 + 实例化渲染**：Reynolds 群集，着色器按速度定向 | `demo_boids_a/b.png` |
| `shadow_map.py` | 30s | **两趟渲染 / render-to-texture**：R32F 离屏深度图 + 3×3/5×5 PCF 阴影 | `demo_shadow_a/b.png` |
| `minecraft_style.py` | 48s | **程序化像素材质 + 昼夜统一光照 + 实例化下落方块**：五幕短片（日出飞越 / 村庄 / 建造 / 生物 / 日落入夜点灯），23 种 16×16 贴图启动时现算 | `demo_mc_dawn/build/mobs/dusk.png` |
| `engine_showcase.py` | 24s | **引擎综合巡礼**：PBR/IBL、Bloom、SSAO、实例化、视锥剔除、拾取、物理、性能面板、场景序列化 | `engine_showcase_01/02.png` |
| `material_lab.py` | 30s | **新建模 × 新素材**：车削/管扫/环面结 + 程序化 PBR 纹理（拉丝/大理石/电路板等 5 套） | `material_lab_01/02.png` |
| `nordic_saga.py` | 60s | **《诺德之境》**：上古卷轴风格程序化动画（雪原遗迹/巨龙/龙语古墙 + 程序化配乐） | `nordic_01..06.png` |
| `scene_walker.py` | 交互 | **可漫步场景（交互化）**：S1 场景 JSON → 第一人称物理漫步（固定步长抗卡帧）+ 马尔可夫演出变体 + 拾取/E 互动 + SFX/自适应 BGM + 场景热重载 | `scene_walker_01.png` |

### 演示专属开关（`--flag`，逗号可并列）

| flag | 作用 |
|------|------|
| `size3` / `size4`（particles） | 起始粒子尺寸倍率，用来单独看清径向软衰减 |
| `count4k`（particles） | 起始粒子数降到 4000（单颗才分得开） |
| `preset2`（particles） | 起始预设 = 喷泉（稀疏） |
| `pcf5` / `noshadow` / `noon`（shadow_map） | 起始 5×5 PCF / 关阴影 / 关自动环绕 |
| `all` / `seed2`（voxel_terrain） | 起始画全部面（对照剔除）/ 起始换一个种子 |
| `reseed` / `reseed-sync`（terrain_flyover） | 1 秒后触发一次**渐进**重建 / **同步**重建（A/B 对照用） |
| `nostatics`（engine_showcase） | 关掉静态柱林（看视锥剔除的差异） |
| `walk` / `interact` / `hotreload`（scene_walker） | 起始进入漫游 / 无人值守自检互动 / 自检热重载（临时副本） |

常用按键（各 demo 底部有完整帮助行）：`SPACE` 暂停、`ESC` 退出是统一的；
**方块世界** `N`/`P` 切幕、`←`/`→` 快退快进 5s、`C` 切电影/自由相机、`R` 重播；
此外 **粒子** `[`/`]` 调尺寸、`,`/`.` 调数量；**体素** `R` 换种子（渐进重建）、
`H` 切剔除对照（首次会懒建"全部面"网格）、`F` 线框；**阴影** `P` 切 PCF、
`B` 切 bias、`S` 开关阴影；**太阳系** `1..8` 聚焦行星（自动取相位视角）、
`V` 相位锁定、`0` 全景；**地形** `R` 换种子（渐进重建）。

## 渐进重建（换种子不冻画面）

地形与体素都支持"换一个世界"，早期版本是同步重建，画面会明显卡一下：

| | 同步重建（对照） | 渐进重建（现状） |
|---|---|---|
| `terrain_flyover` `R` | 最慢帧 **1132 ms** | 最慢帧 **62 ms** |
| `voxel_terrain` `R` | 最慢帧 **352 ms** | 最慢帧 **23 ms** |

做法：把生成拆成"按行/按列"的小块，每帧只啃固定的毫秒预算（terrain 8ms /
voxel 6ms），旧网格一直画到最后一次性替换。复现对照数据：

```bat
python demos\terrain_flyover.py --bench 5 --flag reseed        :: 渐进
python demos\terrain_flyover.py --bench 5 --flag reseed-sync   :: 对照（同步）
python demos\voxel_terrain.py   --bench 5 --flag reseed        :: 渐进
```

剩下的几十毫秒是**换网格时的显存上传**（一次性），无法再摊——除非改成
流式更新顶点缓冲。体素那 352→23 里另一半功劳来自把"全部面"对照网格
改成按 `H` 懒建（11MB 顶点数据，之前每次重建都白传一遍）。

## 真实帧率（1280×720，vsync 关闭，`--bench 3`）

| demo | 均值 fps | p1 fps | 最慢帧 ms | 备注 |
|------|---------:|-------:|----------:|------|
| demoscene_tunnel | 1214 | 669 | 2.4 | 纯片元，最轻 |
| particles_gpu | 750 | 463 | 3.3 | 4.5 万实例，CPU 零写入 |
| raymarch_sdf | 725 | 514 | 255¹ | 每像素最多 96 步 |
| water_waves | 691 | 404 | 3.4 | 8 万三角 + 顶点位移 |
| solar_system | 686 | 443 | 3.3 | 层级 + 实例化小行星带 |
| voxel_terrain | 675 | 441 | 3.1 | 面剔除 + 懒建对照网格 |
| shadow_map | 660 | 391 | 3.8 | 两趟渲染 + 1024² 深度图 |
| terrain_flyover | 596 | 396 | 4.0 | 7.3 万三角地形 |
| boids | 125 | 105 | 17.5 | **CPU 端群集 step ≈9.5ms/帧**，唯一的 CPU 瓶颈 |
| minecraft_style | 456 | 337 | 315¹ | 2 万面地形 + 1188 水面 + 实例化云/方块 + 3 只生物 |

¹ raymarch 与 minecraft_style 的最慢帧都是**一次性开销**（着色器编译 / 首帧上传
顶点与实例缓冲），2050 帧里各 1 帧、jank 占比 0.0%，预热后进入稳态。

> 这张表就是为什么要做 `--bench`：在 vsync 开着的默认窗口里，上面每个数字
> 都会显示成 "59.9 fps"，看不出任何差别。

`logic/boids.py`、`logic/waves.py` 是其中两个 demo 的**逻辑层模块**：
不 import `pyglet.gl`，可无窗口导入与单测（`python -m tests.run_tests`）。

## 参考样板

新写 demo 请先看 `demoscene_tunnel.py`——它是最短的完整例子，
演示了 `DemoApp` 的全部覆盖点（`setup/update/render/on_key` 与 `SHOTS` 声明）。

## 踩过的坑（写在这里省得每篇重踩）

- **几何 API**：pyglet 2.1.16 已移除 `Batch.add` / `c3f`，一律用
  `program.vertex_list(...)`；着色器里用到的属性必须都声明，否则
  未使用的属性会被 GLSL 优化掉、pyglet 报 `ShaderException`。
- **实例化**：2.1.16 原生支持 `ShaderProgram.vertex_list_instanced(...)` +
  `add_instance(...)`（域初始 `_instances=1`），**不要**手搓 VAO/VBO；
  实例属性若不给初始数据会被**静默丢弃**（源码里是 `except KeyError: continue`），
  随后赋值报 `not an instanced attribute`。每帧只更新少量实例属性可以，
  几万条逐帧更新会掉帧。
- **帧率**：`pyglet.app.run()` 默认 `interval=1/60` 给窗口重绘**排程**，
  所以"关掉 vsync"也不会更快——要量真实吞吐必须 `run(interval=0)`（`--bench` 已处理）。
- **加性混合的饱和**：粒子用 `GL_SRC_ALPHA, GL_ONE` 叠加时，密度一高必然糊成
  色块、单颗的软衰减无从谈起；`(1-d²)²` 这种"平顶"剖面更是雪上加霜。
  现在的做法是高斯剖面 + 面积缩放时按 1/k² 补 alpha + 提供数量档位（`,`/`.`）。
- **`Framebuffer.clear()` 在 2.1.16 是坏的**：它把附件点枚举
  （`GL_COLOR_ATTACHMENT0|GL_DEPTH_ATTACHMENT`）当成 `glClear` 的掩码传下去，
  驱动报 `GL_INVALID_VALUE`；要自己 `glClear(GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT)`。
- **DPI**：`pyglet.options['dpi_scaling'] = 'real'` 必须在建窗口前设置，
  `_harness` 已经统一处理；截图走 `glReadPixels` 按实际 buffer 尺寸读，
  不用 `get_color_buffer()`（后者会按被 DPI 放大的 VIEWPORT 读，出黑边）。
- **旋转**：用 `logic.animation.quat_to_mat4`，pyglet 自带
  `Quaternion.to_mat4()` 实测是转置的。
- **顶点色要 4 元组**：着色器属性是 `vec4`，返回 3 元组会让顶点缓冲少 1/4 的
  float，报 `Invalid data size for 'colors'`（地形渐进重建时踩过）。
- **路径**：不许写死盘符，一律用 `_harness.PROJECT_ROOT` / `DOCS_DIR`；
  控制台输出一律 ASCII（中文在 GBK 代码页的 cmd 里会变乱码）。
