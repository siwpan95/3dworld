# P2-1/2/4 · 渲染能力补强（PCSS 软阴影 + OIT 透明 + 点云/EDL）design note

> 状态：**✅ 已执行并交付**（2026-10-11）· 依据 `docs/补完计划.md` §2 的 P2-1 / P2-2 / P2-4
> 参考源：**Easy3D（GPL-3.0）—— ⛔ 只读思路、独立重写，零代码搬运**（合规红线见 `D:\projects\参考资料库_索引.md` §三）

---

## 1. 现状（实证）

| 项 | 3dworld 现状 | 缺口 |
|----|--------------|------|
| **阴影** | `demos/shadow_map.py`：离屏 R32F 深度图 + **PCF 3×3/5×5**（`u_pcf` 半径）、法线自适应 bias | ❌ **PCSS**（blocker search 求半影宽度 → 自适应采样半径） |
| **透明** | `engine/renderer.py`：`sort_transparent`（按相机距离**排序后**画，`GL_BLEND`） | ❌ **OIT**（顺序无关；排序法在相交/循环遮挡下必然错） |
| **点云** | **无** | ❌ 点云 drawable（imposter）+ **EDL**（无算法线点云的轮廓增强） |

**参考对象的能力清单**（Easy3D，仅思路）：
`soft_shadow.cpp`(196 行，PCSS：blocker search + PCF，5 种 Poisson/Regular 采样档)、
`dual_depth_peeling.cpp`(483，OIT)、`average_color_blending.cpp`(196，透明近似)、
`drawable_points.cpp`(469，imposter SPHERE/SURFEL)、`eye_dome_lighting.cpp`(346，EDL)。

---

## 2. 三项设计

### P2-1 · PCSS 软阴影
**思路**（Bloom 1980s 的经典三步，与 Easy3D 同一原理，**独立实现**）：
```
① blocker search：在光源空间搜索遮挡物，求平均遮挡深度 z_blocker
② 半影估计：penumbra = (z_receiver - z_blocker) / z_blocker * light_size
③ 可变半径 PCF：用 penumbra 决定采样半径（近处硬、远处软）
```
**落地**：`engine/soft_shadow.py`（引擎级，可复用）+ 新增着色器；`demos/shadow_map.py` 加 `P` 键循环
`PCF 3×3 → PCF 5×5 → PCSS`（**保留原 PCF 路径**，零回归）。
**采样**：固定 Poisson 盘（16/32 样本），避免每帧重算噪声。

### P2-2 · OIT 顺序无关透明
两条路线（**待确认①**）：

| 方案 | 原理 | 代价 | GL 要求 |
|------|------|------|:-------:|
| **A. Weighted Blended OIT**（McGuire 2013） | 透明物一次 pass 写成 `(Σc·α, Σα)` 两个 RT，最后统一合成；**无需排序** | **1 趟**，实现简单 | 3.3 ✅（MRT 已有） |
| B. Dual Depth Peeling | 逐层剥离，N 层用 N/2+1 趟 | 多趟 + 多 FBO | 3.3 ✅（但复杂 5×） |

**本机上下文 GL 3.3**（P3-1 实测）——A 方案对 MRT 的依赖已有基础（`engine/renderer.py` 已在用 MRT）。
⇒ **推荐 A**（用最小代价换"顺序无关"这个正确性），把 B 作为可选后续。

**落地**：`engine/renderer.py` 加 `oit_mode`（`'sort'`（现状）/ `'wboit'`）；
`engine/postfx.py` 或新 `engine/oit.py` 承担「透明 pass + 合成」；`PostSettings` 加开关。

### P2-4 · 点云渲染 + EDL
- **点云**：`engine/pointcloud.py` —— `PointCloud` 数据（positions/colors/sizes）+ `PointCloudMesh`
  （`gl_PointSize` 或 impostor quad）；**imposter 两种**（`SPHERE` / `SURFEL`，对齐 Easy3D 的两档）。
- **EDL**（Eye-Dome Lighting，点云无算法线的轮廓增强）：
  比较**邻域深度**与自身深度的差，累加出"暗边"：`edl = exp(-k · Σ max(0, (z_self - z_nb) / z_self))`。
  作为 `postfx` 链的一个 pass（半分辨率 + 上采样，与 SSAO 同构）。
- **载体**：**程序化生成点云**（零外部素材，如"点云地形 / 点云龙卷"），由 `scripts/` 或 demo 内生成。

---

## 3. 落地步骤（每步独立验收）

| 步 | 内容 | 验收 |
|----|------|------|
| 1 | `engine/soft_shadow.py` + PCSS 着色器；`shadow_map.py` 加 P 键三档 | 出图；PCSS 半影随光源尺寸/距离变化 |
| 2 | `engine/oit.py`（WBOIT）+ `renderer.oit_mode` | 相交半透明**无排序伪影**；与 `sort` 模式画面可对照 |
| 3 | `engine/pointcloud.py` + 程序化点云 demo | 点云出图；`SPHERE`/`SURFEL` 两档可切 |
| 4 | EDL pass 接入 `postfx` | 点云轮廓增强可见；开关对照 |
| 5 | 测试 + 文档 + 三套回归 | 见 §4 |

---

## 4. 验收标准

- `python -m tests.test_p2_render`（新）→ `ALL_P2_TESTS_PASSED`：
  ① PCSS 的 blocker/penumbra 数值（CPU 参考实现 vs 着色器约定）；
  ② WBOIT 的**权重函数**与合成公式（CPU 参考）；③ 点云数据生成与 EDL 核的可复现性；
