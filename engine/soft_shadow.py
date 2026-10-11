# -*- coding: utf-8 -*-
"""PCSS 软阴影（P2-1）—— 百分比渐近软阴影。

**合规**：参考 Easy3D `soft_shadow.cpp` 的**思路**（blocker search → 半影估计 → 可变半径 PCF），
**独立实现**，不复制其任何源码（Easy3D 为 GPL-3.0，禁止代码级借鉴）。

经典三步（Fernando 2005 / Bloom 传统做法）::

    ① blocker search：在光源空间固定半径内找遮挡者，求**平均遮挡深度** z_blocker
    ② 半影估计     ：penumbra ∝ (z_receiver - z_blocker) / z_blocker × 光源尺寸
    ③ 可变半径 PCF ：用 penumbra 作采样半径（近处硬边、远处越软）

本模块提供：① 确定性 **Poisson 盘**采样点（黄金角螺旋近似）；
② 可注入片元着色器的 **GLSL 片段**；③ **CPU 参考实现**（测试对照用）。
"""
from __future__ import annotations

import math

# 默认采样数（blocker search 与 PCF 各用一套，同长度）
DEFAULT_SAMPLES = 16


def poisson_disk(n: int = DEFAULT_SAMPLES) -> list:
    """单位圆内的**确定性**采样点（黄金角螺旋 —— 分布均匀、可复现）。

    不用随机 Poisson-disk 生成：阴影采样点每帧变化会产生"噪点抖动"，
    而固定盘在时间上是稳定的（这是离线演示比实时游戏更该做的事）。
    """
    ga = math.pi * (3.0 - math.sqrt(5.0))          # 黄金角 ≈ 2.39996
    pts = []
    for i in range(n):
        r = math.sqrt((i + 0.5) / n)               # 面积均匀
        a = i * ga
        pts.append((r * math.cos(a), r * math.sin(a)))
    return pts


def _glsl_vec2_array(name: str, pts) -> str:
    items = ', '.join(f'vec2({x:.6f}, {y:.6f})' for x, y in pts)
    return f'const vec2 {name}[{len(pts)}] = vec2[{len(pts)}]({items});'


def pcss_glsl(name: str = 'g_poisson', n: int = DEFAULT_SAMPLES) -> str:
    """生成可注入片元着色器的 PCSS 片段（含 Poisson 盘常量 + 两个函数）。

    依赖宿主的这些 uniform（由调用方声明）：``u_shadow``(sampler2D)、``u_texel``(float)、
    ``u_bias``(float)、``u_light_size``(float)、``u_search_texels``(float)、
    ``u_max_penumbra``(float)、``u_pcss``(int)。
    """
    return _glsl_vec2_array(name, poisson_disk(n)) + f'''
// ---- PCSS：① blocker search ② 半影估计 ③ 可变半径 PCF ----
// 返回平均遮挡深度；无遮挡返回 -1。
float pcss_blocker(vec2 uv, float z, float radius) {{
    float sum = 0.0;
    float cnt = 0.0;
    for (int i = 0; i < {n}; ++i) {{
        float d = texture(u_shadow, uv + {name}[i] * radius * u_texel).r;
        if (d < z) {{ sum += d; cnt += 1.0; }}
    }}
    return cnt > 0.0 ? sum / cnt : -1.0;
}}

float pcss_factor(vec2 uv, float z, float bias) {{
    // ① 固定搜索半径内求遮挡者平均深度
    float zb = pcss_blocker(uv, z, u_search_texels);
    if (zb < 0.0) {{ return 1.0; }}                 // 无遮挡 -> 全亮
    // ② 半影宽度 ∝ (接收者深度差) / 遮挡者深度 × 光源尺寸
    float penumbra = clamp((z - zb) / max(1e-5, zb) * u_light_size,
                           0.0, u_max_penumbra);
    // ③ 用半影作半径做 PCF（近处 penumbra→0 退化为单点比较 = 硬边）
    float sum = 0.0;
    float cnt = 0.0;
    for (int i = 0; i < {n}; ++i) {{
        float d = texture(u_shadow, uv + {name}[i] * penumbra * u_texel).r;
        sum += (z - bias > d) ? 0.0 : 1.0;
        cnt += 1.0;
    }}
    return sum / cnt;
}}'''


# ================================================================ CPU 参考
def cpu_pcss(receiver_z: float, blocker_samples, light_size: float,
             max_penumbra: float) -> tuple:
    """CPU 参考实现（测试对照用）。

    Args:
        receiver_z: 接收者深度（光源空间 [0,1]）。
        blocker_samples: 采样到的深度序列（``< receiver_z`` 视为遮挡）。
        light_size / max_penumbra: 与着色器同名的参数。

    Returns:
        ``(z_blocker, penumbra)``；无遮挡时 ``z_blocker = -1.0``、``penumbra = 0.0``。
    """
    occ = [d for d in blocker_samples if d < receiver_z]
    if not occ:
        return (-1.0, 0.0)
    zb = sum(occ) / len(occ)
    pen = (receiver_z - zb) / max(1e-5, zb) * light_size
    return (zb, max(0.0, min(pen, max_penumbra)))


__all__ = ['DEFAULT_SAMPLES', 'poisson_disk', 'pcss_glsl', 'cpu_pcss']
