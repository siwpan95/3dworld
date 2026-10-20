# -*- coding: utf-8 -*-
"""把 AI 生成的片段回插到单集：片头卡之后插入，音画同步对齐。

用法::

    python probes/l100/l100_inject.py 001 <clip.mp4> [clip2.mp4 ...]

规则：
- AI 片段统一缩放到 1280x720、按需截断/定格到 5 s，无音轨；
- 插在片头卡之后（观众先看到集名，再进入 AI 演绎的开场）；
- 音频同步插入等长静音，字幕全体偏移同样时长，重新烧录。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import wave

_HERE = os.path.dirname(os.path.abspath(__file__))
PROBES = os.path.dirname(_HERE)
ROOT = os.path.dirname(PROBES)
OUT = os.path.join(PROBES, 'out', 'l100')
SIZE = (1280, 720)
FPS = 15

import l100_render as R                     # noqa: E402
import l100_tts as T                        # noqa: E402
from l100_render import ffmpeg_exe          # noqa: E402


def _norm_clip(clip, out, seconds=5.0):
    """AI 片段 → 1280x720/15fps/指定时长/无声。"""
    r = subprocess.run(
        [ffmpeg_exe(), '-y', '-i', clip,
         '-vf', (f'scale={SIZE[0]}:{SIZE[1]}:force_original_aspect_ratio='
                 f'decrease,pad={SIZE[0]}:{SIZE[1]}:(ow-iw)/2:(oh-ih)/2,'
                 f'fps={FPS},tpad=stop_mode=clone:stop_duration=10,'
                 f'format=yuv420p'),
         '-t', str(seconds), '-an', '-c:v', 'libx264', '-preset', 'ultrafast',
         '-crf', '20', out], capture_output=True)
    if not os.path.exists(out):
        raise RuntimeError('norm failed: '
                           + r.stderr.decode('utf-8', 'replace')[-400:])
    return out


def _sil(path, seconds):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(T.SR)
        w.writeframes(b'\x00\x00' * int(seconds * T.SR))


def _burn(video, srt, out):
    work = os.path.dirname(out)
    style = ('FontName=Microsoft YaHei,FontSize=11,PrimaryColour=&H00FFFFFF,'
             'OutlineColour=&H64000000,BorderStyle=1,Outline=1.1,Shadow=0.3,'
             'MarginV=22')
    r = subprocess.run([ffmpeg_exe(), '-y', '-i', os.path.basename(video),
                        '-vf', f"subtitles={os.path.basename(srt)}:"
                               f"force_style='{style}'",
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '22',
                        '-pix_fmt', 'yuv420p', os.path.basename(out)],
                       capture_output=True, cwd=work)
    if not os.path.exists(out):
        raise RuntimeError('burn failed: '
                           + r.stderr.decode('utf-8', 'replace')[-500:])
    return out


def _load_cues(ep):
    """读回每场 cue 与时长，重算 SRT（含 AI 插入偏移）。"""
    au = os.path.join(OUT, 'audio', f'ep{ep:03d}')
    out = []
    for sid in sorted(os.listdir(au)):
        if not sid.endswith('.cues.json'):
            continue
        with open(os.path.join(au, sid), encoding='utf-8') as fh:
            out.append(json.load(fh))
    return out


def inject(ep, clips, slot_seconds=5.0):
    tag = f'ep{ep:03d}'
    e_dir = os.path.join(OUT, 'episodes', tag)
    seg_dir = os.path.join(e_dir, 'segs')
    wavs = [os.path.join(OUT, 'audio', tag, f) for f in
            sorted(os.listdir(os.path.join(OUT, 'audio', tag)))
            if f.endswith('.wav') and 'sil' not in f]
    titles = os.path.join(seg_dir, 'title.mp4')
    tail = os.path.join(seg_dir, 'tail.mp4')
    scene_mp4s = []
    seen = {}
    for f in sorted(os.listdir(seg_dir)):
        if not f.endswith('.mp4') or f in ('title.mp4', 'tail.mp4'):
            continue
        if f.startswith('ai') or '_s' in f:
            continue                      # AI 片段与镜头运镜段不进正片序列
        if f[0].isdigit():                # 场次段：001-00.mp4 / 001-00_c.mp4
            seen[f[:-4]] = os.path.join(seg_dir, f)
    for sid in sorted(seen):
        if sid.endswith('_c'):
            continue                      # _c 版会在无后缀键里被优先选取
        pick = seen.get(sid + '_c') or seen[sid]
        if pick:
            scene_mp4s.append(pick)

    ai_norm = []
    sils = []
    for i, c in enumerate(clips):
        p = _norm_clip(c, os.path.join(seg_dir, f'ai{i}.mp4'), slot_seconds)
        ai_norm.append(p)
        s = os.path.join(seg_dir, f'ai{i}_sil.wav')
        _sil(s, slot_seconds)
        sils.append(s)

    video_parts = [titles] + ai_norm + scene_mp4s + [tail]
    audio_parts = ([os.path.join(seg_dir, 'title_sil.wav')] + sils + wavs
                   + [os.path.join(seg_dir, 'tail_sil.wav')])

    raw = os.path.join(e_dir, f'{tag}_ai_raw.mp4')
    R.concat_videos(video_parts, raw, fade=0.0)
    full_audio = os.path.join(e_dir, f'{tag}_audio_ai.wav')
    with wave.open(full_audio, 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(T.SR)
        for p in audio_parts:
            with wave.open(p, 'rb') as w:
                out.writeframes(w.readframes(w.getnframes()))

    # 字幕：原 SRT + 片头2.5s + AI 插入偏移
    srt0 = os.path.join(e_dir, f'{tag}.srt')
    srt_ai = os.path.join(e_dir, f'{tag}_ai.srt')
    shift = 2.5 + slot_seconds * len(clips)
    with open(srt0, encoding='utf-8-sig') as fh:
        text = fh.read()

    def shift_line(m):
        def ts(s):
            hh, mm, rest = s.split(':')
            sec, ms = (rest.replace(',', '.').split('.') + ['0'])[:2]
            total = (int(hh) * 3600 + int(mm) * 60 + float(sec)
                     + (float('0.' + ms) if ms else 0)) + shift
            th = int(total // 3600)
            tm = int(total % 3600 // 60)
            ss = total % 60
            return (f'{th:02d}:{tm:02d}:{int(ss):02d},'
                    f'{int(round((ss - int(ss)) * 1000)):03d}')
        return f'{ts(m.group(1))} --> {ts(m.group(2))}'

    import re
    text = re.sub(r'(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})',
                  shift_line, text)
    with open(srt_ai, 'w', encoding='utf-8-sig') as fh:
        fh.write(text)

    no_sub = os.path.join(e_dir, f'{tag}_ai_nosub.mp4')
    R.mux(raw, full_audio, no_sub)
    final = os.path.join(e_dir, f'{tag}.mp4')
    _burn(no_sub, srt_ai, final)
    print('[out]', final, f'{R.probe_duration(final) / 60:.1f} min',
          f'{os.path.getsize(final) / 1048576:.1f} MB', flush=True)
    return final


def main():
    ep = int(sys.argv[1])
    inject(ep, sys.argv[2:])


if __name__ == '__main__':
    main()
