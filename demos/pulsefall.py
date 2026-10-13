# -*- coding: utf-8 -*-
"""落音 / Pulsefall demo（§16）—— 下落式音击。

4 轨道（DFJK）下落 note + 判定线 + 判定反馈 + 结算面板；音乐由 **audio 核**
按谱面参数程序化合成（三层：低音/和弦/主旋律）。

按键：``D F J K`` 四轨击键　``1/2/3`` 切 BPM 90/120/150　``F4/F5`` offset ∓5ms
　　　``G`` 自动演奏　``R`` 重开　``ESC`` 退出

运行::

    python demos/pulsefall.py
    python demos/pulsefall.py --smoke
    python demos/pulsefall.py --shot

合规：致敬下落式音击品类；**谱面/曲目/命名全部程序化原创**。
"""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pyglet                                                   # noqa: E402
from pyglet.gl import (GL_BLEND, GL_COLOR_BUFFER_BIT,            # noqa: E402
                       GL_DEPTH_TEST, GL_ONE_MINUS_SRC_ALPHA,
                       GL_SRC_ALPHA, GL_TRIANGLES, glBlendFunc,
                       glClear, glClearColor, glDisable, glEnable)
from pyglet.graphics.shader import Shader, ShaderProgram        # noqa: E402
from pyglet.math import Mat4, Vec3                               # noqa: E402

from engine.app import EngineApp, run                            # noqa: E402
from games.pulse.kernel import (BPM_LIST, GOOD_MS,  # noqa: E402
                                             N_TRACKS)
from host.tree import (WANT_PULSE, config_path,  # noqa: E402
                                          mount_tree)

TITLE = '3dworld 落音 · Pulsefall（§16 下落式音击）'
HELP = 'DFJK 击键  1/2/3 切BPM  F4/F5 offset  G 自动  R 重开  ESC 退出'
SHOTS = ((1.5, 'docs/pulsefall_01.png'), (3.5, 'docs/pulsefall_02.png'))

VS = '''#version 330 core
in vec2 position;
uniform mat4 u_mvp;
void main() { gl_Position = u_mvp * vec4(position, 0.0, 1.0); }'''
FS = '''#version 330 core
out vec4 outColor;
uniform vec3 u_tint;
uniform float u_alpha;
void main() { outColor = vec4(u_tint, u_alpha); }'''

TRACK_X = (-0.60, -0.20, 0.20, 0.60)      # 4 轨道中心（NDC x）
TRACK_W = 0.16                            # 轨道宽（NDC）
LANE_H = 0.045                            # note 高（NDC）
JUDGE_Y = -0.72                           # 判定线（NDC y）
FALL_SEC = 1.6                            # note 从顶到判定线的下落时间
T_TRACK = (0.35, 0.75, 0.95)              # 轨道底色
T_NOTE = {'0': (0.45, 0.85, 1.0), '1': (0.55, 0.95, 0.55),
          '2': (0.95, 0.85, 0.40), '3': (0.95, 0.55, 0.85)}
T_LINE = (0.75, 0.80, 0.88)
VERDICT_TINT = {'PERFECT': (0.45, 0.98, 0.75), 'GOOD': (0.98, 0.85, 0.35),
                'MISS': (0.98, 0.42, 0.42)}


