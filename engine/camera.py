# -*- coding: utf-8 -*-
"""相机：透视参数 + 视图矩阵 + 两种控制器（轨道 / 第一人称漫游）。

矩阵一律走 :func:`engine.mathkit.look_at_matrix`（手写版），
不用 ``Mat4.look_at``：部分 pyglet 2.x 过渡版的 Vec3 叉乘方法改过名，
运行期才炸，手写版跨版本稳定。

控制器只维护**状态**，不碰窗口/输入——输入绑定在 ``app`` 层做，
这样相机可以在无窗口环境里单测（相机路径净空这类测试就是这么跑的）。
"""
import math

from engine.frustum import Frustum
from engine.mathkit import (clamp, look_at_matrix, mat4_inverse, mat4_mul,
                            mat4_to_Mat4, quat_from_axis_angle)
from pyglet.math import Mat4, Vec3

__all__ = ['Camera', 'OrbitCamera', 'FlyCamera', 'PITCH_MIN', 'PITCH_MAX',
           'DIST_MIN', 'DIST_MAX']

# 默认钳制：给"站在地面上看场景"选的。俯仰下限 5° 保证相机不入地、
# 上限 89° 避开 up 与视线共线的退化；距离 4~60 避免贴脸与缩成一个点。
PITCH_MIN, PITCH_MAX = 5.0, 89.0
DIST_MIN, DIST_MAX = 4.0, 60.0


class Camera:
    """基础相机：位置 + 目标点 + 透视参数。"""

    def __init__(self, position=(0.0, 2.0, 8.0), target=(0.0, 1.0, 0.0),
                 up=(0.0, 1.0, 0.0), fov=60.0, near=0.1, far=500.0):
        self.position = tuple(position)
        self.target = tuple(target)
        self.up = tuple(up)
        self.fov = float(fov)
        self.near = float(near)
        self.far = float(far)

    # ---- 矩阵 ----
    def view_matrix(self):
        return look_at_matrix(self.position, self.target, self.up)

    def view_Mat4(self):
        return mat4_to_Mat4(self.view_matrix())

    def projection(self, aspect):
        return Mat4.perspective_projection(aspect, z_near=self.near,
                                           z_far=self.far, fov=self.fov)

    def view_projection(self, aspect):
        """返回列主序 16 元组（视锥提取 / 天空反投影都用它）。"""
        return mat4_mul(tuple(self.projection(aspect)), self.view_matrix())

    def inverse_view_projection(self, aspect):
        inv = mat4_inverse(self.view_projection(aspect))
        return inv if inv is not None else mat4_mul(
            tuple(self.projection(aspect)), self.view_matrix())

    def frustum(self, aspect):
        return Frustum.from_view_projection(self.view_projection(aspect))

    # ---- 状态 ----
    def set_position(self, p):
        self.position = tuple(p)
        return self

    def set_target(self, t):
        self.target = tuple(t)
        return self

    def forward(self):
        fx = self.target[0] - self.position[0]
        fy = self.target[1] - self.position[1]
        fz = self.target[2] - self.position[2]
        l = math.sqrt(fx * fx + fy * fy + fz * fz) or 1.0
        return (fx / l, fy / l, fz / l)

    def right(self):
        f = self.forward()
        u = self.up
        # s = f × up
        sx, sy, sz = (f[1] * u[2] - f[2] * u[1], f[2] * u[0] - f[0] * u[2],
                      f[0] * u[1] - f[1] * u[0])
        l = math.sqrt(sx * sx + sy * sy + sz * sz) or 1.0
        return (sx / l, sy / l, sz / l)

    def __repr__(self):
        return f'Camera(pos={self.position}, target={self.target})'


