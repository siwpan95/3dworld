# -*- coding: utf-8 -*-
"""edge-tts 中文配音。

- 主角固定声线（石满仓/苗小满/汤宁/二柱/旁白），配角按性别猜测分配，
  并用 rate/pitch 与主角区分。
- 每条台词一个 mp3（文本 hash 做缓存），转 24kHz mono wav，
  行间插入 0.35 s 静音，按场次拼成一个 wav，同时产出 cue 表
  （每条台词的起点/时长/说话人/文本），供 SRT 字幕使用。

用法::

    python probes/l100/l100_tts.py 001            # 只做第 001 集
    python probes/l100/l100_tts.py --all          # 全部
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import struct
import subprocess
import sys
import wave

_HERE = os.path.dirname(os.path.abspath(__file__))
PROBES = os.path.dirname(_HERE)
ROOT = os.path.dirname(PROBES)

SR = 24000          # 统一采样率
GAP_S = 0.36        # 台词之间的静音
LEAD_S = 0.45       # 每场戏开头的静音（给观众一拍看画面）

import edge_tts                                                # noqa: E402

VOICES = {
    '石满仓': ('zh-CN-YunjianNeural', '-8%', '-2Hz'),
    '苗小满': ('zh-CN-XiaoxiaoNeural', '+2%', '+0Hz'),
    '汤宁':   ('zh-CN-XiaoyiNeural', '+4%', '+2Hz'),
    '二柱':   ('zh-CN-YunxiNeural', '+2%', '+0Hz'),
    '民警':   ('zh-CN-YunyangNeural', '+0%', '-2Hz'),
    '油老头': ('zh-CN-YunyeNeural', '-10%', '-4Hz'),
    '老人':   ('zh-CN-YunyeNeural', '-12%', '-4Hz'),
    '看站人': ('zh-CN-YunyeNeural', '-6%', '-2Hz'),
    '冷链司机': ('zh-CN-YunjianNeural', '+2%', '+1Hz'),
    '男司机': ('zh-CN-YunxiNeural', '+3%', '-1Hz'),
    '女员工': ('zh-CN-XiaoxiaoNeural', '+6%', '+2Hz'),
    '装卸工甲': ('zh-CN-YunxiNeural', '+4%', '+0Hz'),
    '__旁白__': ('zh-CN-YunyangNeural', '-4%', '-3Hz'),
}
FALLBACK_MALE = ('zh-CN-YunxiNeural', '+2%', '-1Hz')
FALLBACK_FEMALE = ('zh-CN-XiaoxiaoNeural', '+4%', '+1Hz')
FEMALE_HINT = ('女', '姨', '婶', '嫂', '婆', '妈', '姐')

from l100_render import ffmpeg_exe                             # noqa: E402


def voice_of(who):
    if who in VOICES:
        return VOICES[who]
    if any(h in who for h in FEMALE_HINT):
        return FALLBACK_FEMALE
    return FALLBACK_MALE


def _cache_key(text, voice, rate, pitch):
    h = hashlib.md5(f'{voice}|{rate}|{pitch}|{text}'.encode()).hexdigest()[:16]
    return h


async def _synth(text, voice, rate, pitch, mp3):
    c = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await c.save(mp3)


def mp3_to_wav(mp3, wav):
    r = subprocess.run([ffmpeg_exe(), '-y', '-i', mp3, '-ar', str(SR),
                        '-ac', '1', '-f', 'wav', wav], capture_output=True)
    if not os.path.exists(wav):
        raise RuntimeError('mp3->wav failed: '
                           + r.stderr.decode('utf-8', 'replace')[-300:])


def wav_duration(path):
    with wave.open(path, 'rb') as w:
        return w.getnframes() / float(w.getframerate())


class TTS:
    def __init__(self, cache_dir):
        self.cache = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _silence_wav(self, seconds=0.30):
        key = f'__sil_{seconds}'
        wav = os.path.join(self.cache, key + '.wav')
        if not os.path.exists(wav):
            with wave.open(wav, 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SR)
                w.writeframes(b'\x00\x00' * int(seconds * SR))
        return wav, seconds

    def _sapi_wav(self, text, wav, rate):
        """本地 Windows SAPI 兜底（Huihui 中文女声）。

        edge-tts 被限流时保证台词有声；rate 按 edge 的 '+N%' 近似映射。
        """
        try:
            import pythoncom
            from win32com.client import Dispatch
            pythoncom.CoInitialize()
            tmp = wav[:-4] + '.sapi.wav'
            stream = Dispatch('SAPI.SpFileStream')
            stream.Format.Type = 34            # SAFT22kHz16BitMono
            stream.Open(tmp, 3)                # SSFMCreateForWrite
            v = Dispatch('SAPI.SpVoice')       # 默认声音即 Huihui（本机唯一中文声）
            try:
                v.Rate = max(-4, min(4, int(round(
                    float(str(rate).strip().strip('%').strip('+')) / 5.0))))
            except (ValueError, AttributeError):
                v.Rate = 0
            v.AudioOutputStream = stream
            v.Speak(text)
            stream.Close()
            r = subprocess.run([ffmpeg_exe(), '-y', '-i', tmp, '-ar', str(SR),
                                '-ac', '1', '-f', 'wav', wav],
                               capture_output=True)
            try:
                os.remove(tmp)                 # 单文件，不触发批量删除钩子
            except OSError:
                pass
            if os.path.exists(wav) and wav_duration(wav) > 0.05:
                return True
        except Exception as e:
            print(f'[tts] SAPI failed: {e}', flush=True)
        return False

    def line(self, text, voice, rate, pitch):
        # 纯标点/无实料的行 edge-tts 会拒绝：直接给静音
        import re as _re
        if not _re.search(r'[0-9A-Za-z\u4e00-\u9fff]', text):
            return self._silence_wav()
        key = _cache_key(text, voice, rate, pitch)
        wav = os.path.join(self.cache, key + '.wav')
        if not os.path.exists(wav):
            mp3 = os.path.join(self.cache, key + '.mp3')
            last = None
            for attempt in range(2):
                try:
                    asyncio.run(_synth(text, voice, rate, pitch, mp3))
                    mp3_to_wav(mp3, wav)
                    last = None
                    break
                except Exception as e:          # 网络抖动 / 限流 / NoAudioReceived
                    last = e
                    import time
                    time.sleep(3.0 * (attempt + 1))   # 退避，edge-tts 会限流
            if last is None:
                import time
                time.sleep(0.20)                # 轻节流，降低被限流概率
            if last is not None:
                # edge-tts 被限流：先试本地 SAPI，再退到静音
                if self._sapi_wav(text, wav, rate):
                    print(f'[tts] SAPI fallback used: {text[:14]}...',
                          flush=True)
                    return wav, wav_duration(wav)
                print(f'[tts] fallback silence: {text[:18]}... ({last})',
                      flush=True)
                return self._silence_wav()
            # 保留 mp3：环境安全钩子会拦截批量删除，且缓存复用有益
        return wav, wav_duration(wav)


def _silence(seconds):
    n = int(round(seconds * SR))
    return b'\x00\x00' * n


def scene_audio(scene, tts, out_wav, on_progress=None):
    """把一个场次的全部台词合成一条 wav；返回 (wav, dur, cues)。

    cue: dict(start, dur, who, text) —— SRT 字幕直接可用。
    """
    cues = []
    frames = bytearray()
    frames += _silence(LEAD_S)
    t = LEAD_S
    n_dlg = 0
    fails = 0
    for kind, a, b in scene['blocks']:
        if kind != 'dlg':
            continue
        text = (b or '').strip()
        if not text:
            continue
        # 说话人 + 冒号前缀（保留在字幕里，TTS 只念内容）
        voice, rate, pitch = voice_of(a)
        clean = _clean_for_tts(text)
        if not clean:
            continue
        wav, dur = tts.line(clean, voice, rate, pitch)
        if wav.endswith('__sil_0.3.wav'):
            fails += 1
        with wave.open(wav, 'rb') as w:
            data = w.readframes(w.getnframes())
        cues.append({'start': round(t, 3), 'dur': round(dur, 3),
                     'who': a, 'text': text})
        frames += data
        t += dur
        frames += _silence(GAP_S)
        t += GAP_S
        n_dlg += 1
        if on_progress and n_dlg % 20 == 0:
            on_progress(n_dlg)
    d = os.path.dirname(out_wav)
    if d:
        os.makedirs(d, exist_ok=True)
    with wave.open(out_wav, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(frames))
    marker = out_wav[:-4] + '.needs_tts'
    if fails:
        with open(marker, 'w', encoding='utf-8') as fh:
            fh.write(str(fails))
        print(f'  [tts] {scene["scene_id"]} {fails} 条失败（已标 needs_tts）',
              flush=True)
    elif os.path.exists(marker):
        os.remove(marker)   # 重跑成功后清除标记（单文件，不会触发批量删除）
    return out_wav, round(t, 3), cues


def _clean_for_tts(s):
    import re
    s = re.sub(r'（[^）]{0,24}）', '', s)      # 舞台指示不念
    s = s.replace('——', '，').replace('……', '，').replace('…', '，')
    s = re.sub(r'\*+', '', s)
    s = re.sub(r'\s{2,}', ' ', s).strip(' 　，')
    return s


# ---------------------------------------------------------------- SRT --
def _ts(sec):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


def write_srt(cues_per_scene, offsets, out_srt):
    """cues_per_scene: 每场戏的 cue 列表；offsets: 每场戏在全片中的起点。"""
    idx = 0
    lines = []
    for cues, off in zip(cues_per_scene, offsets):
        for c in cues:
            idx += 1
            start = off + c['start']
            end = start + c['dur'] + 0.15
            lines.append(f"{idx}\n{_ts(start)} --> {_ts(end)}\n"
                         f"{c['who']}：{c['text']}\n")
    with open(out_srt, 'w', encoding='utf-8-sig') as fh:
        fh.write('\n'.join(lines))
    return out_srt


# ---------------------------------------------------------------- CLI --
def _load_script():
    p = os.path.join(PROBES, 'out', 'l100', 'script.json')
    with open(p, encoding='utf-8') as fh:
        return json.load(fh)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('eps', nargs='*', help='集号，如 001 002；空=全部')
    args = ap.parse_args()
    script = _load_script()
    want = {int(e) for e in args.eps} if args.eps else None
    out_root = os.path.join(PROBES, 'out', 'l100', 'audio')
    cache = os.path.join(PROBES, 'out', 'l100', 'tts_cache')
    tts = TTS(cache)
    for ep in script['episodes']:
        if want and ep['ep'] not in want:
            continue
        e_dir = os.path.join(out_root, f"ep{ep['ep']:03d}")
        os.makedirs(e_dir, exist_ok=True)
        total = 0.0
        for sc in ep['scenes']:
            wav, dur, cues = scene_audio(sc, tts,
                                         os.path.join(e_dir,
                                                      f"{sc['scene_id']}.wav"))
            with open(os.path.join(e_dir, f"{sc['scene_id']}.cues.json"), 'w',
                      encoding='utf-8') as fh:
                json.dump(cues, fh, ensure_ascii=False, indent=1)
            total += dur
            print(f"  {sc['scene_id']} {sc['place']}/{sc['time']} "
                  f"{len(cues)} 台词 {dur:.1f}s")
        print(f"ep{ep['ep']:03d} 《{ep['title']}》 音频合计 {total/60:.1f} min")


if __name__ == '__main__':
    main()
