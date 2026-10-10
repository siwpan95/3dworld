# -*- coding: utf-8 -*-
"""音频共同核 ``audio``（实现 ``core.contracts.AudioCore``）。

封装 ``engine.audio`` 的程序化合成器（Track / render_wav）、播放封装
（TimelineAudio / SfxPlayer）与可用性探测（audio_available）。

> 依赖：``engine.audio``（纯 Python 合成 + 延迟导入 pyglet.media）+ ``cordis``。
> headless / 无声卡环境安全：Track 为纯 Python；播放器内部已 try/except 降级。
"""
from __future__ import annotations

import os

from core._cordis import Service
from engine import audio as _audio


class AudioService(Service):
    """即时合成音效/配乐（零外部素材）。"""

    def __init__(self, ctx, config):
        super().__init__(ctx, "audio")
        cfg = config or {}
        self._sfx_dir = cfg.get('sfx_dir', os.path.join('assets', 'sfx'))
        self._sfx = None            # 懒建 SfxPlayer
        self._music = None          # 当前 TimelineAudio

    def create_track(self, duration, rate=32000):
        """新建一条合成音轨（``engine.audio.Track``）。"""
        return _audio.Track(duration, rate)

    def tone(self, track, t0, dur, f0, f1=None, wave='saw', amp=0.2, **kw):
        """在音轨上叠一段音调（``Track.tone``）。"""
        return track.tone(t0, dur, f0, f1, wave, amp, **kw)

    def noise(self, track, t0, dur, amp=0.2, decay=0.0, lp=0.25, seed=1):
        """在音轨上叠一段噪声（``Track.noise``）。"""
        return track.noise(t0, dur, amp, decay, lp, seed)

    def render_wav(self, track, path, normalize=0.92):
        """把音轨渲染为 wav 文件，返回**路径**字符串。

        注：底层 ``engine.audio.render_wav`` 返回 ``(path, duration)``；本服务按
        ``AudioCore`` 契约只回路径（时长可从 ``track.duration`` 取）。
        """
        out = _audio.render_wav(track, path, normalize)
        return out[0] if isinstance(out, tuple) else out

    def play_music(self, wav_path, loop=True, volume=0.8):
        """播放配乐（``engine.audio.TimelineAudio``）。"""
        self._music = _audio.TimelineAudio(wav_path, loop, volume)
        return self._music

    def sfx(self, name, gain=1.0):
        """播放即时音效（``engine.audio.SfxPlayer.play``）。"""
        if self._sfx is None:
            self._sfx = _audio.SfxPlayer(self._sfx_dir)
        return self._sfx.play(name, gain)

    def available(self) -> bool:
        """音频后端是否可用（``engine.audio.audio_available``）。"""
        return bool(_audio.audio_available())

    def __cordis_init__(self):
        return None


__all__ = ['AudioService']
