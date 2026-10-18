# -*- coding: utf-8 -*-
"""GPU 实例化粒子系统 —— 逐帧动画 100% 在顶点着色器里算。

自 ``examples/particles_gpu.py`` 上提（2026-10-15 demo 剥离）：demo 删除后，
本模块（含 ``engine/shaders.py`` 的 ``particles`` 条目）是「GPU 实例化粒子」
这项能力的引擎侧落点。

为什么这么写（本模块的重点）
--------------------------
CPU 每帧只做三件事：``set_uniforms``（几个标量）、``draw``。粒子的位置/大小/
颜色全部由顶点着色器从 ``i_seed``（每实例随机种子）+ ``u_time`` 现推出来 ——
所以粒子数翻倍几乎不影响 CPU 占用，也不存在"每帧重建几万个顶点"的开销。

实例化用 pyglet 2.1.16 的**原生** API，不需要手搓 VAO/VBO。三个坑位
（``build`` 的 docstring 里逐条对应）：

1. ``i_seed=(...)`` 的初始数据必须按**基础顶点数**给长度（四边形 6 顶点 ×
   vec4 = 24 个 float）。只给一条 vec4 会抛
   ``ValueError: Invalid data size for 'i_seed'. Expected 24, got 4.``
   —— pyglet 的 ``set_attribute_data`` 是按 ``vlist.count`` 写整块的。
2. ``add_instance`` 内部 ``_instances += 1`` 后写的是下标 ``_instances - 1``，
   即第 1 次 ``add_instance`` 写的是 **1 号槽**：0 号槽保留初始数据。所以
   ``range(N-1)`` 配 ``seeds[k+1]`` 刚好把 0..N-1 填满，不多不少。
3. 顶点着色器里**用到**的属性才活着：GLSL 会把没用到的 ``in`` 优化掉，pyglet
   按名字找不到会抛 ShaderException。position / colors / i_seed / i_color
   四个都真用上了（colors 作为静态色调乘子）。

另外两条使用约定（调用方负责）：
- 加性混合要自己开：``glEnable(GL_BLEND)`` + ``glBlendFunc(GL_SRC_ALPHA, GL_ONE)``；
  画粒子时 ``glDepthMask(GL_FALSE)``，**画完必须还原成 GL_TRUE**，否则基类的
  ``window.clear()`` 连深度缓冲都清不掉。
- 能量用**累加相位**而不是 ``u_time * energy``：后者在按键瞬间相位跳变，
  画面会闪一下；前者只改变推进速率（``advance(dt, energy)`` 已按此实现）。
"""
from __future__ import annotations

import random

from world3d.engine.shaders import PARTICLES_FRAG, PARTICLES_VERT

# 预设：(名字, 相机距离, 注视点高度) —— 与着色器里的 u_preset 分支一一对应
PRESETS = (('星系漩涡', 21.0, 1.6),
           ('喷泉烟花', 26.0, 4.5),
           ('湍流云', 25.0, 2.0))

BASE_VERTS = 6                # 每颗粒子的基础四边形顶点数（两个三角形）


def base_quad():
    """基础四边形几何：角点在 [-1,1]^2、z=0，6 顶点（纯函数，可无窗口单测）。

    返回 ``(positions, colors)``：位置 18 个 float、颜色 24 个 float（vec4）。
    """
    pos, col = [], []
    for cx, cy in ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0),
                   (-1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)):
        pos += [cx, cy, 0.0]
        col += [1.0, 1.0, 1.0, 1.0]
    return pos, col


def make_instance_data(count, seed=20240607):
    """固定种子生成 ``(seeds, cols)`` 实例数据（纯函数，可无窗口单测）。

    ``seeds[k]`` 是 4 个 [0,1) 随机数（顶点着色器逐帧动画的唯一输入）；
    ``cols[k]`` 是基色（只当色彩抖动源）。固定种子 ⇒ 截图可复现。
    """
    rng = random.Random(seed)
    n = int(count)
    seeds = [[rng.random() for _ in range(4)] for _ in range(n)]
    cols = [[0.75 + 0.45 * rng.random(), 0.75 + 0.45 * rng.random(),
             0.75 + 0.45 * rng.random(), 1.0] for _ in range(n)]
    return seeds, cols


class ParticleSystem:
    """pyglet 2.1.16 原生实例化的封装（坑位见模块 docstring）。

    用法::

        ps = ParticleSystem(program, count=45000)   # program = shaders.get('particles')
        ps.set_uniforms(time=ps.advance(dt, energy), energy=1.0,
                        preset=0, size_scale=1.0)
        ps.draw()

    数量可变是**为了看清径向软衰减**：4.5 万颗在加性混合下必然糊成一片，
    单颗的软边根本无从谈起；降到几千颗 + 放大尺寸，才看得到"圆点 + 软边"。
    代价是要 delete 旧列表重建（实例数据一次性生成，约 0.1s）。
    """

    def __init__(self, program, count=45000):
        self.program = program
        self.count = 0
        self.vlist = None
        self._base_pos, self._base_col = base_quad()
        self.build(count)

    def build(self, count):
        """（重）建实例缓冲（内部用；切数量走 ``set_count``）。"""
        from pyglet.gl import GL_TRIANGLES
        seeds, cols = make_instance_data(count)
        n = int(count)
        old = self.vlist
        if old is not None:
            old.delete()
        seed0 = seeds[0] * BASE_VERTS      # 坑位 1：初始数据要按顶点数给
        col0 = cols[0] * BASE_VERTS
        self.vlist = self.program.vertex_list_instanced(
            BASE_VERTS, GL_TRIANGLES,
            instance_attributes=['i_seed', 'i_color'],
            position=('f', self._base_pos), colors=('f', self._base_col),
            i_seed=('f', seed0), i_color=('f', col0))
        for k in range(n - 1):            # 坑位 2：0 号槽由初始数据占着
            self.vlist.add_instance(i_seed=seeds[k + 1], i_color=cols[k + 1])
        self.count = n

    def set_count(self, count):
        """切换粒子数。实例数据重建，相位与预设保持不变。"""
        if int(count) != self.count:
            self.build(count)

    def advance(self, dt, energy=1.0):
        """累加相位并返回新相位（能量只改推进速率，不改已积累的相位）。"""
        self._phase = getattr(self, '_phase', 0.0) + dt * (0.5 + 0.5 * energy)
        return self._phase

    def set_uniforms(self, time, energy=1.0, preset=0, size_scale=1.0):
        """把每帧标量写进着色器（CPU 侧全部工作就这些）。"""
        prog = self.program
        prog['u_time'] = time
        prog['u_energy'] = energy
        prog['u_preset'] = int(preset) % len(PRESETS)
        prog['u_size_scale'] = size_scale
        # 能量守恒式缩放：面积涨 k² 倍，单颗亮度就除以 k²
        prog['u_alpha_scale'] = 1.0 / (size_scale * size_scale)

    def draw(self):
        from pyglet.gl import GL_TRIANGLES
        self.vlist.draw(GL_TRIANGLES)
