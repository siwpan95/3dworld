# -*- coding: utf-8 -*-
"""GPU 网格：把 :class:`~engine.geometry.MeshData` 上传成 pyglet 顶点列表。

两种形态::

    Mesh          单个模型（一次 draw call），自带 model 矩阵与包围盒
    InstancedMesh 一份几何 + N 份实例数据（一次 draw call 画 N 个）

实例化的实例属性是 **4 个 vec4**（矩阵的四列）+ **1 个 vec4**（tint.rgb + 参数）。
为什么不直接传 mat4：pyglet 的属性类型表里没有 ``GL_FLOAT_MAT4``（见
``pyglet/graphics/shader.py`` 的 ``_attribute_types``），传了会在 introspect
阶段 KeyError。拆成 4 个 vec4 后在着色器里 ``mat4(m0,m1,m2,m3)`` 拼回去，
是 pyglet 上唯一稳的路子。

实例数据的写入走 pyglet 的 ``add_instance`` 预分配 + 直接写 buffer region，
绘制数量用 ``domain._instances`` 控制（私有字段，包了一层兜底）。
"""
from pyglet.gl import GL_TRIANGLES

from engine.frustum import AABB
from engine.geometry import MeshData
from engine.mathkit import mat4_identity, normal_matrix, mat4_to_Mat4
from engine.material import Material

__all__ = ['Mesh', 'InstancedMesh', 'INSTANCE_ATTRS', 'build_instance_matrix']


INSTANCE_ATTRS = ('i_m0', 'i_m1', 'i_m2', 'i_m3', 'i_tint')


def build_instance_matrix(matrix):
    """把列主序 4x4 拆成 4 个 vec4（矩阵的四列）。"""
    return (tuple(matrix[0:4]), tuple(matrix[4:8]),
            tuple(matrix[8:12]), tuple(matrix[12:16]))


def _set_instance_count(vlist, n):
    """设置实际绘制的实例数。

    pyglet 用 ``InstancedVertexDomain._instances`` 作为 ``glDrawArraysInstanced``
    的 primcount，但没给公开 setter。这里直接写；拿不到该字段时退化为
    "全部实例都画"（功能不丢，只是多画一些）。
    """
    domain = getattr(vlist, 'domain', None)
    if domain is not None and hasattr(domain, '_instances'):
        domain._instances = max(1, int(n))
        return True
    return False


