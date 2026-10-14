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
EXAMPLES_DIR = os.path.join(ROOT, 'examples')
if EXAMPLES_DIR not in sys.path:
    sys.path.insert(0, EXAMPLES_DIR)

OUT_DIR = os.path.join(ROOT, 'recordings')
DEFAULT_FPS = 30
DEMO_SIZE = (1280, 720)

# name, 模块路径(相对 ROOT), App 类名(None=自动找 DemoApp 子类), 时长兜底, 尺寸(None=App 自带)
ENTRIES = [
    dict(name='main_48s',            mod='main.py',                cls='WorldApp',    dur=48.0, size=None),
    dict(name='kkrieger_150s',       mod='kkrieger_demo.py',       cls='KKApp',       dur=150.0, size=None,
         audio='assets/kkrieger_ost_150s.wav'),   # 录完把程序化配乐混进视频
    dict(name='boids_45s',           mod='examples/boids.py',         cls=None,          dur=45.0, size=DEMO_SIZE),
    dict(name='demoscene_tunnel_24s', mod='examples/demoscene_tunnel.py', cls=None,      dur=24.0, size=DEMO_SIZE),
    dict(name='minecraft_style_48s', mod='examples/minecraft_style.py', cls=None,        dur=48.0, size=DEMO_SIZE),
    dict(name='particles_gpu_30s',   mod='examples/particles_gpu.py', cls=None,          dur=30.0, size=DEMO_SIZE),
    dict(name='raymarch_sdf_32s',    mod='examples/raymarch_sdf.py',  cls=None,          dur=32.0, size=DEMO_SIZE),
    dict(name='shadow_map_30s',      mod='examples/shadow_map.py',    cls=None,          dur=30.0, size=DEMO_SIZE),
    dict(name='solar_system_60s',    mod='examples/solar_system.py',  cls=None,          dur=60.0, size=DEMO_SIZE),
    dict(name='terrain_flyover_48s', mod='examples/terrain_flyover.py', cls=None,        dur=48.0, size=DEMO_SIZE),
    dict(name='mountain_100s',       mod='examples/mountain.py',       cls=None,          dur=100.0, size=DEMO_SIZE),
    dict(name='tower_defense_60s',   mod='examples/tower_defense.py',  cls=None,          dur=60.0, size=DEMO_SIZE),
    dict(name='godhand_30s',         mod='examples/godhand.py',        cls=None,          dur=30.0, size=DEMO_SIZE),
         # auto 约 28s 通关 -> 录 30s（原先 40s 有 12s 是静止结算画面）
    dict(name='rayhold_25s',         mod='examples/rayhold.py',        cls=None,          dur=25.0, size=DEMO_SIZE,
         flags='auto'),                            # 交互型必须走 auto（否则玩家站桩挨打）
         # auto 约 21s 通关 -> 录 25s（原先 60s 有 39s 是静止结算画面）
    dict(name='boxward_60s',         mod='examples/boxward.py',        cls=None,          dur=60.0, size=DEMO_SIZE,
         flags='auto'),                            # 求解器 auto-play
    dict(name='swarmward_60s',       mod='examples/swarmward.py',      cls=None,          dur=60.0, size=DEMO_SIZE,
         flags='auto'),                            # 闪避 AI 与 headless 验收同源
    dict(name='brickward_60s',       mod='examples/brickward.py',      cls=None,          dur=60.0, size=DEMO_SIZE,
         flags='auto,pace=0.45'),                  # 机关解谜：求解器 auto 连闯 7 关
         # pace 把每步放慢到 0.45s（默认 0.3s 是给剧照/验收用的）——
         # 7 关共约 90 步 + 每关停留 1s ≈ 47s，60s 的片长连着"回到第一关"的循环也录进去
    dict(name='voxel_terrain_36s',   mod='examples/voxel_terrain.py', cls=None,          dur=36.0, size=DEMO_SIZE),
    dict(name='water_waves_40s',     mod='examples/water_waves.py',   cls=None,          dur=40.0, size=DEMO_SIZE),
    dict(name='engine_showcase_24s', mod='examples/engine_showcase.py', cls='ShowcaseApp', dur=24.0, size=DEMO_SIZE),
    dict(name='material_lab_30s',    mod='examples/material_lab.py',  cls='MaterialLabApp', dur=30.0, size=DEMO_SIZE),
    dict(name='nordic_saga_60s',     mod='examples/nordic_saga.py',  cls='NordicSagaApp', dur=60.0, size=DEMO_SIZE,
         audio='assets/nordic_saga_ost_60s.wav',
         ost_fn='world3d.logic.nordic_audio:ensure_ost'),  # 北欧配乐（通用 ost 钩子）
    dict(name='blockworld_30s',      mod='examples/blockworld.py',   cls='BlockWorldApp', dur=30.0,
         size=DEMO_SIZE, flags='cine'),            # 可交互方块世界（脚本化运镜）
    dict(name='kid_a_walker_3d_40s', mod='examples/kid_a_walker_3d.py', cls='KidAApp', dur=40.0,
         size=DEMO_SIZE, flags='go'),              # KID-A WALKER 3D（自动探索运镜）
    dict(name='director_20s',        mod='examples/director.py',      cls='DirectorApp', dur=20.0,
         size=DEMO_SIZE),                          # 可视化导演台：默认演出（闭环环绕 + 灯光）
         # 想看别的运镜就加 shot_json='shots/xxx.shot.json'（导演台 F2 导出的那份）

    # ---- P5-c 攒批补录（成片缺口一次性补齐；时长优先取 demo 自己的 DURATION，
    #      那几个是"绕场一周/扫焦一圈"的循环周期，录满一个周期才无缝）----
    dict(name='csm_shadow_40s',      mod='examples/csm_shadow.py',    cls=None,          dur=40.0, size=DEMO_SIZE),
         # DURATION=40：光照绕场一周
    dict(name='volumetric_fog_60s',  mod='examples/volumetric_fog.py', cls=None,         dur=60.0, size=DEMO_SIZE),
         # DURATION=60：光源绕场一周
    dict(name='dof_focus_24s',       mod='examples/dof_focus.py',     cls=None,          dur=24.0, size=DEMO_SIZE,
         flags='rack'),                            # DURATION=24；`rack` 是 demo 自带的"自动扫焦，
         #                                           适合看成片"模式（对焦 3m ⇄ 90m）——
         #                                           默认模式要手动拖对焦，录出来是静止的
    dict(name='motion_blur_40s',     mod='examples/motion_blur.py',   cls=None,          dur=40.0, size=DEMO_SIZE),
         # DURATION=None（相机运动由模式驱动，不按时钟循环）⇒ 用默认运动模式录 40s；
         # 别加 `stop`，那是"静止相机"的对照模式，录出来什么都没动
    dict(name='frame_graph_36s',     mod='examples/frame_graph.py',   cls=None,          dur=36.0, size=DEMO_SIZE),
         # DURATION=36：默认全效果开启（关效果的对照留给 demo 里按 1~4）
    dict(name='scene_walker_40s',    mod='examples/scene_walker.py',  cls=None,          dur=40.0, size=DEMO_SIZE,
         flags='walk,interact'),                    # 交互型：自动漫步 + 自动互动
    dict(name='pointcloud_edl_30s',  mod='examples/pointcloud_edl.py', cls='PointCloudEDLApp',
         dur=30.0, size=DEMO_SIZE),                 # 下面这 6 条是 EngineApp 宿主，显式给类名
    dict(name='wboit_demo_30s',      mod='examples/wboit_demo.py',    cls='WBOITDemo',
         dur=30.0, size=DEMO_SIZE),
    dict(name='skinned_creature_30s', mod='examples/skinned_creature.py', cls='SkinnedCreatureApp',
         dur=30.0, size=DEMO_SIZE),
    dict(name='gpu_cull_field_30s',  mod='examples/gpu_cull_field.py', cls='GpuCullFieldApp',
         dur=30.0, size=DEMO_SIZE),
    dict(name='voidward_60s',        mod='examples/voidward.py',      cls='VoidwardApp',
         dur=60.0, size=DEMO_SIZE,
         flags='auto'),                            # 贸易脚本自动跑（够跑到资产达标）
    dict(name='pulsefall_45s',       mod='examples/pulsefall.py',     cls='PulsefallApp',
         dur=45.0, size=DEMO_SIZE,
         flags='auto'),                            # 自动演奏一首谱面（BPM 90 / 16 小节 ≈ 42.7s）
]

