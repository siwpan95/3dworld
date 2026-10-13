# -*- coding: utf-8 -*-
"""§16 落音 / Pulsefall 内核 —— 下落式音击（Cordis 服务化）。

设计 note 见 ``docs/remake_ideas.md`` §16。**致敬下落式音击品类**（Beatmania IIDX 等），
不碰原 IP 名称与素材（合规见 §7）。

切片：4 轨道（DFJK）、3 谱面（BPM 90/120/150，种子复现）、判定 3 档
（PERFECT ±40ms / GOOD ±90ms / MISS）、血量（miss 扣 → lost）、连击与得分（economy）、
结算（P/G/M 计数 + 准确率 + 最大连击）、实机 offset 校准。

**audio 核的首个内核消费者**：``chart()`` 同时产出 **notes** 与 **synth_events**
（一一对应、同 t），内核据此合成三层曲子（低音 / 和弦 / 主旋律）。

全部逻辑纯 Python、无 GL 依赖；``hit(track, t_now)`` 与 headless 自动演奏同 API。
"""
from __future__ import annotations

import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import _cordis_locate  # noqa: E402

_cordis_locate.ensure()

from cordis import Service  # noqa: E402

# ===================================================================== 常量
N_TRACKS = 4                          # DFJK 四键
BPM_LIST = (90, 120, 150)             # 3 谱面（§16.3）
BARS = 16                             # 每谱 16 小节
PERFECT_MS = 40.0                     # 判定窗（±ms）
GOOD_MS = 90.0

NOTE_SCORE = {'PERFECT': 300, 'GOOD': 120, 'MISS': 0}
HULL0 = 10                            # 血量（base_hp 复用）；MISS 扣 1
COMBO_BONUS_CAP = 40                  # 连击倍率上限（1 + combo*0.02，cap ×1.8）
AUTOPLAY_TOL_MS = 24.0                # 自动演奏的"手抖"。**必须 >= 帧步长 16.7ms**，
                                      # 否则 note 落在两帧之间会被漏判成 MISS（实测 M10）。
                                      # 24ms 仍远在 PERFECT 窗 40ms 内。
OFFSET_RANGE_MS = 20.0                # 实机 offset 校准范围（F4 ±20ms，§16.3）

DEMO_SEEDS = (20261012, 7, 42)


# ============================================================ 纯函数（可单测）
def chart(seed, bpm, bars=BARS):
    """**谱面生成**：``(notes, synth_events, meta)``。

    - ``notes``：``[(t_sec, track_idx), ...]``（升序）——判定用；
    - ``synth_events``：``[(t_sec, kind, midi), ...]``——合成用，**与 notes 同 t**；
    - 可打性约束（生成器内置，单测把关）：**同轨最小间隔 ≥ 1/8 拍**、全局密度上限
      （每秒 ≤ 8）、时长 = ``bars`` 精确（``bars*4*60/bpm``）。
    """
    beat = 60.0 / bpm
    total = bars * 4 * beat
    rng = _Rng(_hash32(f'{seed}/{bpm}/{bars}'))
    notes = []
    # 网格：每小节 8 个 1/8 格；按概率落 note（避免全 1/8 太密）
    slots = int(bars * 8)
    last_by_track = [-1e9] * N_TRACKS
    min_gap = beat * 0.5 - 1e-9                 # 1/8 拍（留浮点容差：网格步长恰等于它）
    for i in range(slots):
        t = i * (beat * 0.5)                     # 1/8 拍 = beat/2
        # 密度控制：小节首拍必落，其余按概率
        on_beat = (i % 8 == 0)
        p = 0.95 if on_beat else 0.55
        if rng.next_f() > p:
            continue
        tr = int(rng.next_f() * N_TRACKS) % N_TRACKS
        # 同轨间隔不足则换轨（保证可打）
        if t - last_by_track[tr] < min_gap:
            for alt in range(N_TRACKS):
                k = (tr + alt) % N_TRACKS
                if t - last_by_track[k] >= min_gap:
                    tr = k
                    break
            else:
                continue
        last_by_track[tr] = t
        notes.append((round(t, 6), tr))

    # 密度上限校验（生成器自我约束）：任一秒窗口内 <= 8
    for k in range(len(notes)):
        t0 = notes[k][0]
        cnt = sum(1 for j in range(k, len(notes))
                  if notes[j][0] < t0 + 1.0)
        assert cnt <= 8, f'密度超限（{cnt}/s @{t0:.2f}）'

    # synth_events：与 notes 同 t（低音每小节、和弦每 2 小节、主旋律 = notes）
    synth = []
    scale = (0, 2, 4, 5, 7, 9, 11)              # 大调音阶（相对主音）
    root = 60 - 12                              # C3
    for b in range(bars):
        t = b * 4 * beat
        synth.append((round(t, 6), 'bass', root + scale[(b * 3) % 7]))
        if b % 2 == 0:
            synth.append((round(t, 6), 'pad', root + 12 + scale[(b * 5) % 7]))
    for t, tr in notes:
        deg = scale[(int(t / beat) * 3 + tr) % 7]
        synth.append((round(t, 6), 'lead', root + 24 + deg + (12 if tr >= 2 else 0)))
    synth.sort(key=lambda e: e[0])
    meta = dict(bpm=bpm, bars=bars, duration=round(total, 6),
                n_notes=len(notes), seed=seed)
    return notes, synth, meta


