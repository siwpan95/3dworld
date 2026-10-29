# -*- coding: utf-8 -*-
"""GL 上下文探针（测试专用）：**能不能跑 GL 用例**的唯一判据。

为什么不能只看 `pyglet.window.Window()` 成不成功
------------------------------------------------
GitHub 的 `windows-latest` runner 没有 GPU。GDI 会给出一个 **OpenGL 1.1** 的通用
上下文：`Window()` 建得出来、`gl_info.get_version()` 也读得到，但 `glCreateShader`
**根本没导出**。于是所有"建上下文 → 编译 GLSL"的用例一齐报：

    glCreateShader is not exported by the available OpenGL driver.
    OpenGL 2.0 is required for this functionality.

这句报错长得像"着色器编译失败"，实际含义是"**这台机器不能跑 GL 用例**"。
2026-10-29 之前，`test_p2_render` / `test_gpu_cull` 的 `_win()` 直接建窗口就往下写，
在 CI 上稳定红 5 个用例；而 `test_shaders` 的探测恰好更早失败、走了 SKIP，
所以"同一个环境，一半模块红一半绿"，很容易被误读成真的回归。

判据（两步都过才算可用）
------------------------
1. 建得出隐藏窗口（有上下文）；
2. **真编译一个最小着色器**（有 `glCreateShader` + 能编 330 core）。

只做第 1 步就是上面那个坑。只做第 2 步也不行 —— 没有上下文时 `Shader` 的失败信息
更难读。

用法
----
    from tests._glctx import NoGL, gl_window
    _WIN = None
    def _win():
        global _WIN
        if _WIN is None:
            _WIN = gl_window(160, 120)      # 不可用时抛 NoGL
        return _WIN

测试模块的 `main()` 里把 `NoGL` 单独 catch 成 SKIP（**跳过 ≠ 通过**，要打印原因）。
"""
from __future__ import annotations

_PROBE = {}


class NoGL(RuntimeError):
    """这台机器跑不了 GL 用例（无上下文 / 无 glCreateShader / 编不了 330 core）。"""


def _probe() -> str | None:
    """返回 None 表示可用，否则返回**不可用的原因**（缓存：同一进程只探一次）。"""
    if 'why' in _PROBE:
        return _PROBE['why']
    why = None
    win = None
    try:
        import pyglet
        pyglet.options['dpi_scaling'] = 'real'
        win = pyglet.window.Window(64, 64, visible=False)
    except Exception as e:                                   # noqa: BLE001
        why = f'建不出窗口（{type(e).__name__}: {e}）'
    if why is None:
        try:
            from pyglet.graphics.shader import Shader, ShaderProgram
            vs = ('#version 330 core\n'
                  'in vec3 position;\n'
                  'uniform WindowBlock { mat4 projection; mat4 view; } window;\n'
                  'void main() { gl_Position = window.projection * window.view '
                  '* vec4(position, 1.0); }')
            fs = '#version 330 core\nout vec4 o;\nvoid main() { o = vec4(1.0); }'
            ShaderProgram(Shader(vs, 'vertex'), Shader(fs, 'fragment'))
        except Exception as e:                               # noqa: BLE001
            why = f'编不了 #version 330 core（{type(e).__name__}: {e}）'
    if win is not None:
        try:
            win.close()
        except Exception:                                    # noqa: BLE001
            pass
    _PROBE['why'] = why
    return why


def gl_ready() -> bool:
    """这台机器能否跑 GL 用例。"""
    return _probe() is None


def gl_window(width: int = 160, height: int = 120):
    """拿一个隐藏的 GL 窗口；**不可用时抛 `NoGL`**（调用方按 SKIP 处理）。"""
    why = _probe()
    if why is not None:
        raise NoGL(why)
    import pyglet
    pyglet.options['dpi_scaling'] = 'real'
    return pyglet.window.Window(width, height, visible=False)
