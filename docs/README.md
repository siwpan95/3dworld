# 文档索引

> **本文件由 `scripts/docs_index.py` 生成，不要手改** —— 
> 加了文档就重跑一次：`python scripts/docs_index.py --write`。
> 门禁 `docs-index-fresh`（`python scripts/verify.py`）会核对它是否最新，
> 所以"新加了文档但忘了登记"会在验收里变红，而不是悄悄腐烂。

共 **27 篇**，按轨道分组：

## P0 试点转正

| 文档 | 讲什么 |
|------|--------|
| [`P0-1 · 核心抽离到 core/（design note）`](P0-1_核心抽离_design_note.md) | 1. 让 12 个共同核（core/contracts.py 已定义契约）有正式、唯一、跨内核复用的实现； |

## P1 库化

| 文档 | 讲什么 |
|------|--------|
| [`P1-⑤ 玩法通用模块（背包 / 技能树 / 掉落 / 寻路强化）design note`](P1-5_玩法通用模块_design_note.md) | 把"每个内核各写一份"的玩法零件，提成 3 个新共同核 + 1 处寻路强化， |

## P2 分层归位

| 文档 | 讲什么 |
|------|--------|
| [`P2-1/2/4 · 渲染能力补强（PCSS 软阴影 + OIT 透明 + 点云/EDL）design note`](P2-124_渲染能力补强_design_note.md) | soft_shadow.cpp(196 行，PCSS：blocker search + PCF，5 种 Poisso… |
| [`P2-3 · 蒙皮骨骼动画（design note）`](P2-3_蒙皮骨骼动画_design_note.md) | logic/skeleton.py 顶部 docstring 记录了当时的 Spike 结论： |
| [`P2-4 命名空间收口设计说明（world3d.*）—— P2 收尾`](P2-4_命名空间收口_design_note.md) | SERVICES 表、core/contracts.py 的契约清单、world3d/_info.py 的公共面清单… |

## P3 帧图管线

| 文档 | 讲什么 |
|------|--------|
| [`P3-1 · GPU-Driven Rendering（design note）`](P3-1_GPU驱动渲染_design_note.md) | 把 3dworld 的剔除/绘制从 CPU 侧迁到 GPU 侧，让"可见性判定 + 绘制参数生成"在 GPU 上跑， |
| [`P3-a 帧图（Frame Graph）设计说明`](P3-a_帧图_design_note.md) | ④ 轨道攒下四个效果（CSM / 体积雾 / 景深 / 运动模糊），它们互相共用资源： |
| [`P3-b 帧图驱动渲染 设计说明（P3 收口）`](P3-b_帧图驱动渲染_design_note.md) | P3-a 的帧图能编译出 plan.order / culled / aliases，但没有 consumer —— |

## ③ 可视化导演台

| 文档 | 讲什么 |
|------|--------|
| [`③ 可视化导演台（Director）design note`](3_可视化导演台_design_note.md) | 3dworld 已有全套底层零件，但缺"把它们串起来给人用"的那一层： |

## ④ 引擎渲染扩展

| 文档 | 讲什么 |
|------|--------|
| [`④-1 级联阴影（CSM）设计说明`](4-1_级联阴影CSM_design_note.md) | 单张阴影图的精度是固定的：纹素在世界里的尺寸 = 2·半径 / 分辨率。 |
| [`④-2 体积雾与光轴（god rays）设计说明`](4-2_体积雾与光轴_design_note.md) | 体积雾最常见写法是"沿射线采 N 步、每步乘 exp(-ρ·dt)"。它能跑，但有两个硬问题： |
| [`④-3 景深（DoF）设计说明`](4-3_景深DoF_design_note.md) | 景深不是「把背景高斯模糊一下」。真实相机是每一点按弥散圆（CoC）大小摊成一个圆盘： |
| [`④-4 运动模糊设计说明（④ 轨道收口）`](4-4_运动模糊_design_note.md) | 屏幕空间的"整屏方向模糊"（拿一个全局向量去采样）让所有像素糊得一样多 —— |
| [`④ 引擎渲染扩展 design note（CSM 级联阴影 / 体积雾 / 景深 / 运动模糊）`](4_引擎渲染扩展_design_note.md) | ❌ 缺口（本项要补）： |

## BQM 机制致敬线（并行轨道）

| 文档 | 讲什么 |
|------|--------|
| [`BQM（砖块迷宫建造者）机制致敬 · 复刻可行性探讨`](BQM机制致敬_复刻可行性.md) | 开发 Wonderland Kazakiri inc.（日本），发行 Gamirror Games / idealC… |

## 规划与总览

| 文档 | 讲什么 |
|------|--------|
| [`3dworld 引擎化改造规划（Plan · 待 review 放行）`](5_引擎化改造规划.md) | 每阶段可独立验收、独立提交，不搞大爆炸式重构（见 §8 的"绞杀者"策略）。 |
| [`3dworld 核心引擎（engine/）设计与技术要点`](引擎设计.md) | 铁律：依赖只允许向下（games → core → engine），内核对之间、游戏之间不得互相 import； |
| [`3dworld 补完计划（Plan · 待 review 放行）`](补完计划.md) | GPL-3.0（Blender / Easy3D / GEngine）、无许可（univ）禁止任何形式的代码搬运； |

## 专题笔记与指南

| 文档 | 讲什么 |
|------|--------|
| [`LLM 场景生成工作流`](LLM场景生成工作流.md) | 只存「生成器名 + 参数 + 材质 + 变换」——几何在构建时由 world3d.engine.geometry 现… |
| [`P4 机械门禁与文档站 设计说明（P4 收口 = 全部路线完成）`](P4_机械门禁与文档站_design_note.md) | 本轮改造里，同一件事被手改过 5~7 次，每一次都可能漏一处： |
| [`Cordis 集成（配置树 · 服务宿主）`](cordis集成.md) | 用 Cordis（「时空可组合性」元框架——插件即服务、 |
| [`pyglet 3.0 迁移预研`](pyglet3_迁移预研.md) | 来源：https://pyglet.readthedocs.io/en/development/programmin… |
| [`复刻早期经典游戏 · 规划（3dworld × Cordis）`](remake_ideas.md) | 用 3dworld 已有的图形/音频/动画能力，复刻一批早期单机代表作的玩法机制。 |
| [`体素实现对比：voxel_terrain.py × minecraft_style.py`](体素实现对比.md) | （邻列高度 vs 当前 y），三行判断、无邻居坐标运算； |
| [`分发指南（打包 / 录制 / 发布）`](分发指南.md) | scripts/record_all.py 查找顺序：PATH → 内置候选路径。本机可用位置（2026-10-08… |
| [`3dworld 架构设计 — 向 3D 动画制作方向演进`](架构设计_3D动画方向.md) | 当前 demo 已具备三维世界的全部要素（透视投影、Batch 几何、轨道相机、主循环）， |
| [`生态库引入评估：bloodmusic / allin-engine`](生态库引入评估.md) | 一次渲染成功（渲染耗时仅约 30 秒） |

---

> 图片产物（剧照 / 深度图小窗等）在 `docs/*.png`，由 `scripts/verify_demos.py` 与各 demo 的 `--shot` 生成，不入索引。
