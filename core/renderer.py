# -*- coding: utf-8 -*-
"""渲染器：窗口、投影、场景上传与绘制。

- 地面：静态，默认着色器 + Batch
- 立方体：动画对象，自定义着色器（model uniform）逐帧矩阵驱动，不入 Batch

渲染器不持有动画状态——矩阵由表现层从逻辑层采样结果写入。
"""
import math
import os

import pyglet
from pyglet.gl import *
from pyglet.math import Mat4, Vec3

from core.geometry import make_box_list, make_cube_list, make_ground
from core.resources import load_model

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

        # --- 外部模型（M4）：assets/ 下存在 .obj 则加载，参与时间轴 ---
        self.model_meta = []                 # [(name, base_pos: Vec3)]
        self.models = []                     # [pyglet.model.Model]
        model_path = os.path.join('assets', 'gem.obj')
        if os.path.exists(model_path):
            try:
                for i, model in enumerate(load_model(model_path, self.batch)):
                    base = Vec3(-3.5, 1.3, -3.5 + i * 1.6)
                    self.models.append(model)
                    self.model_meta.append((f'model_{i}', base))
            except Exception as e:           # 模型缺失/损坏不阻断启动
                print(f'[renderer] 模型加载失败 {model_path}: {e}')

    # ---- 逻辑层写入模型矩阵 ----
    def set_cube_model(self, index, matrix):
        self.cube_models[index] = matrix

    # ---- M5：层级骨骼臂（枢轴在关节的盒体段，矩阵来自逻辑层节点树） ----
    def build_arm(self, base):
        """底座（静态）+ 两段臂（seg1 长 2.4 / seg2 长 1.8，枢轴在关节）。"""
        self.arm_lists = [
            make_cube_list(self.cube_program, base.x, 0.3, base.z, 1.0, (0.45, 0.47, 0.52)),
            make_box_list(self.cube_program, 0.4, 2.4, 0.4, 0, 1.2, 0, (0.55, 0.58, 0.85)),
            make_box_list(self.cube_program, 0.3, 1.8, 0.3, 0, 0.9, 0, (0.85, 0.65, 0.25)),
        ]
        self.arm_models = [Mat4(), Mat4(), Mat4()]
        # 索引约定：0=底座(静态)，1=seg1，2=seg2
        self.set_arm_model = self._set_arm_model

    def _set_arm_model(self, index, matrix):
        self.arm_models[index] = matrix

    # ---- 投影（on_resize 时由表现层调用） ----
    def apply_projection(self):
        self.window.viewport = (0, 0, self.window.width, self.window.height)
        self.perspective = Mat4.perspective_projection(
            self.window.aspect_ratio, z_near=0.1, z_far=255, fov=60)
        self.window.projection = self.perspective

    # ---- 2D UI 屏幕空间切换（M3 时间轴面板用） ----
    def begin_ui(self):
        """切入 UI 状态：正交投影 + 关闭深度测试。

        深度测试必须关闭——3D 场景写入的深度缓冲会剔除 UI 的 z=0 片元，
        表现为面板区域被地面"掏空"、控件不可见。
        """
        w, h = self.window.width, self.window.height
        self.window.projection = Mat4.orthogonal_projection(0, w, 0, h, -255, 255)
        self.window.view = Mat4()
        glDisable(GL_DEPTH_TEST)

    def end_ui(self):
        """还原 3D 状态：重新开启深度测试 + 透视投影（视图矩阵由表现层恢复）。"""
        glEnable(GL_DEPTH_TEST)
        self.window.projection = self.perspective

    def draw(self):
        self.window.clear()
        self.batch.draw()                    # 地面 + 外部模型（各自着色器）
        prog = self.cube_program
        prog.use()
        for vl, model in zip(self.cube_lists, self.cube_models):
            prog['model'] = model
            vl.draw(GL_TRIANGLES)
        if hasattr(self, 'arm_lists'):
            for vl, model in zip(self.arm_lists, self.arm_models):
                prog['model'] = model
                vl.draw(GL_TRIANGLES)
