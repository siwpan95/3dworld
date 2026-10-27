# -*- coding: utf-8 -*-
"""GPU 计算调度核心（P4-1）：compute shader + SSBO + memory barrier 通用层。

从 ``engine/gpu_cull`` 的实践抽出（那是本引擎首个内嵌 compute 的模块）；
gpu_cull 走自己的专用路径（已发布行为不动），本模块供新消费者使用。

为什么能力探测不能只看版本号
----------------------------
pyglet 的 shadow context 报 GL 4.6，**真实窗口上下文可能只报 3.3**（本机
实测），而 compute/SSBO 属 GL 4.3 —— 驱动放宽可用但规范不保证。故探测 =
版本串 + GL 入口 + **试编译最小 compute 程序**（与 ``GpuCullSupport.detect``
同源：那边是 gpu-driven 专用探测，这边是通用探测）。

典型用法（compute 写、绘制读的「经典三步」）
--------------------------------------------
    det = detect()
    if det['available']:
        buf = ComputeBuffer(np_positions)              # ① 建缓冲（可带初值）
        prog = ComputeShader(SRC)                      #    编译（失败抛 ComputeError）
        prog.use(); buf.bind(0)
        prog.dispatch(*groups_for(n, 64))              #    CPU 侧只算线程组数
        barrier()                                      # ② 显存写序（对后续访问可见）
        ...                                            # ③ 同一块 buffer 作顶点属性
                                                       #    绘制（零回读）

线程组约定
----------
着色器内 ``layout(local_size_x = 64) in;``；CPU 侧用 :func:`groups_for`
把「元素数」换算成「线程组数」，不必手写 ceil。

CPU 回退
--------
``detect()['available']`` 为 False 时**上层**走 CPU 路径（回退是业务决策，
本模块只如实报告能力，不替上层选路径）。参考用法见演示仓 computeward：
GPU 路径百万粒子 / CPU 回退 3 万粒子（同一套预设语义）。
"""
from __future__ import annotations

import ctypes
import math
import re

__all__ = ['ComputeError', 'ComputeBuffer', 'ComputeShader',
           'detect', 'supports', 'groups_for', 'barrier']


class ComputeError(Exception):
    """compute 着色器编译失败或 GL 资源操作失败。"""


# ===================================================================== 能力探测
def detect(verbose: bool = False) -> dict:
    """探测当前 GL 上下文的 compute 能力。

    返回 ``{'available': bool, 'gl_version': str, 'glsl': str, 'reason': str}``。

    ⚠️ 必须在**真实窗口**已创建后调用——否则拿到的是 pyglet shadow context 的
    版本（本机 shadow 报 4.6、真实窗口报 3.3，差异实打实）。三道检查：
    ① GL/GLSL 版本串（仅作诊断信息）；② GL 入口存在性；
    ③ **试编译最小 compute 程序**（最终裁决——驱动放宽 3.3 上下文跑 430
    着色器在消费级驱动上很常见，规范不保证，只能试）。
    """
    from ctypes import c_char_p, cast

    import pyglet.gl as G

    out = {'available': False, 'gl_version': '?', 'glsl': '?', 'reason': ''}
    try:
        gv = cast(G.glGetString(G.GL_VERSION), c_char_p).value
        sv = cast(G.glGetString(G.GL_SHADING_LANGUAGE_VERSION), c_char_p).value
        out['gl_version'] = gv.decode() if gv else '?'
        out['glsl'] = sv.decode() if sv else '?'
    except Exception as e:
        out['reason'] = f'no GL context: {type(e).__name__}'
        if verbose:
            print(f'[compute] {out}')
        return out

    need = ('glDispatchCompute', 'glBindBufferBase', 'glMemoryBarrier')
    missing = [n for n in need if not hasattr(G, n)]
    if missing:
        out['reason'] = f'缺 GL 入口: {missing}'
        if verbose:
            print(f'[compute] {out}')
        return out

    try:
        prog = ComputeShader(
            '#version 430 core\n'
            'layout(local_size_x = 1) in;\n'
            'layout(std430, binding = 0) buffer T { uint v[]; };\n'
            'void main() { v[0] = 1u; }\n', name='_probe')
        if not prog.id:
            raise ComputeError('compute program id == 0')
        out['available'] = True
    except BaseException as e:          # ⚠️ pyglet ShaderException 继承 BaseException
        out['reason'] = f'compute 编译失败: {type(e).__name__}: {str(e)[:80]}'
    if verbose:
        print(f'[compute] {out}')
    return out