FFMPEG_CANDIDATES = [
    # 本机（2026-10 盘符变更后）：E 盘为主；G:\repos 的旧路径已失效（G 盘移除），
    # 保留在末尾仅作跨机兼容。
    r'E:\ffmpeg\bin\ffmpeg.exe',
    r'D:\custom\ffmpeg\bin\ffmpeg.exe',
    r'G:\repos\musickit\radio\ffmpeg\ffmpeg.exe',
    r'G:\repos\pluginplayer\vendor\ffmpeg\bin\ffmpeg.exe',
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
    """按文件路径导入模块（examples/ 与 ROOT 都塞进 sys.path）。"""
    spec = importlib.util.spec_from_file_location(f'_rec_{name}', os.path.join(ROOT, rel_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def find_app_class(mod, cls_name):
    """取 App 类：显式指定优先，否则自动找 `DemoApp` **或** `EngineApp` 的子类。

    ⚠ P5-c 实踩：这里原来自动识别**只认 `DemoApp`**（platform 那套），而 `build_app`
    其实支持三类宿主（harness `DemoApp` / engine `EngineApp` / presentation 裸 App）。
    于是 `pointcloud_edl` / `wboit_demo` / `skinned_creature` / `gpu_cull_field` /
    `voidward` / `pulsefall`（全是 `EngineApp` 子类）一律以
    `未找到 DemoApp 子类` **直接退出**，而且**整批录制会随之中断**（后面的条目一条不录）。
    现在两类都认；真找不到时把"本模块里的候选类"列出来，省得再猜。
    """
    import inspect
    if cls_name:
        return getattr(mod, cls_name)
    from world3d.platform.app import DemoApp
    from world3d.engine.app import EngineApp
    bases = (DemoApp, EngineApp)
    cands = []
    for _, obj in vars(mod).items():
        if inspect.isclass(obj) and any(issubclass(obj, b) for b in bases) \
                and obj not in bases:
            cands.append(obj)
    if cands:
        return cands[0]
    raise SystemExit(f'{mod.__name__}: 未找到 DemoApp / EngineApp 子类')


def build_app(entry):
    """按入口构造 App 实例（三类宿主：harness DemoApp / engine EngineApp / presentation 裸 App）。"""
    from world3d.platform.app import DemoApp, parse_args as harness_args
    from world3d.engine.app import EngineApp, parse_args as engine_args

    mod = import_module(entry['mod'], entry['name'])
    cls = find_app_class(mod, entry['cls'])
    w, h = entry['size'] or (None, None)

    if issubclass(cls, DemoApp):
        argv = ['--size', f'{w}x{h}', '--fps', '60']
        if entry.get('flags'):
            argv += ['--flag', entry['flags']]
        # 演出 JSON（导演台）：`--shot-json` 是 demo 私有开关，`_harness.parse_args`
        # 不认识它（还会跟 `--shot` 撞前缀歧义），所以走**类属性**注入——
        # 和 `examples/director.py:main()` 里 `DirectorApp.SHOT_PATH = path` 同一条路。
        if entry.get('shot_json'):
            cls.SHOT_PATH = os.path.abspath(
                os.path.join(ROOT, entry['shot_json']))
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
        # ost_fn 形如 'world3d.logic.xxx_audio:ensure_ost'；缺省沿用 kkrieger 的。
        if entry.get('audio'):
            spec = entry.get('ost_fn', 'world3d.logic.kkrieger_audio:ensure_ost')
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
    from world3d.logic.fusion_audio import (FUSION_SEGMENTS, XFADE, build_fusion_ost,
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
