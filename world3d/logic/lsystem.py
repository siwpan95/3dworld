# -*- coding: utf-8 -*-
"""L-System（林氏系统）：字符串重写 + 2D / 3D 龟图解释器（纯标准库）。

自研（非移植）
--------------
本地仓库盘点（2026-10-19）结论：``mc-clone`` 的 ``lsystem`` 命中是误报
（精确计数 0），本地并无可用的 L-System 实现，故本模块从零自研。立场与
``logic/`` 一致：**纯标准库、零依赖、确定性、可 headless 单测**。

L-System 三件套
--------------
1. **重写（expand）**：公理 + 产生式（字符→字符串），迭代 N 次得到长字符串。
   支持确定性产生式 ``{'F': 'F+F'}`` 与随机产生式
   ``{'F': [(0.5,'F+F'), (0.5,'F-F')]}``（随机时须注入 ``rng``，保持可复现）。
2. **2D 龟图（Turtle2D）**：把字符串解释成线段序列。指令：
   ``F`` 前进画线 / ``f`` 前进不画 / ``+ -`` 转向 / ``[ ]`` 压栈出栈。
3. **3D 龟图（Turtle3D）**：在 3D 里用航向 + 上向量做 偏航/俯仰/翻滚，
   指令额外含 ``& ^ \\ /``，产出 3D 线段（分形植物 / 螺旋楼梯等）。

确定性：确定式 L-System 同参数同输出；随机式由注入的 ``random.Random`` 控制。
"""
from __future__ import annotations

import math
import random

__all__ = [
    'expand', 'Turtle2D', 'Turtle3D',
]


# --------------------------------------------------------------------- 重写 --

def _choose_weighted(options, rng):
    """从 ``[(prob, replacement), ...]`` 中按权重抽一个（rng 可注入）。"""
    total = sum(p for p, _ in options)
    r = rng.random() * total
    acc = 0.0
    for p, rep in options:
        acc += p
        if r <= acc:
            return rep
    return options[-1][1]


def expand(axiom, rules, iterations, rng=None):
    """L-System 重写：把 ``axiom`` 按 ``rules`` 迭代 ``iterations`` 次。

    参数：
        axiom:      初始字符串（如 ``'F'``）
        rules:      产生式。两种写法：
                    * 确定性：``{'F': 'FF', 'X': 'F+X'}``
                    * 随机式：``{'F': [(0.5,'F+F'),(0.5,'F-F')]}``
                      —— 此时**必须**传 ``rng``（``random.Random(seed)``），
                      否则抛 ``ValueError``。
        iterations: 迭代次数（≥0）
        rng:        可选随机源（仅随机式需要）

    返回最终的字符串（可能很长）。
    """
    if iterations < 0:
        raise ValueError('iterations 必须 ≥0')
    s = axiom
    for _ in range(iterations):
        out = []
        for ch in s:
            if ch in rules:
                r = rules[ch]
                if isinstance(r, str):
                    out.append(r)
                else:  # 随机式：list of (prob, replacement)
                    if rng is None:
                        raise ValueError('随机式产生式必须传入 rng（random.Random）')
                    out.append(_choose_weighted(r, rng))
            else:
                out.append(ch)
        s = ''.join(out)
    return s


# ------------------------------------------------------------------ 2D 龟图 --

class Turtle2D:
    """2D 龟图解释器：把 L-System 字符串变成线段列表。

    线段为 ``((x1,y1),(x2,y2))``。支持指令：
    ``F`` 前进并画线 / ``f`` 前进不画 / ``+`` 左转 / ``-`` 右转 /
    ``[`` 压栈 / ``]`` 出栈；其余字符忽略。

    用法::

        t = Turtle2D(step=10.0, angle=math.radians(90))
        segs = t.run('F+F-F-F+F')   # 科赫曲线的一阶骨架
    """

    def __init__(self, pos=(0.0, 0.0), heading=0.0, step=1.0, angle=math.radians(90)):
        self.pos = (float(pos[0]), float(pos[1]))
        self.heading = float(heading)
        self.step = float(step)
        self.angle = float(angle)
        self.stack = []
        self.segments = []

    def _forward(self, draw):
        dx = math.cos(self.heading) * self.step
        dy = math.sin(self.heading) * self.step
        nxt = (self.pos[0] + dx, self.pos[1] + dy)
        if draw:
            self.segments.append((self.pos, nxt))
        self.pos = nxt

    def run(self, commands):
        self.segments = []
        for ch in commands:
            if ch == 'F':
                self._forward(True)
            elif ch == 'f':
                self._forward(False)
            elif ch == '+':
                self.heading += self.angle
            elif ch == '-':
                self.heading -= self.angle
            elif ch == '[':
                self.stack.append((self.pos, self.heading))
            elif ch == ']':
                if self.stack:
                    self.pos, self.heading = self.stack.pop()
        return self.segments