def supports(verbose: bool = False) -> bool:
    """便捷封装：当前上下文是否可用 compute。"""
    return detect(verbose=verbose)['available']


# ===================================================================== 线程组
def groups_for(n: int, local: int = 64) -> tuple:
    """把「元素数」换算成 dispatch 的线程组数 ``(x, 1, 1)``。

    ``n <= 0`` 返回 ``(1, 1, 1)``（dispatch 空转无害，调用方不必特判）。
    """
    return (max(1, math.ceil(int(n) / int(local))), 1, 1)


def barrier(bits=None):
    """发射 ``glMemoryBarrier``。

    Args:
        bits: 屏障位（``pyglet.gl`` 的 ``GL_*_BARRIER_BIT`` 常量按位或）。
              ``None`` 用 ``GL_ALL_BARRIER_BITS``（最保守，性能敏感处按需收窄：
              compute 写 → 顶点属性读 用 ``GL_VERTEX_ATTRIB_ARRAY_BARRIER_BIT |
              GL_SHADER_STORAGE_BARRIER_BIT``）。
    """
    import pyglet.gl as G
    G.glMemoryBarrier(G.GL_ALL_BARRIER_BITS if bits is None else bits)


# ======================================================================= SSBO
def _as_carray(data, *, need_writable: bool = False):
    """numpy / bytes / memoryview / ctypes 数组 -> ctypes 字节数组（零拷贝优先）。

    ⚠️ pyglet 的 ``glBufferData`` 系列把数据参数声明为指针：
    ``ndarray.ctypes.data``（裸 int 地址）**不行**，必须传 ctypes 对象
    （particleward 排查实录）。``memoryview`` 走 ``from_buffer`` 零拷贝，
    ``bytes``（不可写）走 ``from_buffer_copy``。
    """
    if isinstance(data, ctypes.Array):
        return data
    mv = memoryview(data)
    if not mv.contiguous:
        mv = memoryview(bytes(mv))                     # 稀疏视图退化为拷贝
    arr_t = ctypes.c_ubyte * mv.nbytes
    try:
        return arr_t.from_buffer(mv)                   # 可写缓冲：零拷贝
    except (TypeError, ValueError):                    # bytes 等只读缓冲
        return arr_t.from_buffer_copy(mv)


