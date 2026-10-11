# -*- coding: utf-8 -*-
"""P2-1/2/4 渲染能力测试（PCSS / WBOIT / 点云+EDL）。

覆盖：CPU 参考实现的数值、生成器可复现性、着色器可编译、能力探测。

运行::

    python -m tests.test_p2_render
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pyglet                                                   # noqa: E402
pyglet.options['debug_gl'] = False

from engine.pointcloud import (EDL_NEIGHBORS, cpu_edl,           # noqa: E402
                               edl_glsl, generate_helix_cloud,
                               generate_terrain_cloud)
from engine.soft_shadow import (DEFAULT_SAMPLES, cpu_pcss,      # noqa: E402
                                pcss_glsl, poisson_disk)

_WIN = None


def _win():
    global _WIN
    if _WIN is None:
        _WIN = pyglet.window.Window(160, 120, visible=False)
    return _WIN


# ================================================================ P2-1
def test_poisson_disk_deterministic_and_bounded():
    """Poisson 盘必须**确定性**（时间上稳定）且落在单位圆内。"""
    a = poisson_disk(16)
    b = poisson_disk(16)
    assert a == b, '采样点必须可复现（否则阴影会抖）'
    assert len(a) == DEFAULT_SAMPLES
    for x, y in a:
        assert math.hypot(x, y) <= 1.0 + 1e-9, f'采样点越出单位圆: {(x, y)}'
    # 分布检查：半径应大致均匀铺开（最小半径不应太小）
    rs = sorted(math.hypot(x, y) for x, y in a)
    assert rs[0] < 0.35 and rs[-1] > 0.9, rs
    print(f'  ok: Poisson 盘 {len(a)} 点确定且界内（r∈[{rs[0]:.2f},{rs[-1]:.2f}]）')


def test_pcss_cpu_reference():
    """PCSS 三步：无遮挡 -> (-1, 0)；有遮挡 -> 半影随深度差/光源尺寸递增。"""
    assert cpu_pcss(0.5, [0.6, 0.7], 2.0, 10.0) == (-1.0, 0.0), '全在更远处 = 无遮挡'
    zb, pen = cpu_pcss(0.5, [0.4, 0.4], 1.0, 10.0)
    assert abs(zb - 0.4) < 1e-9, zb
    exp = (0.5 - 0.4) / 0.4 * 1.0
    assert abs(pen - exp) < 1e-9, (pen, exp)
    # 深度差越大 -> 半影越宽
    _, pen2 = cpu_pcss(0.8, [0.4, 0.4], 1.0, 10.0)
    assert pen2 > pen, '接收者更深应产生更宽半影'
    # 光源越大 -> 半影越宽
    _, pen3 = cpu_pcss(0.5, [0.4, 0.4], 3.0, 10.0)
    assert abs(pen3 - 3 * exp) < 1e-9, pen3
    # 上限被钳制
    _, pen4 = cpu_pcss(0.99, [0.01, 0.01], 10.0, 2.0)
    assert pen4 == 2.0, pen4
    print(f'  ok: PCSS CPU 参考（zb={zb} penumbra={pen:.4f}，随深度差/光源尺寸单调）')


def test_pcss_and_wboit_glsl_compile():
    _win()
    from pyglet.graphics.shader import Shader, ShaderProgram
    from engine.shaders import get
    from engine.shaders import FULLSCREEN_VERT
    # PCSS 片段本身是"片段注入"，用一个最小宿主编译它
    host = ('#version 330 core\n'
            'uniform sampler2D u_shadow;\n'
            'uniform float u_texel, u_bias, u_light_size, u_search_texels, u_max_penumbra;\n'
            'uniform int u_pcss;\n'
            'out vec4 c;\n' + pcss_glsl() + '''
void main() { c = vec4(vec3(pcss_factor(vec2(0.5), 0.5, 0.001)), 1.0); }''')
    ShaderProgram(Shader('#version 330 core\nin vec3 position;\nvoid main(){gl_Position=vec4(position,1.0);}',
                         'vertex'), Shader(host, 'fragment'))
    assert get('wboit').id and get('wboit_composite').id
    print('  ok: PCSS 片段 + wboit/wboit_composite 程序均编译通过')


# ================================================================ P2-2
def test_wboit_support_and_chain():
    """WBOIT：glBlendFunci 可用 + MRT 双附件可建。"""
    _win()
    from engine.oit import WBOIT, supported
    assert supported(), 'glBlendFunci 不可用（WBOIT 无法运行）'
    oit = WBOIT(64, 48)
    assert len(oit.rt.color) == 2, 'WBOIT 需要 2 张 MRT 附件'
    oit.begin()
    import pyglet.gl as G
    assert G.glGetError() == 0, 'oit.begin() 不应产生 GL 错误'
    oit.end()
    assert G.glGetError() == 0
    oit.delete()
    print('  ok: WBOIT supported=True，MRT=2 附件，begin/end 无 GL 错误')


# ================================================================ P2-4
def test_pointcloud_generators():
    """点云生成器：点数、范围、可复现性。"""
    p1, c1, s1 = generate_terrain_cloud(n=4000, half=8.0, seed=1)
    p2, c2, s2 = generate_terrain_cloud(n=4000, half=8.0, seed=1)
    assert p1 == p2 and c1 == c2 and s1 == s2, '同种子必须可复现'
    n = len(p1) // 3
    # 地形点云按 isqrt(n)² 铺网格 -> 实际点数略小于请求值
    assert abs(n - 4000) < 400, n
    assert len(c1) == n * 3 and len(s1) == n
    assert all(abs(v) <= 8.4 for v in p1[0::3]), 'X 应在 half 邻域内'
    assert all(1.0 <= s <= 3.0 for s in s1), '点大小应在合理区间'
    ph, ch, sh = generate_helix_cloud(n=6000, seed=3)
    nh = len(ph) // 3
    assert abs(nh - 12000) < 100, nh        # 双螺旋 -> 两分支，点数≈2n
    # 双螺旋 -> 点数应是 2×（两分支）
    print(f'  ok: 生成器可复现（terrain {n} 点 / helix {nh} 点，属性长度自洽）')


def test_edl_cpu_reference():
    """EDL：邻域更近才贡献；无更近邻域 -> 系数 1（不变暗）；越强 k -> 越暗。"""
    assert abs(cpu_edl(0.5, [0.6, 0.7], 20.0) - 1.0) < 1e-12, '邻域更远不应贡献'
    z, nb = 0.5, [0.4] * 8                       # 每个邻域贡献 (0.5-0.4)/0.5 = 0.2
    e1 = cpu_edl(z, nb, 3.0)
    e2 = cpu_edl(z, nb, 6.0)
    exp = math.exp(-3.0 * 8 * 0.2)
    assert abs(e1 - exp) < 1e-9, (e1, exp)
    assert e2 < e1 < 1.0, 'k 越大越暗'
    assert len(EDL_NEIGHBORS) == 8, 'EDL 用 8 邻域'
    print(f'  ok: EDL CPU 参考（无近邻=1.0；8 邻域 e1={e1:.4f} > e2={e2:.4f}）')


def test_edl_glsl_compile():
    _win()
    from pyglet.graphics.shader import Shader, ShaderProgram
    from engine.shaders import FULLSCREEN_VERT
    p = ShaderProgram(Shader(FULLSCREEN_VERT, 'vertex'), Shader(edl_glsl(), 'fragment'))
    assert p.id
    print(f'  ok: EDL 片段编译通过（id={p.id}）')


TESTS = [
    test_poisson_disk_deterministic_and_bounded,
    test_pcss_cpu_reference,
    test_pcss_and_wboit_glsl_compile,
    test_wboit_support_and_chain,
    test_pointcloud_generators,
    test_edl_cpu_reference,
    test_edl_glsl_compile,
]


def main():
    print('=' * 60)
    print('P2-1/2/4 渲染能力测试（PCSS / WBOIT / 点云+EDL）')
    print('=' * 60)
    failed = []
    for t in TESTS:
        print(t.__name__)
        try:
            t()
        except Exception as e:
            failed.append((t.__name__, e))
            import traceback
            traceback.print_exc()
            print(f'  FAIL: {e}')
    print('-' * 60)
    for name, e in failed:
        print(f'FAILED {name}: {e}')
    if failed:
        print(f'{len(failed)}/{len(TESTS)} FAILED')
        return 1
    print('ALL_P2_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
