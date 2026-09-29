# -*- coding: utf-8 -*-
"""时间轴 UI 面板（M3/M4）：播放/暂停、循环开关、可拖动时间指针、时间读数。

绘制在屏幕空间（正交投影下），与 3D 视口解耦。
尺寸与对比度按"小窗口可读"校准：大按钮、粗轨道、高亮度文字。
交互契约：
  on_press(x, y) -> bool   命中面板返回 True（拦截相机拖动）
  on_drag(x, y)            拖动中（仅刷取时间指针时生效）
  on_release()             松开
回调由装配层注入：on_toggle_play / on_toggle_loop / on_seek(t)
"""
import pyglet
from pyglet import shapes, text

PANEL_MARGIN = 14
PANEL_HEIGHT = 64
BTN = 40
TRACK_H = 10
TIME_LABEL_W = 130
BORDER = 2

COL_PANEL = (18, 20, 26, 235)
COL_PANEL_BORDER = (185, 192, 205, 150)
COL_BTN = (58, 64, 76, 255)
COL_BTN_BORDER = (165, 172, 184, 170)
COL_BTN_ON = (28, 148, 84, 255)
COL_TRACK = (46, 50, 60, 255)
COL_PROGRESS = (0, 165, 255, 255)
COL_ICON = (255, 255, 255, 255)
COL_TEXT = (245, 247, 250, 255)
COL_TEXT_DIM = (152, 157, 165, 255)


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

        self.panel = shapes.BorderedRectangle(x0, y0, pw, PANEL_HEIGHT, BORDER,
                                              color=COL_PANEL,
                                              border_color=COL_PANEL_BORDER,
                                              batch=self.batch)

        # 播放/暂停按钮
        bx = x0 + 14
        by = y0 + (PANEL_HEIGHT - BTN) / 2
        self.btn_play_bg = shapes.BorderedRectangle(bx, by, BTN, BTN, BORDER,
                                                    color=COL_BTN,
                                                    border_color=COL_BTN_BORDER,
                                                    batch=self.batch)
        cx, cy = bx + BTN / 2, by + BTN / 2
        self.icon_play = shapes.Triangle(cx - 8, cy - 11, cx - 8, cy + 11, cx + 12, cy,
                                         color=COL_ICON, batch=self.batch)
        self.icon_pause_l = shapes.Rectangle(cx - 7, cy - 11, 6, 22,
                                             color=COL_ICON, batch=self.batch)
        self.icon_pause_r = shapes.Rectangle(cx + 2, cy - 11, 6, 22,
                                             color=COL_ICON, batch=self.batch)

        # 循环按钮
        lx = bx + BTN + 12
        loop_w = BTN + 18
        self.btn_loop_bg = shapes.BorderedRectangle(lx, by, loop_w, BTN, BORDER,
                                                    color=COL_BTN,
                                                    border_color=COL_BTN_BORDER,
                                                    batch=self.batch)
        self.lbl_loop = text.Label('LOOP', font_size=13, weight='bold',
                                   x=lx + loop_w / 2, y=by + BTN / 2,
                                   anchor_x='center', anchor_y='center',
                                   color=COL_TEXT_DIM, batch=self.batch)

        # 轨道 + 进度 + 播头
        self.track_x0 = lx + loop_w + 18
        self.track_x1 = x0 + pw - TIME_LABEL_W - 12
        ty = y0 + PANEL_HEIGHT / 2 - TRACK_H / 2
        self.track = shapes.Rectangle(self.track_x0, ty, self.track_x1 - self.track_x0,
                                      TRACK_H, color=COL_TRACK, batch=self.batch)
        self.progress = shapes.Rectangle(self.track_x0, ty, 0, TRACK_H,
                                         color=COL_PROGRESS, batch=self.batch)
        self.handle = shapes.Circle(self.track_x0, ty + TRACK_H / 2, 9,
                                    color=COL_ICON, batch=self.batch)

        # 时间读数
        self.lbl_time = text.Label('', font_size=14,
                                   x=self.track_x1 + 12, y=ty + TRACK_H / 2 + 2,
                                   anchor_x='left', anchor_y='center',
                                   color=COL_TEXT, batch=self.batch)
        self._layout_ready = True
        self.update(self.playhead, self.playing, self.looping)

    # ------------------------------------------------------------ 状态 --
    def update(self, playhead, playing, looping):
        """每帧由装配层同步播放状态（读直传，不走事件）。"""
        self.playhead, self.playing, self.looping = playhead, playing, looping
        if not self._layout_ready:
            return
        frac = 0.0 if self.duration <= 0 else min(1.0, playhead / self.duration)
        span = self.track_x1 - self.track_x0
        self.progress.width = span * frac
        self.handle.x = self.track_x0 + span * frac
        self.icon_play.visible = not playing
        self.icon_pause_l.visible = playing
        self.icon_pause_r.visible = playing
        self.btn_loop_bg.color = COL_BTN_ON if looping else COL_BTN
        self.lbl_loop.color = COL_TEXT if looping else COL_TEXT_DIM
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
        elif x >= self.track_x0 - 10:
            self.scrubbing = True
            self.on_seek(self._t_from_x(x))
        return True

    def on_drag(self, x, y):
        if self.scrubbing:
            self.on_seek(self._t_from_x(x))

    def on_release(self):
        self.scrubbing = False