def judge(dt_ms) -> str:
    """判定：``|误差| <= PERFECT_MS`` -> PERFECT；``<= GOOD_MS`` -> GOOD；否则 MISS。"""
    a = abs(float(dt_ms))
    if a <= PERFECT_MS:
        return 'PERFECT'
    if a <= GOOD_MS:
        return 'GOOD'
    return 'MISS'


def accuracy(p, g, m) -> float:
    """准确率：满分 = 全部 PERFECT（P 记 1.0、G 记 0.4、M 记 0）。"""
    tot = p + g + m
    if tot <= 0:
        return 0.0
    return 100.0 * (p * 1.0 + g * 0.4) / tot


def synth_events_to_track(audio, events, duration, rate=32000):
    """把 ``synth_events`` 合成到一条音轨（**audio 核的首个消费者**）。"""
    track = audio.create_track(duration, rate=rate)
    wave_for = {'bass': 'square', 'pad': 'sine', 'lead': 'saw'}
    amp_for = {'bass': 0.30, 'pad': 0.12, 'lead': 0.16}
    dur_for = {'bass': 0.22, 'pad': 0.9, 'lead': 0.14}
    for t, kind, midi in events:
        f = 440.0 * (2.0 ** ((midi - 69) / 12.0))
        audio.tone(track, t, dur_for[kind], f, None, wave_for[kind], amp_for[kind])
    return track


def _hash32(s: str) -> int:
    h = 2166136261
    for ch in s:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h


class _Rng:
    """确定性 LCG（不用 random，避免跨版本漂移）。"""

    def __init__(self, seed):
        self.s = (seed or 1) & 0xFFFFFFFF

    def next_f(self) -> float:
        self.s = (self.s * 1103515245 + 12345) & 0x7FFFFFFF
        return self.s / 0x7FFFFFFF


def _next(note):
    """``(t, track)`` 取时间（兼容元组/列表）。"""
    return float(note[0])


