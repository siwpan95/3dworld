# -*- coding: utf-8 -*-
"""播放控制器（M3）：播放/暂停/循环/倍速/seek 的纯状态机。

职责边界：只操作 Timeline 的播头，不采样不渲染——
采样由表现层每帧执行（暂停/seek 后姿态同样可见）。
"""
import math


class PlaybackController:
    # 回绕/结束哨兵值（advance 返回）
    WRAPPED, ENDED = 'wrapped', 'ended'

    def __init__(self, timeline, looping=True, speed=1.0):
        self.timeline = timeline
        self.playing = False
        self.looping = looping
        self.speed = speed

    # ---- 状态切换 ----
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
        """跳转到任意时刻（表现层拖动时间指针时调用）。"""
        self.timeline.seek(t)

    # ---- 每帧推进 ----
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
                self.timeline.seek(t)                     # seek 内部取模回绕
                return self.WRAPPED
            self.timeline.seek(self.timeline.duration - 1e-6)
            self.playing = False
            return self.ENDED
        self.timeline.seek(t)
        return None
