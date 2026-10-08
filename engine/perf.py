# -*- coding: utf-8 -*-
"""性能面板：帧时间统计、GPU 计时查询、渲染计数。

三类指标，缺一不可::

    CPU 帧时间   均值/中位数/p1/最差帧/>20ms 占比（jank）
    GPU 耗时     GL_TIME_ELAPSED 计时查询（异步回读，滞后 1~3 帧）
    渲染计数     draw call / 三角形 / 剔除掉的物体

为什么只看均值不够：均值会被偶发快帧拉高，"卡不卡"要看 **p1**（1% 最慢帧
的下界）和 **jank**（超过 20ms 的帧占比）。这两个才是玩家真正感知到的东西。

GPU 计时用查询对象而不是 CPU 计时：``glFinish`` 会破坏并行度（量到的
包含了等待时间），查询对象由 GPU 自己打时间戳，代价几乎为零。
"""
import time

__all__ = ['FrameStats', 'GPUTimer', 'PerfMonitor']


class FrameStats:
    """CPU 侧帧时间统计（环形窗口）。"""

    def __init__(self, window=240):
        self.window = int(window)
        self.samples = []
        self.fps_ema = 0.0
        self.worst_ms = 0.0

    def add(self, dt_ms):
        self.samples.append(float(dt_ms))
        if len(self.samples) > self.window:
            self.samples.pop(0)
        if dt_ms > self.worst_ms:
            self.worst_ms = dt_ms

    def reset(self):
        self.samples.clear()
        self.worst_ms = 0.0

    def report(self):
        """返回 (frames, mean_fps, median_fps, p1_fps, worst_ms, jank)。"""
        ms = sorted(self.samples)
        if not ms:
            return (0, 0.0, 0.0, 0.0, 0.0, 0.0)
        span = sum(ms) / 1000.0
        mean = len(ms) / span if span > 0 else 0.0
        median = 1000.0 / ms[len(ms) // 2]
        p1 = 1000.0 / ms[min(len(ms) - 1, int(len(ms) * 0.99))]
        jank = sum(1 for m in ms if m > 20.0) / len(ms)
        return (len(ms), mean, median, p1, ms[-1], jank)

    def text(self):
        n, mean, median, p1, worst, jank = self.report()
        if not n:
            return 'fps --'
        return (f'fps {mean:5.1f} (med {median:5.1f} p1 {p1:5.1f}) '
                f'worst {worst:5.1f}ms jank {jank * 100:4.1f}%')


class GPUTimer:
    """GPU 计时查询（GL_TIME_ELAPSED）。不支持时安静降级为 0。"""

    def __init__(self, capacity=4):
        self.available = False
        self.capacity = capacity
        self._queries = []
        self._pending = []
        self.last_ms = 0.0
        self._begin = None
        try:
            from pyglet.gl import (GL_QUERY_RESULT_AVAILABLE, GL_TIME_ELAPSED,
                                   GLuint, glBeginQuery, glDeleteQueries,
                                   glEndQuery, glGenQueries,
                                   glGetQueryObjectiv, glGetQueryObjectui64v)
            self._gl = dict(GL_QUERY_RESULT_AVAILABLE=GL_QUERY_RESULT_AVAILABLE,
                            GL_TIME_ELAPSED=GL_TIME_ELAPSED, GLuint=GLuint,
                            glBeginQuery=glBeginQuery, glEndQuery=glEndQuery,
                            glGenQueries=glGenQueries,
                            glGetQueryObjectiv=glGetQueryObjectiv,
                            glGetQueryObjectui64v=glGetQueryObjectui64v)
            self.available = True
        except Exception:
            self._gl = None

    def begin(self):
        if not self.available:
            return
        g = self._gl
        qid = g['GLuint']()
        g['glGenQueries'](1, qid)
        g['glBeginQuery'](g['GL_TIME_ELAPSED'], qid)
        self._begin = qid

    def end(self):
        if not self.available or self._begin is None:
            return
        g = self._gl
        g['glEndQuery'](g['GL_TIME_ELAPSED'])
        self._pending.append(self._begin)
        self._begin = None
        # 结果滞后若干帧才可读，只保留有限个在飞查询
        while len(self._pending) > self.capacity:
            self._pending.pop(0)

    def poll(self):
        """取一个已完成的查询结果（毫秒）。未就绪返回上次的值。"""
        if not self.available or not self._pending:
            return self.last_ms
        g = self._gl
        qid = self._pending[0]
        ready = g['GLuint']()
        try:
            g['glGetQueryObjectiv'](qid, g['GL_QUERY_RESULT_AVAILABLE'], ready)
        except Exception:
            self.available = False
            return self.last_ms
        if not ready.value:
            return self.last_ms
        self._pending.pop(0)
        try:
            ns = g['GLuint']() if False else None
            from pyglet.gl import GL_QUERY_RESULT
            import ctypes
            result = ctypes.c_uint64(0)
            g['glGetQueryObjectui64v'](qid, GL_QUERY_RESULT,
                                       ctypes.byref(result))
            self.last_ms = result.value / 1e6
        except Exception:
            try:
                import ctypes
                from pyglet.gl import GL_QUERY_RESULT
                r = ctypes.c_uint(0)
                g['glGetQueryObjectiv'](qid, GL_QUERY_RESULT, ctypes.byref(r))
                self.last_ms = r.value / 1e6
            except Exception:
                self.available = False
        return self.last_ms

    @property
    def text(self):
        if not self.available:
            return 'gpu n/a'
        return f'gpu {self.last_ms:5.2f}ms'


class PerfMonitor:
    """CPU + GPU + 计数的汇总，直接产出 HUD 文本。"""

    def __init__(self, window=240):
        self.cpu = FrameStats(window)
        self.gpu = GPUTimer()
        self.draw_calls = 0
        self.triangles = 0
        self.culled = 0
        self.visible = 0
        self.instances = 0
        self._last = None
        self._warm = False
        self.extra = {}

    # ---- 每帧 ----
    def tick_frame(self):
        """在 on_draw 里调用：量渲染帧间隔（不是逻辑 tick）。"""
        now = time.perf_counter()
        if self._last is not None:
            dt = (now - self._last) * 1000.0
            # 前 1 秒丢弃：着色器编译 / 首帧上传会把最慢帧带偏
            if self._warm:
                self.cpu.add(dt)
        self._last = now

    def mark_warm(self, elapsed_s):
        if elapsed_s > 1.0:
            self._warm = True

    def begin_gpu(self):
        self.gpu.begin()

    def end_gpu(self):
        self.gpu.end()
        self.gpu.poll()

    def reset_counters(self):
        self.draw_calls = 0
        self.triangles = 0
        self.culled = 0
        self.visible = 0
        self.instances = 0

    # ---- 展示 ----
    def lines(self):
        out = [self.cpu.text, self.gpu.text,
               f'draw {self.draw_calls:4d}  tri {self.triangles:7d}  '
               f'cull {self.culled:4d}/{self.culled + self.visible:4d}']
        if self.instances:
            out.append(f'instances {self.instances}')
        for k, v in self.extra.items():
            out.append(f'{k} {v}')
        return out

    def report_dict(self):
        n, mean, median, p1, worst, jank = self.cpu.report()
        return {
            'frames': n, 'fps_mean': mean, 'fps_median': median,
            'fps_p1': p1, 'worst_ms': worst, 'jank': jank,
            'gpu_ms': self.gpu.last_ms, 'draw_calls': self.draw_calls,
            'triangles': self.triangles, 'culled': self.culled,
            'visible': self.visible,
        }
