# -*- coding: utf-8 -*-
"""曲线库：Bézier（任意阶）与 Catmull-Rom 样条（2D / 3D 通用，纯标准库）。

自研（非移植）
--------------
本地仓库盘点（2026-10-19）结论：``an-engine`` 的 ``curve.py`` 是「参数曲线渲染
原语」而非插值样条，本地并无现成的 Bézier / Catmull-Rom 实现，故本模块从零自研。
立场与 ``logic/`` 其余模块一致：**纯标准库、零依赖、确定性、可 headless 单测**
（不碰 pyglet / GL / 全局随机状态）。

设计要点
--------
* 点统一表示为 n 维 ``tuple``（2 或 3 维皆可），所有运算按分量进行，
  因此同一套函数服务 2D 与 3D。
* Bézier 用 De Casteljau 递推（任意阶、数值稳定），不是展开伯恩斯坦多项式。
* Catmull-Rom 用标准基数样条基（τ=0.5），**必过所有控制点**；端点用邻点
  镜像外推（open）或环绕（closed）。
* 确定性：给定控制点与参数，输出逐位一致（无随机）。
"""
from __future__ import annotations

import math

__all__ = [
    'bezier_point', 'bezier_curve', 'bezier_tangent',
    'catmull_rom_spline', 'catmull_rom_tangent_at', 'curve_length',
]


# ----------------------------------------------------------------- n 维向量基元 --
# 点 = tuple[float, ...]（长度 2 或 3）。以下运算按分量进行，与维度无关。

def _add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _scale(a, s):
    return tuple(x * s for x in a)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _len(a):
    return math.sqrt(_dot(a, a))


# --------------------------------------------------------------------- Bézier --

def bezier_point(control, t):
    """求 n 阶 Bézier 曲线在参数 ``t∈[0,1]`` 处的点（De Casteljau 递推）。

    控制点 ``control`` 为长度 ≥2 的 n 维点序列。``t=0`` → 第一个控制点，
    ``t=1`` → 最后一个控制点（端点插值性质）。任意阶、任意维。

    例：二次 Bézier ``[p0,p1,p2]`` 在 ``t=0.5`` 处为
    ``0.25·p0 + 0.5·p1 + 0.25·p2``。
    """
    pts = [tuple(c) for c in control]
    if len(pts) < 2:
        raise ValueError('Bézier 至少需要 2 个控制点')
    # De Casteljau：每一轮把相邻两点按 t 线性插值，直到剩 1 点
    while len(pts) > 1:
        pts = [_add(_scale(pts[i], 1.0 - t), _scale(pts[i + 1], t))
               for i in range(len(pts) - 1)]
    return pts[0]


def bezier_curve(control, segments=64):
    """把 n 阶 Bézier 离散成 ``segments+1`` 个采样点（含首尾控制点）。

    用于把曲线变成可渲染 / 可测的多段线。``segments`` 越大越平滑。
    """
    if segments < 1:
        raise ValueError('segments 必须 ≥1')
    return [bezier_point(control, i / segments) for i in range(segments + 1)]


def bezier_tangent(control, t, eps=1e-5):
    """用中心差分估算 Bézier 在 ``t`` 处的切线方向（未归一化）。

    n 维通用；返回的是方向向量（非单位向量）。``t`` 落在端点时单侧差分。
    """
    if t <= 0.0:
        return _sub(bezier_point(control, eps), bezier_point(control, 0.0))
    if t >= 1.0:
        return _sub(bezier_point(control, 1.0), bezier_point(control, 1.0 - eps))
    return _sub(bezier_point(control, t + eps), bezier_point(control, t - eps))


# -------------------------------------------------------------- Catmull-Rom --

def _catmull_rom_point(p0, p1, p2, p3, t):
    """单段 Catmull-Rom（τ=0.5）在 ``t∈[0,1]`` 处的点。

    ``p1``→``p2`` 为当前段，``p0``/``p3`` 为前后邻点；``t=0``→``p1``，``t=1``→``p2``
    （保证整条样条经过所有控制点）。
    """
    t2 = t * t
    t3 = t2 * t

    def comp(c0, c1, c2, c3):
        return 0.5 * (
            2.0 * c1
            + (-c0 + c2) * t
            + (2.0 * c0 - 5.0 * c1 + 4.0 * c2 - c3) * t2
            + (-c0 + 3.0 * c1 - 3.0 * c2 + c3) * t3
        )

    return tuple(comp(c0, c1, c2, c3)
                 for c0, c1, c2, c3 in zip(p0, p1, p2, p3))


def catmull_rom_spline(control, segments_per_span=16, closed=False):
    """把控制点序列插值成平滑样条（经过全部控制点）。

    参数：
        control:          控制点列表（长度 ≥2；open 需要 ≥3 才能外推端点）。
        segments_per_span: 每段（相邻两控制点之间）离散的段数。
        closed:           True 时首尾相接（环绕，最后一段回到第一个点）。

    返回：采样点列表（多段线），末点等于最后一个控制点。
    """
    pts = [tuple(p) for p in control]
    if len(pts) < 2:
        raise ValueError('Catmull-Rom 至少需要 2 个控制点')
    if not closed and len(pts) < 3:
        raise ValueError('open 样条至少需要 3 个控制点（端点需邻点外推）')
    if segments_per_span < 1:
        raise ValueError('segments_per_span 必须 ≥1')

    n = len(pts)
    spans = []
    if closed:
        for i in range(n):
            p0 = pts[(i - 1) % n]
            p1 = pts[i]
            p2 = pts[(i + 1) % n]
            p3 = pts[(i + 2) % n]
            spans.append((p0, p1, p2, p3))
    else:
        for i in range(n - 1):
            p0 = pts[i - 1] if i - 1 >= 0 else pts[i]        # 首段左邻镜像
            p3 = pts[i + 2] if i + 2 < n else pts[i + 1]      # 末段右邻镜像
            p1 = pts[i]
            p2 = pts[i + 1]
            spans.append((p0, p1, p2, p3))

    out = []
    for (p0, p1, p2, p3) in spans:
        for s in range(segments_per_span):
            out.append(_catmull_rom_point(p0, p1, p2, p3, s / segments_per_span))
    out.append(pts[-1])  # 末控制点
    return out


def catmull_rom_tangent_at(control, i, closed=False):
    """控制点 ``control[i]`` 处的 Catmull-Rom 切线方向（未归一化，= 0.5·(后邻-前邻)）。

    open 样条端点用邻点镜像（首点切线 = ``p1 - p0``，末点 = ``p_{n-1} - p_{n-2}``）。
    """
    pts = [tuple(p) for p in control]
    n = len(pts)
    if not (0 <= i < n):
        raise IndexError('i 超出控制点范围')
    if closed:
        prev = pts[(i - 1) % n]
        nxt = pts[(i + 1) % n]
    else:
        if i == 0:
            prev = pts[0]
            nxt = pts[1] if n > 1 else pts[0]
        elif i == n - 1:
            prev = pts[n - 2]
            nxt = pts[n - 1]
        else:
            prev = pts[i - 1]
            nxt = pts[i + 1]
    return _sub(nxt, prev)


# -------------------------------------------------------------------- 长度 --

def curve_length(points):
    """多段线总长度（相邻采样点欧氏距离之和）。"""
    if len(points) < 2:
        return 0.0
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += _len(_sub(b, a))
    return total
