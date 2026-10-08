# -*- coding: utf-8 -*-
"""时间轴 / 关键帧 / 插值 —— 兼容再导出层。

实现已下沉到 :mod:`engine.timeline`（核心引擎抽取，2026-10）。这里保留
原签名：``quat_from_axis_angle`` / ``slerp`` 返回 pyglet ``Quaternion``，
``quat_to_mat4`` 返回 ``Mat4``——项目内大量代码把它们的返回值直接塞进
Sampler / 矩阵乘法，改语义会牵连一片，故在本层做类型适配。

新代码请直接用 ``engine.timeline``（tuple 四元数、无 GL 依赖、支持
CUBICSPLINE）。
"""
from pyglet.math import Quaternion

from engine.timeline import Channel, Sampler, Timeline          # noqa: F401
from engine.mathkit import quat_from_axis_angle as _axis_angle
from engine.mathkit import slerp as _slerp
from engine.timeline import quat_to_mat4                        # noqa: F401

__all__ = ['Sampler', 'Channel', 'Timeline',
           'quat_from_axis_angle', 'slerp', 'quat_to_mat4']


def quat_from_axis_angle(axis, angle_deg):
    """轴角 -> pyglet Quaternion(w,x,y,z)（兼容旧签名）。"""
    return Quaternion(*_axis_angle(axis, angle_deg))


def slerp(q0, q1, t):
    """四元数球面插值（返回 Quaternion，兼容旧签名）。"""
    return Quaternion(*_slerp(tuple(q0), tuple(q1), t))
