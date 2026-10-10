# -*- coding: utf-8 -*-
"""离线录制所有动画为独立 MP4（确定性时间步进，不受实时帧率影响）。

与实时运行的差别：不开 pyglet 主循环，而是手动推进每个动画的时间轴——
每帧固定 ``dt = 1/fps``，调 ``update(dt)`` 再调绘制入口，然后 glReadPixels
把 RGBA 帧直接喂给 ffmpeg 的 rawvideo 管道。这样：

- 帧序号与动画时间严格一一对应，慢机器也不会丢帧/跳帧；
- 每个动画录满自己的完整循环时长（五幕 48s / kkrieger 96s / 各 demo 的
  DURATION），输出 ``recordings/<名字>_<时长>s.mp4``。

用法（项目根目录）::

    python scripts/record_all.py                     全部 13 个动画
    python scripts/record_all.py --only main,kkrieger 只录指定的
    python scripts/record_all.py --list              列出录制计划
    python scripts/record_all.py --dur-override 3 --only water_waves
                                                     短录一段（调试用）

ffmpeg 查找顺序：PATH -> 常见安装位置 -> 报错说明。录制是逐帧离屏回读，
比实时慢（约 25~45ms/帧），全程预计 10 分钟上下。
"""
import argparse
import ctypes
import importlib.util
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
DEMOS_DIR = os.path.join(ROOT, 'demos')
if DEMOS_DIR not in sys.path:
    sys.path.insert(0, DEMOS_DIR)

OUT_DIR = os.path.join(ROOT, 'recordings')
DEFAULT_FPS = 30
DEMO_SIZE = (1280, 720)

# name, 模块路径(相对 ROOT), App 类名(None=自动找 DemoApp 子类), 时长兜底, 尺寸(None=App 自带)
ENTRIES = [
    dict(name='main_48s',            mod='main.py',                cls='WorldApp',    dur=48.0, size=None),
    dict(name='kkrieger_150s',       mod='kkrieger_demo.py',       cls='KKApp',       dur=150.0, size=None,
         audio='assets/kkrieger_ost_150s.wav'),   # 录完把程序化配乐混进视频
    dict(name='boids_45s',           mod='demos/boids.py',         cls=None,          dur=45.0, size=DEMO_SIZE),
    dict(name='demoscene_tunnel_24s', mod='demos/demoscene_tunnel.py', cls=None,      dur=24.0, size=DEMO_SIZE),
    dict(name='minecraft_style_48s', mod='demos/minecraft_style.py', cls=None,        dur=48.0, size=DEMO_SIZE),
    dict(name='particles_gpu_30s',   mod='demos/particles_gpu.py', cls=None,          dur=30.0, size=DEMO_SIZE),
    dict(name='raymarch_sdf_32s',    mod='demos/raymarch_sdf.py',  cls=None,          dur=32.0, size=DEMO_SIZE),
    dict(name='shadow_map_30s',      mod='demos/shadow_map.py',    cls=None,          dur=30.0, size=DEMO_SIZE),
    dict(name='solar_system_60s',    mod='demos/solar_system.py',  cls=None,          dur=60.0, size=DEMO_SIZE),
    dict(name='terrain_flyover_48s', mod='demos/terrain_flyover.py', cls=None,        dur=48.0, size=DEMO_SIZE),
    dict(name='mountain_100s',       mod='demos/mountain.py',       cls=None,          dur=100.0, size=DEMO_SIZE),
    dict(name='tower_defense_60s',   mod='demos/tower_defense.py',  cls=None,          dur=60.0, size=DEMO_SIZE),
    dict(name='godhand_40s',         mod='demos/godhand.py',        cls=None,          dur=40.0, size=DEMO_SIZE),
    dict(name='rayhold_60s',         mod='demos/rayhold.py',        cls=None,          dur=60.0, size=DEMO_SIZE),
    dict(name='boxward_60s',         mod='demos/boxward.py',        cls=None,          dur=60.0, size=DEMO_SIZE),
    dict(name='swarmward_60s',       mod='demos/swarmward.py',      cls=None,          dur=60.0, size=DEMO_SIZE),
    dict(name='voxel_terrain_36s',   mod='demos/voxel_terrain.py', cls=None,          dur=36.0, size=DEMO_SIZE),
    dict(name='water_waves_40s',     mod='demos/water_waves.py',   cls=None,          dur=40.0, size=DEMO_SIZE),
    dict(name='engine_showcase_24s', mod='demos/engine_showcase.py', cls='ShowcaseApp', dur=24.0, size=DEMO_SIZE),
    dict(name='material_lab_30s',    mod='demos/material_lab.py',  cls='MaterialLabApp', dur=30.0, size=DEMO_SIZE),
    dict(name='nordic_saga_60s',     mod='demos/nordic_saga.py',  cls='NordicSagaApp', dur=60.0, size=DEMO_SIZE,
         audio='assets/nordic_saga_ost_60s.wav',
         ost_fn='logic.nordic_audio:ensure_ost'),  # 北欧配乐（通用 ost 钩子）
    dict(name='blockworld_30s',      mod='demos/blockworld.py',   cls='BlockWorldApp', dur=30.0,
         size=DEMO_SIZE, flags='cine'),            # 可交互方块世界（脚本化运镜）
    dict(name='kid_a_walker_3d_40s', mod='demos/kid_a_walker_3d.py', cls='KidAApp', dur=40.0,
         size=DEMO_SIZE, flags='go'),              # KID-A WALKER 3D（自动探索运镜）
]

