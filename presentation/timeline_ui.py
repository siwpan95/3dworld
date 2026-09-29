# -*- coding: utf-8 -*-
"""时间轴 UI 面板（M3）：播放/暂停、循环开关、可拖动时间指针、时间读数。

绘制在屏幕空间（正交投影下），与 3D 视口解耦。
交互契约：
  on_press(x, y) -> bool   命中面板返回 True（拦截相机拖动）
  on_drag(x, y)            拖动中（仅刷取时间指针时生效）
  on_release()             松开
回调由装配层注入：on_toggle_play / on_toggle_loop / on_seek(t)
"""
import math

import pyglet
from pyglet import shapes, text

PANEL_MARGIN = 12
PANEL_HEIGHT = 46
BTN = 26
TRACK_H = 6
TIME_LABEL_W = 110


class TimelineUI:
    def __init__(self, duration, on_toggle_play, on_toggle_loop, on_seek):
        self.duration = duration
        self.on_toggle_play = on_toggle_play
        self.on_toggle_loop = on_toggle_loop
        self.on_seek = on_seek
        self.scrubbing = False

        self.batch = pyglet.graphics.Batch()
        self._layout_ready = False
        self._width = self._height = 0
        self.playhead = 0.0
        self.playing = False
        self.looping = True

    # ------------------------------------------------------------ 布局 --
    def layout(self, width, height):
        """窗口尺寸变化时重建所有图元。"""
        self._width, self._height = width, height
        self.batch = pyglet.graphics.Batch()

        x0 = PANEL_MARGIN
        y0 = PANEL_MARGIN
        pw = width - 2 * PANEL_MARGIN

        self.panel = shapes.Rectangle(x0, y0, pw, PANEL_HEIGHT,
                                      color=(25, 28, 34, 210), batch=self.batch)

        # 播放/暂停按钮
        bx = x0 + 10
        by = y0 + (PANEL_HEIGHT - BTN) / 2
        self.btn_play_bg = shapes.Rectangle(bx, by, BTN, BTN,
                                            color=(60, 64, 72, 255), batch=self.batch)
        cx, cy = bx + BTN / 2, by + BTN / 2
        self.icon_play = shapes.Triangle(cx - 6, cy - 8, cx - 6, cy + 8, cx + 9, cy,
                                         color=(235, 235, 235, 255), batch=self.batch)
        self.icon_pause_l = shapes.Rectangle(cx - 5, cy - 8, 4, 16,
                                             color=(235, 235, 235, 255), batch=self.batch)
        self.icon_pause_r = shapes.Rectangle(cx + 2, cy - 8, 4, 16,
                                             color=(235, 235, 235, 255), batch=self.batch)

        # 循环按钮
        lx = bx + BTN + 10
        self.btn_loop_bg = shapes.Rectangle(lx, by, BTN + 14, BTN,
                                            color=(60, 64, 72, 255), batch=self.batch)
        self.lbl_loop = text.Label('LOOP', font_size=9, weight='bold',
                                   x=lx + (BTN + 14) / 2, y=by + BTN / 2,
                                   anchor_x='center', anchor_y='center',
                                   batch=self.batch)

        # 轨道 + 进度 + 播头
        self.track_x0 = lx + BTN + 14 + 16
        self.track_x1 = x0 + pw - TIME_LABEL_W - 10
        ty = y0 + PANEL_HEIGHT / 2 - TRACK_H / 2
        self.track = shapes.Rectangle(self.track_x0, ty, self.track_x1 - self.track_x0,
                                      TRACK_H, color=(70, 74, 84, 255), batch=self.batch)
        self.progress = shapes.Rectangle(self.track_x0, ty, 0, TRACK_H,
                                         color=(80, 180, 255, 255), batch=self.batch)
        self.handle = shapes.Circle(self.track_x0, ty + TRACK_H / 2, 7,
                                    color=(240, 244, 248, 255), batch=self.batch)

        # 时间读数
        self.lbl_time = text.Label('', font_size=10,
                                   x=self.track_x1 + 10, y=ty + TRACK_H / 2 + 2,
                                   anchor_x='left', anchor_y='center',
                                   color=(200, 205, 212, 255), batch=self.batch)
        self._layout_ready = True
        self.update(self.playhead, self.playing, self.looping)

    # ------------------------------------------------------------ 状态 --
    def update(self, playhead, playing, looping):
        """每帧由装配层同步播放状态（读直传，不走事件）。"""
        self.playhead, self.playing, self.looping = playhead, playing, looping
        if not self._layout_ready:
            return
        frac = 0.0 if self.duration <= 0 else min(1.0, playhead / self.duration)
        self.progress.width = (self.track_x1 - self.track_x0) * frac
        self.handle.x = self.track_x0 + (self.track_x1 - self.track_x0) * frac
        self.icon_play.visible = not playing
        self.icon_pause_l.visible = playing
        self.icon_pause_r.visible = playing
        self.btn_loop_bg.color = (40, 120, 70, 255) if looping else (60, 64, 72, 255)
        self.lbl_loop.color = (235, 245, 238, 255) if looping else (150, 154, 160, 255)
        self.lbl_time.text = f'{playhead:5.2f} / {self.duration:.2f} s'

    def draw(self):
        self.batch.draw()

    # ------------------------------------------------------------ 交互 --
    @staticmethod
    def _in_rect(x, y, rx, ry, rw, rh, pad=4):
        """点 (x,y) 是否落在矩形 (rx,ry,rw,rh) 内（含 pad 外扩）。"""
        return rx - pad <= x <= rx + rw + pad and ry - pad <= y <= ry + rh + pad

    def _t_from_x(self, x):
        span = self.track_x1 - self.track_x0
        frac = 0.0 if span <= 0 else (x - self.track_x0) / span
        frac = max(0.0, min(1.0, frac))
        return frac * self.duration

    def on_press(self, x, y):
        """左键按下。命中面板返回 True（拦截相机），否则 False。"""
        if not self._layout_ready:
            return False
        if not self._in_rect(x, y, self.panel.x, self.panel.y,
                             self.panel.width, self.panel.height, pad=0):
            return False
        if self._in_rect(x, y, self.btn_play_bg.x, self.btn_play_bg.y, BTN, BTN):
            self.on_toggle_play()
        elif self._in_rect(x, y, self.btn_loop_bg.x, self.btn_loop_bg.y,
                           self.btn_loop_bg.width, self.btn_loop_bg.height):
            self.on_toggle_loop()
        elif x >= self.track_x0 - 8:
            self.scrubbing = True
            self.on_seek(self._t_from_x(x))
        return True

    def on_drag(self, x, y):
        if self.scrubbing:
            self.on_seek(self._t_from_x(x))

    def on_release(self):
        self.scrubbing = False