class OrbitCamera(Camera):
    """轨道相机：绕目标点转，鼠标拖动改 yaw/pitch，滚轮改距离。

    Args:
        pitch_range: 俯仰钳制（度），默认地面视角 (5, 89)
        dist_range:  距离钳制，默认 (4, 60)。空间场景（太阳系、SDF）需要
                     更大自由度时才放开——贴近黄道面才看得到行星相位。
    """

    def __init__(self, target=(0.0, 1.0, 0.0), yaw=45.0, pitch=30.0, dist=18.0,
                 pitch_range=(PITCH_MIN, PITCH_MAX),
                 dist_range=(DIST_MIN, DIST_MAX), **kwargs):
        super().__init__(target=target, **kwargs)
        self.yaw = float(yaw)
        self.pitch = float(pitch)
        self.dist = float(dist)
        self.pitch_range = (float(pitch_range[0]), float(pitch_range[1]))
        self.dist_range = (float(dist_range[0]), float(dist_range[1]))
        assert self.pitch_range[0] < self.pitch_range[1], 'pitch_range 需递增'
        assert self.dist_range[0] < self.dist_range[1], 'dist_range 需递增'
        assert abs(self.pitch_range[1]) < 90.0, \
            'pitch 上限必须 < 90°：等于 90° 时视线与 up 共线，look-at 退化'

    # ---- 输入 ----
    def orbit(self, dx, dy):
        lo, hi = self.pitch_range
        self.yaw += dx * 0.4
        self.pitch = clamp(self.pitch + dy * 0.4, lo, hi)
        return self

    def zoom(self, sy):
        lo, hi = self.dist_range
        self.dist = clamp(self.dist - sy * 2.0, lo, hi)
        return self

    def advance(self, dt):
        """默认编排：8°/s 自动环绕。"""
        self.yaw += dt * 8.0
        return self

    # ---- 查询 ----
    @property
    def eye(self):
        yr, pr = math.radians(self.yaw), math.radians(self.pitch)
        return Vec3(
            self.target[0] + self.dist * math.cos(pr) * math.cos(yr),
            self.target[1] + self.dist * math.sin(pr),
            self.target[2] + self.dist * math.cos(pr) * math.sin(yr))

    def view_matrix(self):
        return look_at_matrix(self.eye, self.target, self.up)

    def __repr__(self):
        return (f'OrbitCamera(yaw={self.yaw:.1f}, pitch={self.pitch:.1f}, '
                f'dist={self.dist:.1f})')


class FlyCamera(Camera):
    """第一人称漫游相机：WASD 平移 + 鼠标转向（yaw/pitch）。

    与轨道相机的区别：位置自由，朝向由 yaw/pitch 直接驱动；
    目标点 = 位置 + 前向量（由 yaw/pitch 现算），不维护独立 target。
    """

    def __init__(self, position=(0.0, 1.7, 8.0), yaw=-90.0, pitch=0.0,
                 move_speed=6.0, look_speed=0.25, damping=12.0, **kwargs):
        super().__init__(position=position, **kwargs)
        self.yaw = float(yaw)
        self.pitch = float(pitch)
        self.move_speed = float(move_speed)
        self.look_speed = float(look_speed)
        self.damping = float(damping)          # 速度阻尼（越大越"跟手"）
        self.velocity = (0.0, 0.0, 0.0)
        self.sprint_multiplier = 2.5

    # ---- 朝向 ----
    def forward(self):
        yr, pr = math.radians(self.yaw), math.radians(self.pitch)
        return (math.cos(pr) * math.cos(yr), math.sin(pr), math.cos(pr) * math.sin(yr))

    def right(self):
        f = self.forward()
        # right = normalize(f × up)
        sx, sy, sz = f[1] * self.up[2] - f[2] * self.up[1], \
            f[2] * self.up[0] - f[0] * self.up[2], \
            f[0] * self.up[1] - f[1] * self.up[0]
        l = math.sqrt(sx * sx + sy * sy + sz * sz) or 1.0
        return (sx / l, sy / l, sz / l)

    def look(self, dx, dy):
        self.yaw += dx * self.look_speed
        self.pitch = clamp(self.pitch - dy * self.look_speed, -89.0, 89.0)
        return self

    # ---- 移动 ----
    def move(self, forward=0.0, right=0.0, up=0.0, sprint=False):
        """按当前朝向请求位移（写入速度，由 update 积分）。"""
        speed = self.move_speed * (self.sprint_multiplier if sprint else 1.0)
        f, r = self.forward(), self.right()
        vx = (f[0] * forward + r[0] * right) * speed
        vy = (f[1] * forward + r[1] * right + up) * speed
        vz = (f[2] * forward + r[2] * right) * speed
        self.velocity = (vx, vy, vz)
        return self

    def update(self, dt):
        """积分速度到位置（带指数阻尼，松手会滑一小段）。"""
        k = 1.0 - math.exp(-self.damping * dt)
        vx, vy, vz = self.velocity
        self.position = (self.position[0] + vx * k * dt,
                         self.position[1] + vy * k * dt,
                         self.position[2] + vz * k * dt)
        f = self.forward()
        self.target = (self.position[0] + f[0], self.position[1] + f[1],
                       self.position[2] + f[2])
        return self

    def view_matrix(self):
        f = self.forward()
        return look_at_matrix(self.position,
                              (self.position[0] + f[0], self.position[1] + f[1],
                               self.position[2] + f[2]), self.up)

    def __repr__(self):
        return f'FlyCamera(pos={self.position}, yaw={self.yaw:.1f})'
