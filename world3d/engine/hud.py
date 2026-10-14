# -*- coding: utf-8 -*-
"""屏幕空间 HUD：标题 / 帮助 / 状态行 / 性能面板。

两个实测坑（都在项目里真实发生过）:

- **纯文字会被亮背景吃掉**：水面、雪地这类 demo，顶部帮助行糊在背景里
  看不清。所以顶部/底部各铺一条半透明暗底（先入 Batch，永远在文字之下）。
- **窄窗口下标题与状态行撞车**：窗口小于约 1000px 时两行会叠成一团。
  撞车时让标题让位（窗口标题栏里还有一份，信息不丢）。
"""
from pyglet import shapes, text

__all__ = ['Hud']

COL_TITLE = (245, 247, 250, 255)
COL_STATUS = (200, 210, 225, 255)
COL_HELP = (168, 176, 190, 255)
COL_PERF = (150, 220, 255, 255)


class Hud:
    """HUD 图层。``layout`` 在 resize 时调，``draw()`` 在每帧末尾调。"""

    BAR_H = 30
    BAR_COLOR = (8, 10, 14, 96)
    LINE_H = 16

    def __init__(self, title='', help_text='', batch=None):
        self.batch = batch if batch is not None else __import__('pyglet').graphics.Batch()
        self.bar_top = shapes.Rectangle(0, 0, 10, self.BAR_H, color=self.BAR_COLOR,
                                        batch=self.batch)
        self.bar_bottom = shapes.Rectangle(0, 0, 10, self.BAR_H, color=self.BAR_COLOR,
                                           batch=self.batch)
        self.title = text.Label(title, font_size=15, weight='bold',
                                x=14, y=0, anchor_x='left', anchor_y='center',
                                color=COL_TITLE, batch=self.batch)
        self.status = text.Label('', font_size=12, x=0, y=0,
                                 anchor_x='right', anchor_y='center',
                                 color=COL_STATUS, batch=self.batch)
        self.help = text.Label(help_text, font_size=12, x=14, y=0,
                               anchor_x='left', anchor_y='center',
                               color=COL_HELP, batch=self.batch)
        self.perf_labels = []
        self.visible = True
        self._width = 9999
        self._height = 0
        self._perf_texts = []

    def layout(self, width, height):
        """窗口尺寸变化时重排。"""
        self._width = width
        self._height = height
        self.bar_top.position = (0, height - self.BAR_H)
        self.bar_top.width, self.bar_top.height = width, self.BAR_H
        self.bar_bottom.position = (0, 0)
        self.bar_bottom.width, self.bar_bottom.height = width, self.BAR_H
        self.title.position = (14, height - 22, 0)
        self.status.position = (width - 14, height - 22, 0)
        self.help.position = (14, 16, 0)
        self._layout_perf(width, height)

    def _layout_perf(self, width, height):
        for lbl in self.perf_labels:
            lbl.delete()
        self.perf_labels = []
        # 性能面板放右上角，向下堆叠（顶部条之下）
        for i, _ in enumerate(self._perf_texts):
            lbl = text.Label('', font_size=11, x=width - 14,
                             y=height - 46 - i * self.LINE_H,
                             anchor_x='right', anchor_y='center',
                             color=COL_PERF, batch=self.batch)
            self.perf_labels.append(lbl)

    def set_status(self, text_):
        self.status.text = text_
        self.title.visible = (self.title.content_width + self.status.content_width
                              <= self._width - 40)

    def set_perf(self, lines):
        """更新性能面板行（行数变化时重排一次）。"""
        if list(lines) != self._perf_texts:
            self._perf_texts = list(lines)
            self._layout_perf(self._width, self._height)
        for i, line in enumerate(self._perf_texts):
            if i < len(self.perf_labels):
                self.perf_labels[i].text = line

    def draw(self):
        if self.visible:
            self.batch.draw()
