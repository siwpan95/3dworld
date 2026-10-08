# -*- coding: utf-8 -*-
"""pyglet 3D 引擎（3dworld 核心引擎抽取物）。

零业务依赖：只依赖 pyglet（数学用 pyglet.math，绘制用 pyglet.gl），
不含任何演出编排 / demo 场景内容。

分层（依赖只能自上而下，反向禁止）::

    app  ->  renderer / postfx  ->  mesh / material / shaders
                                ->  scene -> camera / frustum
    gltf  ->  mesh / material / scene
    mathkit / timeline / picking / physics / frustum ：纯数学，可无窗口单测

``engine`` 同时是 3dworld 中 ``core/`` ``logic/`` ``presentation/`` 的
公共下沉层：原先散落在三层的通用件（四元数插值、时间轴、相机、HUD、
抓帧、主循环骨架）在这里有统一、可复用、可单测的实现。
"""

__version__ = '0.1.0'


def _safe(name, factory):
    """延迟导入：GL 相关模块在无上下文环境下不应阻断 import engine。"""
    try:
        return factory()
    except Exception:            # pragma: no cover - 仅在缺 GL 上下文时触发
        return None


from engine.mathkit import (      # noqa: E402  纯数学，恒定可用
    clamp, lerp, smoothstep,
    quat_from_axis_angle, slerp, quat_to_mat4,
    look_at_matrix, mat4_from_trs, mat4_mul, mat4_inverse,
    normal_matrix, trs_decompose,
)
from engine.timeline import (     # noqa: E402
    Sampler, Channel, Timeline, PlaybackController,
)
from engine.frustum import Frustum, AABB, Sphere   # noqa: E402

__all__ = [
    '__version__',
    'clamp', 'lerp', 'smoothstep',
    'quat_from_axis_angle', 'slerp', 'quat_to_mat4',
    'look_at_matrix', 'mat4_from_trs', 'mat4_mul', 'mat4_inverse',
    'normal_matrix', 'trs_decompose',
    'Sampler', 'Channel', 'Timeline', 'PlaybackController',
    'Frustum', 'AABB', 'Sphere',
]
