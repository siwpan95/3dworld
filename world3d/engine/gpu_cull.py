# -*- coding: utf-8 -*-
"""GPU-Driven Rendering（P3-1）—— GPU 剔除 + GPU LOD + 命令生成。

方案（**一趟 compute 覆盖 A/B/C，且不依赖 draw parameter**）
------------------------------------------------------------
每帧：CPU 只做 **1 次 dispatch + ``len(LOD)`` 次 ``glDrawArraysIndirect``**。

  ① compute 逐实例：视锥剔除 → 按距离选 LOD → 落表
      · 被剔除：不写任何表
      · 可见：``slot = atomicAdd(cmd[lv*4+1], 1)``（**直接累加命令的 instanceCount**）
              ``visible[lv*N + slot] = i``（按 LOD **固定分区**）
  ② 顶点着色器：``id = visible[uLodBase + gl_InstanceID]`` → 从 SSBO 取该实例矩阵

为什么不用 ``gl_BaseInstance`` / ``gl_DrawID``（draw parameter）
----------------------------------------------------------------
**实测**：本机真实窗口上下文是 **GL 3.3**（pyglet 的 shadow context 才报 4.6），
``GL_ARB_shader_draw_parameters`` 的 ``gl_BaseInstanceARB`` / ``gl_DrawIDARB`` **不生效**
—— 早先"每实例一条命令 + baseInstance 索引实例"的方案，会让**全部实例塌到 mat[0]**
（画面只剩一个球，且**不报错**）。故改用 ``gl_InstanceID``（GL 3.1+ 核心）+ 分区表。

SSBO 绑定契约（compute 与 VS 共享；改这里必须同步 ``shaders.GPU_CULL_COMP``）
------------------------------------------------------------------------------
    binding 0  InstanceData : vec4 inst[]   每实例 (cx, cy, cz, radius) 世界包围球
    binding 1  InstanceMat  : vec4 mat[]    每实例 4 个 vec4 = 列主序 4×4 模型矩阵
    binding 3  Counts       : uint counts[] [0] = 总可见
    binding 4  Commands     : uint cmd[]    ``MAX_LOD`` 条 × 4 uint：(count, instanceCount, first, 0)
    binding 5  Visible      : uint visible[]``MAX_LOD`` 段 × N（固定分区，第 lv 段起点 = lv*N）

能力探测与回退（P3-1 决策②）
-----------------------------
``GL_VERSION`` 可能只报 3.3，而 compute/SSBO 属 4.3 —— 驱动放宽可用但**规范不保证**。
故先 ``detect()``；不可用则上层走 CPU 路径（``cpu_update``，命令在 Python 生成）。
"""
from __future__ import annotations

import ctypes
import math

from world3d.engine.frustum import Frustum

CMD_U32 = 4          # 每条 indirect 命令的 uint 数（glDrawArraysIndirect 格式）
MAX_LOD = 4          # LOD 级数上界（shader 里按这个上界声明数组）


