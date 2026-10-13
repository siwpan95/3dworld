# -*- coding: utf-8 -*-
"""固定步长循环调度：渲染帧率与物理/逻辑更新解耦。

把抖动/高刷/卡顿的「渲染帧 dt」切成固定的物理步（``fixed_dt``），并给出
渲染插值因子 ``alpha``。三个收益（对照实验见 ``tests/bench_fixedstep.py``）：

1. **帧率无关**——30fps 与 144fps 下物理结果一致（步数由时间决定，不由帧数）；
2. **卡帧稳健**——单帧钳制 ``max_frame_time``，防死亡螺旋 / 高速穿透；
3. **可插值**——``alpha`` 供渲染层平滑（本引擎当前直出，接口已预留）。

记账：实现语义对齐 siwpan/an-engine 的
``allin_engine/core/game_loop.py: FixedTimestepLoop``（MIT，siwpan 自研；
范式源自 Gaffer "Fix Your Timestep"）；本模块为 3dworld 内置版（纯标准库）。

用法::

    from engine.fixedstep import FixedStepper
    stepper = FixedStepper(lambda dt: physics.step(dt), fixed_dt=1/60)
    # 每渲染帧：
    stepper.advance(real_dt)      # 内部 0..n 次物理步 + alpha
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

__all__ = ['FixedStepper', 'StepperStats']


@dataclass
class StepperStats:
    """运行统计（调试与验收用）。"""
    fixed_steps: int = 0        # 累计固定更新次数
    render_calls: int = 0       # 累计渲染回调次数
    clamps: int = 0             # 触发单帧钳制的次数（卡帧证据）
    accumulator: float = 0.0    # 当前累加器（秒）
    alpha: float = 0.0          # 最近一次渲染插值因子 [0,1)
    total_sim_time: float = 0.0  # 累计模拟时间（秒）


class FixedStepper:
    """固定时间步长调度器。"""

    def __init__(self, update_fn: Callable[[float], None],
                 render_fn: Optional[Callable[[float], None]] = None,
                 fixed_dt: float = 1.0 / 60.0,
                 max_frame_time: float = 0.25):
        self.update_fn = update_fn
        self.render_fn = render_fn
        self.fixed_dt = float(fixed_dt)
        self.max_frame_time = float(max_frame_time)
        self.accumulator = 0.0
        self.stats = StepperStats()

    def advance(self, real_dt: float):
        """推进一个真实时间片段：内部 0..n 次 update + 1 次 render(alpha)。"""
        if real_dt > self.max_frame_time:
            real_dt = self.max_frame_time        # 钳制：防死亡螺旋
            self.stats.clamps += 1
        self.accumulator += real_dt
        while self.accumulator >= self.fixed_dt:
            self.update_fn(self.fixed_dt)
            self.accumulator -= self.fixed_dt
            self.stats.fixed_steps += 1
            self.stats.total_sim_time += self.fixed_dt
        self.stats.accumulator = self.accumulator
        self.stats.alpha = (self.accumulator / self.fixed_dt
                            if self.fixed_dt > 0 else 0.0)
        if self.render_fn is not None:
            self.render_fn(self.stats.alpha)
            self.stats.render_calls += 1
        return self.stats

    def reset(self):
        self.accumulator = 0.0
        self.stats = StepperStats()
        return self


if __name__ == '__main__':
    # 自检：1 秒 60fps 帧序列 → 恰好 60 步
    log = []
    s = FixedStepper(lambda dt: log.append(dt), fixed_dt=1 / 60)
    for _ in range(60):
        s.advance(1 / 60)
    assert s.stats.fixed_steps == 60, s.stats
    assert abs(s.stats.total_sim_time - 1.0) < 1e-6, s.stats
    print('fixedstep self-check OK', s.stats)
