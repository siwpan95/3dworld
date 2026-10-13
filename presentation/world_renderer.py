# -*- coding: utf-8 -*-
"""渲染器：窗口、投影、场景上传与绘制。

场景分四类资源，绘制顺序与生命周期各不相同：

- **地面 + 静态装饰**（Batch + 默认着色器）：棋盘格地面、8 根外围立柱、
  机械臂台座、两座门拱、外围标记块；CPU 端合并成两个顶点列表，2 次 draw call。
- **动画立方体**（自定义着色器 + per-object ``model`` / ``tint`` / ``glow``）：
  环形 10 个 + 中央 1 个 + 轨道球 4 个，矩阵与材质每帧由表现层从逻辑层写入。
- **外部模型**（``assets/gem.obj`` → pyglet Model）：矩阵逐帧写入。
- **机械臂**：底座（静态）+ 转台 + 两段臂 + 夹爪，共 5 个盒体，矩阵来自
  逻辑层节点树（``logic/skeleton.py``）的世界矩阵。

渲染器不持有动画状态——所有矩阵/材质都由表现层采样后写入。
"""
import math
import os

import pyglet
from pyglet.gl import *
from pyglet.math import Mat4, Vec3

from core.geometry import make_box_list, make_cube_list, make_ground, make_static_boxes
from core.resources import load_model
from logic.choreography import (ARM_BASE, CENTER_BASE, GEM_REST, ORB_N, PLINTH,
                                PLINTH_H, RING_N, RING_R, RING_Y)

WIDTH, HEIGHT = 960, 540
SKY_COLOR = (0.53, 0.81, 0.92, 1.0)
PALETTE = [(0.9, 0.3, 0.3), (0.3, 0.6, 0.9), (0.95, 0.75, 0.2),
           (0.4, 0.75, 0.4), (0.7, 0.5, 0.85)]
ORB_COLOR = (0.95, 0.62, 0.25)

# 动画立方体着色器：默认管线 + per-object model 矩阵 + 材质（tint / glow）
#   tint：整体颜色乘子（1.0 = 原色，>1 过曝成白，用于"点亮"）
#   glow：自发光叠加项（螺旋幕的橙光、核心脉冲靠它出效果）
CUBE_VERTEX_SRC = '''#version 330 core
in vec3 position;
in vec4 colors;
out vec4 vertex_colors;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform mat4 model;
uniform vec3 tint;
uniform float glow;
void main() {
    gl_Position = window.projection * window.view * model * vec4(position, 1.0);
    vec3 c = colors.rgb * tint + glow * tint * 0.8;
    vertex_colors = vec4(c, colors.a);
}'''
CUBE_FRAGMENT_SRC = '''#version 330 core
in vec4 vertex_colors;
out vec4 final_colors;
void main() { final_colors = vertex_colors; }'''