class ComputeBuffer:
    """SSBO（shader storage buffer object）封装：创建 / 绑定 / 上传 / 回读。

    典型生命周期::

        buf = ComputeBuffer(np_init_values)            # 带 numpy 初值创建
        buf.bind(0)                                    # 绑到 binding=0
        ...dispatch...
        buf.download(out_np)                           # 需要时回读（通常零回读）
        buf.release()                                  # 显式释放（或依赖 __del__）

    同一块 buffer 可以在 compute pass 里当 SSBO 写、在 draw pass 里当顶点
    属性源读——中间插一次 :func:`barrier` 即可，这正是「GPU 模拟 + GPU 绘制
    零回读」的关键。
    """

    _USAGE = {'dynamic': 'GL_DYNAMIC_DRAW', 'static': 'GL_STATIC_DRAW',
              'read': 'GL_DYNAMIC_READ'}

    def __init__(self, data=None, *, size_bytes: int = 0, usage: str = 'dynamic'):
        import pyglet.gl as G

        if data is None and size_bytes <= 0:
            raise ValueError('data 与 size_bytes 至少给一个')
        arr = _as_carray(data) if data is not None else None
        nbytes = ctypes.sizeof(arr) if arr is not None else int(size_bytes)

        buf = G.GLuint(0)
        G.glGenBuffers(1, ctypes.byref(buf))
        self.id = buf.value
        self.size_bytes = nbytes
        self._usage = self._USAGE.get(usage, self._USAGE['dynamic'])

        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, self.id)
        G.glBufferData(G.GL_SHADER_STORAGE_BUFFER, nbytes, arr,
                       getattr(G, self._usage))
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, 0)
        if self.id == 0:
            raise ComputeError('glGenBuffers 返回 0（上下文不可用？）')

    # ------------------------------------------------------------ 绑定 --
    def bind(self, unit: int):
        """绑定为 SSBO（``glBindBufferBase``）。"""
        import pyglet.gl as G
        G.glBindBufferBase(G.GL_SHADER_STORAGE_BUFFER, int(unit), self.id)

    @staticmethod
    def unbind(unit: int):
        import pyglet.gl as G
        G.glBindBufferBase(G.GL_SHADER_STORAGE_BUFFER, int(unit), 0)

    # ------------------------------------------------------------ 传输 --
    def upload(self, data, offset: int = 0):
        """CPU -> GPU（``glBufferSubData``）。data 类型约定同构造参数。"""
        import pyglet.gl as G
        arr = _as_carray(data)
        n = ctypes.sizeof(arr)
        if offset + n > self.size_bytes:
            raise ValueError(f'上传 {n}B@{offset} 超出缓冲 {self.size_bytes}B')
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, self.id)
        G.glBufferSubData(G.GL_SHADER_STORAGE_BUFFER, offset, n, arr)
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, 0)

    def download(self, out):
        """GPU -> CPU，填入 ``out``（可写 numpy / ctypes 数组）并返回它。

        优先 ``glGetBufferSubData``；入口不存在时退
        ``glMapBufferRange`` + ``memmove``（两者语义等价）。
        """
        import pyglet.gl as G
        arr = _as_carray(out)
        n = min(ctypes.sizeof(arr), self.size_bytes)
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, self.id)
        if hasattr(G, 'glGetBufferSubData'):
            G.glGetBufferSubData(G.GL_SHADER_STORAGE_BUFFER, 0, n, arr)
        else:
            ptr = G.glMapBufferRange(G.GL_SHADER_STORAGE_BUFFER, 0, n,
                                     G.GL_MAP_READ_BIT)
            if ptr:
                ctypes.memmove(arr, ctypes.c_void_p(ptr.value), n)
            G.glUnmapBuffer(G.GL_SHADER_STORAGE_BUFFER)
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, 0)
        return out

    # ------------------------------------------------------------ 释放 --
    def release(self):
        """显式删除 GL 缓冲（此后对象不可再用）。"""
        if self.id:
            import pyglet.gl as G
            buf = G.GLuint(self.id)
            G.glDeleteBuffers(1, ctypes.byref(buf))
            self.id = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False

    def __del__(self):
        try:
            self.release()
        except Exception:
            pass

    def __repr__(self):
        return f'ComputeBuffer(id={self.id}, {self.size_bytes}B)'


# ==================================================================== 程序
_LS_RE = re.compile(r'local_size_x\s*=\s*(\d+).*?local_size_y\s*=\s*(\d+)'
                    r'.*?local_size_z\s*=\s*(\d+)', re.S)
_LS_X_RE = re.compile(r'local_size_x\s*=\s*(\d+)')


