# -*- coding: utf-8 -*-
"""程序化音频引擎：离线合成 WAV + 时间轴同步播放（纯 Python，无 numpy 依赖）。

两层职责::

    Track          多轨音序器：振荡器/噪声/滑音/包络，加法混合成单声道
    render_wav()   float 混音 -> 16-bit WAV（wave 标准库，pyglet 内置可解码）
    TimelineAudio  pyglet.media.Player 封装：播放/暂停/seek/循环/静音，
                   与动画时间轴做漂移校正同步

为什么离线合成而不是实时发声：pyglet 没有实时合成 API（media 只能播文件），
而 kkrieger 的精神就是"程序化生成"——音乐同样只存生成规则，首次运行把
120s 配乐渲染成 WAV 缓存，之后直接播。SFX（开门、警报、爆发）按**采样级
精度**烘进同一条轨道，与时间轴天然对齐，不用做多轨对时。

音频驱动（Windows）：pyglet 2.x 默认 (xaudio2, directsound, openal, pulse,
silent)，本机实测走 XAudio2；无声卡/驱动失败时 pyglet 自动落到 silent
驱动——播放调用全部有效但不发声，因此上层无需特判。
"""
import math
import struct
import wave

__all__ = ['Track', 'render_wav', 'TimelineAudio', 'audio_available']


# ----------------------------------------------------------------- 合成器 --
class Track:
    """单声道浮点混音轨。所有事件加法叠加，最后统一归一化写盘。"""

    def __init__(self, duration, rate=32000):
        self.rate = int(rate)
        self.duration = float(duration)
        self.n = int(round(duration * rate))
        self.buf = [0.0] * self.n

    # ---- 内部 ----
    def _add(self, i0, chunk):
        """越界裁剪叠加（音符尾部越界直接截断：循环边界必须静音）。"""
        n = self.n
        buf = self.buf
        end = min(n, i0 + len(chunk))
        j = 0
        for i in range(max(0, i0), end):
            buf[i] += chunk[j]
            j += 1

    def _wave(self, wave, phase, duty):
        """波形查值。phase ∈ [0,1)。"""
        if wave == 'sine':
            return math.sin(2.0 * math.pi * phase)
        if wave == 'saw':
            return 2.0 * phase - 1.0
        if wave == 'square':
            return 1.0 if phase < duty else -1.0
        if wave == 'tri':
            return 4.0 * phase - 1.0 if phase < 0.5 else 3.0 - 4.0 * phase
        return math.sin(2.0 * math.pi * phase)

    # ---- 事件 ----
    def tone(self, t0, dur, f0, f1=None, wave='saw', amp=0.2,
             attack=0.004, release=0.05, duty=0.5, lp=0.0):
        """音符：频率可从 f0 线性滑到 f1；attack/release 为线性包络（秒）。

        ``lp`` 是一阶低通系数 (0=直通, 越小越闷)，用于 pad/噪声整形。
        """
        rate = self.rate
        n = max(1, int(dur * rate))
        i0 = int(t0 * rate)
        f1 = f0 if f1 is None else f1
        chunk = [0.0] * n
        phase = 0.0
        lp_y = 0.0
        a_att = 1.0 / max(1, int(attack * rate))
        a_rel = 1.0 / max(1, int(release * rate))
        env = 0.0
        for k in range(n):
            u = k / n
            f = f0 + (f1 - f0) * u
            phase = (phase + f / rate) % 1.0
            s = self._wave(wave, phase, duty)
            if lp > 0.0:
                lp_y += lp * (s - lp_y)
                s = lp_y
            if k < attack * rate:
                env += a_att
            elif k > n - release * rate:
                env -= a_rel
            env = 0.0 if env < 0.0 else (1.0 if env > 1.0 else env)
            chunk[k] = s * amp * env
        self._add(i0, chunk)

    def noise(self, t0, dur, amp=0.2, decay=0.0, lp=0.25, seed=1):
        """噪声爆发（镲/爆裂/气浪）：``decay`` 为指数衰减率（1/s），0=恒定。"""
        rate = self.rate
        n = max(1, int(dur * rate))
        i0 = int(t0 * rate)
        chunk = [0.0] * n
        state = (int(t0 * 997) * 2654435761 + seed * 40503) & 0x7FFFFFFF
        lp_y = 0.0
        for k in range(n):
            state = (state * 1103515245 + 12345) & 0x7FFFFFFF
            s = state / 0x3FFFFFFF - 1.0        # [-1, 1)
            lp_y += lp * (s - lp_y)
            env = math.exp(-decay * k / rate) if decay > 0.0 else 1.0
            chunk[k] = lp_y * amp * env
        self._add(i0, chunk)

    def sweep(self, t0, dur, f0, f1, wave='sine', amp=0.25, release=0.10):
        """频率滑音（警示/上升气流/坠落）：tone 的便捷封装，缓入快出。"""
        self.tone(t0, dur, f0, f1, wave=wave, amp=amp,
                  attack=min(0.05, dur * 0.2), release=release)

    # ---- 输出 ----
    def peak(self):
        return max(abs(v) for v in self.buf) if self.buf else 0.0

    def rms(self, t0=0.0, dur=None):
        i0 = int(t0 * self.rate)
        n = self.n - i0 if dur is None else min(self.n - i0, int(dur * self.rate))
        if n <= 0:
            return 0.0
        acc = 0.0
        for k in range(i0, i0 + n):
            acc += self.buf[k] * self.buf[k]
        return math.sqrt(acc / n)


