# experiments/zhuangtaiji_orchestration —— zhuangtaiji 编排试点（P1+）

> 目标：用 [zhuangtaiji](https://gitee.com/siwpan/zhuangtaiji)（多范式决策引擎）的
> **编排层**（事件总线 + 声明式绑定 `when→then`）把两个独立范式连成**演出系统**——
> 演示「多引擎协同」与「演出随机变体」两条 3dworld 进阶能力。
> 依据：`docs/生态库引入评估.md` §三.C。2026-10-09。

## 结构（全部声明式，单一数据源 `director_config.json`）

```
SM_Director（状态机）    演出阶段：teleport→descend→orbit→dive→face_off→return
MC_Embers（马尔可夫链）  圣殿反应堆余烬的随机闪烁（dim / glow / spark / flare）
binding:  when sm.enter → then mc.step      （换阶段即级联演化一步余烬）
```

引擎、绑定、启动全部写在 JSON 配置里；脚本只负责「按真实幕时刻驱动 + 展示」。

## 运行与验收

```bash
python experiments/zhuangtaiji_orchestration/run_director.py
# 源码路径：环境变量 ZHUANGTAIJI_PATH（默认 G:\repos\zhuangtaiji）；纯标准库
```

实测输出（摘录）：

```
t= 121.5s  trigger at_121_5  → sm=descend   mc=spark
t= 125.0s  trigger at_125    → sm=orbit     mc=glow
...
变体 A 余烬: dim → spark → glow → dim → dim → spark
变体 B 余烬: glow → spark → dim → dim → dim → glow
序列是否相同: False（同一时间轴驱动，余烬为随机变体）
```

**验收点**：① 级联链路可见（`sm.enter` → `[mc.step]`）；② 状态机 5 次转移全中；
③ **同一时间轴两次运行余烬序列不同**（随机变体成立）；④ `ORCHESTRATION_OK`。

## 接入价值（对照 3dworld 现状）

1. **跨引擎级联**：一个事件（换阶段）自动驱动另一范式演化——
   「多个孤岛 → 可编排系统」的骨架；
2. **演出随机变体**：3dworld 演出目前**完全确定性**；本试点给出「每次播放
   略有不同」的最小实现（马尔可夫驱动余烬/灯带/碎片等元素）；
3. **声明式配置**：编排即数据（JSON 单一数据源）——配合 zhuangtaiji 的
   Web 编排编辑器可拖拽调整，未来可做「可视化导演台」；
4. **分层共存**：时间轴（logic）管「连续演化」；编排层管「阶段与联动」。

## 三方案对比（已完成的决策能力试点）

| 方案 | 表达力 | 适用 |
|------|--------|------|
| allin `ai/decision`（P1 试点） | 单引擎 FSM | 轻量、零依赖入口 |
| **zhuangtaiji 编排层（本文）** | 多引擎 + `when→then` 绑定 + 9 范式可选 | 演出联动 / 随机变体 / 声明式 |
| 3dworld 现状（手写窗口函数） | 确定性窗口 | 保持用于基准演出，可与前两者叠加 |
