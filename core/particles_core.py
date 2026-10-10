# -*- coding: utf-8 -*-
"""粒子共同核 ``particles``（实现 ``core.contracts.ParticlesCore``）。

P0-1 新建（封装）：把 ``demos/particles_gpu`` 的粒子**参数/生命周期**收成一个
可复用核心。GPU 端的实例化绘制由渲染共同核负责；本核持有 CPU 侧的粒子状态与
简单数值步进，**headless 安全**（不触碰 GL 上下文）。

> 依赖：仅 Python 标准库（``math``/``random``）+ ``cordis``。
"""
from __future__ import annotations

import math
import random

from core._cordis import Service

# 预设参数（名字 -> (初速, 寿命, 重力)）——与 demo 的观感取向一致
PRESETS = {
    'spark':   dict(speed=6.0, life=0.8, gravity=-9.0),
    'smoke':   dict(speed=1.5, life=2.4, gravity=0.6),
    'debris':  dict(speed=4.0, life=1.6, gravity=-12.0),
    'magic':   dict(speed=3.0, life=1.2, gravity=0.0),
}


class ParticlesService(Service):
    """GPU 粒子（法术/天气/碎屑/弹道）——CPU 状态 + 数值步进。"""

    def __init__(self, ctx, config):
        super().__init__(ctx, "particles")
        cfg = config or {}
        self._max = int(cfg.get('max', 45000))
        self._count = int(cfg.get('count', 0))
        self._parts = []            # list[dict(pos, vel, age, life)]
        self._rng = random.Random(int(cfg.get('seed', 1)))

    def emit(self, preset, count, origin=(0.0, 0.0, 0.0), **kw):
        """以某预设发射一批粒子。

        Args:
            preset: 预设名（见 ``PRESETS``）或含 ``speed/life/gravity`` 的 dict。
            count: 发射数量。
            origin: 发射原点 ``(x, y, z)``。
        """
        p = PRESETS.get(preset, PRESETS['spark']) if isinstance(preset, str) else (preset or {})
        speed = float(p.get('speed', 6.0))
        life = float(p.get('life', 1.0))
        ox, oy, oz = origin
        for _ in range(int(count)):
            dx, dy, dz = self._rng.gauss(0, 1), abs(self._rng.gauss(0, 1)), self._rng.gauss(0, 1)
            n = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
            self._parts.append(dict(
                pos=[ox, oy, oz],
                vel=[dx / n * speed, dy / n * speed, dz / n * speed],
                age=0.0, life=life, gravity=float(p.get('gravity', 0.0)),
            ))
        self._count = len(self._parts)
        return self._count

    def set_count(self, count):
        """调整粒子总数（截断或补齐为占位粒子）。"""
        count = max(0, min(int(count), self._max))
        if count < len(self._parts):
            del self._parts[count:]
        else:
            while len(self._parts) < count:
                self._parts.append(dict(pos=[0.0, 0.0, 0.0], vel=[0.0, 0.0, 0.0],
                                        age=0.0, life=1.0, gravity=0.0))
        self._count = len(self._parts)
        return self._count

    def update(self, dt):
        """推进粒子模拟（重力 + 位置积分 + 寿命回收）。"""
        alive = []
        for q in self._parts:
            q['age'] += dt
            if q['age'] >= q['life']:
                continue
            q['vel'][1] += q['gravity'] * dt
            for k in range(3):
                q['pos'][k] += q['vel'][k] * dt
            alive.append(q)
        self._parts = alive
        self._count = len(alive)

    def draw(self):
        """绘制粒子（headless 空操作；真实绘制由渲染共同核的实例化管线执行）。"""
        return None

    def count(self):
        return len(self._parts)

    def __cordis_init__(self):
        return None


__all__ = ['ParticlesService', 'PRESETS']
