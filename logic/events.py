# -*- coding: utf-8 -*-
"""事件总线：复用 pyglet EventDispatcher（发布/订阅，松耦合）。

事件清单（M1）：
  on_frame(dt)        每帧逻辑更新后派发
  on_view_changed()   相机参数变化（拖动/缩放/自动环绕）后派发
后续事件（M2+）在此登记：on_timeline_end / on_channel_changed ...
"""
from pyglet.event import EventDispatcher


class EventBus(EventDispatcher):
    # 注意：pyglet 的 register_event_type 会 append，必须用 list 而非 tuple
    event_types = ['on_frame', 'on_view_changed']


EventBus.register_event_type('on_frame')
EventBus.register_event_type('on_view_changed')