# ============================================================ 内核
class PulseKernel(Service):
    """下落式音击内核：谱面 + 判定 + 血量/连击/得分 + 结算（``hit()`` 可 headless 驱动）。"""

    inject = ['render', 'rules', 'economy', 'audio']

    def __init__(self, ctx, config):
        super().__init__(ctx, "pulse")
        cfg = config or {}
        self.seed = int(cfg.get('seed', DEMO_SEEDS[0]))
        self.chart_bpm = int(cfg.get('bpm', BPM_LIST[0]))
        self.offset_ms = float(cfg.get('offset_ms', 0.0))
        self.notes = []
        self.synth = []
        self.meta = {}
        self.cursor = 0                       # 下一个待判定 note 的下标
        self.time = 0.0
        self.stats = dict(PERFECT=0, GOOD=0, MISS=0)
        self.combo = 0
        self.max_combo = 0
        self.score = 0.0
        self.events = []
        self.judged = []                      # [(t, track, verdict, err_ms)]
        self._gl_handles = []
        self._audio_track = None
        self._wav_path = None

    # ---------------------------------------------------------- 生命周期
    def __cordis_init__(self):
        self._acquire_gl()
        self.load_chart(self.chart_bpm)

    def _acquire_gl(self):
        render = self.ctx.render
        from experiments.cordis.tower_defense_kernel import _unit_cube
        h_prog = render.compile_program('pulse')
        h_mesh = render.upload_mesh('pulse_cube', *_unit_cube())
        self._gl_handles.extend([h_prog, h_mesh])
        for h in self._gl_handles:
            def release(h=h):
                return lambda: render.release(h)
            self.ctx.effect(release)

    # ---------------------------------------------------------- 谱面
    def load_chart(self, bpm=None, seed=None) -> dict:
        """加载/切换谱面（含**音乐合成**：audio 核的首个消费者）。"""
        if bpm is not None:
            self.chart_bpm = int(bpm)
        if seed is not None:
            self.seed = int(seed)
        self.notes, self.synth, self.meta = chart(self.seed, self.chart_bpm, BARS)
        self._reset_run()
        audio = self.ctx.audio
        if audio is not None:
            try:
                self._audio_track = synth_events_to_track(audio, self.synth,
                                                          self.meta['duration'])
            except Exception:                 # 合成失败不影响判定（headless 也要能跑）
                self._audio_track = None
        self._emit('chart', bpm=self.chart_bpm, n=self.meta['n_notes'])
        return self.meta

    def _reset_run(self):
        self.cursor = 0
        self.time = 0.0
        self.stats = dict(PERFECT=0, GOOD=0, MISS=0)
        self.combo = 0
        self.max_combo = 0
        self.score = 0.0
        self.judged = []
        self.ctx.rules.reset()
        ec = self.ctx.economy
        # 清零计分。⚠️ economy 是 KV 计账：**键没建立时 balance/spend 会 KeyError**，
        # 所以先 add(0) 建键，再扣掉余额。
        ec.add('score', 0)
        ec.add('score', -ec.balance('score'))

    def render_music(self, path=None):
        """把当前谱面的音乐渲染成 wav（**音频合成 smoke**；返回路径或 None）。"""
        import tempfile
        if self._audio_track is None:
            return None
        path = path or os.path.join(tempfile.mkdtemp(prefix='pulsefall_'), 'chart.wav')
        try:
            return self.ctx.audio.render_wav(self._audio_track, path)
        except Exception:
            return None

    # ---------------------------------------------------------- 判定
    def hit(self, track, t_now=None, t_note=None) -> dict:
        """**击键 API**（headless 自动演奏与 demo 同一入口）。

        Args:
            track: 0..3（DFJK）。
            t_now: 当前逻辑时钟（缺省 = ``self.time``）。
            t_note: 显式指定目标 note 时间（单测用）；缺省取**该轨最早的未判定 note**。

        Returns: ``{'ok', 'verdict', 'err_ms', 'combo', 'score'}``。
        """
        t_now = self.time if t_now is None else float(t_now)
        if self.ctx.rules.state() in ('won', 'lost'):
            return dict(ok=False, verdict=None, detail='已结束')
        if t_note is None:
            t_note = self._next_note_in_track(int(track))
        if t_note is None:
            return dict(ok=False, verdict=None, detail='该轨无可判定 note')
        err_ms = (t_now - t_note) * 1000.0 - self.offset_ms
        verdict = judge(err_ms)
        self._apply(int(track), t_note, verdict, err_ms)
        return dict(ok=True, verdict=verdict, err_ms=err_ms,
                    combo=self.combo, score=self.score,
                    detail=f'{verdict} ({err_ms:+.0f}ms)')

    def _next_note_in_track(self, track):
        for i in range(self.cursor, len(self.notes)):
            t, tr = self.notes[i]
            if tr == track:
                return t
        return None

    def _apply(self, track, t_note, verdict, err_ms):
        self.judged.append((t_note, track, verdict, err_ms))
        self.stats[verdict] += 1
        if verdict == 'MISS':
            self.combo = 0
            self.ctx.rules.on_event({'type': 'player_hit', 'dmg': 1})
            self._emit('miss', t=t_note)
        else:
            self.combo += 1
            self.max_combo = max(self.max_combo, self.combo)
            mult = 1.0 + min(self.combo, COMBO_BONUS_CAP) * 0.02
            self.score += NOTE_SCORE[verdict] * mult
            self.ctx.economy.add('score', NOTE_SCORE[verdict] * mult)
            self._emit(verdict.lower(), t=t_note, combo=self.combo)
        # 推进游标：跳过所有"已过判定窗"的 note
        while self.cursor < len(self.notes) and self.notes[self.cursor][0] <= t_note + 1e-6:
            self.cursor += 1

    def offset_adjust(self, delta_ms):
        """实机 offset 校准（F4 ±20ms，§16.3）：把 delta 夹到范围内累加。"""
        d = max(-OFFSET_RANGE_MS, min(OFFSET_RANGE_MS, float(delta_ms)))
        self.offset_ms += d
        self._emit('offset', value=self.offset_ms)
        return self.offset_ms

    # ---------------------------------------------------------- 主循环
    def update(self, dt):
        rules = self.ctx.rules
        if rules.state() in ('won', 'lost'):
            return
        self.time += dt
        # 结算：谱面走完（时钟超过最后一个 note + GOOD 窗）
        end = self.meta['duration'] + GOOD_MS / 1000.0
        if self.time >= end:
            if rules.state() == 'prep':
                self._emit('cleared')
                rules.on_event({'type': 'wave_cleared'})     # total_waves=1 -> won
            return
        # 漏键：越过 GOOD 窗仍未判定的 note 自动记 MISS（下落式音击惯例）
        for i in range(self.cursor, len(self.notes)):
            t, tr = self.notes[i]
            if self.time - t > GOOD_MS / 1000.0 + self.offset_ms / 1000.0:
                self._apply(tr, t, 'MISS', (self.time - t) * 1000.0 - self.offset_ms)

    def auto_step(self, dt):
        """**自动演奏**：到点即 hit（带 ±12ms 手抖），headless 验收与 demo ``--flag auto`` 同源。"""
        rules = self.ctx.rules
        if rules.state() in ('won', 'lost'):
            return
        # 先在"当前时刻"打掉所有落在判定窗内的 note
        for i in range(self.cursor, len(self.notes)):
            t, tr = self.notes[i]
            if t > self.time + 1e-9:
                break
            if abs((self.time - t) * 1000.0) <= AUTOPLAY_TOL_MS:
                self.hit(tr, t_now=self.time, t_note=t)
        self.update(dt)

    # ---------------------------------------------------------- 结算
    def result(self) -> dict:
        s = self.stats
        return dict(meta=dict(self.meta), **s,
                    combo_max=self.max_combo, score=round(self.score, 1),
                    acc=round(accuracy(s['PERFECT'], s['GOOD'], s['MISS']), 2),
                    full_combo=(s['MISS'] == 0),
                    state=self.ctx.rules.state())

    def _emit(self, name, **kw):
        self.events.append(dict(type=name, **kw))

    def drain_events(self):
        evs = self.events
        self.events = []
        return evs

    def status_text(self) -> str:
        s = self.stats
        return (f"BPM {self.chart_bpm}  t={self.time:5.1f}/{self.meta.get('duration', 0):.1f}s  "
                f"P{s['PERFECT']} G{s['GOOD']} M{s['MISS']}  连击 {self.combo}"
                f"(max {self.max_combo})  分 {self.score:.0f}  offset {self.offset_ms:+.0f}ms  "
                f"state={self.ctx.rules.state()}")


__all__ = ['PulseKernel', 'N_TRACKS', 'BPM_LIST', 'BARS', 'PERFECT_MS', 'GOOD_MS',
           'chart', 'judge', 'accuracy', 'synth_events_to_track', 'DEMO_SEEDS']