def render_wav(track, path, normalize=0.92):
    """混音轨 -> 16-bit 单声道 WAV。返回 (路径, 实际秒数)。"""
    peak = track.peak()
    gain = (normalize / peak) if peak > 1e-9 else 1.0
    frames = bytearray(track.n * 2)
    j = 0
    for v in track.buf:
        s = int(max(-1.0, min(1.0, v * gain)) * 32767)
        frames[j] = s & 0xFF
        frames[j + 1] = (s >> 8) & 0xFF
        j += 2
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(track.rate)
        w.writeframes(bytes(frames))
    return path, track.duration


# ----------------------------------------------------------------- 播放器 --
def audio_available():
    """音频能否真正出声（silent 驱动 = 播放调用有效但无声）。"""
    try:
        from pyglet.media import get_audio_driver
        drv = get_audio_driver()
        return drv is not None and 'silent' not in type(drv).__module__
    except Exception:
        return False


class TimelineAudio:
    """pyglet Player 的薄封装：与动画时间轴做同步。

    - ``loop=True``：源时长必须等于时间轴时长（采样数对齐），循环无缝；
    - ``sync(playhead)``：每帧调用，漂移超阈值才 seek（频繁 seek 会爆音）；
    - 所有方法在无声卡环境同样安全（pyglet silent 驱动吞掉一切）。
    """

    def __init__(self, wav_path, loop=True, volume=0.8):
        import pyglet
        self.path = wav_path
        self.ok = True
        self.muted = False
        self._volume = volume
        self.player = pyglet.media.Player()
        try:
            src = pyglet.media.load(wav_path)
            self.duration = src.duration
            self.player.queue(src)
            self.player.loop = loop
            self.player.volume = volume
        except Exception as e:
            self.ok = False
            self.duration = 0.0
            print(f'[audio] load failed: {e}')

    @property
    def time(self):
        return float(self.player.time) if self.ok else 0.0

    def play(self):
        if self.ok:
            self.player.play()

    def pause(self):
        if self.ok:
            self.player.pause()

    def seek(self, t):
        if self.ok:
            self.player.seek(max(0.0, min(self.duration - 0.01, t)))

    def sync(self, playhead, playing=True, tol=0.25):
        """与时间轴对齐：漂移超容差才 seek；暂停状态跟随暂停。"""
        if not self.ok:
            return
        if not playing:
            self.player.pause()
            return
        if abs(self.time - playhead) > tol:
            self.seek(playhead)
        self.player.play()

    def toggle_mute(self):
        self.muted = not self.muted
        if self.ok:
            self.player.volume = 0.0 if self.muted else self._volume
        return self.muted

    def close(self):
        if self.ok:
            try:
                self.player.pause()
                self.player.delete()
            except Exception:
                pass
