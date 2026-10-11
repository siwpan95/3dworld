# P3-1 · GPU-Driven Rendering（design note）

> 状态：**待全哥放行** · 2026-10-11 · 依据 `docs/补完计划.md` §5 决策④（**已立项**：先做 GPU 剔除切片）
> 参照：feng3d `packages/rendering`（MIT，可借鉴**分层**；其 GPU 剔除/LOD/命令生成的产品级做法）

---

## 1. 目标

把 3dworld 的剔除/绘制从 **CPU 侧**迁到 **GPU 侧**，让"可见性判定 + 绘制参数生成"在 GPU 上跑，
CPU 每帧只做一次 dispatch + 一次 indirect draw（而不是逐物体 Python 循环）：

| 现状（CPU） | 目标（GPU） |
|-------------|-------------|
| `engine/scene.py:216 scene.cull()` 逐 drawable 做球/AABB 测试（Python 循环） | compute shader 并行剔除 |
| 逐物体 `Mesh.draw()`（`renderer._draw_list` 里的 Python for） | **indirect draw** 一次提交 |
| 剔除计数写 `scene.stats`（CPU 统计） | GPU 写回计数 buffer |

**分层参照**（feng3d）：`core`（场景图）→ `rendering`（GPU 剔除/LOD/命令生成）→ `webgpu`（设备/缓冲）。
映射到 3dworld：`core/`（场景）→ **`engine/gpu_cull.py`（新增）** → `engine/targets.py`/`mesh.py`（GL 资源）。

---

## 2. 前置验证（2026-10-11 实测，**已完成**）

> 依据补完计划 §5 的风险提示"先做 GPU 剔除切片**验证 pyglet 可行性**"——本节即该验证。

**环境**：Intel UHD Graphics 630 / pyglet 2.1.16 / `GL_VERSION` 报 **3.3.0**、GLSL **3.30**。

| 能力 | 规范要求 | 本机实测 | 证据 |
|------|:--------:|:--------:|------|
| compute shader | GL 4.3 | ✅ **可用** | 编译执行 `v[i] = v[i]*2+1` → 读回 `[1,3,5,7,9,11,13,15]` 与期望**完全一致** |
| SSBO（`std430` + `glBindBufferBase`） | GL 4.3 | ✅ **可用** | 同上；`glGetBufferSubData` 读回正确 |
| **indirect draw**（`glDrawArraysIndirect`） | GL 4.0 | ✅ **可用** | `glGetError == 0`，中心像素 `(255,128,51)`（与直接绘制一致） |
| `glMultiDrawArraysIndirect` | GL 4.3 | 函数存在（未实测） | — |
| fence / `glMapBufferRange` | GL 3.2 / 3.0 | 函数存在 | — |

**⚠️ 关键风险（必须写进设计）**：`GL_VERSION` 报 **3.3**，而 compute/SSBO 属 **4.3**、indirect 属 **4.0** ——
本机 Intel 驱动**实际放宽**了这些能力，但**规范上 3.3 不含它们**，**换驱动/换机器不保证可用**。
⇒ **P3-1 必须带"运行时能力探测 + CPU 回退"**，否则会把"能跑"变成"某台机器上崩"。

**顺带记录两个踩坑**（都曾造成"静默黑屏"）：
- GL 3.3 core **强制 VAO**：裸 `glVertexAttribPointer` 不建 VAO 会静默不画（pyglet 的 `vertex_list` 会自动建）；
- `pyglet.graphics.ShaderProgram.attributes['x']` 是 **dict**（`['location']` 取值），不是对象属性。

---

## 3. 范围（分三阶段，**先做 A**）

| 阶段 | 内容 | 风险 | 本批 |
|------|------|:----:|:----:|
| **A · GPU 剔除** | compute 对每实例包围球做视锥测试 → 写 `indirect` 参数 + 可见计数 | 中 | ✅ **做** |
| B · GPU LOD | compute 按屏幕占比选 LOD 级，写进 indirect | 中高 | 后续 |
| C · 命令生成 | `glMultiDrawArraysIndirect` 多批命令，一次提交 | 高 | 后续 |

**A 的边界**：只覆盖"**同几何、多实例**"的实例化场景（与现有 `InstancedMesh` 对齐）；
非实例化的独立网格仍走现有 CPU 路径（不做全量重写——铁律：推倒重来只用于结构层）。

---

## 4. 设计（阶段 A）