- `run_tests` / `test_engine` / `test_cores` / `test_skin` / `test_gpu_cull` **全绿**（零回归）；
- `config_boot all` 6/6 OK（渲染改动不应影响逻辑验收）；
- 各 demo `--smoke` / `--shot` 出图；**原 PCF 与排序透明路径保留可用**（开关回退）。

---

## 5. 风险与对策

| 风险 | 对策 |
|------|------|
| **GPL 传染**（参考 Easy3D） | **只读思路、独立重写**；不复制任何源码片段；不引入其依赖 |
| WBOIT 在 GL 3.3 的 MRT/双 RT 支持 | 3dworld 已在用 MRT（颜色+法线深度）；本机实测可用 |
| PCSS 采样数↑ → 帧率↓ | 固定 Poisson 盘 + 半影半径自适应（远/近自动降采样） |
| 点云 `gl_PointSize` 的驱动上限（通常 63/255） | impostor quad 路径为备选（不依赖点大小上限） |
| 破坏现有渲染 | 新路径**默认关**（开关 + 回退），旧路径保留 |

---

## 6. 待确认（4 问，带推荐）

| # | 问题 | 推荐 |
|---|------|------|
| 1 | **OIT 方案**：Weighted Blended OIT（单趟）vs Dual Depth Peeling（多趟，更贴 Easy3D 原思路） | **WBOIT** —— 1 趟、GL 3.3 友好、正确性收益相同 |
| 2 | **PCSS 载体**：扩展现有 `demos/shadow_map.py`（P 键三档）vs 新建独立 demo | **扩展 shadow_map** —— 与现有 PCF 直接对照，改动小 |
| 3 | **点云载体**：程序化生成（零素材）vs 需要外部点云文件 | **程序化生成**（符合项目"零外部素材"基因） |
| 4 | **执行顺序**：三项一次做完 vs 分批交付（每项独立 commit） | **分批**：P2-1 → P2-2 → P2-4，每项跑通验收后提交 |

---

## 7. 执行结果（2026-10-11 ✅ 已交付）

**决策落实**：① WBOIT ✅ ② 扩展 `shadow_map` ✅ ③ 程序化点云 ✅ ④ 一次做完（全哥选）✅

**落地清单**：

| 项 | 文件 |
|----|------|
| **P2-1 PCSS** | `engine/soft_shadow.py`（确定性 Poisson 盘 + 可注入 GLSL 片段 + **CPU 参考**）；`demos/shadow_map.py` 的 `P` 键改为**三档**（PCF3×3 → PCF5×5 → PCSS） |
| **P2-2 WBOIT** | `engine/oit.py`（`WBOIT` 链 + `supported()` 探测）；`engine/shaders.py` 增 `WBOIT_VERT/FRAG/COMPOSITE`；`demos/wboit_demo.py`（4 个相交球 + `S` 切 WBOIT/排序 + `O` 反转顺序） |
| **P2-4 点云+EDL** | `engine/pointcloud.py`（地形/双螺旋生成器 + EDL GLSL + **CPU 参考**）；`demos/pointcloud_edl.py`（40000 点 + `E` 开 EDL + `1/2` 换云） |
| 测试 | `tests/test_p2_render.py`（**7 项**） |

**验收（全绿）**：
- `tests.test_p2_render` → `ALL_P2_TESTS_PASSED`：Poisson 盘确定性与界内、**PCSS 三步数值**（zb/半影随深度差与光源尺寸单调 + 上限钳制）、
  PCSS/WBOIT/EDL **着色器全部编译通过**、WBOIT `supported()=True` 且 begin/end 无 GL 错误、点云生成器可复现、**EDL CPU 参考**；
- `test_gpu_cull` / `test_skin` / `test_cores` / `test_engine` / `run_tests` 全绿；`config_boot all` **6/6**；
- 三 demo 出图：**PCSS 阴影明显变软**（HUD 显示 `PCSS(light=2.6 search=6tex)`）、
  **相交半透明球颜色混合正确**（WBOIT 与 sort 覆盖率 5.02% vs 5.50%，量级一致）、
  **点云地形 40000 点 + EDL 轮廓增强**（山脊有立体感）。

**实测五坑（都是"静默失败/直接报错"型）**：

1. **`glClearBufferfv` 第 1 参必须是 `GL_COLOR`(0x1800)** —— 我误写成 `0x0400`（那是 `GL_STENCIL_BUFFER_BIT`）→ 直接 `GL_INVALID_ENUM`；
2. **`oit.begin()` 必须清深度缓冲** —— 否则 WBOIT 自己的 depth 是垃圾值，深度测试全失败、**透明物整片消失**；
3. **`sphere()` / `ground_grid()` 返回的是索引网格** —— 用非索引 `vertex_list` 画会**静默忽略 `indices`**，三角形连接错乱（画面呈**碎片状**）；必须 `to_non_indexed()`；
4. **`basic` 程序的 `model` 不显式设置 = uniform 0** → 顶点全塌到原点（画面空）；同理 `tint`/`glow`；
5. **EDL 需要可采样的深度** → `RenderTarget(..., depth_attachment='texture')`（默认是 renderbuffer，不能采样）。

> ⚠️ 另记一条**判据教训**：验证"4 个分散球"的覆盖率时，我一度用"彩色像素 / 包围盒面积"当判据 ——
> 球之间本就有空隙，这个比值天然低（14%），差点误判成"渲染碎片"。**判据要选对**（改用"彩色像素 / 全屏"后两模式量级一致）。
