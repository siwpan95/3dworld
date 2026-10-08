# experiments/allin_decision —— allin-engine 决策试点（P1）

> 目标：用 [allin-engine](https://pypi.org/project/allin-engine/) 的
> `ai/decision`（零依赖）把 kkrieger 第六幕的镜头编排表达为**导演状态机**。
> 依据：`docs/生态库引入评估.md`。2026-10-09。

## 状态机结构

```
teleport ──at_121_5──▶ descend ──at_125──▶ orbit ──at_136_5──▶
dive ──at_140_8──▶ face_off ──at_141_8──▶ return
```

- 每个状态携带「内容 + 进入动作 + 转移表」；
- 机位关键帧为**真实数据**（`logic/kkrieger.py` CAM，118.2–144，10 个 key）；
- 事件为时间轴触发（`at_*`）——未来交互化时可叠加动态事件（玩家干扰/随机演出）。

## 运行与验收

```bash
python experiments/allin_decision/perform_director.py
# allin-engine 源码路径：环境变量 AN_ENGINE_PATH（默认 G:\repos\an-engine）
```

验收点（实测全过 ✓）：状态序列 teleport→…→return 5 次转移全中、
动作日志 16 条（enter/exit/transition）、输出 `DIRECTOR_OK`。

## 结论

- 行为/决策范式可**替代手写窗口函数**表达演出结构（状态与转移自明、易扩展）；
- 与 3dworld 现有事件总线（`logic/events.py`）可共存：
  **状态机管「阶段与转移」，时间轴管「连续演化」**；
- 后续若做交互化（可玩场景），状态机可自然叠加动态事件——
  这是本次试点验证的核心接入价值。
