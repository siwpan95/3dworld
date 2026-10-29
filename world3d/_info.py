# -*- coding: utf-8 -*-
"""``world3d-info``：安装/环境自检（库级入口，**任何安装方式都可用**）。

回答四个问题：**版本对不对、依赖齐不齐、GL 能力够不够、配置树在哪**。
它是 wheel 安装后的"冒烟"手段，也是排障第一站。

用法::

    world3d-info            # 人类可读
    world3d-info --json     # 机器可读（CI/脚本用）
"""
from __future__ import annotations

import json
import os
import sys

__all__ = ['collect', 'main']


def _try(fn, default=None):
    try:
        return fn()
    except Exception as e:                    # 自检工具不该因单项失败而整体炸
        return f'<{type(e).__name__}: {e}>'


def collect() -> dict:
    """收集环境信息（全部带兜底，缺 GL/缺可选件都能返回）。"""
    import world3d

    info = {
        'world3d': getattr(world3d, '__version__', '?'),
        'python': sys.version.split()[0],
        'executable': sys.executable,
        'prefix': sys.prefix,
    }
    # ---- 依赖 ----
    # 用发行版元数据取版本：不是每个包都暴露 __version__
    # （实测 watchdog 4.x 就没有，`import watchdog; watchdog.__version__` 会 AttributeError）
    # numpy 2026-10-29 补进来：9 个随包分发的模块在顶层 import 它（此前一直漏声明，
    # 导致 `pip install 3dworld` 之后那些功能一 import 就 ModuleNotFoundError）。
    # 自检漏报它的话，"依赖齐不齐"这件事就只能靠人记得——正是这次踩的坑。
    from importlib.metadata import version as _v
    deps = {}
    for dist in ('pyglet', 'PyYAML', 'watchdog', 'numpy'):
        deps[dist] = _try(lambda d=dist: _v(d))
    info['dependencies'] = deps
    info['cordis'] = _try(lambda: __import__('cordis').__file__)
    # ---- GL 能力（pyglet 需窗口上下文；无上下文时给字符串兜底）----
    def _gl():
        import pyglet
        w = pyglet.window.Window(64, 64, visible=False)
        ver = pyglet.gl.gl_info.get_version()
        rend = pyglet.gl.gl_info.get_renderer()
        w.close()
        return {'version': ver, 'renderer': rend}
    info['gl'] = _try(_gl)
    info['shaders'] = _try(lambda: len(__import__('world3d.engine.shaders', fromlist=['SOURCES']).SOURCES))
    info['services'] = _try(lambda: len(__import__('world3d.host.registry', fromlist=['SERVICES']).SERVICES))
    # ---- 配置树位置（P1：仓库资产，不随 wheel 分发）----
    def _configs():
        from world3d.host.tree import CONFIG_DIR
        return {'dir': CONFIG_DIR,
                'exists': os.path.isdir(CONFIG_DIR),
                'count': len([f for f in os.listdir(CONFIG_DIR) if f.endswith('.yml')])
                if os.path.isdir(CONFIG_DIR) else 0}
    info['configs'] = _try(_configs)
    return info


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    info = collect()
    if '--json' in argv:
        print(json.dumps(info, ensure_ascii=False, indent=1))
        return 0
    print(f"3dworld(world3d) {info['world3d']}   python {info['python']}")
    print(f"  解释器 {info['executable']}")
    print(f"  依赖   " + '  '.join(f'{k}={v}' for k, v in info['dependencies'].items()))
    print(f"  cordis {info['cordis']}")
    print(f"  GL     {info['gl']}")
    print(f"  着色器 {info['shaders']} 个   服务表 {info['services']} 条")
    cfg = info['configs']
    if isinstance(cfg, dict):
        flag = '✓' if cfg['exists'] else '✗ 未找到（wheel 安装不含 configs/，请设 WORLD3D_CONFIGS 或从仓库运行）'
        print(f"  配置树 {cfg['dir']}  {cfg['count']} 棵  {flag}")
    else:
        print(f"  配置树 {cfg}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
