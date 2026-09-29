# -*- coding: utf-8 -*-
"""渲染器：窗口、投影、场景上传与绘制。不持有动画状态（状态在逻辑层）。"""
import math
import pyglet
from pyglet.gl import *
from pyglet.math import Mat4

from core.geometry import make_cube, make_ground

WIDTH, HEIGHT = 960, 540
SKY_COLOR = (0.53, 0.81, 0.92, 1.0)
PALETTE = [(0.9, 0.3, 0.3), (0.3, 0.6, 0.9), (0.95, 0.75, 0.2),
           (0.4, 0.75, 0.4), (0.7, 0.5, 0.85)]


class WorldRenderer:
    def __init__(self):
        self.window = pyglet.window.Window(
            WIDTH, HEIGHT, caption='pyglet 简单三维世界',
            resizable=True, vsync=True)
        self.batch = pyglet.graphics.Batch()
        self.program = pyglet.graphics.get_default_shader()
        glEnable(GL_DEPTH_TEST)              # 深度测试：保证近处遮挡远处
        glClearColor(*SKY_COLOR)             # 天空蓝背景

        # --- 场景内容：棋盘地面 + 环形彩色立方体 + 中央白色大立方体 ---
        self.ground = make_ground(self.program, self.batch, half=10)
        self.cubes = []
        for i in range(10):
            ang = i / 10 * 2 * math.pi
            self.cubes.append(make_cube(
                self.program, self.batch,
                math.cos(ang) * 5, 0.5, math.sin(ang) * 5,
                1.0, PALETTE[i % len(PALETTE)]))
        self.cubes.append(make_cube(
            self.program, self.batch, 0, 1.0, 0, 2.0, (0.95, 0.95, 0.95)))

    # ---- 投影（on_resize 时由表现层调用） ----
    def apply_projection(self):
        self.window.viewport = (0, 0, self.window.width, self.window.height)
        self.window.projection = Mat4.perspective_projection(
            self.window.aspect_ratio, z_near=0.1, z_far=255, fov=60)

    def draw(self):
        self.window.clear()
        self.batch.draw()
