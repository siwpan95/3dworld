# -*- coding: utf-8 -*-
"""自适应 BGM —— bloodmusic interactive 引擎的运行时适配（**可选依赖**）。

设计（对齐 scene_walker 的交互语义）：

    交互事件（互动/拾取） → arousal(intensity) 上升 → 引擎状态机
    EXPLORE ⇄ TENSION 平滑过渡（3s 交叉淡化）→ 实时音频输出

- 后端：siwpan/bloodmusic（原 audio-generation）的 `RealtimeMusicEngine`（MIT，自研；
  `pip install -e <PATH>` 或等价安装）；
  **未安装/无音频设备时静默降级**（接口保持可用），`WORLD3D_BGM=0` 显式禁用；
- `game_state = {"intensity": 0..1}`：默认 0.1（平静）；交互事件后短暂
  0.4~0.55（引擎阈值 `intensity>0.3 → TENSION`）；
- 音频：`sounddevice.OutputStream` 回调内 `update()+process_frame()`
  —— **引擎只在回调线程被触碰**（单线程无锁）；主线程仅更新语义标量（GIL 原子）。

记账：本模块是 scene_walker 自适应 BGM 的**正式实现**；原先并列的 bloodmusic 适配试点（`experiments/scene_walker/bgm.py`）已在 P0-5 退役（能力已被本模块覆盖）。
"""
from __future__ import annotations

import os

try:                       # numpy 属"可选依赖链"（bloodmusic 一侧）；缺失时本模块整体降级
    import numpy as np
except Exception:          # pragma: no cover
    np = None

SR = 44100
BLOCK = 512


class AdaptiveBgm:
    """自适应 BGM 播放器（失败自降级）。"""

    def __init__(self, enabled=True, volume=0.5):
        self._arousal = 0.1        # 平静基线（EXPLORE）
        self._until = 0.0          # 警觉剩余秒数（主线程递减）
        self._volume = float(volume)
        self._engine = None
        self._stream = None
        self._last_state = None
        self._extra = {}           # 附加 game_state 字段（v3.3：COMBAT/VICTORY）
        self.enabled = (bool(enabled)
                        and os.environ.get('WORLD3D_BGM', '1') != '0'
                        and np is not None)
        if not self.enabled:
            reason = 'numpy missing' if np is None else 'by env'
            print(f'[bgm] disabled ({reason})', flush=True)
            return
        try:
            from bloodmusic.interactive import RealtimeMusicEngine
            import sounddevice as sd
            eng = RealtimeMusicEngine()
            eng.setup_default_layers()
            eng.start()
            self._engine = eng
            self._stream = sd.OutputStream(
                samplerate=SR, channels=2, blocksize=BLOCK,
                dtype='float32', callback=self._cb)
            self._stream.start()
            print('[bgm] adaptive engine started (EXPLORE)', flush=True)
        except Exception as e:
            print(f'[bgm] disabled: {type(e).__name__}: {e}', flush=True)
            self.enabled = False
            self._engine = None
            self._stream = None

    # ---------------- 主线程接口 ----------------
    def arouse(self, seconds=6.0, level=0.55):
        """交互事件 -> 警觉上升（音乐进入 TENSION）。"""
        if not self.enabled:
            return
        self._arousal = float(level)
        self._until = float(seconds)
        print(f'[bgm] arouse {level:.2f} for {seconds:.1f}s', flush=True)

    def set_extra(self, extra):
        """注入附加 game_state 字段（如 ``enemy_count=3`` → COMBAT_LOW、
        ``victory=True`` → VICTORY）；传 None/{} 清除。"""
        self._extra = dict(extra or {})
        if self.enabled:
            print(f'[bgm] extra={self._extra or "-"}', flush=True)

    def tick(self, dt):
        """主线程每帧调用：警觉衰减回落 EXPLORE。"""
        if not self.enabled or self._until <= 0:
            return
        self._until -= dt
        if self._until <= 0:
            self._arousal = 0.1
            print('[bgm] calm down -> EXPLORE', flush=True)

    @property
    def state_name(self):
        if not self.enabled or self._engine is None:
            return 'off'
        try:
            st = self._engine.current_state
            return getattr(st, 'name', str(st))
        except Exception:
            return '?'

    # ---------------- 音频回调（引擎唯一接触点） ----------------
    def _cb(self, outdata, frames, time_info, status):
        try:
            eng = self._engine
            state = {'intensity': self._arousal}
            state.update(self._extra)
            eng.update(state, frames / float(SR))
            st = eng.current_state
            if st != self._last_state:
                self._last_state = st
                print(f'[bgm] state -> {getattr(st, "name", st)}', flush=True)
            frame = np.asarray(eng.process_frame(), dtype=np.float32).reshape(-1)
            n = min(len(frame), frames)
            out = np.zeros((frames, 2), dtype=np.float32)
            out[:n, 0] = frame[:n] * self._volume
            out[:n, 1] = frame[:n] * self._volume
            outdata[:] = out
        except Exception:
            outdata.fill(0.0)

    def close(self):
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception:
            pass