def _static_prop_boxes():
    """静态装饰盒体清单：(尺寸 xyz, 中心偏移 xyz, 颜色)。

    包含：8 根外围立柱（含琥珀顶环与基座）、两座门拱、外围琥珀标记块、
    机械臂台座（宝石落座处）。全部塞进一个合并顶点列表。

    半径取 13、门拱取 z=±14：**必须大于运镜半径（≤12）**，否则镜头会被
    柱子挡脸——第一版立柱放在 8.5，第三幕特写左边直接糊了一根柱子。
    """
    boxes = []
    pillar_rgb, cap_rgb = (0.42, 0.45, 0.52), (0.85, 0.50, 0.12)
    # 8 根立柱：半径 13，高 5.5
    for k in range(8):
        ang = 2 * math.pi * k / 8 + math.pi / 8
        px, pz = math.cos(ang) * 13.0, math.sin(ang) * 13.0
        boxes.append((1.2, 5.5, 1.2, px, 2.75, pz, pillar_rgb))
        boxes.append((1.45, 0.24, 1.45, px, 5.55, pz, cap_rgb))
        boxes.append((1.55, 0.45, 1.55, px, 0.22, pz, (0.30, 0.33, 0.38)))
    # 两座门拱（z = ±14，跨 x ∈ [-11, 11]，高 7）
    for z in (-14.0, 14.0):
        for side in (-1, 1):
            boxes.append((0.9, 7.0, 0.9, side * 10.6, 3.5, z, (0.36, 0.39, 0.45)))
        boxes.append((22.0, 0.8, 1.0, 0.0, 6.7, z, (0.40, 0.43, 0.50)))
        boxes.append((22.0, 0.14, 1.05, 0.0, 6.28, z, cap_rgb))
    # 外围琥珀标记块（每 30° 一块，压在地面上，给环形运动一个视觉参考）
    for k in range(12):
        ang = 2 * math.pi * k / 12
        boxes.append((0.7, 0.08, 0.7, math.cos(ang) * 11.4, 0.04,
                      math.sin(ang) * 11.4, cap_rgb))
    # 机械臂台座：宝石落座处（PLINTH），顶面 PLINTH_H
    boxes.append((1.3, PLINTH_H, 1.3, PLINTH.x, PLINTH_H / 2, PLINTH.z,
                  (0.34, 0.37, 0.44)))
    boxes.append((1.5, 0.14, 1.5, PLINTH.x, PLINTH_H + 0.07, PLINTH.z, cap_rgb))
    return boxes


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

        # --- 静态几何（Batch）：地面 + 立柱/门拱/台座/标记块 ---
        self.ground = make_ground(self.ground_program, self.batch, half=15)
        self.static_props = make_static_boxes(self.ground_program, self.batch,
                                              _static_prop_boxes())

        # --- 动画立方体：环形 10 + 中央 1 + 轨道球 4 ---
        self.cube_meta = []                  # [(name, base_pos: Vec3)]
        self.cube_lists = []                 # [VertexList]
        self.cube_models = []                # [Mat4] 逻辑层每帧写入
        self.cube_tints = []                 # [Vec3] 材质乘子
        self.cube_glows = []                 # [float] 自发光
        for i in range(RING_N):
            ang = 2 * math.pi * i / RING_N
            self.cube_meta.append((f'ring_{i}',
                                   Vec3(math.cos(ang) * RING_R, RING_Y,
                                        math.sin(ang) * RING_R)))
        self.cube_meta.append(('center', CENTER_BASE))
        for i in range(ORB_N):
            self.cube_meta.append((f'orb_{i}', Vec3(3.4 + 0.35 * i, 2.2 + 0.42 * i, 0.0)))
        for idx, (name, pos) in enumerate(self.cube_meta):
            if name == 'center':
                size, rgb = 2.0, (0.95, 0.95, 0.95)
            elif name.startswith('orb_'):
                size, rgb = 1.0, ORB_COLOR
            else:
                size, rgb = 1.0, PALETTE[idx % len(PALETTE)]
            self.cube_lists.append(
                make_cube_list(self.cube_program, pos.x, pos.y, pos.z, size, rgb))
            self.cube_models.append(Mat4())
            self.cube_tints.append(Vec3(1, 1, 1))
            self.cube_glows.append(0.0)

        # --- 外部模型（M4）：assets/ 下存在 .obj 则加载，参与时间轴 ---
        self.model_meta = []                 # [(name, base_pos: Vec3)]
        self.models = []                     # [pyglet.model.Model]
        model_path = os.path.join('assets', 'gem.obj')
        if os.path.exists(model_path):
            try:
                for i, model in enumerate(load_model(model_path, self.batch)):
                    base = Vec3(GEM_REST.x, GEM_REST.y, GEM_REST.z + i * 1.6)
                    self.models.append(model)
                    self.model_meta.append((f'model_{i}', base))
            except Exception as e:           # 模型缺失/损坏不阻断启动
                print(f'[renderer] 模型加载失败 {model_path}: {e}')

    # ---- 逻辑层写入模型矩阵 / 材质 ----
    def set_cube_model(self, index, matrix):
        self.cube_models[index] = matrix

    def set_cube_material(self, index, tint, glow):
        self.cube_tints[index] = tint
        self.cube_glows[index] = glow

    # ---- M5：层级骨骼臂（枢轴在关节的盒体段，矩阵来自逻辑层节点树） ----
    def build_arm(self):
        """底座（静态）+ 转台 + 大臂 + 小臂 + 夹爪，枢轴全部在关节上。

        索引约定：0=底座(静态) 1=转台 2=seg1 3=seg2 4=grip；
        转台/大臂/小臂/夹爪的世界矩阵由 ``logic/skeleton.py`` 逐帧给出。
        """
        bx, bz = ARM_BASE.x, ARM_BASE.z
        self.arm_lists = [
            # 底座：静态基座
            make_cube_list(self.cube_program, bx, 0.25, bz, 1.6, (0.35, 0.37, 0.42)),
            # 转台：枢轴在底座中心，几何沿 +Y 展开（绕 Y 旋转）
            make_box_list(self.cube_program, 1.2, 0.6, 1.2, 0, 0.3, 0, (0.48, 0.51, 0.58)),
            # 大臂：长 2.4，枢轴在肩关节
            make_box_list(self.cube_program, 0.4, 2.4, 0.4, 0, 1.2, 0, (0.55, 0.58, 0.85)),
            # 小臂：长 1.8，枢轴在肘关节
            make_box_list(self.cube_program, 0.3, 1.8, 0.3, 0, 0.9, 0, (0.85, 0.65, 0.25)),
            # 夹爪：掌部在关节下方、两根指爪张开 ±0.75（宝石夹在原点处）
            self._gripper_list(),
        ]
        self.arm_models = [Mat4() for _ in self.arm_lists]

    def _gripper_list(self):
        """夹爪合并几何（掌 + 两指），枢轴在抓取点（局部原点）。"""
        from core.geometry import box_data
        verts, colors = [], []
        for dims, off, rgb in (
            ((1.7, 0.18, 0.5), (0.0, -0.06, 0.0), (0.42, 0.45, 0.52)),
            ((0.12, 0.6, 0.12), (-0.75, 0.25, 0.0), (0.60, 0.62, 0.68)),
            ((0.12, 0.6, 0.12), (0.75, 0.25, 0.0), (0.60, 0.62, 0.68)),
            ((0.12, 0.22, 0.12), (-0.62, 0.42, 0.0), (0.85, 0.50, 0.12)),
            ((0.12, 0.22, 0.12), (0.62, 0.42, 0.0), (0.85, 0.50, 0.12)),
        ):
            v, c = box_data(dims[0], dims[1], dims[2], off[0], off[1], off[2], rgb)
            verts += v
            colors += c
        return self.cube_program.vertex_list(len(verts) // 3, GL_TRIANGLES,
                                             position=('f', verts), colors=('f', colors))

    def set_arm_model(self, index, matrix):
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
        视图矩阵在此存档，``end_ui`` 原样还原（否则 UI 的 view=单位阵会
        把下一帧的相机吃掉）。
        """
        w, h = self.window.width, self.window.height
        self._saved_view = self.window.view
        self.window.projection = Mat4.orthogonal_projection(0, w, 0, h, -255, 255)
        self.window.view = Mat4()
        glDisable(GL_DEPTH_TEST)

    def end_ui(self):
        """还原 3D 状态：深度测试 + 透视投影 + 存档的相机视图矩阵。"""
        glEnable(GL_DEPTH_TEST)
        self.window.projection = self.perspective
        self.window.view = getattr(self, '_saved_view', Mat4())

    def draw(self):
        self.window.clear()
        self.batch.draw()                    # 地面 + 静态装饰 + 外部模型
        prog = self.cube_program
        prog.use()
        for vl, model, tint, glow in zip(self.cube_lists, self.cube_models,
                                         self.cube_tints, self.cube_glows):
            prog['model'] = model
            prog['tint'] = tint
            prog['glow'] = glow
            vl.draw(GL_TRIANGLES)
        for vl, model in zip(self.arm_lists, self.arm_models):
            prog['model'] = model
            prog['tint'] = Vec3(1, 1, 1)
            prog['glow'] = 0.0
            vl.draw(GL_TRIANGLES)