### 4.1 数据流
```
每帧：
  CPU  更新实例模型矩阵 → 上传（SSBO 或现有实例 VBO）
  GPU  [compute]  每实例：包围球(世界) 与 6 个视锥平面点积 → 可见标记
                  可见者：紧凑写入"可见实例索引表"（compaction）
                  同时 atomicAdd 写"可见计数"
  CPU  把可见计数写进 indirect 参数（或由 compute 直接写 indirect buffer）
  GPU  [draw]     glDrawArraysIndirect / glDrawElementsInstancedIndirect
  CPU（可选）glGetBufferSubData 读回计数 → HUD / 验收
```

### 4.2 新增模块
```
engine/gpu_cull.py        # 新：能力探测 + 剔除程序缓存 + dispatch + indirect 参数
core/shaders/gpu_cull.comp（GLSL，字符串常量放 engine/shaders.py 与现有一致）
```
- `GpuCullSupport`：探测（`glGetString(GL_VERSION)` + 试编译 compute + `glGetError`）→ `available: bool`；
- `FrustumUniform`：6 个平面 + 视锥 8 角（与 `engine/frustum.py` 的 `Frustum` 同源，**单一事实来源**）；
- `cull_instances(instances_ssbo, count, frustum) -> visible_count`；
- **CPU 回退**：`available == False` 时原样走 `scene.cull()`（**行为不变**，可单测对照）。

### 4.3 黄金判据（验收核心）
**GPU 剔除的可见集合 == CPU 剔除的可见集合**（对同一帧、同一视锥、同一实例数据）。
做法：跑一次 GPU 剔除 + 读回可见索引表，与 `scene.cull()` 的结果**排序后逐项比对**；
差异只允许出现在"边界擦边"实例（浮点 ±ε），且须逐例解释。

---

## 5. 落地步骤（每步可独立验收）

1. `engine/gpu_cull.py` 骨架 + **能力探测**（先只打印结果，不改渲染）；
2. GLSL `gpu_cull.comp`：视锥球测试 → 可见标记 → compaction + 计数（atomic）；
3. Python 侧：SSBO 上传实例包围球、dispatch、`glMemoryBarrier`、读回计数；
4. **CPU/GPU 对照测试**（`tests/test_gpu_cull.py`）：同视锥下两者可见集一致；
5. indirect 参数由 compute 写出 → `glDrawElementsInstancedIndirect` 出画；
6. demo：`demos/gpu_cull_field.py`（几万实例 + F2 切 GPU/CPU 剔除 + 计数对照）；
7. 回退验证：强制 `available=False` 时行为与改造前**逐字一致**。

---

## 6. 验收标准

- `python -m tests.test_gpu_cull` → `ALL_GPU_CULL_TESTS_PASSED`（含"GPU==CPU 可见集"与"回退一致"两组）；
- `python -m tests.run_tests` / `test_engine` / `test_cores` / `test_skin` **全绿**（零回归）；
- `config_boot all` 5/5 OK（剔除属渲染路径，不应影响逻辑验收）；
- demo `--smoke` 无报错、`--shot` 出图；F2 切换 GPU/CPU 时**画面与计数一致**；
- 能力探测在**本机**报 `available=True`；人为置 False 时全链路仍正常（回退）。

---

## 7. 风险与对策

| 风险 | 对策 |
|------|------|
| **GL 3.3 下用 4.x 特性**（本机可用但跨机不保证） | **运行时探测 + CPU 回退**（§4.2）；探测失败一律走 CPU 路径 |
| compute 结果与 CPU 不一致（浮点/边界） | 黄金判据测试（§4.3）+ 边界 ε 逐例解释 |
| `glGetBufferSubData` 同步读回造成 stall | 仅在"对照测试/HUD"用；**正经渲染路径不读回**（计数由 GPU 侧消费） |
| SSBO 与现有实例 VBO 双份数据 | 先复用现有实例属性；后续再合并（阶段 C） |
| 破坏现有渲染（回归） | 新增路径**默认关闭**（开关 + 探测），旧路径保留；逐步切换 |

---

## 8. 决策记录（2026-10-11 全哥已定调 ✅）

| # | 问题 | **决定** |
|---|------|----------|
| 1 | 范围 | ✅ **全做 A/B/C** —— GPU 剔除 + GPU LOD + 命令生成（完整 GPU-driven 流水线） |
| 2 | 回退策略 | ✅ **必须**运行时能力探测 + CPU 回退（本机 GL 报 3.3 却支持 4.x 特性，跨机风险实打实） |
| 3 | 载体 | ✅ **新建** `demos/gpu_cull_field.py`（不动已验收的 `engine_showcase.py`） |
| 4 | 计数读回 | ✅ **可关式**：默认帧不读回（避免 stall），F2 对照/HUD 时读 |