FFMPEG_CANDIDATES = [
    r'G:\repos\musickit\radio\ffmpeg\ffmpeg.exe',
    r'G:\repos\pluginplayer\vendor\ffmpeg\bin\ffmpeg.exe',
    r'G:\repos\audio-generation\remix\bin\ffmpeg.exe',
    r'G:\repos\musickit\remix\bin\ffmpeg.exe',
]


def ffmpeg_exe():
    """按「PATH → 候选表」逐一**校验可用性**（-version 能跑才采用）。

    PATH 里可能有不完整的 ffmpeg 包装器（实测踩过：G:\\Python314\\Scripts\\ffmpeg.exe
    是个 108KB 的坏壳，直接采用会让录制在全黑输出后失败）——所以逐个试跑。
    """
    candidates = []
    p = shutil.which('ffmpeg')
    if p:
        candidates.append(p)
    candidates.extend(FFMPEG_CANDIDATES)
    for c in candidates:
        if not os.path.exists(c):
            continue
        try:
            r = subprocess.run([c, '-version'], capture_output=True, timeout=10)
            blob = (r.stdout or b'') + (r.stderr or b'')
            if r.returncode == 0 and b'ffmpeg version' in blob:
                return c
        except Exception:
            continue
    raise SystemExit('ffmpeg 未找到可用版本：请把它加入 PATH 或改脚本里的 FFMPEG_CANDIDATES')