# ------------------------------------------------------------------ 3D 龟图 --

def _rotate(v, axis, angle):
    """Rodrigues 旋转：向量 ``v`` 绕单位轴 ``axis`` 旋转 ``angle``（弧度）。"""
    ax, ay, az = axis
    c = math.cos(angle)
    s = math.sin(angle)
    # v·axis
    d = v[0] * ax + v[1] * ay + v[2] * az
    # axis × v
    cx = ay * v[2] - az * v[1]
    cy = az * v[0] - ax * v[2]
    cz = ax * v[1] - ay * v[0]
    return (
        v[0] * c + cx * s + ax * d * (1.0 - c),
        v[1] * c + cy * s + ay * d * (1.0 - c),
        v[2] * c + cz * s + az * d * (1.0 - c),
    )


class Turtle3D:
    """3D 龟图解释器：航向 ``H`` + 上向量 ``U``（正交），用旋转更新姿态。

    线段为 ``((x1,y1,z1),(x2,y2,z2))``。指令：
    ``F``/``f`` 前进（画/不画）、``+``/``-`` 偏航（绕 U）、``&``/``^`` 俯仰
    （绕左向量 L）、``\\``/``/`` 翻滚（绕 H）、``[``/``]`` 压栈出栈。

    左向量由 ``L = U × H`` 维持正交（每次旋转后重新正交化）。
    """

    def __init__(self, pos=(0.0, 0.0, 0.0), heading=(1.0, 0.0, 0.0),
                 up=(0.0, 0.0, 1.0), step=1.0, angle=math.radians(90)):
        self.pos = (float(pos[0]), float(pos[1]), float(pos[2]))
        self.H = _norm3(heading)
        self.U = _norm3(up)
        self.step = float(step)
        self.angle = float(angle)
        self.stack = []
        self.segments = []
        self._ortho()

    def _ortho(self):
        # 让 U 正交于 H：U' = U - (U·H)H，再归一
        d = _dot3(self.U, self.H)
        u = (self.U[0] - d * self.H[0],
             self.U[1] - d * self.H[1],
             self.U[2] - d * self.H[2])
        self.U = _norm3(u)

    def _left(self):
        return _norm3(_cross3(self.U, self.H))

    def _forward(self, draw):
        d = v3_scale(self.H, self.step)
        nxt = (self.pos[0] + d[0], self.pos[1] + d[1], self.pos[2] + d[2])
        if draw:
            self.segments.append((self.pos, nxt))
        self.pos = nxt

    def run(self, commands):
        self.segments = []
        for ch in commands:
            if ch == 'F':
                self._forward(True)
            elif ch == 'f':
                self._forward(False)
            elif ch == '+':                       # 左偏航（绕 U）
                self.H = _norm3(_rotate(self.H, self.U, self.angle))
                self._ortho()
            elif ch == '-':                       # 右偏航
                self.H = _norm3(_rotate(self.H, self.U, -self.angle))
                self._ortho()
            elif ch == '&':                       # 下俯仰（绕 left）
                L = self._left()
                self.H = _norm3(_rotate(self.H, L, self.angle))
                self.U = _norm3(_rotate(self.U, L, self.angle))
            elif ch == '^':                       # 上俯仰
                L = self._left()
                self.H = _norm3(_rotate(self.H, L, -self.angle))
                self.U = _norm3(_rotate(self.U, L, -self.angle))
            elif ch == '\\':                      # 左翻滚（绕 H）
                self.U = _norm3(_rotate(self.U, self.H, self.angle))
                self._ortho()
            elif ch == '/':                       # 右翻滚
                self.U = _norm3(_rotate(self.U, self.H, -self.angle))
                self._ortho()
            elif ch == '[':
                self.stack.append((self.pos, self.H, self.U))
            elif ch == ']':
                if self.stack:
                    self.pos, self.H, self.U = self.stack.pop()
        return self.segments


# ------------------------------------------------------------ 3D 向量小工具 --

def _dot3(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross3(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm3(a):
    l = math.sqrt(_dot3(a, a))
    return (0.0, 0.0, 0.0) if l == 0.0 else (a[0] / l, a[1] / l, a[2] / l)


def v3_scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)