**由决策 1 推出的实现分层**（一本 design note 覆盖三阶段，但**分步交付、每步验收**）：

```
阶段 A（GPU 剔除）    compute: 每实例包围球 vs 6 视锥平面 → 可见标记 + compaction + atomic 计数
阶段 B（GPU LOD）     compute: 按屏幕占比/距离选 LOD 级 → 分 LOD 段写索引表 + 各组计数
阶段 C（命令生成）    compute: 直接产出 3 组 indirect 参数 → CPU 只调 3 次
                      glDrawArraysInstancedIndirect（baseInstance 指向各 LOD 段）
顶点侧               VS(#version 430) 用 gl_BaseInstance + gl_InstanceID 查"可见实例表" SSBO
```

**⚠️ 因选了 A/B/C 全量，风险提示**：
- 顶点着色器与 3dworld 现有 `#version 330 core` 体系**不同版本**（需 430 才可 `gl_BaseInstance`）→
  新路径用独立 shader 源，**不改动**现有 330 着色器；
- 三阶段耦合度高（C 依赖 A/B 的索引表布局）→ 先定 **SSBO 布局契约**再写代码，避免返工；
- 仍需 §7 的"探测 + 回退"兜底（决策 2）。

---

*本 design note 的 §2 前置验证与 §9 执行结果均已完成，是本 note 的事实基础。*

---

## 9. 执行结果（2026-10-11 ✅ 已交付）

**决策落实**：① **A/B/C 全做** ✅ ② 运行时探测 + CPU 回退 ✅ ③ 新建 demo ✅ ④ 可关式读回 ✅

**落地清单**：

| 项 | 文件 |
|----|------|
| compute（剔除 + LOD + 命令生成，一趟完成） | `engine/shaders.py` → `GPU_CULL_COMP` |
| 顶点着色器（SSBO 查可见表 → 取实例矩阵） | `engine/shaders.py` → `GPU_DRIVEN_VERT` + `get_compute()` |
| 驱动器（能力探测 / SSBO 管理 / dispatch / indirect 提交 / CPU 对照 / 读回） | `engine/gpu_cull.py` |
| demo（4096 实例 · F2 GPU/CPU 对照 · F3 LOD 开关） | `demos/gpu_cull_field.py` → `docs/gpu_cull_01..03.png` |
| 测试 | `tests/test_gpu_cull.py`（**7 项**） |

**验收（全绿）**：
- `tests.test_gpu_cull` → `ALL_GPU_CULL_TESTS_PASSED`：**GPU 剔除 == CPU 剔除（512/512 逐实例一致）**、
  LOD 分组一致、命令布局逐条核实、边界（近距 114 可见 / 超 far 0 可见）、CPU 回退语义一致；
- `test_engine` / `test_cores` / `test_skin` / `run_tests` 全绿；`config_boot all` **5/5 OK**；
- demo 出图：几千球阵列（起伏地形感），HUD `GPU 剔除 [compute OK] · LOD on · 可见 2568/4096 · LOD组 [0, 36, 2532]`。

**实测四坑（全是"静默失败"型——画面空/只剩一个球，但一律不报错）**：

1. **pyglet 的 shadow context 报 4.6、真实窗口报 3.3** —— 能力探测**必须在真实窗口创建后**做，
   否则会拿 shadow context 的版本误判（本机实测差 4.6 vs 3.3）。
2. **`GL_ARB_shader_draw_parameters`（`gl_BaseInstanceARB` / `gl_DrawIDARB`）在 3.3 上下文不生效** ——
   早先"每实例一条命令 + `baseInstance` 索引实例矩阵"的方案，会让**全部实例塌到 `mat[0]`**
   （画面只剩一个球，**不报错**）。⇒ 改为 **`gl_InstanceID`（GL 3.1+ 核心）+ LOD 固定分区表**：
   compute 把可见实例按 LOD 写进 `visible[lv*N + slot]`，命令的 `instanceCount` 字段直接由 `atomicAdd` 累加
   （⇒ 免第二趟、免 CPU 读回）。
3. **pyglet 的 uniform setter 表不含 `uint`**（`KeyError: ctypes.c_ulong`）—— uint uniform 一律改 **int** 传。
4. **compute 里的 `glUseProgram` 会切走当前 program** —— `draw()` 前必须重新 `prog.use()`，
   否则 `uLodBase` 被设到错的 program 上（画面空）。

**性能口径**：CPU 每帧只做 **1 次 dispatch + `len(LOD)` 次 `glDrawArraysIndirect`**；
剔除/LOD/命令全部在 GPU 侧生成。`read_counts()` 仅在对照/HUD 时调用（决策④：默认可关，避免同步 stall）。