class PulsefallApp(EngineApp):
    TITLE = TITLE
    HELP = HELP
    SHOTS = SHOTS

    def setup(self):
        self.ctx, self.k = self._boot()
        self.prog = ShaderProgram(Shader(VS, 'vertex'), Shader(FS, 'fragment'))
        self.quad = self.prog.vertex_list(6, GL_TRIANGLES, position=('f', (
            -0.5, -0.5, 0.5, -0.5, 0.5, 0.5, -0.5, -0.5, 0.5, 0.5, -0.5, 0.5)))
        self.auto = 'auto' in self.args.flags
        self.last = None                          # (verdict, t)
        self.last_t = 0.0

    def _boot(self):
        return asyncio.run(self._mount())

    async def _mount(self):
        c = await mount_tree(config_path('cordis.pulse.yml'), want=WANT_PULSE)
        return c, c.pulse

    # ------------------------------------------------------------ 每帧 --
    def update(self, dt):
        if self.auto:
            self.k.auto_step(dt)
        else:
            self.k.update(dt)
        self.last_t = max(0.0, self.last_t - dt)

    def _rect(self, cx, cy, w, h, tint, alpha=1.0):
        m = (Mat4.from_translation(Vec3(cx, cy, 0.0))
             @ Mat4.from_scale(Vec3(w, h, 1.0)))
        self.prog['u_mvp'] = Mat4.orthogonal_projection(-1, 1, -1, 1, -1, 1) @ m
        self.prog['u_tint'] = tint
        self.prog['u_alpha'] = alpha
        self.quad.draw(GL_TRIANGLES)

    def render(self):
        import math as _m
        glDisable(GL_DEPTH_TEST)
        glClearColor(0.04, 0.045, 0.06, 1.0)
        glClear(GL_COLOR_BUFFER_BIT)
        self.prog.use()
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        k = self.k
        for i in range(N_TRACKS):                     # 轨道
            self._rect(TRACK_X[i], 0.0, TRACK_W, 1.9, T_TRACK, 0.30)
        self._rect(0.0, JUDGE_Y, 1.55, 0.022, T_LINE, 0.9)   # 判定线
        # 下落 note：t ∈ [now, now+FALL_SEC] 的 note 画在 [顶, 判定线]
        now = k.time
        for t, tr in k.notes[k.cursor:]:
            dt_ = t - now
            if dt_ > FALL_SEC:
                break
            if dt_ < -GOOD_MS / 1000.0:
                continue
            y = JUDGE_Y + (dt_ / FALL_SEC) * (1.0 - JUDGE_Y)
            self._rect(TRACK_X[tr], y, TRACK_W * 0.86, LANE_H,
                       T_NOTE[str(tr)], 0.95)
        # 判定反馈（最近一次）
        if self.last and self.last_t > 0:
            v, _t = self.last
            self._rect(0.0, JUDGE_Y + 0.14, 0.5, 0.06,
                       VERDICT_TINT.get(v, (1, 1, 1)), min(1.0, self.last_t))
        glDisable(GL_BLEND)

    # ------------------------------------------------------------ 交互 --
    def on_key_press(self, symbol, modifiers):
        from pyglet.window import key
        lanes = {key.D: 0, key.F: 1, key.J: 2, key.K: 3}
        if symbol in lanes:
            r = self.k.hit(lanes[symbol])
            if r.get('verdict'):
                self.last, self.last_t = (r['verdict'], self.k.time), 0.5
        elif symbol == key._1:
            self._switch(0)
        elif symbol == key._2:
            self._switch(1)
        elif symbol == key._3:
            self._switch(2)
        elif symbol == key.F4:
            self.k.offset_adjust(-5.0)
        elif symbol == key.F5:
            self.k.offset_adjust(+5.0)
        elif symbol == key.G:
            self.auto = not self.auto
        elif symbol == key.R:
            self.k.load_chart()
            self.auto, self.last, self.last_t = False, None, 0.0
        elif symbol == key.ESCAPE:
            self.close()
        else:
            return pyglet.event.EVENT_UNHANDLED
        return None

    def _switch(self, i):
        self.k.load_chart(BPM_LIST[i])
        self.last, self.last_t = None, 0.0

    def status_text(self):
        k = self.k
        r = k.result()
        s = (f"BPM {k.chart_bpm}  t={k.time:5.1f}/{k.meta.get('duration', 0):.1f}s  "
             f"P{r['PERFECT']} G{r['GOOD']} M{r['MISS']}  连击 {k.combo}(max {r['combo_max']})  "
             f"分 {r['score']:.0f}  acc {r['acc']:.1f}%  offset {k.offset_ms:+.0f}ms  "
             f"state={r['state']}")
        if r['state'] == 'won':
            s += ('  ★ 全连' if r['full_combo'] else '  ✓') + ' 结算完成'
        elif r['state'] == 'lost':
            s += '  ✗ 血量耗尽'
        return s + ('  [AUTO]' if self.auto else '')


def main(argv=None):
    return run(PulsefallApp, argv)


if __name__ == '__main__':
    sys.exit(main())