class Mesh:
    """单个 GPU 网格 + 它的材质与变换。"""

    # 渲染器按此名分组选着色器程序（见 renderer._draw_list）——子类覆盖
    PROGRAM_NAME = 'pbr'

    def __init__(self, data, program, material=None, name=''):
        assert isinstance(data, MeshData), 'data 必须是 MeshData'
        if not data.normals:
            data.compute_normals()
        if not data.uvs:
            data.uvs = [0.0] * (data.vertex_count * 2)
        if not data.tangents and data.uvs:
            data.compute_tangents()
        self.data = data
        self.program = program
        self.material = material or Material()
        self.name = name or data.name
        self.model = mat4_identity()
        self.local_aabb = data.aabb()
        self.local_sphere = data.bounding_sphere()
        self.world_aabb = self.local_aabb.transformed(self.model)
        self.visible = True
        self._uniforms_dirty = True
        self._uploaded_model = None           # 上次真正上传的矩阵（uniform 去重）
        self.vlist = self._upload()

    # ---- 上传 ----
    def _upload(self):
        d = self.data
        kwargs = {'position': ('f', d.positions), 'normals': ('f', d.normals),
                  'uvs': ('f', d.uvs)}
        if d.tangents:
            kwargs['tangents'] = ('f', d.tangents)
        n = d.vertex_count
        if d.indices:
            return self.program.vertex_list_indexed(
                n, GL_TRIANGLES, d.indices, **kwargs)
        return self.program.vertex_list(n, GL_TRIANGLES, **kwargs)

    # ---- 变换 ----
    def set_model(self, matrix):
        """设置模型矩阵（同时刷新世界包围盒：剔除要用）。"""
        m = tuple(matrix)
        if m != self.model:
            self.model = m
            self._uniforms_dirty = True
        self.world_aabb = self.local_aabb.transformed(self.model)
        return self

    @property
    def world_sphere(self):
        """世界空间包围球：中心随模型矩阵变换，半径按最大缩放放大。"""
        c = self.local_sphere[0]
        r = self.local_sphere[1]
        m = self.model
        wc = (m[0] * c[0] + m[4] * c[1] + m[8] * c[2] + m[12],
              m[1] * c[0] + m[5] * c[1] + m[9] * c[2] + m[13],
              m[2] * c[0] + m[6] * c[1] + m[10] * c[2] + m[14])
        sx = (m[0] * m[0] + m[1] * m[1] + m[2] * m[2]) ** 0.5
        sy = (m[4] * m[4] + m[5] * m[5] + m[6] * m[6]) ** 0.5
        sz = (m[8] * m[8] + m[9] * m[9] + m[10] * m[10]) ** 0.5
        return wc, r * max(sx, sy, sz)

    # ---- 绘制 ----
    def draw(self, unit_start=0, skip_material=False):
        """设置 model/法线矩阵 + 材质，然后画自己。

        ``skip_material``：渲染器按材质分组时传 True（材质已由渲染器
        统一应用过，逐个重复设置是纯浪费——实测占了每帧 Python 耗时的大头）。
        """
        if not self.visible:
            return 0
        p = self.program
        if self._uploaded_model != self.model:
            # 与"上次实际上传的矩阵"比较而不是脏标记：同一 program 下多个
            # 网格轮流画会互相覆盖 uniform，只看自身脏标记会漏传。
            p['model'] = mat4_to_Mat4(self.model)
            p['normal_mat'] = mat4_to_Mat4(_pad3(normal_matrix(self.model)))
            self._uploaded_model = self.model
        if not skip_material:
            self.material.apply(p, unit_start)
        self.vlist.draw(GL_TRIANGLES)
        return 0 if skip_material else 1

    def delete(self):
        try:
            self.vlist.delete()
        except Exception:
            pass

    def __repr__(self):
        return f'Mesh({self.name!r}, tris={self.data.triangle_count})'


class SkinnedMesh(Mesh):
    """蒙皮网格（P2-3）：在 :class:`Mesh` 基础上加 joints/weights 顶点属性。

    顶点着色器用 ``uniform mat4 u_joints[MAX_JOINTS]``（见 ``shaders.PBR_SKIN_VERT``）
    做骨骼混合。关节矩阵来自 :class:`engine.skin.Skin`，**每帧重算并上传**
    （关节世界矩阵随动画变化）。

    Args:
        data: 必须带 ``joints``/``weights`` 的 :class:`~engine.geometry.MeshData`。
        program: 应为 ``shaders.get('pbr_skin')``。
        skin: :class:`engine.skin.Skin`——其 ``joints`` 节点的 world_matrix 须已刷新。
    """

    PROGRAM_NAME = 'pbr_skin'

    def __init__(self, data, program, skin, material=None, name=''):
        assert data.skinned, 'SkinnedMesh 需要带 joints/weights 的 MeshData'
        self.skin = skin                     # 先于 super()——_upload 之外的字段
        super().__init__(data, program, material, name)

    def _upload(self):
        d = self.data
        kwargs = {'position': ('f', d.positions), 'normals': ('f', d.normals),
                  'uvs': ('f', d.uvs),
                  'joints': ('f', d.joints), 'weights': ('f', d.weights)}
        if d.tangents:
            kwargs['tangents'] = ('f', d.tangents)
        n = d.vertex_count
        if d.indices:
            return self.program.vertex_list_indexed(
                n, GL_TRIANGLES, d.indices, **kwargs)
        return self.program.vertex_list(n, GL_TRIANGLES, **kwargs)

    def draw(self, unit_start=0, skip_material=False):
        self._upload_joint_matrices()
        return super().draw(unit_start, skip_material)

    def _upload_joint_matrices(self):
        """上传关节矩阵数组（``uniform mat4 u_joints[MAX_JOINTS]``）。

        ⚠️ **实测（pyglet 2.1.16，2026-10-10）**：``program['u_joints'] = [...]``
        **只写入数组第 0 个元素**——pyglet 的 ``_Uniform`` setter 内部用
        ``glUniformMatrix4fv(loc, 1, ...)``（count 固定为 1），其余槽位**保持 0**。
        后果：``skin`` 矩阵对绝大多数顶点为 0 ⇒ 顶点塌缩到原点、网格整片消失
        （画面只剩天空，且**不报任何错**）。

        因此这里**绕过 pyglet**，直接 ``glUniformMatrix4fv`` 一次性上传整个数组。
        """
        import ctypes

        from pyglet.gl import GL_FALSE, glUniformMatrix4fv, glUseProgram

        ms = self.skin.uniform_matrices()               # 长度 == MAX_JOINTS（未用槽补 I）
        flat = [v for m in ms for v in tuple(m)]
        arr = (ctypes.c_float * len(flat))(*flat)
        glUseProgram(self.program.id)
        loc = self.program.uniforms['u_joints']['location']
        glUniformMatrix4fv(loc, len(ms), GL_FALSE, arr)


