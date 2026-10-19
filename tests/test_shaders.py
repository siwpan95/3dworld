# -*- coding: utf-8 -*-
"""着色器库测试套件（2026-10-19 能力复盘建议 1：着色器全量编译门禁）。

运行::

    python -m tests.test_shaders

为什么值得单独一个模块：着色器是**字符串**，语法错误只在编译时暴露 ——
此前的覆盖是间接的（test_engine / test_gpu_cull 的 GL 用例碰巧编译到几个），
新增一对着色器忘了验证、或手滑改坏任何一对，都没有任何检查会红。
本模块把 **SOURCES 全部 + COMPUTE_SOURCES 全部** 逐个编译一遍。

约定（与 test_engine 一致）：GL 用例拿不到上下文时打印 SKIP 而不是失败
（CI 无显示器的环境也能过）；静态核对（注册表 / uniform 契约）无条件跑。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.engine import shaders


# ------------------------------------------------------------ 静态核对 --
def test_registry_sanity():
    """注册表静态核对（无 GL）：条目结构合法、版本头在首行、0.2.0 三项在册。

    版本头不写死 330：``gpu_driven`` 用 SSBO（``layout(std430)``）合法需要
    ``#version 430 core``。GLSL 的真不变量是**首行必须是 #version 指令**，
    断言跟着不变量走，不跟着「大多数条目长什么样」走。
    """
    assert shaders.SOURCES, 'SOURCES 不该为空'
    for name, pair in shaders.SOURCES.items():
        assert isinstance(pair, tuple) and len(pair) == 2, \
            f'{name}: 必须是 (vert, frag) 二元组'
        for stage, src in zip(('vert', 'frag'), pair):
            first = src.splitlines()[0] if src.splitlines() else ''
            assert re.match(r'#version\s+\d+\s+core', first), \
                f'{name}.{stage}: 首行必须是 #version 指令，实际 {first!r}'
    for name, src in shaders.COMPUTE_SOURCES.items():
        first = src.splitlines()[0] if src.splitlines() else ''
        assert re.match(r'#version\s+\d+\s+core', first), \
            f'compute.{name}: 首行必须是 #version 指令，实际 {first!r}'
    for k in ('particles', 'tunnel', 'raymarch'):
        assert k in shaders.SOURCES, f'0.2.0 上提的 {k} 不在册'
    print(f'  ok: 注册表 {len(shaders.SOURCES)} 对图形 + '
          f'{len(shaders.COMPUTE_SOURCES)} 个 compute，结构与版本头全部合法')


def test_particle_uniforms_declared():
    """粒子着色器契约（无 GL）：set_uniforms 写的每个 uniform 都在着色器里声明。

    pyglet 对**不存在**的 uniform 赋值不报错（静默无效）—— 所以「代码改了
    uniform 名、着色器没跟上」这类不一致只有到运行时才表现为「参数调了没反应」。
    这条把契约钉死：两边必须互为子集。
    """
    from world3d.engine.particles import ParticleSystem
    import inspect
    src = inspect.getsource(ParticleSystem.set_uniforms)
    set_keys = set(re.findall(r"prog\[['\"](\w+)['\"]\]", src))
    assert set_keys, 'set_uniforms 里没找到 prog[...] 赋值（写法变了？）'
    declared = set(re.findall(r'uniform\s+\w+\s+(\w+)\s*;', shaders.PARTICLES_VERT))
    missing = sorted(set_keys - declared)
    assert not missing, f'set_uniforms 写了但着色器没声明：{missing}'
    print(f'  ok: 粒子 {len(set_keys)} 个 uniform 与着色器声明一致'
          f'（{sorted(set_keys)}）')


def test_particle_preset_branches():
    """粒子预设契约（无 GL）：PRESETS 的数量 ↔ 着色器分支数一致。

    着色器是 ``if (u_preset == 0) … else if (u_preset == 1) … else`` 的三分支
    结构：Python 侧加第 4 个预设而着色器没加分支时，新预设会**静默落进 else**
    （渲染成湍流云）—— 这类不一致只有看图才能发现。显式分支数必须等于
    ``len(PRESETS) - 1``（最后一个预设走 else）。
    """
    from world3d.engine.particles import PRESETS
    branches = re.findall(r'u_preset == \d+', shaders.PARTICLES_VERT)
    assert len(branches) == len(PRESETS) - 1, \
        f'PRESETS 有 {len(PRESETS)} 个，着色器显式分支 {len(branches)} 个' \
        f'（应为 {len(PRESETS) - 1}，末个走 else）'
    print(f'  ok: 粒子 {len(PRESETS)} 个预设 ↔ 着色器 {len(branches)} 显式分支 + else')


# --------------------------------------------------------------- GL 用例 --
def _gl_available():
    try:
        import pyglet
        pyglet.options['dpi_scaling'] = 'real'
        return pyglet.window.Window(64, 64, visible=False)
    except Exception as e:
        print(f'  SKIP: 无 GL 上下文（{e}）')
        return None


def test_all_shaders_compile():
    """全量编译（GL）：SOURCES 每一对都真编译一遍。"""
    w = _gl_available()
    if w is None:
        return
    try:
        n = 0
        for name in sorted(shaders.SOURCES):
            prog = shaders.build(name)
            assert prog is not None, f'{name}: 编译返回 None'
            n += 1
        print(f'  ok: {n} 对图形着色器全部编译通过')
    finally:
        w.close()


def test_compute_shader_compiles():
    """全量编译（GL）：COMPUTE_SOURCES 每一个都真编译一遍。"""
    w = _gl_available()
    if w is None:
        return
    try:
        n = 0
        for name in sorted(shaders.COMPUTE_SOURCES):
            prog = shaders.build_compute(name)
            assert prog is not None, f'compute.{name}: 编译返回 None'
            n += 1
        print(f'  ok: {n} 个 compute 着色器全部编译通过')
    finally:
        w.close()


def main() -> int:
    tests = [
        test_registry_sanity,
        test_particle_uniforms_declared,
        test_particle_preset_branches,
        test_all_shaders_compile,
        test_compute_shader_compiles,
    ]
    failed = []
    for t in tests:
        print(t.__name__)
        try:
            t()
        except Exception as e:
            failed.append((t.__name__, e))
            import traceback
            traceback.print_exc()
    print('=' * 60)
    print(f'[test_shaders] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for n, e in failed:
            print(f'  - {n}: {e}')
        print('ALL_SHADERS_TESTS_FAILED')
        return 1
    print('ALL_SHADERS_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
