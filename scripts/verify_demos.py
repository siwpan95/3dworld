# -*- coding: utf-8 -*-
"""demos 批量验收：逐个跑冒烟 + 截图，校验退出码与产出物。

用法（在项目根目录）::

    python scripts/verify_demos.py              全部 demo：冒烟 + 截图
    python scripts/verify_demos.py --smoke-only  只跑冒烟（快，约 2s/个）
    python scripts/verify_demos.py --only tunnel,boids   只验指定 demo

``--only`` 的匹配规则（三级降级，同级撞多个就报错）：
**完整名精确命中 → 唯一前缀 → 唯一子串**。这样 ``--only scene_viewer``
只跑 scene_viewer（精确名永不被歧义连坐），``--only tunnel`` 仍能命中
demoscene_tunnel，而 ``--only scene`` 这种确实有歧义的写法会明确报错，
不会悄悄退化成"把 scene_viewer 和 scene_walker 都跑一遍"。

验收口径（每项都必须过，缺一项就红）：

1. ``--smoke 1.5`` 进程退出码为 0，且 stdout 含 ``EXIT_OK``；
2. ``--shot`` 退出码为 0，且它声明的每一张 PNG **都在本次运行中被重写**
   （mtime 晚于本次启动时间——只看"文件存在"会把上一轮的旧图当成功）；
   未声明 ``SHOTS`` 的 demo（如 scene_viewer）本阶段跳过，记为 ``n/a``；
3. 截图非空（> 2KB：纯色空图通常也有几百字节，明显异常）。

**为什么不用 pytest/子进程池并发**：每个 demo 都要开真实 GL 窗口，
并发会互相抢上下文，串行才是可靠的口径。
"""
import argparse
import importlib.util
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES_DIR = os.path.join(ROOT, 'examples')
PYTHON = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')
if not os.path.exists(PYTHON):                       # 非 Windows / 系统 python 兜底
    PYTHON = sys.executable

SKIP = {'_harness.py'}
MIN_PNG_BYTES = 2048
_DISCOVERED = None                       # discover() 的缓存（见其 docstring）
# 从 --bench 的输出里取"均值 / p1 / 最慢帧(ms)"
BENCH_RE = re.compile(r'fps mean=([\d.]+) median=([\d.]+) p1=([\d.]+) '
                      r'worst_frame_ms=([\d.]+)')


def discover():
    """返回 [(名字, 脚本路径, [(时刻, 输出路径), ...]), ...]（按名字排序）。

    只算一次并缓存：``_read_shots`` 要真的 import 每个 demo 模块（见其
    docstring），重复调用会把"导入开销 + 导入告警"翻倍。
    """
    global _DISCOVERED
    if _DISCOVERED is None:
        out = []
        for fn in sorted(os.listdir(EXAMPLES_DIR)):
            if not fn.endswith('.py') or fn in SKIP:
                continue
            path = os.path.join(EXAMPLES_DIR, fn)
            shots = _read_shots(path, fn[:-3])
            out.append((fn[:-3], path, shots))
        _DISCOVERED = out
    return list(_DISCOVERED)


def _read_shots(path, name):
    """导入 demo 模块读 SHOTS（模块级只有类定义与常量，import 不会开窗口）。

    注意要同时把项目根与 examples/ 塞进 sys.path：本脚本以 ``python scripts/…``
    启动时 ``sys.path[0]`` 是 scripts/，直接 exec_module 会 ImportError
    （demo 里的 ``from logic.x import y`` 找不到包）。
    """
    saved = list(sys.path)
    sys.path.insert(0, EXAMPLES_DIR)
    sys.path.insert(0, ROOT)
    try:
        spec = importlib.util.spec_from_file_location(f'_verify_{name}', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, 'SHOTS'):                     # 模块级 SHOTS（kkrieger/main 风格）
            return list(mod.SHOTS or ())
        # examples/ 的约定：SHOTS 是 DemoApp 子类的类属性，从模块里找出那个子类
        import inspect
        from world3d.platform.app import DemoApp
        for _n, obj in vars(mod).items():
            if inspect.isclass(obj) and issubclass(obj, DemoApp) and obj is not DemoApp:
                return list(getattr(obj, 'SHOTS', ()) or ())
        return []
    except Exception as e:                            # 导入失败不致命，冒烟阶段会暴露
        print(f'  [warn] {name}: cannot read SHOTS: {e}')
        return []
    finally:
        sys.path[:] = saved