def _pad3(m3):
    """mat3(9 元组) -> mat4(16 元组)，塞进 mat4 uniform 的左上 3x3。"""
    a, b, c, d, e, f, g, h, i = m3
    return (a, b, c, 0.0, d, e, f, 0.0, g, h, i, 0.0, 0.0, 0.0, 0.0, 1.0)


class InstancedMesh:
    """一份几何 + N 份实例数据，一次 draw call 画完。

    典型用法：草、石块、 voxel 面、粒子块。2000 个立方体走实例化是
    1 次 draw call + 2000 份实例数据；逐个画是 2000 次 draw call。
    """

    PROGRAM_NAME = 'pbr_inst'

    def __init__(self, data, program, material=None, capacity=256, name=''):
        if not data.normals:
            data.compute_normals()
        if not data.uvs:
            data.uvs = [0.0] * (data.vertex_count * 2)
        if not data.tangents and data.uvs:
            data.compute_tangents()
        self.data = data
        self.program = program
        self.material = material or Material()
        self.name = name or data.name
        self.capacity = int(capacity)
        self.count = 0
        self.visible = True
        self._uniforms_dirty = True
        self._uploaded_model = None
        self._matrices = [mat4_identity()] * self.capacity
        self._tints = [(1.0, 1.0, 1.0, 0.0)] * self.capacity
        self.world_aabb = AABB()
        self.vlist = self._upload()

    # ---- 上传 ----
    def _upload(self):
        d = self.data
        kwargs = {'position': ('f', d.positions), 'normals': ('f', d.normals),
                  'uvs': ('f', d.uvs)}
        if d.tangents:
            kwargs['tangents'] = ('f', d.tangents)
        n = d.vertex_count
        # 实例属性**必须**出现在初始 data 里：pyglet 只在遍历 data 时给属性打
        # instance 标记（``'instance': name in instances``）。漏传的后果是
        # 缓冲永不扩容、divisor 不设置——画出来只有第 0 个实例，且完全不报错。
        # 初始数据按"顶点数 x 分量数"给（set_attribute_data 用顶点数算长度），
        # 填 0 即可，真正的实例数据随后由 write_all 批量写入。
        zeros = [0.0] * (n * 4)
        for name in INSTANCE_ATTRS:
            kwargs[name] = ('f', zeros)
        if d.indices:
            vl = self.program.vertex_list_instanced_indexed(
                n, GL_TRIANGLES, d.indices, list(INSTANCE_ATTRS), **kwargs)
        else:
            vl = self.program.vertex_list_instanced(
                n, GL_TRIANGLES, list(INSTANCE_ATTRS), **kwargs)
        # 预分配实例槽：实例缓冲靠 add_instance 扩容，不预分配写数据会越界
        zeros4 = (0.0, 0.0, 0.0, 0.0)
        for _ in range(1, self.capacity):
            vl.add_instance(**{name: zeros4 for name in INSTANCE_ATTRS})
        return vl

    # ---- 实例数据 ----
    def set_instance(self, index, matrix, tint=(1.0, 1.0, 1.0, 0.0)):
        """写入第 index 个实例（矩阵 + tint/参数）。"""
        assert 0 <= index < self.capacity, '实例下标越界'
        self._matrices[index] = tuple(matrix)
        self._tints[index] = tuple(tint)
        cols = build_instance_matrix(matrix)
        bufs = self.vlist.domain.attrib_name_buffers
        for name, col in zip(('i_m0', 'i_m1', 'i_m2', 'i_m3'), cols):
            bufs[name].set_region(index, 1, col)
        bufs['i_tint'].set_region(index, 1, tuple(tint))
        if index + 1 > self.count:
            self.count = index + 1
        return self

    def write_all(self, matrices, tints=None):
        """一次性写入全部实例（每属性一次 set_region，而不是每实例一次）。

        逐实例写是 ``count * 5`` 次小缓冲写入，576 个实例就是 2880 次/帧——
        实测能占到好几毫秒。批量写把次数降到 5 次，是动态实例化唯一该走的路。
        """
        n = min(len(matrices), self.capacity)
        cols = [[] for _ in range(4)]
        tint_flat = []
        for i in range(n):
            m = matrices[i]
            for c in range(4):
                cols[c].extend(m[c * 4:c * 4 + 4])
            tint_flat.extend(tints[i] if tints else (1.0, 1.0, 1.0, 0.0))
        bufs = self.vlist.domain.attrib_name_buffers
        if n > 0:
            for c, name in enumerate(('i_m0', 'i_m1', 'i_m2', 'i_m3')):
                bufs[name].set_region(0, n, cols[c])
            bufs['i_tint'].set_region(0, n, tint_flat)
        self.count = n
        return self

    def write_raw(self, arrays, count):
        """底层快路径：直接写入已拍平的实例数据（每属性一次上传）。

        给"每帧都在动的实例场"用：调用方自己维护扁平列表、只改变化的分量
        （比如只改 y），然后一次 write_raw。比逐实例 set_instance 快一个量级，
        比 write_all 少一轮矩阵拆列。
        """
        assert count <= self.capacity, '超出实例容量'
        bufs = self.vlist.domain.attrib_name_buffers
        if count > 0:
            for name, data in arrays.items():
                assert len(data) >= count * 4, f'{name} 数据不足'
                bufs[name].set_region(0, count, data[:count * 4])
        self.count = count
        return self

    def set_instances(self, matrices, tints=None):
        """批量写入（逐实例路径；大量实例请用 ``write_all``）。"""
        n = min(len(matrices), self.capacity)
        for i in range(n):
            self.set_instance(i, matrices[i],
                              tints[i] if tints else (1.0, 1.0, 1.0, 0.0))
        self.count = n
        return self

    def refresh_bounds(self):
        """按当前实例矩阵刷新整体包围盒（剔除用；实例动得多时要重算）。"""
        boxes = []
        for i in range(self.count):
            boxes.append(self.data.aabb().transformed(self._matrices[i]))
        if boxes:
            acc = boxes[0].copy()
            for b in boxes[1:]:
                acc.union(b)
            self.world_aabb = acc
        else:
            self.world_aabb = AABB()
        return self.world_aabb

    # ---- 绘制 ----
    def draw(self, unit_start=0, skip_material=False):
        if not self.visible or self.count <= 0:
            return 0
        p = self.program
        if self._uploaded_model != mat4_identity():
            p['model'] = mat4_to_Mat4(mat4_identity())
            p['normal_mat'] = mat4_to_Mat4(_pad3(normal_matrix(mat4_identity())))
            self._uploaded_model = mat4_identity()
        if not skip_material:
            self.material.apply(p, unit_start)
        _set_instance_count(self.vlist, self.count)
        self.vlist.draw(GL_TRIANGLES)
        return 0 if skip_material else 1

    def delete(self):
        try:
            self.vlist.delete()
        except Exception:
            pass

    def __repr__(self):
        return (f'InstancedMesh({self.name!r}, count={self.count}/'
                f'{self.capacity})')
