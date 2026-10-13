# examples — 单文件 3D 示例与工具集

> **目录名沿革**：本目录原叫 `demos/`，2026-10-14（P2-3）改名为 `examples/` —— 引擎项目里
> 这些是**示例与仓库工具**，不是产品本体。历史文档里若见 `demos/`，按 `examples/` 读。

每个示例都是**独立可跑的单文件脚本**，共用 `world3d/platform/app.py` 里的 `DemoApp`
骨架（窗口 / DPI / 命令行 / HUD / 截图 / 暂停；P2-1 起真身在此，旧路径 `examples/_harness.py`
是薄兼容层），不改变主工程 `core → engine → logic → presentation` 的分层约定。

## 统一用法

```bat
python examples\<名字>.py                  正常运行
python examples\<名字>.py --smoke [秒]     冒烟：N 秒后自动关闭（默认 2.0）
python examples\<名字>.py --shot           按时抓帧写 docs/ 后自动关闭
python examples\<名字>.py --size 960x540   指定窗口尺寸
python examples\<名字>.py --fps 30         指定逻辑刷新率
python examples\<名字>.py --duration 12    覆盖演示循环时长
python examples\<名字>.py --bench 5        基准：跑 5 秒，打印真实帧率统计后退出
python examples\<名字>.py --flag NAME      演示专属开关（可逗号并列，见下表）
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
| `scene_walker.py` | 交互 | **可漫步场景（交互化）**：S1 场景 JSON → 第一人称物理漫步（固定步长抗卡帧）+ 马尔可夫演出变体 + 拾取/E 互动 + **限时挑战（T 键）与跨幕转场结算卡**（音乐张力阶梯 COMBAT_LOW→HIGH）+ SFX/自适应 BGM + 场景热重载 | `scene_walker_01.png` |
| `blockworld.py` | 交互 | **方块世界 · 可交互版**（voxel_terrain × minecraft_style 结合）：第一人称漫步/跳跃 + **DDA 准星拾取 + 左键挖/右键放 + hotbar(1-6)** + **区块化网格重建**（16 列/块，边界邻块同步；实测编辑 ≈11ms）+ 程序化像素图集与昼夜 | `blockworld_01.png` |
| `kid_a_walker_3d.py` | 交互 | **KID-A WALKER 3D**（arcade 伪 3D 重制为真 3D）：41 个房间模版 → 区块无限走廊 + **能量/碎片/传送门/暗室** + 区域驱动调色与程序化配乐（ZoneSynth 风格）+ 发光踢脚线走廊美学 | `kid_a_3d_01..03.png` |
| `scene_viewer.py` | 工具 | **场景查看器（非定时演出）**：加载 `--file` 指定的场景 JSON 并实时渲染，改文件即热重载（坏 JSON 会被拒绝、保留旧场景）——「LLM 场景生成工作流」的"看"这一端；**未声明 `SHOTS`，批量验收跳过截图阶段** | — |
| `mountain.py` | 观察式 | **《山》**（致敬 David OReilly 的 Mountain）：复用 terrain_flyover 噪声管线生成**悬浮孤峰**（顶/底两面在边界闭合）+ 片元级动态光照昼夜交替 + 云/雨/雪气象轮替 + 坠落物体与生长植物 + 哲学独白叠层 + **人格测验答案嵌进生成参数**（N 键）+ 键盘钢琴（A~K 即时合成五声音阶）+ 流星撞击崩解重生（C 键） | `mountain_day/dusk.png` |
| `rayhold.py` | 交互 | **《迷堡枪影 / Rayhold》**（P1-③ 射线 FPS，机制致敬 Wolfenstein 3D / Doom）：**程序化随机迷宫**（递归回溯 + 12% 编织，种子可复现）+ **全屏片元着色器 SDF 光线步进**（墙 / 地板 / 敌人 / 光弹 / **拾取悬浮球 / 出口金色呼吸光**在同一场景，迷宫距离场与命中判定同源）+ hitscan 射线枪 + 2 种敌人状态机（近战游荡者 / 远程哨卫投光弹）+ 弹药与医疗拾取 + aim trainer 式手感（**hitmarker 命中/击杀标记、−/= 灵敏度与 cm/360 估算、结算命中率小结、失焦清键与回焦重锁**）；玩法全在 `games/fps/kernel.py`，由 `cordis.fps.yml` 配置树挂载（平衡参数集中在 yml） | `rayhold_a/b.png` |
| `volumetric_fog.py` | 交互 | **体积雾与光轴（god rays）**（④-2）：**高度雾的闭式积分**（按 `A` 在「闭式 / 逐步矩形」之间切换 —— 后者既出层状条纹、又会「改步数就让整幅雾变浓变淡」，两者并排可见）+ **Henyey-Greenstein 相函数**（`G` 调各向异性；前向散射才是光柱的成因）+ **单次散射**（每步查 CSM 的阴影图 ⇒ 柱缝里裂出光柱，实测画面中央走廊亮度 **88.3 vs 只雾 78.5 vs 无雾 77.1**）；四趟渲染（深度 → 阴影 → 场景 → 合成）；数学全在 `engine/volumetrics.py`（纯函数、可 headless 断言） | `fog_01/02.png` |
| `csm_shadow.py` | 交互 | **级联阴影（CSM）**（④-1）：把一张阴影图按视距切成 N 级（1~4，C 键切换），近处纹素细、远处也还在。数学全在 `engine/csm.py`（纯函数、可 headless 断言：PSSM 混合分割 / 视锥角点 / 紧贴光盒拟合 / **纹素吸附**消抖动 / 级选择）；地面是**程序化棋盘**（影子锯齿在棋盘上最刺眼），**V 键按级染色**一眼看出级联边界；左上是某级的深度图小窗（render-to-texture 自证） | `csm_01/02.png` |
| `boxward.py` | 交互 | **《归仓 / Boxward》**（P1-④ 方块解谜，机制致敬 Sokoban 1982）：**光栅贪心合批**（静态墙由 `greedy_mesh` 合并建网，补齐 `SdfVoxelCore.world_mesh` 留白）+ **正交等距视角**（偏航 45° + 俯角 45°，每个方块同时露出两个侧面 + 顶面 —— 原先轴对齐时只看到一个面、读起来像 2D；`Q/E` 切 90° 档、`P` 循环俯角 30/38/45/52°，WASD 随相机映射、屏幕上呈四个斜向）+ **BFS 求解器 auto-play**（headless 验收 / 录制 / 生成器验证同源）+ **程序化关卡**（反向拉箱打乱，可解性由构造保证）+ **关卡编辑器**（M 键摆格 / X 试解 / E 导出 JSON，三端同 schema）+ undo 与推数 par；玩法全在 `games/puzzle/kernel.py`，由 `cordis.puzzle.yml` 配置树挂载 | `boxward_a/b.png` |
| `brickward.py` | 交互 | **《砖境 / Brickward》**（BQM 机制致敬 B1~B3，机制致敬 BlockQuest Maker 2018 —— **不是移植、不是复刻**，只吸收玩法机制）：**机关解谜**（推箱 / 按钮 / 拉杆 / 门 / 金门+钥匙 / 尖刺 / 炸弹 / 火球 / 滚石 / 史莱姆，11 类元素，一格一步确定性模拟）+ **「连接」绑定**（开关→门，三种模式 open/toggle/close，落在 P0-5 自研的 `engine.orchestration.Bus` 上）+ **BFS 求解器 auto-play**（headless 验收 / 录制 / 生成器验证同源）+ 程序化种子关（由求解器把关可解）+ **谜题编辑器**（M 键：左键摆格 / `[` `]` 换道具 / **C 连接开关→门** / V 试解 / E 导出 JSON / I 载入 / B 新空白关，编辑器导出的 = 内核吃的 = `--flag level=` 读回的，三端同 schema）；等距视角与 boxward 同一套看的；玩法全在 `games/brickward/kernel.py`，由 `cordis.brickward.yml` 配置树挂载 | `brickward_a/b.png` |
| `swarmward.py` | 交互 | **《弹潮 / Swarmward》**（P1-⑤ 弹幕固定屏射击，机制致敬 Robotron 2084 1982）：**全向生存**（固定屏 24×15 正交）+ **双摇杆**（WASD 移动 + 方向键/鼠标射击）+ **程序化编波**（`wave_plan(seed, wave)` 难度单调 + `pathing.formation` 编队进场——pathing 核首个内核消费者）+ **particles 核首个内核消费者**（击杀碎屑/命中火花/受击冲击，`snapshot()` 渲染层淡出）+ 3 敌型状态机（直冲/游走射击/编队蜂群）+ 连击计分 + Aimlabs 手感件（受击白闪/量化结算）+ 10 波通关制与无尽 flag；玩法全在 `games/storm/kernel.py`，由 `cordis.storm.yml` 配置树挂载 | `swarmward_a/b.png` |

| `godhand.py` | 交互 | **《神之手 / GodHand》**（P1-①，机制致敬 Populous / From Dust）：鼠标「抬山 / 挖海」实时改写地形高度场，淹没或掩埋敌方聚落即胜；玩法在 `games/godhand/kernel.py`，由 `cordis.godhand.yml` 配置树挂载 | — |
| `tower_defense.py` | 交互 | **《戍卫之路 / Roadwarden》**（P1-② 首个游戏内核，机制致敬 Warcraft / Dune II 的「经济→兵力→战斗」循环）：24×24 格 + 固定车道 + 3 类塔（箭/炮/冰）+ 3 类敌人 + 5 波 + 胜负判定；玩法在 `games/tower_defense/kernel.py`，由 `cordis.td.yml` 挂载 | — |
| `skinned_creature.py` | 引擎 | **蒙皮骨骼动画（P2-3）**：加载**程序化生成**的带 skin 的 glTF「骨节蛇」（8 关节 / `assets/skinned_snake.gltf`），用蒙皮顶点着色器（`pbr_skin`）渲染并驱动骨骼链正弦摆动；A 切摆动 / SPACE 暂停 / R 复位；拖动旋转、滚轮缩放 | `skinned_01..03.png` |
| `gpu_cull_field.py` | 引擎 | **GPU 驱动渲染（P3-1，A/B/C 全做）**：4096 实例阵列，**剔除 + LOD + 命令生成全在 compute 里**（按 LOD 固定分区写可见表、命令的 instanceCount 由 atomicAdd 累加）；CPU 每帧只做 1 次 dispatch + 3 次 `glDrawArraysIndirect`；F2 切 GPU/CPU 剔除、F3 切 LOD、HUD 实时计数；含运行时能力探测 + CPU 回退 | `gpu_cull_01..03.png` |
| `wboit_demo.py` | 引擎 | **顺序无关透明（P2-2）**：Weighted Blended OIT（McGuire 2013）—— 4 个**互相相交**的半透明球，透明物一趟写 accum/reveal 两张 MRT 再合成，**无需排序**；S 切 WBOIT/排序、O 反转绘制顺序（可直观看出排序法会跳变） | `wboit_01..02.png` |
| `pointcloud_edl.py` | 引擎 | **点云 + EDL（P2-4）**：**程序化**点云（值噪声地形 / 双螺旋，20000~40000 点），`gl_PointSize` + 片元圆盘（imposter）；**EDL 全屏 pass** 按深度邻域差给轮廓加深（点云从"散点"变"有体积"）；E 开关 EDL、1/2 换云、`[`/`]` 调点大小 | `pointcloud_01..02.png` |
| `stillhall.py` | 交互 | **《静馆 / Stillhall》**（§14 **节点式漫游解谜**，致敬 Myst 1993）：固定机位 + **点击热点换景/取物/开机关**（非自由 WASD）+ 物品栏 + **谜题状态机**（顺序解锁链：取钥 → 点灯得齿轮 → 开终局门）；场景数据走 **JSON（与 scene_viewer / LLM 场景生成工作流同 schema）**且支持热重载（坏 JSON 拒收保旧）；`1` 键自动推进 | `stillhall_01..02.png` |
| `voidward.py` | 交互 | **《星阑 / Voidward》**（§15 **Elite 式太空贸易**，四份 note 里范围最大）：1 星系（恒星 + 3 行星 + 1 空间站）+ 5 种货（价格 = 基准 × 供需正弦 + **种子驱动事件冲击**，确定性可复现）+ **惯性飞行**（松油门滑行）+ 海盗拦截战（击退有赏金）+ 舱位/炮 2 级升级；资产达标 10000 → won；WASD 推进、空格开火、B/V 买卖、N 升级、G 自动贸易 | `voidward_01..02.png` |
| `pulsefall.py` | 交互 | **《落音 / Pulsefall》**（§16 **下落式音击**）：4 轨道（DFJK）+ 3 谱面（BPM 90/120/150，**种子复现**）+ 判定 3 档（PERFECT ±40ms / GOOD ±90ms / MISS）+ 血量与连击得分 + 结算（P/G/M + 准确率 + 最大连击）；音乐由 **audio 核按谱面程序化合成**（三层：低音/和弦/主旋律）；F4/F5 offset 校准、G 自动演奏 | `pulsefall_01..02.png` |
| `director.py` | 工具 | **可视化导演台（③）**：把关键帧系统（`engine/shot.py` + `engine/timeline.py`）和 `TimelineUI` 串成"**编排 → 预览 → 导出 → 录制**"闭环。三个编辑目标用 `T` 循环：`$camera`（position/target/fov）、`$env`（sun_intensity/sun_dir/ibl_intensity，**真·灯光演出**，渲染器每帧 apply）、场景节点（TRS，键盘微调）。`K` 记帧 / `DEL` 删帧 / `[ ]` 选通道 / `I` 换插值 / `M` 加幕 / `F2` 导出演出 JSON / `O` 载入 / `R` 重载场景；右侧**关键帧检察器**显示每通道的插值与键刻度（可点行选中、点键 seek）；内置默认演出「绕祭坛闭环环绕 + 主体自转 + 太阳随相机扫过」，`--shot-json <path>` 可回放导出的运镜 | `director_a..d.png` |

> 上表 31 项与 `examples/*.py`（不含 `_harness.py` 兼容层）一一对应，也是
> `scripts/verify_demos.py` 逐项冒烟的对象；唯一不参与截图阶段的是
> `scene_viewer.py`——它由 `--file` 驱动、没有固定剧照时刻。

`scene_viewer.py` 用法定制（其余 demo 用上面的统一命令行）：

```bat
python examples\scene_viewer.py --file docs\llm_scene_demo.json
python examples\scene_viewer.py --file docs\llm_scene_demo.json --smoke 3
```

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
| `timed` / `victory`（scene_walker） | 自检：短时限挑战 / 自动三连唤醒（验证结算卡与音乐叙事） |
| `digdemo` / `topdown`（blockworld） | 自检：自动挖→放闭环（验证 DDA + 区块重建）/ 高空俯瞰诊断视角 |
| `go`（kid_a_walker_3d） | 自检/录制：自动探索（航点导航 + 走廊居中 + 自动收集） |
| `auto` / `seedN`（rayhold） | 自动驾驶（BFS 奔出口 + 遇敌自动射击，与 headless 验收同款）/ 开局换种子（如 `seed1234`） |
| `auto` / `seedN` / `level=路径`（boxward） | 求解器回放连续闯关（与 headless 验收同款）/ 按种子加载生成关 / 加载 JSON 关卡 |
| `auto` / `endless` / `seedN`（swarmward） | 闪避 AI 自动打满 10 波（与 headless 验收同款）/ 无尽模式（不清波判定）/ 按种子开局 |

常用按键（各 demo 底部有完整帮助行）：`SPACE` 暂停、`ESC` 退出是统一的；
**方块世界** `N`/`P` 切幕、`←`/`→` 快退快进 5s、`C` 切电影/自由相机、`R` 重播；
此外 **粒子** `[`/`]` 调尺寸、`,`/`.` 调数量；**体素** `R` 换种子（渐进重建）、
`H` 切剔除对照（首次会懒建"全部面"网格）、`F` 线框；**阴影** `P` 切 PCF、
`B` 切 bias、`S` 开关阴影；**太阳系** `1..8` 聚焦行星（自动取相位视角）、
`V` 相位锁定、`0` 全景；**地形** `R` 换种子（渐进重建）；
**山** `N` 人格测验（答案嵌进生成参数）、`R` 随机新山、`C` 召唤流星、
`M` 静音、`A S D F G H J K` 弹钢琴。

## 渐进重建（换种子不冻画面）

地形与体素都支持"换一个世界"，早期版本是同步重建，画面会明显卡一下：

| | 同步重建（对照） | 渐进重建（现状） |
|---|---|---|
| `terrain_flyover` `R` | 最慢帧 **1132 ms** | 最慢帧 **62 ms** |
| `voxel_terrain` `R` | 最慢帧 **352 ms** | 最慢帧 **23 ms** |

做法：把生成拆成"按行/按列"的小块，每帧只啃固定的毫秒预算（terrain 8ms /
voxel 6ms），旧网格一直画到最后一次性替换。复现对照数据：

```bat
python examples\terrain_flyover.py --bench 5 --flag reseed        :: 渐进
python examples\terrain_flyover.py --bench 5 --flag reseed-sync   :: 对照（同步）
python examples\voxel_terrain.py   --bench 5 --flag reseed        :: 渐进
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
  `platform/app.py` 已经统一处理；截图走 `glReadPixels` 按实际 buffer 尺寸读，
  不用 `get_color_buffer()`（后者会按被 DPI 放大的 VIEWPORT 读，出黑边）。
- **旋转**：用 `logic.animation.quat_to_mat4`，pyglet 自带
  `Quaternion.to_mat4()` 实测是转置的。
- **顶点色要 4 元组**：着色器属性是 `vec4`，返回 3 元组会让顶点缓冲少 1/4 的
  float，报 `Invalid data size for 'colors'`（地形渐进重建时踩过）。
- **路径**：不许写死盘符，一律用 `platform.app.PROJECT_ROOT` / `DOCS_DIR`；
  控制台输出一律 ASCII（中文在 GBK 代码页的 cmd 里会变乱码）。
