# -*- coding: utf-8 -*-
"""时间轴 / 关键帧 / 插值，数据模型对齐 glTF 2.0 动画规范。

- ``Sampler``   —— input 时间戳 + output 关键值 + 插值方式
- ``Channel``   —— 目标节点 + 属性（translation / rotation / scale / ...）
- ``Timeline``  —— 时长 + 通道列表 + 播头推进与采样
- ``PlaybackController`` —— 播放/暂停/循环/倍速/seek 的纯状态机

插值方式三种，与 glTF 2.0 一一对应::

    LINEAR       线性（Vec3）/ 球面（四元数）
    STEP         保持上一关键帧
    CUBICSPLINE  三次 Hermite（每帧带 in/out 切线，规范定义）

旋转插值统一走四元数 slerp（等距关键帧分段，避免 180°+ 退化）。
纯数学实现，不 import pyglet.gl，可无窗口单测。
"""
import bisect
import math

from pyglet.math import Quaternion, Vec3

from engine.mathkit import slerp, quat_normalize

__all__ = [
    'Sampler', 'Channel', 'Timeline', 'PlaybackController',
    'quat_from_axis_angle', 'slerp', 'quat_to_mat4',
]

# 允许的插值方式（glTF 2.0 规范 + 本工程未用到的均不收）
INTERPOLATIONS = ('LINEAR', 'STEP', 'CUBICSPLINE')


# ------------------------------------------------------------ 四元数工具 --
def quat_from_axis_angle(axis, angle_deg):
    """轴角 -> pyglet Quaternion(w,x,y,z)。axis 会被归一化。"""
    ax, ay, az = axis
    norm = math.sqrt(ax * ax + ay * ay + az * az)
    ax, ay, az = ax / norm, ay / norm, az / norm
    half = math.radians(angle_deg) * 0.5
    s = math.sin(half)
    return Quaternion(math.cos(half), ax * s, ay * s, az * s)


def quat_to_mat4(q):
    """四元数 -> 列主序旋转 Mat4（右手法，glTF 语义）。

    注意：不用 pyglet 的 ``Quaternion.to_mat4`` —— 实测其输出为标准旋转矩阵的
    转置（90° 绕 Y 会把 +X 映到 +Z），左右手系会反。
    """
    from engine.mathkit import mat4_to_Mat4
    return mat4_to_Mat4(_quat_to_mat4_tuple(q))


def _quat_to_mat4_tuple(q):
    from engine.mathkit import mat4_from_trs
    return mat4_from_trs((0.0, 0.0, 0.0), tuple(q), (1.0, 1.0, 1.0))


# ------------------------------------------------------ 值类型的小工具 --
def _lerp(a, b, t):
    return a + (b - a) * t


def _lerp3(a, b, t):
    return Vec3(_lerp(a.x, b.x, t), _lerp(a.y, b.y, t), _lerp(a.z, b.z, t))


def _is_quat(v):
    return isinstance(v, Quaternion)


def _is_vec3(v):
    return isinstance(v, Vec3)


def _cubic(a, m0, b, m1, t):
    """三次 Hermite：在 a..b 之间以切线 m0/m1 插值（标量）。"""
    t2, t3 = t * t, t * t * t
    return ((2 * t3 - 3 * t2 + 1) * a + (t3 - 2 * t2 + t) * m0
            + (-2 * t3 + 3 * t2) * b + (t3 - t2) * m1)


