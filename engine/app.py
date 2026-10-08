# -*- coding: utf-8 -*-
"""应用骨架：窗口 + 主循环 + CLI + HUD + 截图 + 基准 + 性能面板。

这是 ``demos/_harness.py`` 的"引擎化"版本：把 demo 公共骨架里那些与
具体 demo 无关的部分（DPI、命令行、抓帧、主循环、关闭排程）沉淀下来，
并补上引擎需要的东西（性能面板、相机/场景/渲染器挂载点、暂停与调试键）。

命令行（所有基于它的程序一致）::

    python xxx.py                    正常运行
    python xxx.py --smoke [秒]       冒烟：N 秒后自动关闭（默认 2.0）
    python xxx.py --shot             按 SHOTS 抓帧存 docs/ 后自动关闭
    python xxx.py --size 960x540     指定窗口尺寸
    python xxx.py --fps 30           指定逻辑刷新率
    python xxx.py --bench 5          基准模式（关 vsync，输出帧统计后退出）
    python xxx.py --flag name        demo 自定义开关（可重复，读 args.flags）

实测坑位（pyglet 2.1.16 / Win 150% DPI，全部在这里一次性兜住）:

- ``dpi_scaling`` 默认 ``'platform'`` 时 framebuffer = 请求尺寸，但 viewport
  setter 会乘 scale -> 渲染被放大裁切；统一设 ``'real'``（逻辑 = 物理）。
- 截图不用 ``get_color_buffer()``：它按 GL_VIEWPORT 读会越界读出黑边，
  这里用 ``glReadPixels`` 按实际 buffer 尺寸直读。
- 控制台输出一律 ASCII：Windows GBK 代码页下中文 print 会变问号，
  污染自动化脚本的解析。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys

import pyglet

# 必须在创建 Window 之前设置（导入本模块即生效）
pyglet.options['dpi_scaling'] = 'real'
# 关掉逐 GL 调用的 glGetError 巡检（pyglet 默认开着）：实测它占掉每帧
# Python 耗时的 ~44%（cProfile: 27 万次 errcheck 调用/4 秒）。排渲染问题时
# 在入口处改回 True 即可恢复"出错即抛异常"。
pyglet.options['debug_gl'] = False

from pyglet import text                                          # noqa: E402
from pyglet.gl import (GL_BLEND, GL_DEPTH_TEST, GL_ONE_MINUS_SRC_ALPHA,  # noqa: E402
                       GL_RGBA, GL_SRC_ALPHA, GL_UNSIGNED_BYTE,
                       glBlendFunc, glDisable, glEnable, glReadPixels)
from pyglet.math import Mat4                                     # noqa: E402

from engine.hud import Hud                                       # noqa: E402
from engine.perf import PerfMonitor                              # noqa: E402

__all__ = ['parse_args', 'capture', 'EngineApp', 'run']


# --------------------------------------------------------------- 命令行 --
def parse_args(argv=None):
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument('--smoke', nargs='?', type=float, const=2.0, default=None,
                   metavar='SEC', help='smoke test: auto-close after N sec (default 2.0)')
    p.add_argument('--shot', action='store_true',
                   help='capture frames to docs/ then auto-close (times in SHOTS)')
    p.add_argument('--size', default='1280x720', metavar='WxH',
                   help='window size, default 1280x720')
    p.add_argument('--fps', type=float, default=60.0, help='logic tick rate, default 60')
    p.add_argument('--duration', type=float, default=None,
                   help='override demo loop duration (seconds)')
    p.add_argument('--no-vsync', action='store_true',
                   help='disable vsync (use with --bench for true frame rate)')
    p.add_argument('--bench', type=float, default=None, metavar='SEC',
                   help='benchmark SEC seconds without vsync, print frame stats, exit')
    p.add_argument('--flag', action='append', default=[], metavar='NAME',
                   help='demo-specific switch, readable as args.flags (repeatable)')
    args = p.parse_args(argv)
    w, _, h = args.size.lower().partition('x')
    try:
        args.width, args.height = int(w), int(h)
    except ValueError:
        p.error(f'--size needs WxH like 1280x720, got {args.size!r}')
    if args.bench is not None:
        args.no_vsync = True
        args.smoke = None
    args.flags = {f.strip() for item in args.flag
                  for f in str(item).split(',') if f.strip()}
    return args


# ----------------------------------------------------------------- 截图 --
def capture(window, path):
    """按实际 buffer 尺寸抓帧并存 PNG（DPI 无关，正立）。"""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    w, h = window.width, window.height
    row = w * 4
    buf = (ctypes.c_ubyte * (row * h))()
    glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, buf)
    pyglet.image.ImageData(w, h, 'RGBA', bytes(buf), row).save(path)
    return path


# ------------------------------------------------------------------ App --
class EngineApp:
    """所有 engine 程序的基类。

    子类覆盖::

        TITLE / HELP / SHOTS / DURATION     类属性
        setup()                             建 GL 资源（窗口上下文已就绪）
        update(dt)                          推进逻辑（self.time 已累加）
        render()                            绘制（清屏由基类负责）
        on_key(symbol, modifiers)           额外按键（ESC/SPACE/F1/F2 基类已处理）
        on_mouse_drag / on_mouse_scroll     鼠标
    """

    TITLE = 'pyglet engine'
    HELP = 'ESC quit   SPACE pause   F1 HUD   F2 perf'
    SHOTS = ()
    DURATION = None
    SHOW_HUD = True
    FOV = 60.0
    Z_NEAR, Z_FAR = 0.1, 500.0
    CLEAR_COLOR = (0.05, 0.07, 0.10, 1.0)

    def __init__(self, width=1280, height=720, args=None):
        self.args = args if args is not None else parse_args([])
        if self.args.duration is not None:
            self.DURATION = self.args.duration
        self.window = pyglet.window.Window(
            width, height, caption=self.TITLE, resizable=True,
            vsync=not getattr(self.args, 'no_vsync', False))
        self.time = 0.0
        self.paused = False
        self.fps = 0.0
        self.perspective = Mat4()
        self.hud = Hud(self.TITLE, self.HELP) if self.SHOW_HUD else None
        self.perf = PerfMonitor()
        self.show_perf = False
        self._shots = sorted(self.SHOTS)
        self._shot_i = 0
        self._closing = False
        self._last_draw = None
        self._bench_warm = False

        self.window.push_handlers(
            on_resize=self._on_resize,
            on_key_press=self._on_key_press,
            on_draw=self._on_draw,
            on_mouse_drag=self._on_mouse_drag,
            on_mouse_scroll=self._on_mouse_scroll,
            on_mouse_press=self._on_mouse_press,
            on_key_release=self._on_key_release,
            on_close=self._on_close)

        self.setup()
        self._apply_projection(width, height)
        if self.hud is not None:
            self.hud.layout(width, height)
        if self.args.bench is not None:
            # 基准模式：逻辑每帧都跑（schedule = 每个循环一次）
            pyglet.clock.schedule(self._tick)
        else:
            pyglet.clock.schedule_interval(self._tick, 1.0 / max(1e-3, self.args.fps))

    # -------------------------------------------------- 子类覆盖点 --
    def setup(self):
        """创建 GL 资源（窗口上下文已就绪）。"""

    def update(self, dt):
        """推进动画（self.time 已累加）。"""

    def render(self):
        """绘制场景（清屏已由基类完成）。"""

    def on_key(self, symbol, modifiers):
        """额外按键（ESC / SPACE / F1 / F2 由基类处理）。"""

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        """鼠标拖动。"""

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        """滚轮。"""

    def on_mouse_press(self, x, y, button, modifiers):
        """鼠标按下（拾取用）。"""

    def on_key_release(self, symbol, modifiers):
        """按键释放（连续移动的按键在这里清状态，否则会一直走下去）。"""

    def status_text(self):
        """HUD 右上角状态文本。"""
        dur = '' if self.DURATION is None else f' / {self.DURATION:.1f}s'
        flag = '  [PAUSED]' if self.paused else ''
        return f't={self.time:7.2f}s{dur}   {self.fps:5.1f} fps{flag}'

    def extra_perf_lines(self):
        """附加到性能面板的行。"""
        return []

    # ---------------------------------------------------------- 主循环 --
    def _tick(self, dt):
        if dt > 1e-6:
            inst = 1.0 / dt
            self.fps = inst if self.fps <= 0.0 else self.fps * 0.9 + inst * 0.1
        if not self.paused:
            self.time += dt
            if self.DURATION:
                self.time %= self.DURATION
        self.update(dt)

    def _apply_projection(self, width, height):
        height = max(1, height)
        self.window.viewport = (0, 0, width, height)
        self.perspective = Mat4.perspective_projection(
            width / float(height), z_near=self.Z_NEAR, z_far=self.Z_FAR,
            fov=self.FOV)
        self.window.projection = self.perspective

    def _on_resize(self, width, height):
        self._apply_projection(width, height)
        if self.hud is not None:
            self.hud.layout(width, height)
        if hasattr(self, 'on_resize_hook'):
            self.on_resize_hook(width, height)
        return pyglet.event.EVENT_HANDLED

    def _on_key_press(self, symbol, modifiers):
        key = pyglet.window.key
        if symbol == key.ESCAPE:
            self.window.close()
        elif symbol == key.SPACE:
            self.paused = not self.paused
        elif symbol == key.F1:
            if self.hud is not None:
                self.hud.visible = not self.hud.visible
        elif symbol == key.F2:
            self.show_perf = not self.show_perf
        else:
            self.on_key(symbol, modifiers)

    def _on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        self.on_mouse_drag(x, y, dx, dy, buttons, modifiers)

    def _on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        self.on_mouse_scroll(x, y, scroll_x, scroll_y)

    def _on_mouse_press(self, x, y, button, modifiers):
        self.on_mouse_press(x, y, button, modifiers)

    def _on_key_release(self, symbol, modifiers):
        self.on_key_release(symbol, modifiers)

    def _on_close(self):
        if hasattr(self, 'dispose'):
            self.dispose()

    def _on_draw(self):
        self.window.clear()
        self.perf.begin_gpu()
        self.render()
        self.perf.end_gpu()          # GPU 计时查询（异步回读，滞后 1~3 帧）
        if self.hud is not None:
            self.hud.set_status(self.status_text())
            if self.show_perf:
                self.hud.set_perf(self.perf.lines() + self.extra_perf_lines())
            else:
                self.hud.set_perf([])
            self._draw_hud()
        self._capture_due()
        self._sample_frame_time()

    def _draw_hud(self):
        w, h = self.window.width, self.window.height
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glDisable(GL_DEPTH_TEST)
        self.window.projection = Mat4.orthogonal_projection(0, w, 0, h, -255, 255)
        self.window.view = Mat4()
        self.hud.draw()
        self.window.projection = self.perspective
        glEnable(GL_DEPTH_TEST)

    def _sample_frame_time(self):
        """量**渲染帧**间隔（不是逻辑 tick）。前 1s 丢弃（着色器编译预热）。"""
        import time as _t
        now = _t.perf_counter()
        if self._last_draw is not None and self._bench_warm:
            self.perf.cpu.add((now - self._last_draw) * 1000.0)
        self._last_draw = now
        if not self._bench_warm and self.time > 1.0:
            self._bench_warm = True

    def _capture_due(self):
        while (self._shot_i < len(self._shots)
               and self.time >= self._shots[self._shot_i][0]):
            _, path = self._shots[self._shot_i]
            if not os.path.isabs(path):
                path = os.path.join(os.getcwd(), path)
            print('[shot]', capture(self.window, path))
            self._shot_i += 1
        if (self._shots and self._shot_i >= len(self._shots)
                and not self._closing):
            self._closing = True
            pyglet.clock.schedule_once(lambda dt: self.close(), 0.2)

    def close(self):
        self.window.close()

    # ------------------------------------------------------------- 入口 --
    def bench_report(self):
        n, span, mean, median, p1, worst, jank = self.perf.cpu.report()
        return n, span, mean, median, p1, worst, jank


def run(app_cls, argv=None):
    """统一入口：解析命令行 -> 建 app -> 排定关闭 -> 跑主循环。"""
    args = parse_args(argv)
    if args.shot and not app_cls.SHOTS:
        raise SystemExit(f'{app_cls.__name__}: --shot requires SHOTS')
    app = app_cls(args.width, args.height, args=args)

    if args.bench is not None:
        pyglet.clock.schedule_once(lambda dt: app.close(), args.bench + 0.5)
        print(f'[bench] vsync=off, running {args.bench}s ...')
    elif args.smoke is not None:
        pyglet.clock.schedule_once(lambda dt: app.close(), args.smoke)
        print(f'[smoke] auto-close in {args.smoke}s')
    elif args.shot:
        print(f'[shot] planned {len(app_cls.SHOTS)} frames, close when done')
    pyglet.app.run(interval=0 if args.bench is not None else 1 / 60)

    if args.bench is not None:
        n, mean, median, p1, worst, jank = app.perf.cpu.report()
        span = n / mean if mean > 0 else 0.0
        print(f'[bench] frames={n} span={span:.2f}s fps mean={mean:.1f} '
              f'median={median:.1f} p1={p1:.1f} worst_frame_ms={worst:.2f} '
              f'jank_gt20ms={jank * 100:.1f}%')
    if args.shot:
        missing = [p for _, p in app_cls.SHOTS
                   if not os.path.exists(os.path.join(os.getcwd(), p))]
        if missing:
            raise SystemExit(f'[shot] missing: {missing}')
        print(f'[shot] OK {len(app_cls.SHOTS)} files')
    print('EXIT_OK')
    return 0