def import_module(rel_path, name):
    """按文件路径导入模块（demos/ 与 ROOT 都塞进 sys.path）。"""
    spec = importlib.util.spec_from_file_location(f'_rec_{name}', os.path.join(ROOT, rel_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def find_app_class(mod, cls_name):
    """取 App 类：显式指定优先，否则找 demos/_harness.DemoApp 的子类。"""
    import inspect
    if cls_name:
        return getattr(mod, cls_name)
    from _harness import DemoApp
    for _, obj in vars(mod).items():
        if inspect.isclass(obj) and issubclass(obj, DemoApp) and obj is not DemoApp:
            return obj
    raise SystemExit(f'{mod.__name__}: 未找到 DemoApp 子类')


def build_app(entry):
    """按入口构造 App 实例（三类宿主：harness DemoApp / engine EngineApp / presentation 裸 App）。"""
    from _harness import DemoApp, parse_args as harness_args
    from engine.app import EngineApp, parse_args as engine_args

    mod = import_module(entry['mod'], entry['name'])
    cls = find_app_class(mod, entry['cls'])
    w, h = entry['size'] or (None, None)

    if issubclass(cls, DemoApp):
        argv = ['--size', f'{w}x{h}', '--fps', '60']
        if entry.get('flags'):
            argv += ['--flag', entry['flags']]
        app = cls(w, h, args=harness_args(argv))
    elif issubclass(cls, EngineApp):
        argv = ['--size', f'{w}x{h}', '--fps', '60']
        if entry.get('flags'):
            argv += ['--flag', entry['flags']]
        app = cls(w, h, args=engine_args(argv))
    else:
        # presentation 层裸 App（WorldApp/KKApp）：固定 960x540，无 args 参数
        app = cls() if entry['cls'] == 'WorldApp' else cls(shots=None)
    # 关掉截图/自动关闭逻辑（SHOTS 越过时刻会抓帧并 schedule close）
    if hasattr(app, '_capture_due'):
        app._capture_due = lambda: None
    if hasattr(app, 'shots'):
        app.shots = []
    return app, cls


def app_duration(app, cls, fallback):
    for src in (getattr(app, 'timeline', None), cls):
        d = getattr(src, 'duration', None)
        if d and d > 0:
            return float(d)
        d = getattr(src, 'DURATION', None)
        if d and d > 0:
            return float(d)
    return float(fallback)


def record(entry, out_dir, fps, crf, dur_override):
    from pyglet.gl import GL_RGBA, GL_UNSIGNED_BYTE, glFinish, glReadPixels

    app, cls = build_app(entry)
    try:
        # 窗口位置两类宿主不同：harness/engine 在 app.window，
        # presentation 裸 App（WorldApp/KKApp）在 app.renderer.window
        window = getattr(app, 'window', None) or app.renderer.window
        window.switch_to()
        # HUD 帧率显示：离线录制不走实时 tick，手动把 EMA 值设为目标帧率
        for attr in ('fps', '_fps'):
            if hasattr(app, attr):
                setattr(app, attr, float(fps))
        w, h = window.width, window.height
        dur = dur_override or app_duration(app, cls, entry['dur'])
        n_frames = int(round(dur * fps))
        out_path = os.path.join(out_dir, f"{entry['name']}.mp4")

        cmd = [ffmpeg_exe(), '-y', '-hide_banner', '-loglevel', 'error',
               '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', f'{w}x{h}',
               '-r', str(fps), '-i', '-',
               '-vf', 'vflip',                     # GL 帧自下而上，翻回来
               '-c:v', 'libx264', '-preset', 'veryfast', '-crf', str(crf),
               '-pix_fmt', 'yuv420p', '-movflags', '+faststart', out_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

        dt = 1.0 / fps
        buf = (ctypes.c_ubyte * (w * h * 4))()
        t0 = time.time()
        is_bare = not hasattr(app, '_capture_due')   # WorldApp/KKApp 走裸驱动
        for i in range(n_frames):
            t = i * dt
            if is_bare:
                app.update(dt)                       # 内部推进 playback 播头
            else:
                app.time = t
                if getattr(app, 'DURATION', None):
                    app.time = t % app.DURATION
                app.update(dt)
            app._on_draw()
            glFinish()
            glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, buf)
            proc.stdin.write(bytes(buf))
            if (i + 1) % (fps * 5) == 0:
                rate = (i + 1) / (time.time() - t0)
                print(f'  [{entry["name"]}] frame {i + 1}/{n_frames} '
                      f'({rate:.1f} fps)', flush=True)
        proc.stdin.close()
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f'ffmpeg 退出码 {proc.returncode}')
        # 可选：把程序化配乐混进视频（OST 与时间轴同长，-shortest 对齐）。
        # ost_fn 形如 'logic.xxx_audio:ensure_ost'；缺省沿用 kkrieger 的。
        if entry.get('audio'):
            spec = entry.get('ost_fn', 'logic.kkrieger_audio:ensure_ost')
            mod_name, fn_name = spec.split(':')
            ensure = getattr(importlib.import_module(mod_name), fn_name)
            wav = ensure()
            muxed = out_path + '.mux.mp4'
            subprocess.run([ffmpeg_exe(), '-y', '-loglevel', 'error',
                            '-i', out_path, '-i', wav,
                            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '160k',
                            '-shortest', muxed], check=True)
            os.replace(muxed, out_path)
        size_mb = os.path.getsize(out_path) / 1048576
        print(f'[ok] {entry["name"]}: {n_frames} 帧 / {dur:.0f}s / '
              f'{w}x{h} / {size_mb:.1f}MB -> {out_path}', flush=True)
        return out_path, n_frames
    finally:
        try:
            window.close()
        except Exception:
            pass


def fusion(out_dir, fps, crf):
    """融合总集篇：recordings/ 全部段落 xfade 交叉溶解串成一部片 + 程序化总配乐。

    段落表（顺序/时长/情绪）在 ``logic/fusion_audio.FUSION_SEGMENTS``，
    它同时驱动这里 ffmpeg 的 xfade 时间轴和配乐生成——音视频因此同源对齐。
    缺失的段落会先自动录制补齐。
    """
    from logic.fusion_audio import (FUSION_SEGMENTS, XFADE, build_fusion_ost,
                                    segment_offsets)
    # 1) 段落文件齐全？缺失的先录
    segs = []
    for name, _dur, _mood in FUSION_SEGMENTS:
        path = os.path.join(out_dir, f'{name}.mp4')
        if not os.path.exists(path):
            entry = next((e for e in ENTRIES if e['name'] == name), None)
            if entry is None:
                raise SystemExit(f'fusion 段落无录制入口: {name}')
            print(f'--- 段落缺失，先录 {name} ---', flush=True)
            record(entry, out_dir, fps, crf, None)
        segs.append(path)
    # 2) 总配乐（一次性渲染，之后走缓存）
    wav = os.path.join(ROOT, 'assets', 'fusion_ost.wav')
    if not os.path.exists(wav):
        print('[fusion] 渲染总配乐（约 1~2 分钟）...', flush=True)
        build_fusion_ost(wav,
                         progress=lambda n: print(f'  [fusion-ost] {n}', flush=True))
    # 3) ffmpeg：视频 xfade 链 + 音频用总配乐
    out_path = os.path.join(out_dir, 'fusion_all.mp4')
    cmd = [ffmpeg_exe(), '-y', '-hide_banner', '-loglevel', 'error']
    for p in segs:
        cmd += ['-i', p]
    cmd += ['-i', wav]
    n = len(segs)
    parts = []
    for i in range(n):
        parts.append(
            f'[{i}:v]scale=1280:720:force_original_aspect_ratio=decrease,'
            f'pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps={fps},'
            f'format=yuv420p,setsar=1[v{i}]')
    offs = segment_offsets()
    prev = '[v0]'
    for k in range(1, n):
        xlabel = f'[x{k}]'
        parts.append(f'{prev}[v{k}]xfade=transition=fade:duration={XFADE}:'
                     f'offset={offs[k]:.3f}{xlabel}')
        prev = xlabel
    # xfade 链末端必须再压一次 yuv420p——xfade 输出 4:4:4，libx264 会跟着
    # 编 yuv444p（Windows/QuickTime 播不了），第一版融合片就踩了这个
    parts.append(f'{prev}format=yuv420p[vout]')
    cmd += ['-filter_complex', ';'.join(parts),
            '-map', '[vout]', '-map', f'{n}:a',
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', str(max(1, crf - 1)),
            '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', out_path]
    print(f'[fusion] 合成 {n} 段 × xfade {XFADE}s → {out_path}', flush=True)
    subprocess.run(cmd, check=True)
    size_mb = os.path.getsize(out_path) / 1048576
    print(f'[ok] fusion_all: {size_mb:.1f}MB -> {out_path}', flush=True)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default='', help='逗号分隔的名字过滤（前缀匹配）')
    ap.add_argument('--fps', type=int, default=DEFAULT_FPS)
    ap.add_argument('--crf', type=int, default=20, help='x264 质量（越小越清晰）')
    ap.add_argument('--out', default=OUT_DIR)
    ap.add_argument('--dur-override', type=float, default=None,
                    help='覆盖时长（秒），调试短录用')
    ap.add_argument('--fusion', action='store_true',
                    help='融合总集篇：全部段落 xfade 串成 fusion_all.mp4')
    ap.add_argument('--list', action='store_true')
    args = ap.parse_args()

    if args.list:
        for e in ENTRIES:
            print(f"{e['name']:<24} {e['mod']:<28} dur={e['dur']:.0f}s "
                  f"size={e['size'] or 'native(960x540)'}")
        return 0

    os.makedirs(args.out, exist_ok=True)
    if args.fusion:
        path = fusion(args.out, args.fps, args.crf)
        print('FUSION_OK', path)
        return 0
    entries = ENTRIES
    if args.only:
        keys = [k.strip() for k in args.only.split(',') if k.strip()]
        entries = [e for e in entries if any(k in e['name'] for k in keys)]
    if not entries:
        raise SystemExit('没有匹配的录制目标')

    print(f'recording {len(entries)} animations @ {args.fps}fps -> {args.out}')
    results, failures = [], []
    for e in entries:
        print(f'--- {e["name"]} ---', flush=True)
        try:
            path, n = record(e, args.out, args.fps, args.crf, args.dur_override)
            if n <= 0 or os.path.getsize(path) < 50 * 1024:
                failures.append(f'{e["name"]}: 产物过小')
            else:
                results.append(path)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            failures.append(f'{e["name"]}: {exc}')
    print('-' * 72)
    for p in results:
        print('  [ok]', p)
    for f in failures:
        print('  [FAIL]', f)
    if failures:
        print(f'RECORD_FAILED ({len(failures)})')
        return 1
    print(f'ALL_RECORDED ({len(results)} files)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