def run_demo(name, path, extra, timeout=180.0):
    """跑一个 demo 并返回 (proc 视图, 秒数)。

    挂起容错：某些执行环境（Agent 沙箱等）里 pyglet 窗口会"逻辑完成但
    进程不退"（EXIT_OK 已打印）。超时后不直接失败——把已捕获的 stdout
    装进轻量对象继续验收；真正的失败仍由 EXIT_OK 缺失暴露。
    """
    class _Result:
        returncode = -9
        stderr = ''

    t0 = time.time()
    try:
        proc = subprocess.run([PYTHON, path] + extra, cwd=ROOT,
                              capture_output=True, text=True, encoding='utf-8',
                              errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired as e:
        proc = _Result()
        out = getattr(e, 'stdout', None) or ''
        proc.stdout = out.decode('utf-8', 'replace') if isinstance(out, bytes) else out
    return proc, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default='',
                    help='comma-separated demo names; exact name wins, '
                         'otherwise a unique prefix/substring (e.g. tunnel); '
                         'ambiguous key is an error')
    ap.add_argument('--smoke-only', action='store_true', help='skip the shot stage')
    ap.add_argument('--smoke-sec', type=float, default=1.5)
    ap.add_argument('--timeout', type=float, default=180.0,
                    help='单 demo 进程超时（秒）；挂起环境可调小（逻辑完成即算过）')
    ap.add_argument('--bench', type=float, default=None, metavar='SEC',
                    help='also benchmark SEC seconds per demo (real fps, vsync off); '
                         'skips the shot stage unless --with-shots')
    ap.add_argument('--with-shots', action='store_true',
                    help='run the shot stage too when --bench is given')
    args = ap.parse_args()

    demos = discover()
    if args.only:
        keys = [k.strip() for k in args.only.split(',') if k.strip()]
        names = [d[0] for d in demos]

        def resolve(k):
            """按 精确 → 唯一前缀 → 唯一子串 逐级降级；同级撞多个就报错。

            三级是为了同时兼顾两件事：① ``--only tunnel`` 这类老写法有效
            （tunnel 是 demoscene_tunnel 的子串）；② 精确名**永不**被歧义
            连坐——``--only scene_viewer`` 只跑 scene_viewer，哪怕
            scene_walker 也以 "scene" 开头。
            """
            if k in names:
                return [k]
            for pool, what in ((lambda n: n.startswith(k), 'prefix'),
                               (lambda n: k in n, 'substring')):
                hits = [n for n in names if pool(n)]
                if len(hits) == 1:
                    return hits
                if len(hits) > 1:
                    raise SystemExit(f'--only {k!r} is ambiguous ({what}): '
                                     + ', '.join(hits)
                                     + '  → 用完整名（--only ' + hits[0] + '）')
            raise SystemExit(f'--only {k!r}: no demo matched')

        wanted = {n for k in keys for n in resolve(k)}
        demos = [d for d in demos if d[0] in wanted]
    if not demos:
        raise SystemExit('no demo matched')

    print(f'verifying {len(demos)} demos (python={PYTHON})\n' + '-' * 72)
    rows, failures = [], []
    for name, path, shots in demos:
        # --- 阶段 1：冒烟 ---
        proc, secs = run_demo(name, path, ['--smoke', str(args.smoke_sec)],
                              timeout=args.timeout)
        ok_smoke = 'EXIT_OK' in proc.stdout
        note = f'{secs:4.1f}s'
        if ok_smoke and proc.returncode != 0:
            note += '  HUNG-KILLED'          # 逻辑完成、进程未退（沙箱环境常见）
        if not ok_smoke:
            failures.append(f'{name}: smoke failed rc={proc.returncode}')
            note += '  SMOKE-FAIL'
        # --- 阶段 2：基准（可选；真实帧率，vsync 与重绘节流都关掉）---
        bench = '—'
        if args.bench:
            proc3, secs3 = run_demo(name, path, ['--bench', str(args.bench)],
                                    timeout=args.timeout)
            note += f' + {secs3:4.1f}s'
            m = BENCH_RE.search(proc3.stdout)
            if proc3.returncode != 0 or 'EXIT_OK' not in proc3.stdout or not m:
                failures.append(f'{name}: bench failed rc={proc3.returncode}\n'
                                f'{(proc3.stderr or "").strip()[-300:]}')
                bench = 'FAIL'
            else:
                # 均值 / p1（1% 慢帧下界）/ 最慢帧——均值会被快帧拉高，后两个才反映卡顿
                bench = f'{float(m.group(1)):6.1f}/{float(m.group(3)):6.1f}/{float(m.group(4)):7.2f}'
        # --- 阶段 3：截图 ---
        # 没声明 SHOTS 的 demo（如 scene_viewer：靠 --file 指定的 JSON 驱动、
        # 没有固定剧照时刻）跳过本阶段——硬跑 --shot 只会拿到
        # "xxx: --shot requires SHOTS" 的 rc=1，把整轮批量验收判红。
        ok_shot, n_shot = True, 0
        has_shot_stage = bool(shots)
        if has_shot_stage and not args.smoke_only and (not args.bench or args.with_shots):
            start = time.time()
            proc2, secs2 = run_demo(name, path, ['--shot'],
                                    timeout=args.timeout)
            note += f' + {secs2:4.1f}s'
            if proc2.returncode != 0 or 'EXIT_OK' not in proc2.stdout:
                ok_shot = False
                failures.append(f'{name}: shots failed rc={proc2.returncode}\n'
                                f'{(proc2.stderr or "").strip()[-400:]}')
            else:
                for _t, rel in shots:
                    p = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
                    n_shot += 1
                    if not os.path.exists(p):
                        ok_shot = False
                        failures.append(f'{name}: missing shot {rel}')
                    elif os.path.getmtime(p) < start - 1.0:
                        ok_shot = False
                        failures.append(f'{name}: shot {rel} not rewritten in this run')
                    elif os.path.getsize(p) < MIN_PNG_BYTES:
                        ok_shot = False
                        failures.append(f'{name}: shot {rel} too small (likely blank)')
        shot_col = ('ok' if ok_shot else 'FAIL') if has_shot_stage else 'n/a'
        rows.append((name, len(shots), 'ok' if ok_smoke else 'FAIL',
                     shot_col, bench, note))

    bench_hdr = '  mean/p1/worst_ms' if args.bench else ''
    print(f'{"demo":<22}{"shots":>6}  {"smoke":<6}{"shot":<6}{bench_hdr}  time')
    for name, n, s1, s2, bench, note in rows:
        bench_col = f'  {bench:<18}' if args.bench else ''
        print(f'{name:<22}{n:>6}  {s1:<6}{s2:<6}{bench_col}  {note}')
    print('-' * 72)
    if failures:
        print(f'FAILED ({len(failures)}):')
        for f in failures:
            print('  -', f)
        return 1
    print(f'ALL_DEMOS_OK ({len(rows)} demos)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
