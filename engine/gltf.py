# -*- coding: utf-8 -*-
"""glTF 2.0 加载器（含 GLB 容器），纯 Python，不碰 GL。

支持范围（对齐规范的"能可靠渲染的最小集"）::

    容器      .gltf（JSON + 外部 .bin / data:URI） 与 .glb（JSON + BIN 块）
    几何      mesh.primitives：POSITION / NORMAL / TEXCOORD_0 / TANGENT / COLOR_0 /
              JOINTS_0 / WEIGHTS_0 / indices
    蒙皮      skins[]：joints（节点下标）+ inverseBindMatrices -> 关节矩阵管线（P2-3）
    访问器    componentType 全类型、type 全类型、normalized、byteStride 交错
              sparse（稀疏替换：base + indices/values）
    材质      pbrMetallicRoughness（baseColor / metallic / roughness /
              emissive / alphaMode / doubleSided），贴图槽只解析不强制加载
    节点      matrix 或 TRS（matrix 按规范分解为 TRS）
    动画      sampler(LINEAR/STEP/CUBICSPLINE) + channel(target node/path)
              -> 直接产出 engine.timeline 的 Sampler / Channel

GLB 布局（12 字节头 + 块序列）：uint32 magic('glTF') / version / length，
随后是 JSON 块（必须第一个）与可选的 BIN 块；每块 4 字节对齐。
"""
import base64
import json
import math
import os
import struct

from pyglet.math import Vec3

from engine.geometry import MeshData
from engine.material import Material
from engine.timeline import Sampler

__all__ = ['GLTF', 'load', 'GLTFError']

# componentType -> (struct 格式, 每分量字节数)
COMPONENT = {
    5120: ('b', 1),    # BYTE
    5121: ('B', 1),    # UNSIGNED_BYTE
    5122: ('h', 2),    # SHORT
    5123: ('H', 2),    # UNSIGNED_SHORT
    5125: ('I', 4),    # UNSIGNED_INT
    5126: ('f', 4),    # FLOAT
}
# type -> 分量数
NCOMP = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4,
         'MAT2': 4, 'MAT3': 9, 'MAT4': 16}

# 4×4 单位矩阵（列主序）—— skin 省略 inverseBindMatrices 时的默认值
IDENTITY4 = (1.0, 0.0, 0.0, 0.0,
             0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0,
             0.0, 0.0, 0.0, 1.0)

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942


class GLTFError(Exception):
    """glTF 解析失败。"""


