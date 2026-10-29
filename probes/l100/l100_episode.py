# -*- coding: utf-8 -*-
"""单集编排：静帧 → 运镜分段 → 拼接 → 配音 → 字幕 → 成片。

每集的组装规则：
1. 片头卡 2.5 s：黑底，"第 N 集《标题》"（drawtext）。
2. 每个场次：3 张静帧（时长 < 6 s 用 2 张，< 3 s 用 1 张），
   每张用 zoompan 做缓慢运镜，段与段 xfade 溶接；
   段时长按该场配音长度均分。
3. 场与场直接硬切（hard cut），保留"场次感"。
4. 集尾 2 s 黑场。
5. 字幕：全集 cue 表 → SRT → libass 烧录（微软雅黑）。

用法::

    python probes/l100/l100_episode.py 001          # 出一集
    python probes/l100/l100_episode.py 1 2 3        # 批量
    python probes/l100/l100_episode.py --all
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import wave

_HERE = os.path.dirname(os.path.abspath(__file__))
PROBES = os.path.dirname(_HERE)
ROOT = os.path.dirname(PROBES)
for _p in (ROOT, PROBES, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import l100_render as R                          # noqa: E402
import l100_tts as T                             # noqa: E402
import l100_world as W                           # noqa: E402
import l100_cast as C                            # noqa: E402

from l100_render import ffmpeg_exe               # noqa: E402

OUT = os.path.join(PROBES, 'out', 'l100')
SIZE = (1280, 720)
FPS = 15
MOVES = ['in', 'pan_r', 'out', 'pan_l', 'drift_u', 'still']
MIN_SHOT = 1.6          # 单段画面最短秒数
SILENCE_FLOOR = 3.0     # 无台词场次的最短画面


def _load_script():
    with open(os.path.join(OUT, 'script.json'), encoding='utf-8') as fh:
        return json.load(fh)


def _silence_wav(path, seconds):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(T.SR)
        w.writeframes(b'\x00\x00' * int(round(seconds * T.SR)))
    return path


def _title_card(text, out_mp4, seconds=2.5):
    """黑底片头卡：第 N 集《标题》。"""
    d = os.path.dirname(out_mp4)
    if d:
        os.makedirs(d, exist_ok=True)
    n = int(round(seconds * FPS))
    font = 'C\\:/Windows/Fonts/msyh.ttc'
    filt = (f"color=c=black:s={SIZE[0]}x{SIZE[1]}:r={FPS}:d={seconds},"
            f"drawtext=fontfile='{font}':text='{text}':"
            f"fontcolor=0xE8E2D4:fontsize=44:x=(w-text_w)/2:y=(h-text_h)/2,"
            f"format=yuv420p")
    r = subprocess.run([ffmpeg_exe(), '-y', '-f', 'lavfi',
                        '-i', f'color=c=black:s={SIZE[0]}x{SIZE[1]}:r={FPS}',
                        '-t', str(seconds), '-vf',
                        f"drawtext=fontfile='{font}':text='{text}':"
                        f"fontcolor=0xE8E2D4:fontsize=44:"
                        f"x=(w-text_w)/2:y=(h-text_h)/2",
                        '-frames:v', str(n), '-c:v', 'libx264',
                        '-preset', 'ultrafast', '-crf', '20', out_mp4],
                       capture_output=True)
    if not os.path.exists(out_mp4):
        raise RuntimeError('title card failed: '
                           + r.stderr.decode('utf-8', 'replace')[-500:])
    return out_mp4


def _black_tail(out_mp4, seconds=2.0):
    n = int(round(seconds * FPS))
    r = subprocess.run([ffmpeg_exe(), '-y', '-f', 'lavfi',
                        '-i', f'color=c=black:s={SIZE[0]}x{SIZE[1]}:r={FPS}',
                        '-t', str(seconds), '-frames:v', str(n),
                        '-c:v', 'libx264', '-preset', 'ultrafast',
                        '-crf', '20', out_mp4], capture_output=True)
    if not os.path.exists(out_mp4):
        raise RuntimeError('tail failed: '
                           + r.stderr.decode('utf-8', 'replace')[-300:])
    return out_mp4


def _burn_subtitles(video, srt, out):
    """把 SRT 烧进视频（同目录相对路径规避 Windows 冒号转义）。"""
    work = os.path.dirname(out)
    v_name = os.path.basename(video)
    s_name = os.path.basename(srt)
    style = ('FontName=Microsoft YaHei,FontSize=11,PrimaryColour=&H00FFFFFF,'
             'OutlineColour=&H64000000,BorderStyle=1,Outline=1.1,Shadow=0.3,'
             'MarginV=22')
    vf = f"subtitles={s_name}:force_style='{style}'"
    r = subprocess.run([ffmpeg_exe(), '-y', '-i', v_name, '-vf', vf,
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '20',
                        '-pix_fmt', 'yuv420p', os.path.basename(out)],
                       capture_output=True, cwd=work)
    if not os.path.exists(out):
        raise RuntimeError('burn failed: '
                           + r.stderr.decode('utf-8', 'replace')[-600:])
    return out


def _concat_wavs(wavs, out_wav):
    with wave.open(out_wav, 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(T.SR)
        for p in wavs:
            with wave.open(p, 'rb') as w:
                assert w.getframerate() == T.SR, p
                out.writeframes(w.readframes(w.getnframes()))
    return out_wav


def build_episode(ep, renderer, tts):
    ep_no = ep['ep']
    tag = f'ep{ep_no:03d}'
    e_dir = os.path.join(OUT, 'episodes', tag)
    fr_dir = os.path.join(e_dir, 'frames')
    seg_dir = os.path.join(e_dir, 'segs')
    au_dir = os.path.join(OUT, 'audio', tag)
    for d in (fr_dir, seg_dir, au_dir):
        os.makedirs(d, exist_ok=True)

    print(f'==== {tag} 《{ep["title"]}》 {len(ep["scenes"])} 场 ====', flush=True)
    cues_all, offsets, wavs, seg_videos = [], [], [], []
    cursor = 0.0

    for i, sc in enumerate(ep['scenes']):
        sid = sc['scene_id']
        # -------- 配音 --------
        wav_path = os.path.join(au_dir, f'{sid}.wav')
        cue_path = os.path.join(au_dir, f'{sid}.cues.json')
        marker = wav_path[:-4] + '.needs_tts'
        cached = (os.path.exists(wav_path) and os.path.exists(cue_path)
                  and not os.path.exists(marker))
        if cached:
            dur = R.probe_duration(wav_path, 0.0)
            if dur <= 0.01:
                dur = SILENCE_FLOOR
            with open(cue_path, encoding='utf-8') as fh:
                cues = json.load(fh)
        else:
            wav_path, dur, cues = T.scene_audio(sc, tts, wav_path)
            with open(cue_path, 'w', encoding='utf-8') as fh:
                json.dump(cues, fh, ensure_ascii=False, indent=1)
        if not cues:
            if dur < SILENCE_FLOOR:
                wav_path = os.path.join(au_dir, f'{sid}.sil.wav')
                _silence_wav(wav_path, SILENCE_FLOOR)
                dur = SILENCE_FLOOR

        cues_all.append(cues)
        offsets.append(cursor)
        wavs.append(wav_path)
        cursor += dur

        # -------- 静帧 --------
        scene = W.build_scene(sc['place'], sc['time'],
                              seed=1000 + ep_no * 7, variant=i)
        from world3d.engine.scene_build import validate
        errs = [e for e in validate({k: v for k, v in scene.items()
                                     if not k.startswith('_')})
                if e[0] == 'error']
        if errs:
            print(f'  [skip] {sid} validate: {errs[:2]}')
            return None
        renderer.switch_scene(scene)
        n_shots = 3 if dur >= 6 else (2 if dur >= 3 else 1)
        poses = W.poses_for(scene, n_shots)
        shots = []
        for j, pn in enumerate(poses):
            pos, tgt, fov = W.pose(pn, scene['_place'], variant=i)
            png = os.path.join(fr_dir, f'{sid}_s{j}.png')
            if not os.path.exists(png):
                renderer.shoot(pos, tgt, fov, png)
            # ---- 角色合成（出场名单 + 构图允许时）----
            cast = C.cast_for(sc['blocks'], scene['_place'], pn)
            if cast:
                png_c = png[:-4] + '_cast.png'
                if not os.path.exists(png_c):
                    C.composite(png, cast, png_c, tod=scene['_tod'])
                png = png_c
            shots.append(png)

        # -------- 运镜分段 --------
        per = max(dur / len(shots), MIN_SHOT)
        segs = []
        has_cast = any('_cast' in p for p in shots)
        for j, png in enumerate(shots):
            mv = MOVES[(i * 2 + j) % len(MOVES)]
            ctag = '_c' if '_cast' in png else ''
            mp4 = os.path.join(seg_dir, f'{sid}_s{j}{ctag}_f{FPS}.mp4')
            seg_len = per if j < len(shots) - 1 else max(dur - per * (len(shots) - 1), MIN_SHOT)
            if not os.path.exists(mp4):
                R.kenburns(png, seg_len, mp4, size=SIZE, move=mv, fps=FPS)
            segs.append(mp4)
        scene_mp4 = os.path.join(seg_dir, f'{sid}{"_c" if has_cast else ""}.mp4')
        if not os.path.exists(scene_mp4):
            R.concat_videos(segs, scene_mp4, fade=0.35)
        seg_videos.append(scene_mp4)
        print(f'  {sid} {sc["place"]}/{sc["time"]} {dur:5.1f}s '
              f'{len(cues):3d} 台词 {len(shots)} 镜头', flush=True)

    # -------- 集级拼接 --------
    raw_video = os.path.join(e_dir, f'{tag}_raw.mp4')
    audio_wav = os.path.join(e_dir, f'{tag}_audio.wav')

    title = _title_card(f"第 {ep_no} 集  {ep['title']}",
                        os.path.join(seg_dir, 'title.mp4'))
    tail = _black_tail(os.path.join(seg_dir, 'tail.mp4'))
    R.concat_videos([title] + seg_videos + [tail], raw_video, fade=0.0)

    _concat_wavs([_silence_wav(os.path.join(seg_dir, 'title_sil.wav'), 2.5)]
                 + wavs
                 + [_silence_wav(os.path.join(seg_dir, 'tail_sil.wav'), 2.0)],
                 audio_wav)

    srt = os.path.join(e_dir, f'{tag}.srt')
    T.write_srt(cues_all, [o + 2.5 for o in offsets], srt)

    no_sub = os.path.join(e_dir, f'{tag}_nosub.mp4')
    print('  [stage] mux audio...', flush=True)
    R.mux(raw_video, audio_wav, no_sub)
    final = os.path.join(e_dir, f'{tag}.mp4')
    print('  [stage] burn subtitles...', flush=True)
    _burn_subtitles(no_sub, srt, final)
    # 中间产物 raw/nosub 保留（批量删除触发环境安全钩子），需人工清理
    dur = R.probe_duration(final)
    mb = os.path.getsize(final) / 1048576
    print(f'  [out] {final}  {dur/60:.1f} min  {mb:.1f} MB')
    return final


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('eps', nargs='*')
    ap.add_argument('--all', action='store_true')
    args = ap.parse_args()
    script = _load_script()
    if args.all:
        want = [e['ep'] for e in script['episodes']]
    else:
        want = [int(x) for x in args.eps]
    renderer = R.StillRenderer(size=(1920, 1080))
    tts = T.TTS(os.path.join(OUT, 'tts_cache'))
    for ep in script['episodes']:
        if ep['ep'] not in want:
            continue
        build_episode(ep, renderer, tts)
    renderer.close()


if __name__ == '__main__':
    main()
