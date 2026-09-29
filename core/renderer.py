# -*- coding: utf-8 -*-
"""渲染器：窗口、投影、场景上传与绘制。

- 地面：静态，默认着色器 + Batch
- 立方体：动画对象，自定义着色器（model uniform）逐帧矩阵驱动，不入 Batch

渲染器不持有动画状态——矩阵由表现层从逻辑层采样结果写入。
"""
import math

import pyglet
from pyglet.gl import *
from pyglet.math import Mat4, Vec3

from core.geometry import make_cube_list, make_ground

WIDTH, HEIGHT = 960, 540
SKY_COLOR = (0.53, 0.81, 0.92, 1.0)
PALETTE = [(0.9, 0.3, 0.3), (0.3, 0.6, 0.9), (0.95, 0.75, 0.2),
           (0.4, 0.75, 0.4), (0.7, 0.5, 0.85)]

# 动画立方体着色器：在默认管线基础上加 per-object model 矩阵
CUBE_VERTEX_SRC = '''#version 330 core
in vec3 position;
in vec4 colors;
out vec4 vertex_colors;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform mat4 model;
void main() {
    gl_Position = window.projection * window.view * model * vec4(position, 1.0);
    vertex_colors = colors;
}'''
CUBE_FRAGMENT_SRC = '''#version 330 core
in vec4 vertex_colors;
out vec4 final_colors;
void main() { final_colors = vertex_colors; }'''


class WorldRenderer:
    def __init__(self):
        self.window = pyglet.window.Window(
            WIDTH, HEIGHT, caption='pyglet 简单三维世界',
            resizable=True, vsync=True)
        self.batch = pyglet.graphics.Batch()
        self.ground_program = pyglet.graphics.get_default_shader()
        self.cube_program = pyglet.graphics.shader.ShaderProgram(
            pyglet.graphics.shader.Shader(CUBE_VERTEX_SRC, 'vertex'),
            pyglet.graphics.shader.Shader(CUBE_FRAGMENT_SRC, 'fragment'))
        glEnable(GL_DEPTH_TEST)              # 深度测试：保证近处遮挡远处
        glClearColor(*SKY_COLOR)             # 天空蓝背景

        # --- 静态地面（Batch） ---
        self.ground = make_ground(self.ground_program, self.batch, half=10)

        # --- 动画立方体：环形 10 个 + 中央白色大立方体 ---
        self.cube_meta = []                  # [(name, base_pos: Vec3)]
        self.cube_lists = []                 # [VertexList]
        self.cube_models = []                # [Mat4] 逻辑层每帧写入
        for i in range(10):
            ang = i / 10 * 2 * math.pi
            self.cube_meta.append((f'ring_{i}',
                                   Vec3(math.cos(ang) * 5, 0.5, math.sin(ang) * 5)))
        self.cube_meta.append(('center', Vec3(0, 1.0, 0)))
        for idx, (name, pos) in enumerate(self.cube_meta):
            if name == 'center':
                size, rgb = 2.0, (0.95, 0.95, 0.95)
            else:
                size, rgb = 1.0, PALETTE[idx % len(PALETTE)]
            self.cube_lists.append(
                make_cube_list(self.cube_program, pos.x, pos.y, pos.z, size, rgb))
            self.cube_models.append(Mat4())

    # ---- 逻辑层写入模型矩阵 ----
    def set_cube_model(self, index, matrix):
        self.cube_models[index] = matrix

    # ---- 投影（on_resize 时由表现层调用） ----
    def apply_projection(self):
        self.window.viewport = (0, 0, self.window.width, self.window.height)
        self.perspective = Mat4.perspective_projection(
            self.window.aspect_ratio, z_near=0.1, z_far=255, fov=60)
        self.window.projection = self.perspective

    # ---- 2D UI 屏幕空间切换（M3 时间轴面板用） ----
    def apply_screen_projection(self):
        w, h = self.window.width, self.window.height
        self.window.projection = Mat4.orthogonal_projection(0, w, 0, h, -255, 255)
        self.window.view = Mat4()

    def restore_3d_projection(self):
        self.window.projection = self.perspective

    def draw(self):
        self.window.clear()
        self.batch.draw()                    # 地面（默认着色器）
        prog = self.cube_program
        prog.use()
        for vl, model in zip(self.cube_lists, self.cube_models):
            prog['model'] = model
            vl.draw(GL_TRIANGLES)
