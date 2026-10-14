# -*- coding: utf-8 -*-
"""屏幕空间 HUD：幕标题字幕 + 状态读数 + 按键帮助（两个主线 demo 共用）。

字幕的时间轴语义：每幕开始时标题**淡入**、停留数秒后**淡出**，
不长期占据画面——这比常驻文字更像"演出"，也更接近 demoscene 的字幕节奏。
淡出后只在状态行保留幕名，随时可查。

绘制约定：调用方负责切到正交投影、关深度测试（``WorldRenderer.begin_ui()``），
本模块只画文字；所有图元挂在同一个 Batch 上，一次 draw call。
"""
import pyglet
from pyglet import text

COL_TITLE = (255, 255, 255, 255)
COL_SUBTITLE = (196, 208, 226, 255)
COL_STATS = (222, 230, 240, 255)
COL_HELP = (168, 176, 190, 255)
COL_ACT = (255, 196, 110, 255)

FADE_IN, HOLD, FADE_OUT = 0.6, 3.4, 1.2


def _with_alpha(rgb, alpha):
    r, g, b = rgb[:3]
    return (r, g, b, max(0, min(255, int(alpha))))


class Hud:
    """幕字幕 + 状态行 + 帮助行。

    Args:
        acts: [(起始时刻, 标题, 副标题), ...]（逻辑层的幕表，直接传进来）
        help_text: 底部按键帮助
    """

    def __init__(self, acts=(), help_text=''):
        self.acts = list(acts)
        self.batch = pyglet.graphics.Batch()
        self.lbl_title = text.Label('', font_size=26, weight='bold',
                                    x=0, y=0, anchor_x='center', anchor_y='center',
                                    color=COL_TITLE, batch=self.batch)
        self.lbl_subtitle = text.Label('', font_size=14,
                                       x=0, y=0, anchor_x='center', anchor_y='center',
                                       color=COL_SUBTITLE, batch=self.batch)
        self.lbl_stats = text.Label('', font_size=12,
                                    x=0, y=0, anchor_x='right', anchor_y='center',
                                    color=COL_STATS, batch=self.batch)
        self.lbl_help = text.Label(help_text, font_size=12,
                                   x=0, y=0, anchor_x='left', anchor_y='center',
                                   color=COL_HELP, batch=self.batch)
        self._act_idx = -1
        self._title = ''
        self._subtitle = ''
        self._alpha = 0.0

    # ------------------------------------------------------------ 布局 --
    def layout(self, width, height):
        cx = width / 2.0
        # 帮助行放**顶部**：底部 14~78px 是时间轴面板的地盘，
        # 早期把帮助行放在 y=16 会被面板压住（实测截图里字叠在一起）。
        self.lbl_help.position = (14, height - 22, 0)
        self.lbl_stats.position = (width - 14, height - 22, 0)
        self.lbl_title.position = (cx, height - 88, 0)
        self.lbl_subtitle.position = (cx, height - 116, 0)

    # ------------------------------------------------------------ 刷新 --
    def update(self, playhead, stats='', duration=None):
        """按播头刷新字幕与状态行。"""
        self.lbl_stats.text = stats
        if not self.acts:
            return
        idx = 0
        for k, act in enumerate(self.acts):
            if playhead >= act[0]:
                idx = k
        if idx != self._act_idx:                 # 换幕：换文案，重新淡入
            self._act_idx = idx
            self._title = self.acts[idx][1]
            self._subtitle = self.acts[idx][2] if len(self.acts[idx]) > 2 else ''
            self.lbl_title.text = self._title
            self.lbl_subtitle.text = self._subtitle
        since = playhead - self.acts[idx][0]
        if since < FADE_IN:
            a = since / FADE_IN
        elif since < FADE_IN + HOLD:
            a = 1.0
        elif since < FADE_IN + HOLD + FADE_OUT:
            a = 1.0 - (since - FADE_IN - HOLD) / FADE_OUT
        else:
            a = 0.0
        self._alpha = max(0.0, min(1.0, a)) * 255.0
        self.lbl_title.color = _with_alpha(COL_TITLE, self._alpha)
        self.lbl_subtitle.color = _with_alpha(COL_SUBTITLE, self._alpha * 0.95)
        self.lbl_title.visible = self.lbl_subtitle.visible = self._alpha > 1.0

    def draw(self):
        self.batch.draw()