class ComputeShader:
    """compute 着色器程序封装（pyglet ``ShaderProgram`` 的薄壳）。

    - ``use()`` + ``dispatch()`` 两步走；uniform 用 :meth:`set`（自动按类型
      分派 ``glUniform1i/1f/2f/3f/4f`` 与 mat4）；
    - ``local_size`` 从着色器源码正则解析（``layout(local_size_x=64) in;``），
      配 ``groups_for_n(n)`` 一行算线程组数；
    - 编译失败抛 :class:`ComputeError`（pyglet 的 ShaderException 继承
      BaseException 而非 Exception——gpu_cull 踩过，这里统一转译）。
    """

    def __init__(self, source: str, name: str = 'compute'):
        from pyglet.graphics.shader import Shader, ShaderProgram
        try:
            cs = Shader(source, 'compute')
            self.prog = ShaderProgram(cs)
        except BaseException as e:      # ⚠️ ShaderException 继承 BaseException
            raise ComputeError(
                f'compute 着色器 {name!r} 编译失败: '
                f'{type(e).__name__}: {str(e)[:120]}') from e
        if not self.prog.id:
            raise ComputeError(f'compute 着色器 {name!r} program id == 0')
        self.name = name
        self.id = self.prog.id
        self._source = source

    # -------------------------------------------------------- 属性 --
    @property
    def local_size(self) -> tuple:
        """着色器声明的 ``(x, y, z)`` 线程组大小（未声明 y/z 时按 1）。"""
        m = _LS_X_RE.search(self._source)
        if not m:
            return (1, 1, 1)
        # 只在 local_size(...) 括号内找 y/z（窗宽 120 字符足够盖住三个赋值）
        window = self._source[m.start():m.start() + 120]
        m3 = _LS_RE.search(window)
        if m3:
            return (int(m3.group(1)), int(m3.group(2)), int(m3.group(3)))
        return (int(m.group(1)), 1, 1)

    def groups_for_n(self, n: int) -> tuple:
        """按本程序 ``local_size_x`` 算覆盖 ``n`` 个元素需要的线程组数。"""
        return groups_for(n, self.local_size[0])

    # -------------------------------------------------------- 执行 --
    def use(self):
        import pyglet.gl as G
        G.glUseProgram(self.id)

    @staticmethod
    def unuse():
        import pyglet.gl as G
        G.glUseProgram(0)

    def dispatch(self, x: int = 1, y: int = 1, z: int = 1):
        import pyglet.gl as G
        G.glDispatchCompute(int(x), int(y), int(z))

    def run_over(self, n: int, barrier_bits=None):
        """便捷一步：``use()`` + 覆盖 n 元素的 dispatch + barrier。"""
        self.use()
        g = self.groups_for_n(n)
        self.dispatch(*g)
        barrier(barrier_bits)
        self.unuse()

    # -------------------------------------------------------- uniform --
    def set(self, name: str, value):
        """按 Python 类型分派 uniform（int→1i，float→1f，tuple→nf，Mat4→mat4）。"""
        import pyglet.gl as G
        u = self.prog.uniforms
        if name not in u:
            raise ComputeError(f'{self.name}: 未声明 uniform {name!r} '
                               f'（或被编译器剔除）')
        loc = u[name]['location']
        if isinstance(value, bool):
            G.glUniform1i(loc, int(value))
        elif isinstance(value, int):
            G.glUniform1i(loc, value)          # ⚠️ pyglet setter 表不含 uint，用 int
        elif isinstance(value, float):
            G.glUniform1f(loc, value)
        elif isinstance(value, (tuple, list)):
            vals = [float(v) for v in value]
            n = len(vals)
            if n == 2:
                G.glUniform2f(loc, *vals)
            elif n == 3:
                G.glUniform3f(loc, *vals)
            elif n == 4:
                G.glUniform4f(loc, *vals)
            elif n == 16:
                self.set_mat4(name, tuple(vals), loc)
            else:
                raise ComputeError(f'uniform {name!r}: 不支持长度 {n}')
        else:
            # pyglet Mat4 等有平铺 16 元组协议的对象
            try:
                self.set_mat4(name, tuple(value), loc)
            except Exception as e:
                raise ComputeError(f'uniform {name!r}: 类型 {type(value).__name__}'
                                   f' 不支持') from e

    @staticmethod
    def set_mat4(name, flat16, loc):
        """列主序 16 元组 -> ``glUniformMatrix4fv``。

        ⚠️ 数据参数必须 ``data_as(POINTER(c_float))``（裸 int 地址不行，
        particleward 实录）。
        """
        import pyglet.gl as G
        arr = (ctypes.c_float * 16)(*flat16)
        G.glUniformMatrix4fv(loc, 1, G.GL_FALSE,
                             arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float)))

    def release(self):
        prog = getattr(self, 'prog', None)
        if prog is not None and getattr(prog, 'id', 0):
            try:
                prog.delete() if hasattr(prog, 'delete') else None
            except Exception:
                pass

    def __repr__(self):
        return f'ComputeShader({self.name!r}, id={self.id})'