class GLTF:
    """一份已解析的 glTF 资产。"""

    def __init__(self, json_data, buffers=None, base_dir=''):
        self.json = json_data
        self.buffers = buffers or []
        self.base_dir = base_dir
        self._accessor_cache = {}

    # ------------------------------------------------------------ 加载 --
    @staticmethod
    def load(path):
        """按扩展名分派：.glb 走二进制容器，.gltf 走 JSON。"""
        path = os.path.abspath(path)
        ext = os.path.splitext(path)[1].lower()
        with open(path, 'rb') as f:
            raw = f.read()
        if ext == '.glb':
            return GLTF._load_glb(raw, os.path.dirname(path))
        if ext in ('.gltf', '.json'):
            return GLTF._load_json(raw, os.path.dirname(path))
        raise GLTFError(f'不支持的扩展名: {ext}')

    @staticmethod
    def _load_json(raw, base_dir):
        data = json.loads(raw.decode('utf-8'))
        buffers = [GLTF._resolve_buffer(b, base_dir)
                   for b in data.get('buffers', [])]
        return GLTF(data, buffers, base_dir)

    @staticmethod
    def _load_glb(raw, base_dir):
        if len(raw) < 12:
            raise GLTFError('GLB 太短')
        magic, version, length = struct.unpack_from('<III', raw, 0)
        if magic != GLB_MAGIC:
            raise GLTFError(f'不是 GLB：magic=0x{magic:08X}')
        if len(raw) < length:
            raise GLTFError(f'GLB 被截断：{len(raw)} < {length}')
        pos = 12
        json_data, bin_data = None, None
        while pos + 8 <= length:
            chunk_len, chunk_type = struct.unpack_from('<II', raw, pos)
            body = raw[pos + 8:pos + 8 + chunk_len]
            if chunk_type == CHUNK_JSON:
                json_data = json.loads(body.decode('utf-8').rstrip('\x20'))
            elif chunk_type == CHUNK_BIN:
                bin_data = body
            # 未知块：按规范忽略（扩展可能引用）
            pos += 8 + chunk_len + ((4 - chunk_len % 4) % 4)
        if json_data is None:
            raise GLTFError('GLB 缺少 JSON 块')
        buffers = []
        for b in json_data.get('buffers', []):
            if 'uri' in b:
                buffers.append(GLTF._resolve_buffer(b, base_dir))
            else:
                # 无 uri = 引用 GLB 的 BIN 块（规范：GLB-stored buffer）
                if bin_data is None:
                    raise GLTFError('buffer 无 uri 但 GLB 没有 BIN 块')
                buffers.append(bin_data[:b.get('byteLength', len(bin_data))])
        return GLTF(json_data, buffers, base_dir)

    @staticmethod
    def _resolve_buffer(buffer, base_dir):
        uri = buffer.get('uri', '')
        if uri.startswith('data:'):
            header, _, payload = uri.partition(',')
            if 'base64' not in header:
                raise GLTFError(f'不支持的 data URI: {header}')
            return base64.b64decode(payload)
        if uri.startswith('data:;'):
            return base64.b64decode(uri.split(',', 1)[1])
        path = os.path.join(base_dir, uri)
        with open(path, 'rb') as f:
            return f.read()

    # -------------------------------------------------------- 访问器 --
    def accessor(self, index):
        """读一个 accessor，返回扁平数值列表。

        - 按 componentType 解包（小端）；
        - 支持 ``byteStride`` 交错数据；
        - 支持 sparse（先取 base 再按 indices 覆盖 values）；
        - ``normalized`` 的整数按规范归一化到 [0,1] / [-1,1]。
        """
        if index in self._accessor_cache:
            return self._accessor_cache[index]
        accs = self.json.get('accessors', [])
        if not (0 <= index < len(accs)):
            raise GLTFError(f'accessor 下标越界: {index}')
        acc = accs[index]
        ctype = acc.get('componentType', 5126)
        n = NCOMP[acc['type']]
        count = acc['count']
        fmt, size = COMPONENT[ctype]
        values = []

        if 'bufferView' in acc:
            view = self.json['bufferViews'][acc['bufferView']]
            buf = self.buffers[view['buffer']]
            base_off = view.get('byteOffset', 0) + acc.get('byteOffset', 0)
            stride = view.get('byteStride') or (size * n)
            unpack = struct.Struct('<' + fmt * n).unpack_from
            for i in range(count):
                off = base_off + i * stride
                if off + size * n > len(buf):
                    raise GLTFError(f'accessor {index} 越界读取')
                values.extend(unpack(buf, off))
        else:
            # 规范：无 bufferView 时以 0 初始化
            values = [0.0] * (count * n)

        if acc.get('normalized', False):
            values = [_normalize_int(v, ctype) for v in values]

        sparse = acc.get('sparse')
        if sparse:
            idx_view = self.json['bufferViews'][sparse['indices']['bufferView']]
            val_view = self.json['bufferViews'][sparse['values']['bufferView']]
            idx_buf = self.buffers[idx_view['buffer']]
            val_buf = self.buffers[val_view['buffer']]
            idx_ctype = sparse['indices'].get('componentType', 5123)
            ifmt, isize = COMPONENT[idx_ctype]
            idx_off = idx_view.get('byteOffset', 0) + sparse['indices'].get('byteOffset', 0)
            val_off = val_view.get('byteOffset', 0) + sparse['values'].get('byteOffset', 0)
            for k in range(sparse['count']):
                i = struct.unpack_from('<' + ifmt, idx_buf, idx_off + k * isize)[0]
                chunk = struct.unpack_from('<' + fmt * n, val_buf,
                                           val_off + k * size * n)
                chunk = [_normalize_int(v, ctype) for v in chunk] \
                    if acc.get('normalized', False) else list(chunk)
                values[i * n:(i + 1) * n] = chunk

        self._accessor_cache[index] = values
        return values

    def accessor_minmax(self, index):
        return (self.json.get('accessors', [{}])[index].get('min'),
                self.json.get('accessors', [{}])[index].get('max'))

    # ------------------------------------------------------------ 网格 --
    def primitive_meshdata(self, primitive):
        """一个 primitive -> MeshData（含法线/UV/切线，缺法线时计算）。"""
        attrs = primitive.get('attributes', {})
        if 'POSITION' not in attrs:
            raise GLTFError('primitive 缺少 POSITION')
        positions = self.accessor(attrs['POSITION'])
        normals = self.accessor(attrs['NORMAL']) if 'NORMAL' in attrs else []
        uvs = self.accessor(attrs['TEXCOORD_0']) if 'TEXCOORD_0' in attrs else []
        tangents = self.accessor(attrs['TANGENT']) if 'TANGENT' in attrs else []
        if tangents:
            # glTF 的 TANGENT 是 vec4（w = 副切线朝向），本引擎只用 xyz
            tangents = [v for i, v in enumerate(tangents) if i % 4 != 3]
        colors = []
        if 'COLOR_0' in attrs:
            colors = self.accessor(attrs['COLOR_0'])
            if len(colors) == len(positions) // 3 * 3:      # VEC3 -> RGBA
                colors = [c for i, v in enumerate(colors)
                          for c in (v, v, v, 1.0)]
        # 蒙皮（P2-3）：JOINTS_0（VEC4 整数关节下标）+ WEIGHTS_0（VEC4，可能 normalized）
        joints = weights = None
        if 'JOINTS_0' in attrs and 'WEIGHTS_0' in attrs:
            joints = [int(j) for j in self.accessor(attrs['JOINTS_0'])]
            weights = _normalize_weights(self.accessor(attrs['WEIGHTS_0']))
        indices = []
        if 'indices' in primitive:
            indices = [int(i) for i in self.accessor(primitive['indices'])]
        md = MeshData(positions, normals, uvs, colors, indices, tangents,
                      name=primitive.get('name', ''),
                      joints=joints, weights=weights)
        if not md.normals:
            md.compute_normals()
        return md

    def material(self, index):
        """材质索引 -> :class:`~engine.material.Material`（贴图槽留空）。"""
        if index is None or not (0 <= index < len(self.json.get('materials', []))):
            return Material(name='default')
        m = self.json['materials'][index]
        pbr = m.get('pbrMetallicRoughness', {})
        bc = pbr.get('baseColorFactor', [1, 1, 1, 1])
        em = m.get('emissiveFactor', [0, 0, 0])
        return Material(
            name=m.get('name', f'material_{index}'),
            base_color=(bc[0], bc[1], bc[2]),
            metallic=float(pbr.get('metallicFactor', 1.0)),
            roughness=float(pbr.get('roughnessFactor', 1.0)),
            emissive=(em[0], em[1], em[2]),
            alpha=float(bc[3]) if len(bc) > 3 else 1.0,
            alpha_mode=m.get('alphaMode', 'OPAQUE'),
            alpha_cutoff=float(m.get('alphaCutoff', 0.5)),
            double_sided=bool(m.get('doubleSided', False)))

    def meshes(self):
        """返回 [(mesh_name, [(MeshData, material_index), ...]), ...]。"""
        out = []
        for mi, mesh in enumerate(self.json.get('meshes', [])):
            prims = []
            for p in mesh.get('primitives', []):
                prims.append((self.primitive_meshdata(p), p.get('material')))
            out.append((mesh.get('name', f'mesh_{mi}'), prims))
        return out

    # ------------------------------------------------------------ 蒙皮 --
    def skins(self):
        """解析 ``skins[]`` -> ``[{'name', 'joints', 'inverse_bind', 'skeleton'}]``。

        - ``joints``：**节点下标**列表（对应 ``json['nodes']``）；
        - ``inverse_bind``：每关节一个列主序 4×4 矩阵（16 元组）；规范允许省略，
          省略时取单位矩阵；
        - ``skeleton``：骨架根节点下标（可选）。
        """
        out = []
        for sk in self.json.get('skins', []):
            joints = [int(j) for j in sk.get('joints', [])]
            if 'inverseBindMatrices' in sk:
                vals = self.accessor(sk['inverseBindMatrices'])
                ibm = [tuple(vals[i * 16:(i + 1) * 16])
                       for i in range(len(vals) // 16)]
                if len(ibm) != len(joints):     # 规范要求等长；容错：补齐/截断
                    ibm = (ibm + [IDENTITY4] * len(joints))[:len(joints)]
            else:
                ibm = [IDENTITY4] * len(joints)
            out.append({'name': sk.get('name', ''),
                        'joints': joints,
                        'inverse_bind': ibm,
                        'skeleton': sk.get('skeleton')})
        return out

    # ------------------------------------------------------------ 节点 --
    def node_trs(self, index):
        """节点变换 -> (translation, rotation(wxyz), scale)。

        ``matrix`` 与 TRS 二选一；matrix 按规范必须可分解为 TRS（无斜切），
        这里调 ``mathkit.trs_decompose``。
        """
        from engine.mathkit import trs_decompose
        node = self.json['nodes'][index]
        if 'matrix' in node:
            m = node['matrix']
            # glTF 的 matrix 是列主序，与本引擎一致
            return trs_decompose(tuple(m))
        t = node.get('translation', [0.0, 0.0, 0.0])
        r = node.get('rotation', [0.0, 0.0, 0.0, 1.0])   # 规范是 xyzw
        s = node.get('scale', [1.0, 1.0, 1.0])
        return (tuple(t), (r[3], r[0], r[1], r[2]), tuple(s))

    def scene_nodes(self, scene_index=0):
        """返回场景的根节点下标列表。"""
        scenes = self.json.get('scenes', [])
        if not scenes:
            return list(range(len(self.json.get('nodes', []))))
        idx = scenes[min(scene_index, len(scenes) - 1)]
        return idx.get('nodes', [])

    def build_scene(self, scene_index=0):
        """构建 :class:`~engine.scene.Scene`（含节点层级，不含 GPU 资源）。

        返回 ``(scene, pending)``，``pending`` 是
        ``[(node, MeshData, material_index), ...]``——GPU 网格要等有 GL 上下文
        才能建，所以这里只把"该建什么"列出来交给调用方。
        """
        scene, pending, _ = self.build_scene_ex(scene_index)
        return scene, pending

    def build_scene_ex(self, scene_index=0):
        """同 :meth:`build_scene`，但额外返回 ``node_map``（``{node_index: Node}``）。

        ``node_map`` 供**蒙皮**使用：glTF ``skin.joints`` 是节点下标，需要映射回
        已构建的 ``Node``（见 ``engine.skin.make_skin_from_gltf``）。
        返回 ``(scene, pending, node_map)``。
        """
        from engine.scene import Scene
        nodes = self.json.get('nodes', [])
        scene = Scene(name=(self.json.get('scenes') or [{}])[min(
            scene_index, max(0, len(self.json.get('scenes', [])) - 1))]
            .get('name', 'gltf_scene'))
        pending = []
        built = {}

        def build(i, parent):
            if i in built:
                return built[i]
            t, r, s = self.node_trs(i)
            n = parent.add(_mk_node(nodes[i].get('name', f'node_{i}'), t, r, s))
            built[i] = n
            mesh_index = nodes[i].get('mesh')
            if mesh_index is not None:
                name, prims = self.meshes()[mesh_index]
                for md, mat_index in prims:
                    child = _mk_node(f'{n.name}_prim', (0, 0, 0),
                                     (1, 0, 0, 0), (1, 1, 1))
                    n.add(child)
                    pending.append((child, md, mat_index))
            for c in nodes[i].get('children', []):
                build(c, n)
            return n

        for root in self.scene_nodes(scene_index):
            build(root, scene.root)
        return scene, pending, built

    # ------------------------------------------------------------ 动画 --
    def animations(self):
        """解析动画 -> [{name, duration, channels:[(node_index, path, Sampler)]}]。

        ``path`` 映射：translation / rotation / scale / weights(暂不支持)。
        CUBICSPLINE 的 output 每帧 3 个值（in-tangent, value, out-tangent），
        这里按规范切成三元组交给 Sampler。
        """
        out = []
        for anim in self.json.get('animations', []):
            channels = []
            duration = 0.0
            for ch in anim.get('channels', []):
                sampler = anim['samplers'][ch['sampler']]
                times = self.accessor(sampler['input'])
                raw = self.accessor(sampler['output'])
                interp = sampler.get('interpolation', 'LINEAR')
                path = ch['target']['path']
                node = ch['target'].get('node')
                if path == 'weights':
                    continue                       # morph target 暂不支持
                ncomp = 3 if path == 'translation' or path == 'scale' else (
                    4 if path == 'rotation' else 1)
                values = _group_output(raw, ncomp, interp)
                try:
                    sp = Sampler(times, values, interp)
                except AssertionError as e:
                    raise GLTFError(f'动画采样器非法: {e}') from e
                duration = max(duration, times[-1] if times else 0.0)
                channels.append((node, path, sp))
            out.append({'name': anim.get('name', ''),
                        'duration': duration,
                        'channels': channels})
        return out

    def summary(self):
        """一句话概览（调试/HUD 用）。"""
        j = self.json
        return (f"glTF: scenes={len(j.get('scenes', []))} "
                f"nodes={len(j.get('nodes', []))} "
                f"meshes={len(j.get('meshes', []))} "
                f"materials={len(j.get('materials', []))} "
                f"animations={len(j.get('animations', []))}")

    def __repr__(self):
        return f'<GLTF {self.summary()}>'


# ------------------------------------------------------------------ 工具 --
def _normalize_weights(vals, n=4):
    """把每 ``n`` 个权重归一化到和为 1（全 0 时该顶点首权重置 1）。

    蒙皮矩阵是关节矩阵的**仿射组合**（凸组合），权重和 ≠ 1 会让顶点被整体
    缩放/偏移。部分资产的 WEIGHTS_0 未归一化（或量化后和≠1），故在此统一。
    """
    out = list(vals)
    for i in range(0, len(out), n):
        s = sum(out[i:i + n])
        if s > 1e-12:
            for k in range(n):
                out[i + k] /= s
        else:
            out[i] = 1.0
    return out


def _normalize_int(v, ctype):
    if ctype == 5120:            # BYTE:  (v - (-128)) / 255 的简化形式
        return max(v / 127.0, -1.0)
    if ctype == 5121:
        return v / 255.0
    if ctype == 5122:
        return max(v / 32767.0, -1.0)
    if ctype == 5123:
        return v / 65535.0
    if ctype == 5125:
        return v / 4294967295.0
    return float(v)


def _group_output(raw, ncomp, interp):
    """把扁平 output 数组按分量分组（旋转转成 pyglet Quaternion）。"""
    n = len(raw) // ncomp
    if interp == 'CUBICSPLINE':
        # 每帧 3 组：(in_tangent, value, out_tangent)
        groups = [tuple(raw[i * ncomp * 3 + k * ncomp:
                             i * ncomp * 3 + (k + 1) * ncomp] for k in range(3))
                  for i in range(len(raw) // (ncomp * 3))]
        out = []
        for a, v, b in groups:
            out.append((_as_value(a, ncomp), _as_value(v, ncomp),
                        _as_value(b, ncomp)))
        return out
    return [_as_value(raw[i * ncomp:(i + 1) * ncomp], ncomp) for i in range(n)]


def _as_value(vals, ncomp):
    if ncomp == 3:
        return Vec3(vals[0], vals[1], vals[2])
    if ncomp == 4:
        from pyglet.math import Quaternion
        # glTF 存的是 xyzw，pyglet 要 wxyz
        return Quaternion(vals[3], vals[0], vals[1], vals[2])
    return float(vals[0])


def _mk_node(name, t, r, s):
    from engine.scene import Node
    return Node(name=name, translation=t, rotation=r, scale=s)


def load(path):
    """便捷入口：``gltf = load('model.glb')``。"""
    return GLTF.load(path)