# ===================================================================== 能力探测
class GpuCullSupport:
    """GPU-driven 能力探测（决策②：不可用则回退 CPU）。"""

    @staticmethod
    def detect(verbose: bool = False) -> dict:
        """返回 ``{'available', 'gl_version', 'glsl', 'reason'}``。

        检查：① GL/GLSL 版本串；② 必要 GL 入口；③ **试编译最小 compute 程序**。
        ⚠️ 必须在**真实窗口**已创建后调用——否则拿到的是 pyglet shadow context 的版本
        （本机 shadow 报 4.6、真实窗口报 3.3，差异实打实）。
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
                print(f'[gpu_cull] {out}')
            return out

        need = ('glDispatchCompute', 'glBindBufferBase', 'glMemoryBarrier',
                'glDrawArraysIndirect', 'glMultiDrawArraysIndirect')
        missing = [n for n in need if not hasattr(G, n)]
        if missing:
            out['reason'] = f'缺 GL 入口: {missing}'
            if verbose:
                print(f'[gpu_cull] {out}')
            return out

        try:
            from pyglet.graphics.shader import Shader, ShaderProgram
            cs = Shader('''#version 430 core
layout(local_size_x = 1) in;
layout(std430, binding = 0) buffer T { uint v[]; };
void main() { v[0] = 1u; }
''', 'compute')
            prog = ShaderProgram(cs)
            if not prog.id:
                raise RuntimeError('compute program id == 0')
            out['available'] = True
        except BaseException as e:          # ⚠️ ShaderException 继承 BaseException
            out['reason'] = f'compute 编译失败: {type(e).__name__}: {str(e)[:80]}'
        if verbose:
            print(f'[gpu_cull] {out}')
        return out


# =================================================================== CPU 参照
def cpu_reference(frustum: Frustum, centers, radii) -> list:
    """CPU 参照：返回**可见实例下标**（升序）。

    与 compute 用**同一语义**（``Frustum.intersects_sphere``：所有平面 distance >= -r）。
    """
    return [i for i, (c, r) in enumerate(zip(centers, radii))
            if frustum.intersects_sphere(c, r)]


def cpu_lod(cam_pos, centers, lod_dists) -> list:
    """CPU 参照：按到相机的距离选 LOD 级（与 compute 的阈值同源）。"""
    out = []
    for c in centers:
        d = math.dist(c, cam_pos)
        lv = 0
        while lv < len(lod_dists) and d >= lod_dists[lv]:
            lv += 1
        out.append(min(lv, MAX_LOD - 1))
    return out


# =============================================================== GPU 驱动器
class GpuDrivenField:
    """一组"多 LOD 几何 + 多实例"的场，走 GPU 剔除 + LOD + 命令生成。

    Args:
        instance_matrices: 每实例列主序 16 元组模型矩阵。
        centers / radii: 每实例世界空间包围球（compute 剔除用）。
        lod_vertex_data: ``[(first, count), ...]`` 各 LOD 段在大 VBO 里的位置与顶点数。
        comp_program: ``shaders.get_compute('gpu_cull')``。
        draw_program: ``shaders.get('gpu_driven')``（draw 时按 LOD 设 ``uLodBase``）。
    """

    def __init__(self, instance_matrices, centers, radii, lod_vertex_data,
                 comp_program, draw_program=None):
        self._n = len(instance_matrices)
        self.matrices = list(instance_matrices)
        self.centers = [(float(c[0]), float(c[1]), float(c[2])) for c in centers]
        self.radii = [float(r) for r in radii]
        self.lods = [(int(f), int(c)) for f, c in lod_vertex_data]
        if not (1 <= len(self.lods) <= MAX_LOD):
            raise ValueError(f'LOD 段数须在 1..{MAX_LOD}，实际 {len(self.lods)}')
        self.comp = comp_program
        self.draw_prog = draw_program

        self._buf_inst = None      # binding 0
        self._buf_mat = None       # binding 1
        self._buf_counts = None    # binding 3
        self._buf_cmd = None       # binding 4
        self._buf_vis = None       # binding 5

    # ---------------------------------------------------------- GL 资源
    def _ensure_buffers(self):
        if self._buf_inst is not None:
            return
        import pyglet.gl as G

        def new_buf():
            b = G.GLuint(0)
            G.glGenBuffers(1, ctypes.byref(b))
            return b

        self._buf_inst, self._buf_mat = new_buf(), new_buf()
        self._buf_counts, self._buf_cmd, self._buf_vis = new_buf(), new_buf(), new_buf()

        n = self._n
        flat = [v for c, r in zip(self.centers, self.radii) for v in (c[0], c[1], c[2], r)]
        self._upload(G.GL_SHADER_STORAGE_BUFFER, self._buf_inst, (ctypes.c_float * len(flat))(*flat))
        mflat = [v for m in self.matrices for v in tuple(m)]
        self._upload(G.GL_SHADER_STORAGE_BUFFER, self._buf_mat, (ctypes.c_float * len(mflat))(*mflat))
        # 计数（1 个 uint）
        self._upload(G.GL_SHADER_STORAGE_BUFFER, self._buf_counts, (G.GLuint * 1)(0))
        # 命令（MAX_LOD 条 × 4 uint）
        self._upload(G.GL_SHADER_STORAGE_BUFFER, self._buf_cmd,
                     (G.GLuint * (MAX_LOD * CMD_U32))(*([0] * (MAX_LOD * CMD_U32))))
        # 可见表（MAX_LOD 段 × N）
        self._upload(G.GL_SHADER_STORAGE_BUFFER, self._buf_vis,
                     (G.GLuint * (MAX_LOD * n))(*([0] * (MAX_LOD * n))))
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, 0)

    @staticmethod
    def _upload(target, buf, arr):
        import pyglet.gl as G
        G.glBindBuffer(target, buf)
        G.glBufferData(target, ctypes.sizeof(arr), arr, G.GL_DYNAMIC_DRAW)

    def _bind_all(self):
        import pyglet.gl as G
        for b, buf in ((0, self._buf_inst), (1, self._buf_mat), (3, self._buf_counts),
                       (4, self._buf_cmd), (5, self._buf_vis)):
            G.glBindBufferBase(G.GL_SHADER_STORAGE_BUFFER, b, buf)

    # ---------------------------------------------------------- 每帧剔除
    def update(self, frustum: Frustum, cam_pos, lod_dists):
        """上传视锥/相机 → dispatch compute（剔除 + LOD + 命令生成）。"""
        import pyglet.gl as G

        self._ensure_buffers()
        G.glUseProgram(self.comp.id)

        # ① 预设命令：count/first 由 CPU 给（instanceCount 每帧清零，交给 compute 累加）
        cmds = []
        for (f, c) in self.lods:
            cmds += [c, 0, f, 0]
        cmds += [0] * ((MAX_LOD - len(self.lods)) * CMD_U32)
        ca = (G.GLuint * len(cmds))(*cmds)
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, self._buf_cmd)
        G.glBufferSubData(G.GL_SHADER_STORAGE_BUFFER, 0, ctypes.sizeof(ca), ca)
        # ② 计数清零
        z = (G.GLuint * 1)(0)
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, self._buf_counts)
        G.glBufferSubData(G.GL_SHADER_STORAGE_BUFFER, 0, ctypes.sizeof(z), z)
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, 0)

        # ③ uniform
        u = self.comp.uniforms
        if 'uPlanes' in u:
            planes = [v for p in frustum.planes for v in p]
            G.glUniform4fv(u['uPlanes']['location'], 6, (ctypes.c_float * 24)(*planes))
        if 'uCount' in u:
            G.glUniform1i(u['uCount']['location'], self._n)
        if 'uMaxLod' in u:
            G.glUniform1i(u['uMaxLod']['location'], len(self.lods))
        if 'uCamPos' in u:
            G.glUniform3f(u['uCamPos']['location'],
                          float(cam_pos[0]), float(cam_pos[1]), float(cam_pos[2]))
        if 'uLodDists' in u:
            ds = list(lod_dists)[:MAX_LOD] + [1e30] * (MAX_LOD - len(list(lod_dists)))
            G.glUniform1fv(u['uLodDists']['location'], MAX_LOD,
                           (ctypes.c_float * MAX_LOD)(*[float(d) for d in ds[:MAX_LOD]]))
        # ⚠️ 用 int 传（pyglet 的 uniform setter 表不含 uint）
        if 'uLodFirst' in u:
            fv = [f for f, _c in self.lods] + [0] * (MAX_LOD - len(self.lods))
            G.glUniform1iv(u['uLodFirst']['location'], MAX_LOD,
                           (ctypes.c_int * MAX_LOD)(*fv[:MAX_LOD]))
        if 'uLodCount' in u:
            cv = [c for _f, c in self.lods] + [0] * (MAX_LOD - len(self.lods))
            G.glUniform1iv(u['uLodCount']['location'], MAX_LOD,
                           (ctypes.c_int * MAX_LOD)(*cv[:MAX_LOD]))

        self._bind_all()
        groups = max(1, (self._n + 63) // 64)
        G.glDispatchCompute(groups, 1, 1)
        G.glMemoryBarrier(G.GL_SHADER_STORAGE_BARRIER_BIT)

    # ---------------------------------------------------------- 提交绘制
    def draw(self):
        """按 LOD 提交 ``len(LOD)`` 次 ``glDrawArraysIndirect``（各指本 LOD 的命令）。"""
        import pyglet.gl as G
        from pyglet.gl import GL_TRIANGLES

        prog = self.draw_prog
        # ⚠️ update() 内部 glUseProgram(compute) 会切走当前 program——
        #    这里必须重新 use 回绘制程序，否则 uLodBase 会被设到错的 program 上。
        if prog is not None:
            prog.use()
        G.glBindBuffer(G.GL_DRAW_INDIRECT_BUFFER, self._buf_cmd)
        for lv in range(len(self.lods)):
            if prog is not None and 'uLodBase' in prog.uniforms:
                G.glUniform1i(prog.uniforms['uLodBase']['location'], lv * self._n)
            G.glDrawArraysIndirect(GL_TRIANGLES, ctypes.c_void_p(lv * CMD_U32 * 4))
        G.glBindBuffer(G.GL_DRAW_INDIRECT_BUFFER, 0)

    # ---------------------------------------------------------- CPU 对照
    def cpu_update(self, frustum: Frustum, cam_pos, lod_dists):
        """**CPU 对照路径**：剔除 + LOD + 命令生成全在 Python 做，再上传命令/可见表。

        与 :meth:`update` 的区别**只在"谁生成命令"**——绘制仍走 :meth:`draw`。
        这样 demo 的 F2 切换等价于"隔离剔除位置"这一个变量，画面应**逐像素一致**。
        """
        import pyglet.gl as G

        self._ensure_buffers()
        vis = cpu_reference(frustum, self.centers, self.radii)
        lv_all = cpu_lod(cam_pos, self.centers, lod_dists)
        n_lod = len(self.lods)
        cmds = [0] * (MAX_LOD * CMD_U32)
        vis_tbl = [0] * (MAX_LOD * self._n)
        slots = [0] * MAX_LOD
        for i in vis:
            lv = min(lv_all[i], n_lod - 1)
            vis_tbl[lv * self._n + slots[lv]] = i
            slots[lv] += 1
        for lv, (f, c) in enumerate(self.lods):
            cmds[lv * CMD_U32 + 0] = c
            cmds[lv * CMD_U32 + 1] = slots[lv]
            cmds[lv * CMD_U32 + 2] = f

        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, self._buf_cmd)
        G.glBufferSubData(G.GL_SHADER_STORAGE_BUFFER, 0, ctypes.sizeof((G.GLuint * len(cmds))(*cmds)),
                          (G.GLuint * len(cmds))(*cmds))
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, self._buf_vis)
        G.glBufferSubData(G.GL_SHADER_STORAGE_BUFFER, 0,
                          ctypes.sizeof((G.GLuint * len(vis_tbl))(*vis_tbl)),
                          (G.GLuint * len(vis_tbl))(*vis_tbl))
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, self._buf_counts)
        ka = (G.GLuint * 1)(len(vis))
        G.glBufferSubData(G.GL_SHADER_STORAGE_BUFFER, 0, ctypes.sizeof(ka), ka)
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, 0)
        return {'visible': len(vis), 'lod': slots[:MAX_LOD]}

    # ---------------------------------------------------------- 读回
    def read_counts(self):
        """读回 ``{'visible': n, 'lod': [...]}``（同步读回，会 stall——决策④：可关）。"""
        import pyglet.gl as G

        cnt = (G.GLuint * 1)()
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, self._buf_counts)
        G.glGetBufferSubData(G.GL_SHADER_STORAGE_BUFFER, 0, ctypes.sizeof(cnt), cnt)
        cmds = (G.GLuint * (MAX_LOD * CMD_U32))()
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, self._buf_cmd)
        G.glGetBufferSubData(G.GL_SHADER_STORAGE_BUFFER, 0, ctypes.sizeof(cmds), cmds)
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, 0)
        return {'visible': int(cnt[0]),
                'lod': [int(cmds[i * CMD_U32 + 1]) for i in range(MAX_LOD)]}

    def read_commands(self):
        """读回 indirect 命令（``MAX_LOD`` 条 × 4 uint，扁平列表；对照/调试用）。"""
        import pyglet.gl as G

        out = (G.GLuint * (MAX_LOD * CMD_U32))()
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, self._buf_cmd)
        G.glGetBufferSubData(G.GL_SHADER_STORAGE_BUFFER, 0, ctypes.sizeof(out), out)
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, 0)
        return [int(v) for v in out]

    def read_visible(self):
        """读回**可见表**并按 LOD 解包：``{lod: [实例下标, ...]}``（仅供对照/调试）。

        每段有效长度 = 该 LOD 的 ``instanceCount``（命令里读）；这就是"GPU 判定的可见集合"，
        用于与 ``cpu_reference`` 做**逐实例**比对。
        """
        import pyglet.gl as G

        n, n_lod = self._n, len(self.lods)
        raw = (G.GLuint * (MAX_LOD * n))()
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, self._buf_vis)
        G.glGetBufferSubData(G.GL_SHADER_STORAGE_BUFFER, 0, ctypes.sizeof(raw), raw)
        G.glBindBuffer(G.GL_SHADER_STORAGE_BUFFER, 0)
        counts = self.read_counts()['lod']
        out = {}
        for lv in range(n_lod):
            k = int(counts[lv])
            out[lv] = [int(raw[lv * n + s]) for s in range(k)]
        return out

    def bind_instance_matrix_ssbo(self, binding: int = 1):
        """把实例矩阵 SSBO 绑到指定 binding（顶点着色器用）。"""
        import pyglet.gl as G
        self._ensure_buffers()
        G.glBindBufferBase(G.GL_SHADER_STORAGE_BUFFER, binding, self._buf_mat)

    def bind_visible_ssbo(self, binding: int = 5):
        """把可见表 SSBO 绑到指定 binding（顶点着色器用）。"""
        import pyglet.gl as G
        self._ensure_buffers()
        G.glBindBufferBase(G.GL_SHADER_STORAGE_BUFFER, binding, self._buf_vis)


__all__ = ['GpuCullSupport', 'GpuDrivenField', 'cpu_reference', 'cpu_lod',
           'CMD_U32', 'MAX_LOD']
