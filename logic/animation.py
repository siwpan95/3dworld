# -*- coding: utf-8 -*-
"""动画系统最小版（M2）：时间轴 / 关键帧 / 插值，数据模型对齐 glTF 2.0。

- Sampler: input 时间戳 + output 值 + 插值方式（LINEAR / STEP）
- Channel: 目标节点 + 属性（translation / rotation / scale）
- Timeline: 时长 + 通道列表 + 播头推进与采样

纯数学实现（Quaternion/slerp/lerp），不 import pyglet.gl，可无窗口单测。
旋转插值统一走四元数 slerp（等距关键帧分段，避免 180°+ 退化）。
"""
import bisect
import math

from pyglet.math import Mat4, Quaternion, Vec3


# ------------------------------------------------------------ 四元数工具 --
def quat_from_axis_angle(axis, angle_deg):
    """轴角 -> 四元数（pyglet 顺序 w,x,y,z）。axis 需为单位向量或可归一化。"""
    ax, ay, az = axis
    norm = math.sqrt(ax * ax + ay * ay + az * az)
    ax, ay, az = ax / norm, ay / norm, az / norm
    half = math.radians(angle_deg) * 0.5
    s = math.sin(half)
    return Quaternion(math.cos(half), ax * s, ay * s, az * s)


def slerp(q0, q1, t):
    """四元数球面插值。q0/q1 夹角过小时退化为 nlerp，点积为负时取最短弧。"""
    d = q0.w * q1.w + q0.x * q1.x + q0.y * q1.y + q0.z * q1.z
    if d < 0.0:   # 最短弧：反向端点
        q1 = Quaternion(-q1.w, -q1.x, -q1.y, -q1.z)
        d = -d
    if d > 0.9995:  # 近平行：nlerp 足够
        q = Quaternion(*(a + (b - a) * t for a, b in zip(q0, q1)))
        return q.normalize()
    theta = math.acos(min(1.0, d))
    s = math.sin(theta)
    w0, w1 = math.sin((1 - t) * theta) / s, math.sin(t * theta) / s
    return Quaternion(*(a * w0 + b * w1 for a, b in zip(q0, q1)))


def quat_to_mat4(q):
    """四元数 -> 旋转 Mat4（标准右手系，glTF 语义）。

    注意：不用 pyglet 的 Quaternion.to_mat4 —— 实测其输出为标准旋转矩阵的
    转置（90° 绕 Y 把 +X 映到 +Z），会把左旋当右旋。
    """
    w, x, y, z = q
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return Mat4(
        1 - 2 * (yy + zz), 2 * (xy + wz), 2 * (xz - wy), 0.0,   # 第 0 列
        2 * (xy - wz), 1 - 2 * (xx + zz), 2 * (yz + wx), 0.0,   # 第 1 列
        2 * (xz + wy), 2 * (yz - wx), 1 - 2 * (xx + yy), 0.0,   # 第 2 列
        0.0, 0.0, 0.0, 1.0)


def _lerp(a, b, t):
    return a + (b - a) * t


def _lerp3(a, b, t):
    return Vec3(_lerp(a.x, b.x, t), _lerp(a.y, b.y, t), _lerp(a.z, b.z, t))


# --------------------------------------------------- Sampler / Channel --
class Sampler:
    """glTF 意义上的采样器：input 时间戳（递增）、output 关键值、插值方式。"""

    def __init__(self, times, values, interpolation='LINEAR'):
        assert len(times) == len(values) and len(times) >= 1
        assert all(times[i] < times[i + 1] for i in range(len(times) - 1)), \
            'input 时间戳必须严格递增'
        assert interpolation in ('LINEAR', 'STEP')
        self.times = list(times)
        self.values = list(values)
        self.interpolation = interpolation

    def sample(self, t):
        times = self.times
        if t <= times[0]:
            return self.values[0]
        if t >= times[-1]:
            return self.values[-1]
        i = bisect.bisect_right(times, t) - 1     # times[i] <= t < times[i+1]
        if self.interpolation == 'STEP':
            return self.values[i]
        span = times[i + 1] - times[i]
        u = (t - times[i]) / span if span > 0 else 0.0
        a, b = self.values[i], self.values[i + 1]
        if isinstance(a, Quaternion):
            return slerp(a, b, u)
        if isinstance(a, Vec3):
            return _lerp3(a, b, u)
        return _lerp(a, b, u)


class Channel:
    """通道：绑定目标节点与属性（translation / rotation / scale）。"""

    def __init__(self, node, prop, sampler):
        assert prop in ('translation', 'rotation', 'scale')
        self.node = node
        self.prop = prop
        self.sampler = sampler

    def sample(self, t):
        return self.sampler.sample(t)


# ------------------------------------------------------------- Timeline --
class Timeline:
    """时间轴：播头推进（循环）+ 全通道采样。"""

    def __init__(self, duration, channels):
        assert duration > 0
        self.duration = duration
        self.channels = list(channels)
        self.playhead = 0.0

    def advance(self, dt):
        """推进播头；发生回绕时返回 True（供 on_timeline_end 事件）。"""
        t = self.playhead + dt
        if t >= self.duration:
            self.playhead = t % self.duration
            return True
        self.playhead = t
        return False

    def seek(self, t):
        self.playhead = t % self.duration

    def sample(self):
        """返回 {node: {prop: value}}，由表现层合成模型矩阵。"""
        out = {}
        for ch in self.channels:
            out.setdefault(ch.node, {})[ch.prop] = ch.sample(self.playhead)
        return out


# ---------------------------------------------------------- 演示编排 --
def build_demo_timeline(cube_meta, duration=4.0):
    """按节点元数据 [(name, base_pos)] 构建演示编排（升降 + 旋转）。

    - 环形立方体：相位错开的上下浮动（9 关键帧 LINEAR，首尾连续）
      + 绕 Y 轴 360°/周期自旋（每 90° 一个四元数关键帧，slerp 分段）
    - 中央大立方体：仅绕 Y 自旋
    """
    channels = []
    spin_times = [0.0, 1.0, 2.0, 3.0, 4.0]
    spin_quats = [quat_from_axis_angle((0, 1, 0), a) for a in (0, 90, 180, 270, 360)]

    for name, pos in cube_meta:
        if name == 'center':
            continue
        phase = 2 * math.pi * float(name.split('_')[1]) / 10.0
        bob_times = [j * 0.5 for j in range(9)]                       # 0.0 ~ 4.0
        bob_values = [
            Vec3(pos.x, pos.y + 0.25 + 0.25 * math.sin(2 * math.pi * j / 8.0 + phase), pos.z)
            for j in range(9)]
        channels.append(Channel(name, 'translation',
                                Sampler(bob_times, bob_values, 'LINEAR')))
        channels.append(Channel(name, 'rotation',
                                Sampler(spin_times, spin_quats, 'LINEAR')))

    channels.append(Channel('center', 'rotation',
                            Sampler(spin_times, spin_quats, 'LINEAR')))
    return Timeline(duration, channels)
