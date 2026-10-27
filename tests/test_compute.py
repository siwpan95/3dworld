# -*- coding: utf-8 -*-
"""GPU 计算调度核心测试（P4-1 ``engine/compute.py``）。

覆盖：能力探测 / 线程组换算 / SSBO 往返（numpy -> GPU -> numpy）/
**compute 黄金判据**（GPU 逐元素运算 == CPU 参照）/ uniform 分派 /
``gl_GlobalInvocationID`` 映射正确性。

运行::

    python -m tests.test_compute

需要 GL 上下文（隐藏窗口）；探测不可用时相关用例打印 SKIP（可移植性）。
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np                                               # noqa: E402
import pyglet                                                    # noqa: E402
pyglet.options['debug_gl'] = False

from world3d.engine.compute import (ComputeBuffer, ComputeShader,  # noqa: E402
                            ComputeError, detect, groups_for)

_WIN = None


def _win():
    """共享一个隐藏窗口（GL 上下文）。"""
    global _WIN
    if _WIN is None:
        _WIN = pyglet.window.Window(160, 120, visible=False)
    return _WIN


# ---------------------------------------------------------------- 纯逻辑
def test_groups_for():
    assert groups_for(0, 64) == (1, 1, 1)
    assert groups_for(1, 64) == (1, 1, 1)
    assert groups_for(64, 64) == (1, 1, 1)
    assert groups_for(65, 64) == (2, 1, 1)
    assert groups_for(1_048_576, 256) == (4096, 1, 1)
    print('  ok: groups_for 向上取整 / 零与负数安全')


def test_local_size_parse():
    src = ('#version 430 core\n'
           'layout(local_size_x = 64, local_size_y = 2, local_size_z = 3) in;\n'
           'void main() {}\n')
    _win()                          # 编译需要上下文（本用例顺带验证解析）
    prog = ComputeShader(src, name='_ls')
    assert prog.local_size == (64, 2, 3), prog.local_size
    assert prog.groups_for_n(129) == (3, 1, 1)
    # GLSL 430 规定 compute 必须声明 local_size —— 不声明要报 ComputeError
    try:
        ComputeShader('#version 430 core\nvoid main() {}\n', name='_nols')
        raise AssertionError('缺 local_size 的 compute 竟然编译通过')
    except ComputeError:
        pass
    print('  ok: local_size 正则解析（三元 / 缺声明报错）')


def test_detect():
    det = detect(verbose=True)
    assert set(det) == {'available', 'gl_version', 'glsl', 'reason'}
    if not det['available']:
        print(f"  SKIP: 本机上下文不支持 compute（{det['reason']}）——GPU 用例跳过")
        return False
    print(f"  ok: 探测可用（GL {det['gl_version']} / GLSL {det['glsl']}）")
    return True


# ---------------------------------------------------------------- GPU 用例
_ADD_SRC = '''#version 430 core
layout(local_size_x = 64) in;
layout(std430, binding = 0) readonly buffer In { float in_val[]; };
layout(std430, binding = 1) writeonly buffer Out { float out_val[]; };
uniform float u_scale;
uniform float u_bias;
void main() {
    uint i = gl_GlobalInvocationID.x;
    if (i >= in_val.length()) return;
    out_val[i] = in_val[i] * u_scale + u_bias;
}'''


def test_compute_vector_add():
    """黄金判据：GPU 逐元素 ``x*2+1`` == CPU 参照。"""
    if not detect()['available']:
        print('  SKIP: no compute')
        return
    n = 1000
    src = np.arange(n, dtype=np.float32) * 0.5
    dst = np.zeros(n, dtype=np.float32)
    b_in = ComputeBuffer(src)
    b_out = ComputeBuffer(size_bytes=dst.nbytes)
    prog = ComputeShader(_ADD_SRC, name='vec_add')
    prog.use()
    b_in.bind(0)
    b_out.bind(1)
    prog.set('u_scale', 2.0)
    prog.set('u_bias', 1.0)
    prog.dispatch(*groups_for(n, 64))
    from world3d.engine.compute import barrier
    barrier()
    prog.unuse()
    b_out.download(dst)
    ref = src * 2.0 + 1.0
    assert np.allclose(dst, ref, atol=1e-5), (dst[:8], ref[:8])
    b_in.release()
    b_out.release()
    print(f'  ok: GPU 逐元素运算 == CPU 参照（n={n}，max|diff|='
          f'{float(np.max(np.abs(dst - ref))):.2e}）')


def test_compute_invocation_map():
    """``gl_GlobalInvocationID.x`` 必须恰好覆盖 0..n-1（不多不少）。"""
    if not detect()['available']:
        print('  SKIP: no compute')
        return
    n = 257                                        # 非 local 的倍数，验边界
    out = np.zeros(n, dtype=np.uint32)
    buf = ComputeBuffer(size_bytes=out.nbytes)
    prog = ComputeShader('''#version 430 core
layout(local_size_x = 64) in;
layout(std430, binding = 0) writeonly buffer Out { uint ids[]; };
void main() {
    uint i = gl_GlobalInvocationID.x;
    if (i >= ids.length()) return;
    ids[i] = i;
}''', name='ids')
    buf.bind(0)
    prog.use()
    prog.dispatch(*prog.groups_for_n(n))
    from world3d.engine.compute import barrier
    barrier()
    prog.unuse()
    buf.download(out)
    assert (out == np.arange(n, dtype=np.uint32)).all(), out[:10]
    buf.release()
    print(f'  ok: 调用 ID 映射 0..{n - 1}（尾组边界裁剪正确）')


def test_ssbo_upload_download():
    """SSBO 往返：upload 后不经 compute 直接 download，逐位一致。"""
    _win()
    data = np.random.default_rng(7).standard_normal(4096).astype(np.float32)
    buf = ComputeBuffer(data)
    out = np.zeros_like(data)
    buf.download(out)
    assert (out == data).all()
    # 就地改写后再读（upload 走 glBufferSubData 路径）
    data2 = (data * -2.0).astype(np.float32)
    buf.upload(data2, offset=0)
    buf.download(out)
    assert (out == data2).all()
    buf.release()
    print(f'  ok: SSBO 往返逐位一致（{data.nbytes}B × 2 次）')


def test_uniform_types():
    """uniform 分派：int/float/vec3 全部生效（写进输出缓冲验证）。"""
    if not detect()['available']:
        print('  SKIP: no compute')
        return
    out = np.zeros(4, dtype=np.float32)
    buf = ComputeBuffer(size_bytes=out.nbytes)
    prog = ComputeShader('''#version 430 core
layout(local_size_x = 1) in;
layout(std430, binding = 0) writeonly buffer Out { vec4 o; };
uniform int u_i;
uniform float u_f;
uniform vec3 u_v3;
void main() { o = vec4(float(u_i), u_f, u_v3.x + u_v3.y + u_v3.z, 42.0); }''',
                         name='uni')
    buf.bind(0)
    prog.use()
    prog.set('u_i', 7)
    prog.set('u_f', 0.5)
    prog.set('u_v3', (1.0, 2.0, 3.0))
    prog.dispatch()
    from world3d.engine.compute import barrier
    barrier()
    prog.unuse()
    buf.download(out)
    assert np.allclose(out, [7.0, 0.5, 6.0, 42.0]), out
    # 未声明的 uniform 要报 ComputeError（而不是静默无效）
    try:
        prog.set('u_missing', 1.0)
        raise AssertionError('未声明 uniform 未报错')
    except ComputeError:
        pass
    buf.release()
    print('  ok: uniform int/float/vec3 分派生效，未声明项报错')


def main() -> int:
    print('test_compute:')
    failed = 0
    tests = [test_groups_for, test_local_size_parse, test_detect,
             test_compute_vector_add, test_compute_invocation_map,
             test_ssbo_upload_download, test_uniform_types]
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f'  FAIL: {t.__name__}: {e}')
            failed += 1
        except Exception as e:                     # noqa: BLE001
            print(f'  ERROR: {t.__name__}: {type(e).__name__}: {e}')
            failed += 1
    if failed:
        print(f'COMPUTE_TESTS_FAILED ({failed})')
        return 1
    print('ALL_COMPUTE_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