# --------------------------------------------------- Sampler / Channel --
class Sampler:
    """glTF 意义上的采样器：input 时间戳（严格递增）+ output 关键值 + 插值。

    ``CUBICSPLINE`` 模式下 ``values`` 的每个元素必须是
    ``(in_tangent, value, out_tangent)`` 三元组（glTF 规范的数据排布）。
    """

    def __init__(self, times, values, interpolation='LINEAR'):
        assert len(times) == len(values) and len(times) >= 1
        assert all(times[i] < times[i + 1] for i in range(len(times) - 1)), \
            'input 时间戳必须严格递增'
        assert interpolation in INTERPOLATIONS, f'未知插值方式: {interpolation}'
        self.times = list(times)
        # 普通模式下把裸三元组规整成 Vec3：手写关键帧时 (x,y,z) 比 Vec3(...) 顺手，
        # 但插值分支要按类型走，统一进来省得调用方踩"tuple 没有 .x"的坑。
        def _as_val(v):
            if (isinstance(v, (tuple, list)) and len(v) == 3
                    and all(isinstance(c, (int, float)) for c in v)):
                return Vec3(*v)
            return v

        if interpolation == 'CUBICSPLINE':
            # 每帧是 (in_tangent, value, out_tangent)，三个都要规整
            self.values = [tuple(_as_val(x) for x in triple) for triple in values]
        else:
            self.values = [_as_val(v) for v in values]
        self.interpolation = interpolation

    # ---- 查询 ----
    @property
    def duration(self):
        return self.times[-1] - self.times[0]

    def _segment(self, t):
        """返回 (i, u)：times[i] <= t < times[i+1] 的段号与段内比例。"""
        times = self.times
        if t <= times[0]:
            return -1, 0.0
        if t >= times[-1]:
            return len(times) - 1, 0.0
        i = bisect.bisect_right(times, t) - 1
        span = times[i + 1] - times[i]
        return i, ((t - times[i]) / span if span > 0 else 0.0)

    def sample(self, t):
        """在时刻 t 采样。边界外钳制到首/末关键值（或末切线）。"""
        i, u = self._segment(t)
        if i < 0:
            return self._unwrap(self.values[0])
        if i >= len(self.times) - 1:
            return self._unwrap(self.values[-1])
        a, b = self.values[i], self.values[i + 1]
        if self.interpolation == 'STEP':
            return self._unwrap(a)
        if self.interpolation == 'CUBICSPLINE':
            return self._sample_cubic(a, b, u, self.times[i + 1] - self.times[i])
        a, b = self._unwrap(a), self._unwrap(b)
        if _is_quat(a):
            return Quaternion(*slerp(tuple(a), tuple(b), u))
        if _is_vec3(a):
            return _lerp3(a, b, u)
        return _lerp(a, b, u)

    def _unwrap(self, v):
        """CUBICSPLINE 存储的是三元组，取中间那个才是实际值。"""
        if self.interpolation == 'CUBICSPLINE':
            return v[1]
        return v

    def _sample_cubic(self, a, b, u, dt):
        """glTF CUBICSPLINE：m0 = out_tangent[i]*dt，m1 = in_tangent[i+1]*dt。

        四元数没有规范定义的"切线"语义（规范只规定了标量/Vec3），这里按
        分量 Hermite 后归一化——足够平滑，且与常见实现对齐。
        """
        v0, m0 = a[1], a[2]
        v1, m1 = b[1], b[0]
        if _is_vec3(v0):
            return Vec3(_cubic(v0.x, m0.x * dt, v1.x, m1.x * dt, u),
                        _cubic(v0.y, m0.y * dt, v1.y, m1.y * dt, u),
                        _cubic(v0.z, m0.z * dt, v1.z, m1.z * dt, u))
        if _is_quat(v0):
            t0, ta = tuple(v0), tuple(m0)
            t1, tb = tuple(v1), tuple(m1)
            q = Quaternion(*(_cubic(t0[k], ta[k] * dt, t1[k], tb[k] * dt, u)
                             for k in range(4)))
            return Quaternion(*quat_normalize(tuple(q)))
        return _cubic(v0, m0 * dt, v1, m1 * dt, u)


class Channel:
    """通道：绑定目标节点与属性。

    属性集分两类（见 ``PROPS``）:

    - ``translation`` / ``rotation`` / ``scale``：glTF 2.0 的节点 TRS，
      值与规范一致（Vec3 / 四元数 / Vec3）；
    - ``tint`` / ``glow``：**本工程的演示级扩展**（Vec3 颜色乘子 / float 自发光），
      glTF 没有材质动画语义，这里只服务于 demo 的"灯光演出"，
      命名与解析都留在表现层，不污染 TRS 的语义。
    """

    PROPS = ('translation', 'rotation', 'scale', 'tint', 'glow')

    def __init__(self, node, prop, sampler):
        assert prop in self.PROPS, f'未知属性: {prop}'
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
        self.markers = {}                # {名字: 时刻} —— 幕标记 / 事件点

    # ---- 播头 ----
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

    @property
    def progress(self):
        return self.playhead / self.duration

    def mark(self, name, t):
        """登记一个幕标记（HUD / 时间轴面板会用）。"""
        self.markers[name] = float(t)
        return self

    def sample(self):
        """返回 {node: {prop: value}}，由表现层合成模型矩阵。"""
        out = {}
        for ch in self.channels:
            out.setdefault(ch.node, {})[ch.prop] = ch.sample(self.playhead)
        return out

    def sample_node(self, node):
        """只采样某个节点（大场景里省掉无关通道的遍历）。"""
        out = {}
        for ch in self.channels:
            if ch.node == node:
                out[ch.prop] = ch.sample(self.playhead)
        return out


# --------------------------------------------------------------- 播放机 --
class PlaybackController:
    """播放/暂停/循环/倍速/seek 的纯状态机。

    职责边界：只操作 Timeline 的播头，不采样不渲染——
    采样由表现层每帧执行（暂停/seek 后姿态同样可见）。
    """

    WRAPPED, ENDED = 'wrapped', 'ended'

    def __init__(self, timeline, looping=True, speed=1.0):
        self.timeline = timeline
        self.playing = False
        self.looping = looping
        self.speed = speed

    def play(self):
        self.playing = True

    def pause(self):
        self.playing = False

    def toggle(self):
        self.playing = not self.playing
        return self.playing

    def set_looping(self, looping):
        self.looping = looping

    def set_speed(self, speed):
        self.speed = max(0.1, min(4.0, speed))

    def seek(self, t):
        self.timeline.seek(t)

    def advance(self, dt):
        """推进播头。返回 WRAPPED / ENDED / None。

        - 循环模式：越界回绕，返回 WRAPPED；
        - 非循环模式：钳制在末尾并自动暂停，返回 ENDED；
        - 暂停状态：不推进，返回 None。
        """
        if not self.playing:
            return None
        t = self.timeline.playhead + dt * self.speed
        if t >= self.timeline.duration:
            if self.looping:
                self.timeline.seek(t)             # seek 内部取模回绕
                return self.WRAPPED
            self.timeline.seek(self.timeline.duration - 1e-6)
            self.playing = False
            return self.ENDED
        self.timeline.seek(t)
        return None
