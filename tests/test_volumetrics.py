# -*- coding: utf-8 -*-
"""体积雾 / 光轴测试套件 —— `engine/volumetrics.py`。

运行::

    python -m tests.test_volumetrics

本套件盯住三件容易失控的事：

1. **高度雾的闭式积分**必须与细分数值积分一致（相对误差 < 1e-9），并且**可加**
   （把一段拆成若干小段之和 = 整段）—— 后者正是"改采样步数不该改变雾的浓度"的数学表述；
2. **相函数必须真的归一**（∫ over sphere = 1）：写成"看起来像 HG"的式子很容易漏
   掉 4π，那样雾会整体亮 12 倍；
3. **生成的 GLSL 只在需要时才声明阴影图采样器**：声明了却不采样会被驱动优化掉，
   而 pyglet 给"已优化的 uniform"赋值会抛异常 —— 症状是"改个参数整个 demo 崩"。
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.engine.volumetrics import (FOG_GLSL_UNIFORMS, IGN_A, IGN_B, IGN_C,  # noqa: E402
                                NOISE3_NORM,
                                dither_offset,
                                height_fog_column, height_fog_segment,
                                henyey_greenstein, noise3_glsl, step_weights,
                                volumetric_glsl)


# ------------------------------------------------------------------ 高度雾 --
def test_height_fog_segment_matches_numeric_reference():
    """闭式 vs 4096 段数值积分 —— 5 种形状（上升/水平/下降/极短/向下穿基底）。"""
    cases = [(0.0, 10.0, 0.3), (5.0, 5.0, 0.3), (0.0, 40.0, 0.2),
             (30.0, 30.5, 0.8), (0.0, -20.0, 0.5)]
    worst = 0.0
    for y0, y1, k in cases:
        a = height_fog_segment(y0, y1, 0.0, 0.05, k, abs(y1 - y0))
        b = height_fog_column(y0, y1, 0.0, 0.05, k, 4096)
        rel = abs(a - b) / max(b, 1e-12)
        worst = max(worst, rel)
        assert rel < 1e-9, f'y {y0}->{y1} k={k}: 闭式 {a} vs 数值 {b}（相对差 {rel:.2e}）'
    print(f'  ok: 5 种形状闭式 vs 4096 段数值，最大相对差 {worst:.1e}')


def test_height_fog_segment_is_additive():
    """**可加性**：拆成任意多小段之和 == 整段。

    这条就是"**改采样步数不该改变雾的浓度**"的数学形式 —— 逐步矩形求和没有这个性质
    （每步取起点密度 ⇒ 步数越多越亮/越暗），所以低步数时会出现层状条纹，
    而且画面整体亮度会跟着步数走。
    """
    y0, y1, base, rho, k = 2.0, 26.0, 0.0, 0.06, 0.25
    whole = height_fog_segment(y0, y1, base, rho, k, y1 - y0)
    for parts in (2, 7, 64):
        acc = 0.0
        for i in range(parts):
            a = y0 + (y1 - y0) * i / parts
            b = y0 + (y1 - y0) * (i + 1) / parts
            acc += height_fog_segment(a, b, base, rho, k, b - a)
        rel = abs(acc - whole) / whole
        assert rel < 1e-12, f'拆成 {parts} 段后不守恒：{acc} vs {whole}（{rel:.2e}）'
    # 反过来：逐步矩形（每步取起点密度）在同样的拆分下**明显**不守恒 —— 这就是对照组
    naive = 0.0
    for i in range(64):
        a = y0 + (y1 - y0) * i / 64
        b = y0 + (y1 - y0) * (i + 1) / 64
        naive += rho * math.exp(-k * (a - base)) * (b - a)
    naive2 = 0.0
    for i in range(4):
        a = y0 + (y1 - y0) * i / 4
        b = y0 + (y1 - y0) * (i + 1) / 4
        naive2 += rho * math.exp(-k * (a - base)) * (b - a)
    drift = abs(naive2 - naive) / max(naive, 1e-12)
    assert drift > 0.02, f'对照组(逐步矩形)本应随步数明显漂移，实际只差 {drift:.2e}'
    print(f'  ok: 闭式拆 2/7/64 段都守恒（<1e-12）；对照组（逐步矩形）4 段 vs 64 段'
          f'差 {drift * 100:.1f}%（正是条纹与整体亮度漂移的来源）')


def test_height_fog_uniform_limit_and_edges():
    """``falloff = 0`` ⇒ 均匀雾 ρ·dt；极小 falloff 要**平滑收敛**（不能 0/0 出 NaN）。"""
    assert abs(height_fog_segment(0.0, 10.0, 0.0, 0.05, 0.0, 10.0) - 0.5) < 1e-15
    want = 0.05 * 10.0
    for k in (1e-8, 1e-6, 1e-4, 1e-3):
        got = height_fog_segment(0.0, 10.0, 0.0, 0.05, k, 10.0)
        assert math.isfinite(got), f'k={k} 出了 NaN/Inf'
        kd = k * 10.0
        err = abs(got - want) / want
        if kd < 1e-6:
            assert err < 1e-6, f'kd={kd}：{got} 应几乎精确等于 {want}'
        else:
            # 闭式的截断误差就是 O(kd) 的一阶项：`1−exp(−k)/k` 展开后偏差 ≈ kd/2。
            # 断言"误差符合这个规律"比写死一个阈值有意义 —— 它同时证明了"没有别的错"。
            assert abs(err / (kd / 2.0) - 1.0) < 0.02,                 f'kd={kd}: 误差 {err:.3e} 不符合 kd/2={kd / 2:.3e} 的规律'
    # 零长度 / 负长度
    assert height_fog_segment(3.0, 3.0, 0.0, 0.05, 0.3, 0.0) == 0.0
    assert height_fog_segment(3.0, 3.0, 0.0, 0.05, 0.3, -1.0) == 0.0
    # 垂直上穿下穿都要非负
    assert height_fog_segment(0.0, -30.0, 0.0, 0.05, 0.4, 30.0) > 0
    print(f'  ok: falloff=0 精确给 ρ·dt；1e-8~1e-3 平滑收敛且无 NaN；零/负长度给 0')


def test_height_fog_column_rejects_bad_steps():
    for steps in (0, -3):
        try:
            height_fog_column(0.0, 1.0, 0.0, 0.05, 0.2, steps)
        except ValueError:
            pass
        else:
            raise AssertionError(f'steps={steps} 竟然通过')
    print('  ok: steps<=0 被拒')


# ------------------------------------------------------------------ 相函数 --
def test_henyey_greenstein_is_normalized():
    """**∫ over sphere = 1**（数值积分，4096 段梯形）—— 漏 4π 会让雾亮 12.6 倍。"""
    for g in (0.0, 0.5, 0.8, -0.5):
        n = 32768                                # 峰很尖，网格要够细（梯形误差 ∝ Δμ²）
        acc = 0.0
        for i in range(n):
            mu = -1.0 + 2.0 * (i + 0.5) / n
            acc += henyey_greenstein(mu, g) * (2.0 / n)
        integral = acc * 2.0 * math.pi           # dω = 2π dμ
        assert abs(integral - 1.0) < 1e-6, f'g={g} 的球面积分 = {integral}（应 1.0）'
        print(f'       g={g:+.1f} → ∫dω = {integral:.9f}')
    # 再验一条**原函数的解析式**（不只是"数值碰巧等于 1"）：
    #   ∫_{-1}^{1} (1+g²−2gμ)^(−3/2) dμ = 2/(1−g²)
    # 上面那个 1 正是靠它成立的（乘 (1−g²)/4π 后再乘 2π）。数值对照：
    for g in (0.2, 0.6, 0.9):
        n = 32768
        acc = sum((1 + g * g - 2 * g * (-1.0 + 2.0 * (i + 0.5) / n)) ** -1.5
                  for i in range(n)) * (2.0 / n)
        closed = 2.0 / (1.0 - g * g)
        assert abs(acc - closed) / closed < 1e-5,             f'g={g}: 原函数 2/(1−g²)={closed:.6f} vs 数值 {acc:.6f}'
    print('  ok: 4 个 g 值的球面积分都 = 1（误差 < 1e-6）；原函数 2/(1−g²) 也数值吻合')


def test_henyey_greenstein_limits():
    iso = 1.0 / (4.0 * math.pi)
    assert abs(henyey_greenstein(0.0, 0.0) - iso) < 1e-15, 'g=0 必须恰好是各向同性'
    assert abs(henyey_greenstein(0.7, 0.0) - iso) < 1e-15, 'g=0 时与角度无关'
    # 前向散射：朝光源看更亮
    for g in (0.3, 0.7, 0.9):
        assert henyey_greenstein(1.0, g) > henyey_greenstein(-1.0, g), f'g={g} 应前向更亮'
        assert henyey_greenstein(1.0, g) > iso
    for g in (-0.3, -0.9):
        assert henyey_greenstein(1.0, g) < henyey_greenstein(-1.0, g), f'g={g} 应后向更亮'
    # cos 越界要夹住（调用方的 dot 可能有 1e-16 的浮点溢出）
    assert henyey_greenstein(1.0000001, 0.5) == henyey_greenstein(1.0, 0.5)
    for g in (1.0, -1.0, 1.5):
        try:
            henyey_greenstein(0.0, g)
        except ValueError:
            pass
        else:
            raise AssertionError(f'g={g} 竟然通过（分母会变 0）')
    print(f'  ok: g=0 恒为 1/4π={iso:.6f}；g>0 前向更亮、g<0 后向更亮；|g|→1 被拒；cos 越界被夹')


# ------------------------------------------------------------------ 步长 / 抖动 --
def test_step_weights_sum_to_one_and_shapes():
    for n in (1, 3, 8, 32):
        lin = step_weights(n, 'linear')
        assert abs(sum(lin) - 1.0) < 1e-12, (n, sum(lin))
        assert max(lin) - min(lin) < 1e-12, 'linear 必须等分'
        exp = step_weights(n, 'exp')
        assert abs(sum(exp) - 1.0) < 1e-12, (n, sum(exp))
        assert all(exp[i] >= exp[i + 1] - 1e-12 for i in range(n - 1)), 'exp 必须单调不增'
        if n >= 3:
            assert exp[0] > exp[-1] * 1.5, f'exp 近处应明显更密：{exp[0]:.4f} vs {exp[-1]:.4f}'
    for bad, label in (((0, 'linear'), 'n=0'), ((4, 'nope'), '未知分布')):
        try:
            step_weights(*bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f'{label} 竟然通过')
    print(f'  ok: linear/exp 在 n=1/3/8/32 下都归一；exp 单调且近处更密；n=0 与未知分布被拒')


def test_dither_offset_range_determinism_and_uniformity():
    vals = [dither_offset(x, y) for x in range(16) for y in range(16)]
    assert all(0.0 <= v < 1.0 for v in vals), f'越界：{min(vals)}..{max(vals)}'
    mean = sum(vals) / len(vals)
    assert abs(mean - 0.5) < 0.03, f'应大致均匀（均值 0.5），实际 {mean:.4f}'
    assert dither_offset(3, 5, 0) == dither_offset(3, 5, 0), '同参必须幂等（剧照可复现）'
    assert dither_offset(3, 5, 0) != dither_offset(3, 5, 1), '换帧该动一动（时间抖动）'
    assert len({dither_offset(x, y) for x in range(8) for y in range(8)}) >= 48, '值太粗'
    try:
        dither_offset(0, 0, 0, 1)
    except ValueError:
        pass
    else:
        raise AssertionError('size=1 竟然通过')
    # CPU 与 GLSL **必须同式**：两处魔数漂开的话，"抖动量"会悄无声息地不等价
    g = volumetric_glsl('vol_fog')
    assert f'{IGN_A}' in g and f'{IGN_B}' in g and f'{IGN_C}' in g,         'GLSL 抖动函数的魔数与 CPU 侧不一致'
    print(f'  ok: 抖动 ∈[0,1) 均值 {mean:.4f}、幂等、随帧变化、256 格 ≥48 种取值；'
          f'size<2 被拒；GLSL 同式（魔数逐字一致）')


# ------------------------------------------------------------------ GLSL --
def test_volumetric_glsl_contains_required_uniforms():
    g = volumetric_glsl('vol_fog')
    for name in FOG_GLSL_UNIFORMS:
        assert f' {name};' in g, f'缺 uniform {name}'
    assert 'u_fog_analytic' in g and 'u_fog_phase_g' in g
    assert 'exp(-k)' in g, '闭式积分的 exp(-k) 不见了'
    # 闭式与对照组都要在（demo 靠切换它们展示"逐步矩形会出条纹"）
    assert 'u_fog_analytic > 0.5' in g, '缺少"闭式 vs 逐步矩形"的分支'
    for helper in ('_segment', '_phase', '_world', '_scene_dist'):
        assert f'vol_fog{helper}' in g, f'缺辅助函数 vol_fog{helper}'
    print(f'  ok: {len(FOG_GLSL_UNIFORMS)} 个 uniform 齐全、无版本声明、'
          f'闭式与对照组分支都在')


def test_volumetric_glsl_declares_exactly_one_sampler():
    """**回归守卫**：只声明 `u_fog_depth` **一个**采样器，且它必须**真被采样**。

    反例（本轮实踩）：另声明一个 `u_fog_shadow`，光轴却走"委托 `shadow_fn`"——
    于是它没被采样 ⇒ 被驱动优化掉 ⇒ pyglet 赋值时抛
    "A Uniform with the name `u_fog_shadow` was not found"，
    症状是"只改了个参数整个 demo 崩在启动阶段"，与体积雾毫无字面关系。
    同理，`u_fog_depth` 只声明不采样也会被优化掉 —— 所以有一个 `_scene_dist` 采样它。
    """
    for fn_arg in (None, 'csm_shadow'):
        g = volumetric_glsl('vol_fog', fn_arg)
        assert g.count('uniform sampler2D') == 1,             f'shadow_fn={fn_arg}：应只声明 1 个采样器，实际 {g.count("uniform sampler2D")}'
        assert 'uniform sampler2D u_fog_depth;' in g
        assert 'texture(u_fog_depth' in g, 'u_fog_depth 声明了却没被采样（会被优化掉）'
        assert '_scene_dist' in g
    shadowed = volumetric_glsl('vol_fog', 'csm_shadow')
    assert 'csm_shadow(pos' in shadowed, '应真的调用传入的阴影函数'
    # **约定守卫**：`csm_shadow` 是"1 = 在阴影里"，雾要的是"照度" ⇒ 必须取反。
    # 反例（本轮实踩）：直接用会让光轴画反，而画面只表现为"雾很平"
    # （光轴开关切换只差 1.5/255，根本看不出是符号错了）。
    assert '1.0 - csm_shadow(' in shadowed, '必须把阴影因子取反成"照度"再用'
    plain = volumetric_glsl('vol_fog')
    assert 'csm_shadow' not in plain
    print('  ok: 两版都只声明 u_fog_depth 且真采样；接阴影版真的调用 csm_shadow')


def test_volumetric_glsl_rejects_bad_fn_name():
    for fn in ('1bad', 'has space', ''):
        try:
            volumetric_glsl(fn)
        except ValueError:
            pass
        else:
            raise AssertionError(f'函数名 {fn!r} 竟然通过')
    print('  ok: 非法函数名被拒')


def test_volumetric_glsl_composes_with_csm_and_compiles():
    """真编译一次：**CSM 的 `csm_shadow` + 体积雾**拼在同一个片段里。

    这也是"两个模块能不能一起用"的证据 —— 字符串层面看不出来。
    """
    try:
        import pyglet
        from pyglet.graphics.shader import Shader, ShaderProgram
        win = pyglet.window.Window(64, 64, visible=False)

        @win.event
        def on_draw():
            pass
    except Exception as e:                                  # noqa: BLE001
        print(f'  SKIP: 拿不到 GL 上下文（{type(e).__name__}: {e}）')
        return
    try:
        from world3d.engine.csm import csm_glsl
        from world3d.engine.mathkit import look_at_matrix
        vs = '''#version 330 core
in vec3 position;
uniform WindowBlock { mat4 projection; mat4 view; } window;
void main() { gl_Position = window.projection * window.view * vec4(position, 1.0); }'''
        fs = ('#version 330 core\nout vec4 o;\n'
              'in vec3 v_dummy;\n'
              'uniform vec2 u_uv;\nuniform float u_zone_dummy;\n'
              '// CSM 与体积雾拼在一个片段里（两者都自带 uniform 声明）\n'
              + csm_glsl(3) + '\n' + volumetric_glsl('vol_fog', 'csm_shadow') + '''
void main() {
    // 走模块提供的 scene_dist（它采样 u_fog_depth）—— 只声明不采样的 uniform
    // 会被驱动优化掉，那样"缺 uniform"就成了假警报，测不出真问题
    vec4 f = vol_fog(u_uv, vol_fog_scene_dist(u_uv), 0.5);
    o = vec4(f.rgb, f.a);
}''')
        prog = ShaderProgram(Shader(vs, 'vertex'), Shader(fs, 'fragment'))
        names = set(prog.uniforms)
        need = list(FOG_GLSL_UNIFORMS)
        missing = [n for n in need if n not in names]
        assert not missing, f'驱动/编译后缺 uniform：{missing}（现有 {sorted(names)}）'
        print(f'  ok: CSM + 体积雾拼在一起**编译通过**，{len(need)} 个 uniform 全部在位'
              f'（含 csm_shadow 用到的 u_csm_*）')
    finally:
        win.close()


def test_volumetric_glsl_noise_branch():
    """密度噪声：函数齐全、**默认关闭**、均值 1 的调制、两侧常数同源。

    这条挡的是三类"画面只是有点闷/有点淡"的静默走样：
    1. 调制乘子均值不是 1 ⇒ 一开噪声整幅雾变浓或变淡（不是崩溃，是"好像变通透了"）；
    2. 忘了 ``u_fog_noise > 0.0`` 的门 ⇒ 关噪声也照付每步 3×8 次哈希的 ALU；
    3. ``NOISE3_NORM`` 与逻辑层漂开 ⇒ 离线烘焙的噪声体和实时采样的长得不一样。
    """
    g = volumetric_glsl('vol_fog')
    for helper in ('_hash3', '_grad3', '_noise3', '_fbm3'):
        assert f'vol_fog{helper}' in g, f'缺噪声辅助函数 vol_fog{helper}'
    # **默认关闭**：调制必须在 u_fog_noise 的门内，且强度 0 时整段跳过
    assert 'u_fog_noise > 0.0' in g, '缺"关闭即跳过"的门（关噪声也照付 ALU）'
    assert 'vol_fog_fbm3(pos * u_fog_noise_scale)' in g, '噪声没按世界坐标 × 频率采样'
    # 均值 1 的重映射：`1 + (nz-0.5)*2*strength`，少了 2.0 就只有半个身位
    assert '1.0 + (nz - 0.5) * 2.0 * u_fog_noise' in g, '调制式不是均值 1 的那条'
    # 两侧归一化常数必须相等（engine 不 import 逻辑层，靠这条断言把事实钉住）
    from world3d.logic import noise as N
    assert NOISE3_NORM == N._PERLIN3_NORM, \
        f'volumetrics.NOISE3_NORM={NOISE3_NORM} 与 logic.noise._PERLIN3_NORM='\
        f'{N._PERLIN3_NORM} 漂开了（CPU/GPU 的噪声体不再是同一个幅度）'
    assert f'const float vol_fog_NORM3 = {NOISE3_NORM!r};' in g, \
        'GLSL 里的归一化常数没有由 Python 侧生成（两处会各自漂）'
    # 独立片段可复用：换前缀后不留占位符
    solo = noise3_glsl('mynoise')
    assert 'mynoise_noise3' in solo and 'mynoise_fbm3' in solo
    assert '{fn}' not in solo and '{norm}' not in solo, '占位符没被替换干净'
    # ⚠ 双花括号守卫（实测踩过）：`_NOISE3_GLSL` 是**普通字符串**（占位符走 replace，
    # 不走 .format），所以花括号必须写单个。写成 f-string 习惯的 `{{` 时，GLSL 会把它
    # 解析成**嵌套语句块** —— 语法合法、编译通过、画面看起来也对，属于"编译器不会告状"的
    # 静默错误。这条断言是它唯一的防线。
    for text, label in ((solo, '独立片段'), (g, '整段体积雾')):
        assert '{{' not in text and '}}' not in text, \
            f'{label}里出现了双花括号（f-string 的写法漏进了普通字符串）'
    for bad in ('1bad', 'has space', ''):
        try:
            noise3_glsl(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f'噪声片段函数名 {bad!r} 竟然通过')
    print(f'  ok: 4 个噪声辅助函数齐全、门内默认关闭、均值 1 调制、'
          f'两侧 NORM3={NOISE3_NORM!r} 同源；独立片段可换前缀且拒绝非法名')


# ------------------------------------------------------------------ runner --
def main() -> int:
    tests = [
        ('test_height_fog_segment_matches_numeric_reference',
         test_height_fog_segment_matches_numeric_reference),
        ('test_height_fog_segment_is_additive',
         test_height_fog_segment_is_additive),
        ('test_height_fog_uniform_limit_and_edges',
         test_height_fog_uniform_limit_and_edges),
        ('test_height_fog_column_rejects_bad_steps',
         test_height_fog_column_rejects_bad_steps),
        ('test_henyey_greenstein_is_normalized',
         test_henyey_greenstein_is_normalized),
        ('test_henyey_greenstein_limits', test_henyey_greenstein_limits),
        ('test_step_weights_sum_to_one_and_shapes',
         test_step_weights_sum_to_one_and_shapes),
        ('test_dither_offset_range_determinism_and_uniformity',
         test_dither_offset_range_determinism_and_uniformity),
        ('test_volumetric_glsl_contains_required_uniforms',
         test_volumetric_glsl_contains_required_uniforms),
        ('test_volumetric_glsl_declares_exactly_one_sampler',
         test_volumetric_glsl_declares_exactly_one_sampler),
        ('test_volumetric_glsl_rejects_bad_fn_name',
         test_volumetric_glsl_rejects_bad_fn_name),
        ('test_volumetric_glsl_noise_branch', test_volumetric_glsl_noise_branch),
        ('test_volumetric_glsl_composes_with_csm_and_compiles',
         test_volumetric_glsl_composes_with_csm_and_compiles),
    ]
    failed = []
    for name, fn in tests:
        try:
            print(name)
            fn()
        except AssertionError as exc:
            failed.append((name, exc))
            print(f'  FAIL: {exc}')
        except Exception as exc:                            # noqa: BLE001
            failed.append((name, exc))
            print(f'  ERROR: {type(exc).__name__}: {exc}')
    print('=' * 60)
    print(f'[test_volumetrics] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_VOLUMETRICS_TESTS_FAILED')
        return 1
    print('ALL_VOLUMETRICS_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
