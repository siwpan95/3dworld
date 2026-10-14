# -*- coding: utf-8 -*-
"""动画/时间轴共同核 ``timeline``（实现 ``world3d.core.contracts.TimelineCore``）。

封装 ``world3d.engine.timeline`` 的 Sampler / Channel / Timeline / PlaybackController，
把它们以**服务**形式暴露（P0-1：可跨内核复用）。

> 依赖：``world3d.engine.timeline``（纯 Python，仅 bisect/math）+ ``cordis``（vendored）。
"""
from __future__ import annotations

from world3d.core._cordis import Service
from world3d.engine.timeline import Channel, PlaybackController, Sampler, Timeline


class TimelineService(Service):
    """关键帧、骨骼/变换插值、播放控制。"""

    def __init__(self, ctx, config):
        super().__init__(ctx, "timeline")

    def make_sampler(self, times, values, interpolation='LINEAR'):
        """构造采样器（``world3d.engine.timeline.Sampler``）。"""
        return Sampler(times, values, interpolation)

    def make_channel(self, node, prop, sampler):
        """把采样器挂到节点的某个属性（``world3d.engine.timeline.Channel``）。"""
        return Channel(node, prop, sampler)

    def make_timeline(self, duration, channels):
        """构造时间轴（``world3d.engine.timeline.Timeline``）。"""
        return Timeline(duration, channels)

    def make_playback(self, timeline, looping=True, speed=1.0):
        """构造播放控制器（``world3d.engine.timeline.PlaybackController``）。"""
        return PlaybackController(timeline, looping, speed)

    def __cordis_init__(self):
        return None


__all__ = ['TimelineService']
