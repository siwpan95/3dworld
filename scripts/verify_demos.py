# -*- coding: utf-8 -*-
"""demos 批量验收：逐个跑冒烟 + 截图，校验退出码与产出物。

用法（在项目根目录）::

    python scripts/verify_demos.py              全部 demo：冒烟 + 截图
    python scripts/verify_demos.py --smoke-only  只跑冒烟（快，约 2s/个）
    python scripts/verify_demos.py --only tunnel,boids   只验指定 demo（前缀匹配）

验收口径（每项都必须过，缺一项就红）：

1. ``--smoke 1.5`` 进程退出码为 0，且 stdout 含 ``EXIT_OK``；
2. ``--shot`` 退出码为 0，且它声明的每一张 PNG **都在本次运行中被重写**
   （mtime 晚于本次启动时间——只看"文件存在"会把上一轮的旧图当成功）；
3. 截图非空（> 2KB：纯色空图通常也有几百字节，明显异常）。

**为什么不用 pytest/子进程池并发**：每个 demo 都要开真实 GL 窗口，
并发会互相抢上下文，串行才是可靠的口径。
"""
import argparse
import importlib.util
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMOS_DIR = os.path.join(ROOT, 'demos')
PYTHON = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')
if not os.path.exists(PYTHON):                       # 非 Windows / 系统 python 兜底
    PYTHON = sys.executable

SKIP = {'_harness.py'}
MIN_PNG_BYTES = 2048


def discover():
    """返回 [(名字, 脚本路径, [(时刻, 输出路径), ...]), ...]（按名字排序）。"""
    out = []
    for fn in sorted(os.listdir(DEMOS_DIR)):
        if not fn.endswith('.py') or fn in SKIP:
            continue
        path = os.path.join(DEMOS_DIR, fn)
        shots = _read_shots(path, fn[:-3])
        out.append((fn[:-3], path, shots))
    return out


def _read_shots(path, name):
    """导入 demo 模块读 SHOTS（模块级只有类定义与常量，import 不会开窗口）。

    注意要同时把项目根与 demos/ 塞进 sys.path：本脚本以 ``python scripts/…``
    启动时 ``sys.path[0]`` 是 scripts/，直接 exec_module 会 ImportError
    （demo 里的 ``from logic.x import y`` 找不到包）。
    """
    saved = list(sys.path)
    sys.path.insert(0, DEMOS_DIR)
    sys.path.insert(0, ROOT)
    try:
        spec = importlib.util.spec_from_file_location(f'_verify_{name}', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, 'SHOTS'):                     # 模块级 SHOTS（kkrieger/main 风格）
            return list(mod.SHOTS or ())
        # demos/ 的约定：SHOTS 是 DemoApp 子类的类属性，从模块里找出那个子类
        import inspect
        from _harness import DemoApp
        for _n, obj in vars(mod).items():
            if inspect.isclass(obj) and issubclass(obj, DemoApp) and obj is not DemoApp:
                return list(getattr(obj, 'SHOTS', ()) or ())
        return []
    except Exception as e:                            # 导入失败不致命，冒烟阶段会暴露
        print(f'  [warn] {name}: 读取 SHOTS 失败: {e}')
        return []
    finally:
        sys.path[:] = saved


def run_demo(name, path, extra):
    t0 = time.time()
    proc = subprocess.run([PYTHON, path] + extra, cwd=ROOT,
                          capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=180)
    return proc, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default='', help='逗号分隔的名字前缀过滤')
    ap.add_argument('--smoke-only', action='store_true', help='跳过截图阶段')
    ap.add_argument('--smoke-sec', type=float, default=1.5)
    args = ap.parse_args()

    demos = discover()
    if args.only:
        keys = [k.strip() for k in args.only.split(',') if k.strip()]
        demos = [d for d in demos if any(k in d[0] for k in keys)]
    if not demos:
        raise SystemExit('没有匹配的 demo')

    print(f'验收 {len(demos)} 个 demo（python={PYTHON}）\n' + '-' * 72)
    rows, failures = [], []
    for name, path, shots in demos:
        # --- 阶段 1：冒烟 ---
        proc, secs = run_demo(name, path, ['--smoke', str(args.smoke_sec)])
        ok_smoke = proc.returncode == 0 and 'EXIT_OK' in proc.stdout
        note = f'{secs:4.1f}s'
        if not ok_smoke:
            failures.append(f'{name}: 冒烟失败 rc={proc.returncode}')
            note += '  SMOKE-FAIL'
        # --- 阶段 2：截图 ---
        ok_shot, n_shot = True, 0
        if not args.smoke_only:
            start = time.time()
            proc2, secs2 = run_demo(name, path, ['--shot'])
            note += f' + {secs2:4.1f}s'
            if proc2.returncode != 0 or 'EXIT_OK' not in proc2.stdout:
                ok_shot = False
                failures.append(f'{name}: 截图失败 rc={proc2.returncode}\n'
                                f'{(proc2.stderr or "").strip()[-400:]}')
            else:
                for _t, rel in shots:
                    p = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
                    n_shot += 1
                    if not os.path.exists(p):
                        ok_shot = False
                        failures.append(f'{name}: 缺截图 {rel}')
                    elif os.path.getmtime(p) < start - 1.0:
                        ok_shot = False
                        failures.append(f'{name}: 截图 {rel} 未在本次运行中重写')
                    elif os.path.getsize(p) < MIN_PNG_BYTES:
                        ok_shot = False
                        failures.append(f'{name}: 截图 {rel} 过小（疑似空图）')
        rows.append((name, len(shots), 'ok' if ok_smoke else 'FAIL',
                     'ok' if ok_shot else 'FAIL', note))

    print(f'{"demo":<22}{"shots":>6}  {"smoke":<6}{"shot":<6}time')
    for name, n, s1, s2, note in rows:
        print(f'{name:<22}{n:>6}  {s1:<6}{s2:<6}{note}')
    print('-' * 72)
    if failures:
        print(f'FAILED ({len(failures)}):')
        for f in failures:
            print('  -', f)
        return 1
    print(f'ALL_DEMOS_OK ({len(rows)} 个 demo)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
