"""向量化噪声（值噪声 / Perlin / fBm / 分形），返回 0..1 的 numpy 数组。

按三仓「复制优于共享」约定，自 worldbuilder.worldbuilder.noise 复制而来
（同为 MIT 仓库、兄弟仓；此复制使 3dworld 不再 import worldbuilder，
满足「三仓互不引用、仅以 worldspec v1 文件契约为集成点」的流水线纪律）。
输入 x, y 可为标量、同形数组，或**可广播**的不同形数组；seed 控制可复现性。

## 复制之后本仓改过什么（记账：复制品与源的分叉要写清）

1. **支持广播**：`fbm` 原先用 `np.zeros_like(x)` 累加，于是 `x` 形如 `(H,1)`、
   `y` 形如 `(1,W)` 时（numpy 用户最自然的网格写法）会报
   `non-broadcastable output operand with shape (24,1) doesn't match the
   broadcast shape (24,17)`。现在三个入口都先 `np.broadcast_arrays(x, y)`。
   同形输入的数值**逐位不变**（由 `tests/test_noise.py` 与标量版逐位比对守着）。
2. 其余逐字保留 —— 包括 `_hash2` 的常数，因为**标量版 `logic/noise.py` 用的是
   同一套**，两边必须逐位一致（否则 2.5D 世界生成与 probes 离线烘焙会悄悄分叉）。
"""
from __future__ import annotations

import numpy as np


def _hash2(ix, iy, seed):
    """整数格点 -> 确定性伪随机 [0,1)。支持数组输入。"""
    ix = np.asarray(ix, dtype=np.int64)
    iy = np.asarray(iy, dtype=np.int64)
    h = ix * 374761393 + iy * 668265263 + np.int64(seed) * 1013904223
    h = (h ^ (h >> 13)) * 1274126177
    h = h ^ (h >> 16)
    return (h.astype(np.int64) & 0x7FFFFFFF).astype(np.float64) / 0x7FFFFFFF


def _smooth(t):
    return t * t * (3.0 - 2.0 * t)


# 2D 梯度噪声理论最大投影幅度（√(xf²+yf²) 在角点最大 ≈ √2/2）
_PERLIN_NORM = np.sqrt(2.0) / 2.0


def _lerp(a, b, t):
    return a + t * (b - a)


def value_noise(x, y, seed: int = 0) -> np.ndarray:
    """值噪声：格点随机值 + 平滑插值，返回 0..1。"""
    x, y = np.broadcast_arrays(np.asarray(x, dtype=np.float64),
                               np.asarray(y, dtype=np.float64))
    xi = np.floor(x).astype(np.int64)
    yi = np.floor(y).astype(np.int64)
    xf = x - xi
    yf = y - yi
    v00 = _hash2(xi, yi, seed)
    v10 = _hash2(xi + 1, yi, seed)
    v01 = _hash2(xi, yi + 1, seed)
    v11 = _hash2(xi + 1, yi + 1, seed)
    u = _smooth(xf)
    v = _smooth(yf)
    return _lerp(_lerp(v00, v10, u), _lerp(v01, v11, u), v)


def perlin_noise(x, y, seed: int = 0) -> np.ndarray:
    """Perlin 梯度噪声（基于格点角度梯度），返回 0..1。"""
    x, y = np.broadcast_arrays(np.asarray(x, dtype=np.float64),
                               np.asarray(y, dtype=np.float64))
    xi = np.floor(x).astype(np.int64)
    yi = np.floor(y).astype(np.int64)
    xf = x - xi
    yf = y - yi
    ang00 = _hash2(xi, yi, seed) * 2.0 * np.pi
    ang10 = _hash2(xi + 1, yi, seed) * 2.0 * np.pi
    ang01 = _hash2(xi, yi + 1, seed) * 2.0 * np.pi
    ang11 = _hash2(xi + 1, yi + 1, seed) * 2.0 * np.pi
    gx00, gy00 = np.cos(ang00), np.sin(ang00)
    gx10, gy10 = np.cos(ang10), np.sin(ang10)
    gx01, gy01 = np.cos(ang01), np.sin(ang01)
    gx11, gy11 = np.cos(ang11), np.sin(ang11)
    d00 = gx00 * xf + gy00 * yf
    d10 = gx10 * (xf - 1) + gy10 * yf
    d01 = gx01 * xf + gy01 * (yf - 1)
    d11 = gx11 * (xf - 1) + gy11 * (yf - 1)
    u = _smooth(xf)
    v = _smooth(yf)
    n = _lerp(_lerp(d00, d10, u), _lerp(d01, d11, u), v)
    # 按理论最大幅度归一化，使输出能覆盖完整 0..1 区间（提升对比度）
    return np.clip((n / _PERLIN_NORM + 1.0) / 2.0, 0.0, 1.0)


def fbm(x, y, octaves: int = 4, lacunarity: float = 2.0, gain: float = 0.5,
        seed: int = 0) -> np.ndarray:
    """分形布朗运动（fBm）：多层 perlin 叠加，返回 0..1。"""
    # `np.zeros_like(x)` 是源版写法，但它按 **x 的形状** 定累加器 —— x 形如 (H,1)、
    # y 形如 (1,W) 时（网格采样最常见的写法）会在 `total += ...` 处炸
    # "non-broadcastable output operand"。先 broadcast 再定形状，同形输入逐位不变。
    x, y = np.broadcast_arrays(np.asarray(x, dtype=np.float64),
                               np.asarray(y, dtype=np.float64))
    total = np.zeros_like(x, dtype=np.float64)
    amp = 1.0
    freq = 1.0
    maxv = 0.0
    for o in range(octaves):
        total += amp * perlin_noise(x * freq, y * freq, seed=seed + o)
        maxv += amp
        amp *= gain
        freq *= lacunarity
    return np.clip(total / maxv, 0.0, 1.0)


def fractal_noise(x, y, octaves: int = 4, seed: int = 0) -> np.ndarray:
    """分形噪声：fBm 的便捷封装（默认 lacunarity=2.0 / gain=0.5）。"""
    return fbm(x, y, octaves=octaves, lacunarity=2.0, gain=0.5, seed=seed)
