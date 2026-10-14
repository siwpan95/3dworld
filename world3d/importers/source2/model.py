# -*- coding: utf-8 -*-
"""vmdl_c（Source 2 模型容器）→ :class:`MeshData` —— 纯 Python，零依赖。

把一条 vmdl_c 数据流拆成：

```
容器头 + 块表 → CTRL（KV3）拿布局 → 逐流解码（meshopt/raw） → MDAT 拿 drawcall
            → 合并多流顶点（位置/UV）+ 索引切片 → 每个 drawcall 一个 MeshData
```

转换约定（§18.5）
-----------------
* **单位**：Source 单位 1:1 直通（1 unit ≈ 1.905 cm；由使用方决定是否换算）；
* **轴**：源数据 **Z-up**；``up='z'``（默认）原样保留，``up='y'`` 转引擎 Y-up
  （``(x, y, z) → (x, z, -y)``）；
* **法线**：首版**不解码** CompressedTangentFrame（打包切线帧，属二期）——
  调用方用 ``MeshData.compute_normals()`` / ``compute_tangents()`` 重建；
* **顶点不被压缩的流**（``m_bMeshoptCompressed=False``）直接按 stride 读；
  zstd 压缩（Source 2 资产实测未见）不支持；
* 顶点属性格式（DXGI）只解位置与 UV 需要的那几种；其余（texcoord3/打包法线）
  原样跳过，布局信息保留在 ``model.drawcalls``。

参考：ValveResourceFormat 的 vmdl_c 容器与 MVTX/MIDX 解析（MIT）。
"""

import os
import struct

from world3d.engine.geometry import MeshData
from world3d.importers.source2 import kv3, meshopt

__all__ = ['Source2Error', 'Source2Model', 'load', 'load_file', 'read_blocks',
           'DXGI_FORMATS']

_CONTAINER = struct.Struct('<IHHII')    # 文件大小 / 头版本 / 版本 / 块表偏移 / 块数
_BLOCK = struct.Struct('<4sII')         # FourCC / 偏移(相对本字段) / 大小

#: DXGI 顶点属性格式 → (种类, 字节数)。只需解码位置/UV 涉及的几种。
DXGI_FORMATS = {
    6: ('f32x3', 12),       # R32G32B32_FLOAT（POSITION）
    16: ('f32x2', 8),       # R32G32_FLOAT
    34: ('f16x2', 4),       # R16G16_FLOAT
    35: ('unorm16x2', 4),   # R16G16_UNORM（texcoord3）
    37: ('snorm16x2', 4),   # R16G16_SNORM（LowPrecisionUv）
    41: ('f32', 4),         # R32_FLOAT
    42: ('u32', 4),         # R32_UINT（CompressedTangentFrame）
    28: ('unorm8x4', 4),    # R8G8B8A8_UNORM
    30: ('u8x4', 4),        # R8G8B8A8_UINT
    2: ('f32x4', 16),       # R32G32B32A32_FLOAT
}


class Source2Error(ValueError):
    """vmdl_c 数据非法（容器/块表/布局不符等）。"""


class Source2Model:
    """解析结果：一组 MeshData + 平行元数据（材质 / 密度 / drawcall 原文）。"""

    __slots__ = ('name', 'meshes', 'materials', 'uv_densities', 'drawcalls',
                 'bounds_min', 'bounds_max', 'vertex_count', 'index_count',
                 'layout')

    def __init__(self):
        self.name = ''
        self.meshes = []          # list[MeshData]
        self.materials = []       # 与 meshes 平行
        self.uv_densities = []    # m_flUvDensity（缺失为 0.0）
        self.drawcalls = []       # drawcall 原文字典（保留全部字段）
        self.bounds_min = None    # MDAT m_vMinBounds（世界坐标）
        self.bounds_max = None
        self.vertex_count = 0     # 顶点流总数（未按 drawcall 切）
        self.index_count = 0
        self.layout = []          # 顶点流布局（调试/工具用）

    def __repr__(self):
        return (f'Source2Model({self.name!r}, meshes={len(self.meshes)}, '
                f'verts={self.vertex_count}, idx={self.index_count})')


# ------------------------------------------------------------------ 容器 --

def read_blocks(data):
    """解析容器块表 → 有序列表 ``[(fourcc, offset, size), ...]``（全局索引）。"""
    if len(data) < _CONTAINER.size:
        raise Source2Error('文件过小，不是资源容器')
    file_size, _hdr_ver, _ver, block_offset, block_count = \
        _CONTAINER.unpack_from(data, 0)
    if block_count > 4096:
        raise Source2Error(f'块数异常：{block_count}')
    if block_offset < 8:
        raise Source2Error(f'块表偏移异常：{block_offset}')
    blocks = []
    # blockOffset 从"头 8 字节之后"起算（VRF：Position += blockOffset - 8）
    pos = 8 + block_offset
    for _ in range(block_count):
        if pos + _BLOCK.size > len(data):
            raise Source2Error('块表截断')
        fcc, rel, size = _BLOCK.unpack_from(data, pos)
        abs_off = pos + 4 + rel
        if abs_off + size > len(data):
            raise Source2Error(f'块 {fcc!r} 越界')
        blocks.append((fcc.decode('ascii', 'replace'), abs_off, size))
        pos += _BLOCK.size
    return blocks


def _block_by_index(data, blocks, index, expect, what):
    if not (0 <= index < len(blocks)):
        raise Source2Error(f'{what} 引用的块索引越界：{index}')
    fcc, off, size = blocks[index]
    if expect and fcc not in expect:
        raise Source2Error(f'{what} 块类型不符：期望 {expect}，实际 {fcc!r}')
    return data[off:off + size]


# ------------------------------------------------------------------ 属性 --

def _decode_attr(raw, stride, fmt_code, offset, count):
    """按布局解出逐顶点属性值（list[float] 或 list[int]）。"""
    entry = DXGI_FORMATS.get(fmt_code)
    if entry is None:
        raise Source2Error(f'未支持的顶点格式 {fmt_code}')
    kind, size = entry
    if offset + size > stride:
        raise Source2Error(f'属性越界：offset {offset}+{size} > stride {stride}')
    out = []
    if kind == 'f32x3':
        for i in range(count):
            out.extend(struct.unpack_from('<3f', raw, i * stride + offset))
    elif kind == 'f32x2':
        for i in range(count):
            out.extend(struct.unpack_from('<2f', raw, i * stride + offset))
    elif kind == 'f16x2':
        for i in range(count):
            out.extend(struct.unpack_from('<2e', raw, i * stride + offset))
    elif kind == 'unorm16x2':
        for i in range(count):
            a, b = struct.unpack_from('<2H', raw, i * stride + offset)
            out.extend((a / 65535.0, b / 65535.0))
    elif kind == 'snorm16x2':
        for i in range(count):
            a, b = struct.unpack_from('<2h', raw, i * stride + offset)
            out.extend((a / 32767.0, b / 32767.0))
    elif kind == 'unorm8x4':
        for i in range(count):
            a, b, c, d = struct.unpack_from('<4B', raw, i * stride + offset)
            out.extend((a / 255.0, b / 255.0, c / 255.0, d / 255.0))
    elif kind == 'u8x4':
        for i in range(count):
            out.extend(struct.unpack_from('<4B', raw, i * stride + offset))
    elif kind == 'f32':
        for i in range(count):
            out.append(struct.unpack_from('<f', raw, i * stride + offset)[0])
    elif kind == 'u32':
        for i in range(count):
            out.append(struct.unpack_from('<I', raw, i * stride + offset)[0])
    elif kind == 'f32x4':
        for i in range(count):
            out.extend(struct.unpack_from('<4f', raw, i * stride + offset))
    return out


def _find_layout_field(st, names, index=None):
    """在已处理的顶点流布局里找字段（大写语义名；可限定语义序号）。"""
    for f in st['layout']:
        if f['sem'] in names and (index is None or f['index'] == index):
            return f
    return None


def _plain(v):
    return v.value if isinstance(v, kv3.FlaggedValue) else v


# ------------------------------------------------------------------ 主流程 --

def load(data, name='', up='z'):
    """解析 vmdl_c 字节流 → :class:`Source2Model`。"""
    if up not in ('z', 'y'):
        raise Source2Error("up 只能是 'z'（原样）或 'y'（转 Y-up）")
    blocks = read_blocks(data)
    by_fcc = {fcc: i for i, (fcc, _o, _s) in enumerate(blocks)}
    if 'CTRL' not in by_fcc:
        raise Source2Error('缺少 CTRL 块')

    ctrl = kv3.read(_block_by_index(data, blocks, by_fcc['CTRL'], ('CTRL',), 'CTRL'))
    emeshes = ctrl.get('embedded_meshes') or []
    if not emeshes:
        raise Source2Error('CTRL 里没有 embedded_meshes')
    emesh = emeshes[0]
    if emesh.get('m_nVBIBBlock', -1) not in (-1, None):
        raise NotImplementedError('新式 VBIB 块引用暂未支持（本模块面向 MVTX/MIDX 内联块）')

    model = Source2Model()
    model.name = name

    # ---- 解码顶点流 ----
    streams = []          # dict(layout, raw, count, stride)
    for vb in emesh.get('m_vertexBuffers', []):
        count = vb['m_nElementCount']
        stride = vb['m_nElementSizeInBytes']
        raw = _block_by_index(data, blocks, vb['m_nBlockIndex'], ('MVTX',), 'MVTX')
        if vb.get('m_bCompressedZSTD'):
            raise NotImplementedError('zstd 压缩的顶点流暂未支持')
        if vb.get('m_bMeshoptCompressed'):
            raw = meshopt.decode_vertex_buffer(count, stride, raw)
        else:
            if len(raw) < count * stride:
                raise Source2Error('顶点流字节数不足')
            raw = raw[:count * stride]
        fields = []
        for f in vb.get('m_inputLayoutFields', []):
            fmt = f.get('m_Format', 0)
            if fmt not in DXGI_FORMATS:
                raise Source2Error(f'未支持的顶点格式 {fmt}')
            fields.append({
                'sem': str(f.get('m_pSemanticName', '')).upper(),
                'index': f.get('m_nSemanticIndex', 0),
                'shader': f.get('m_szShaderSemantic', ''),
                'format': fmt,
                'offset': f.get('m_nOffset', 0),
            })
        streams.append(dict(layout=fields, raw=raw, count=count, stride=stride))
        model.layout.append(fields)

    # ---- 解码索引流 ----
    indices = []
    for ib in emesh.get('m_indexBuffers', []):
        count = ib['m_nElementCount']
        isize = ib['m_nElementSizeInBytes']
        raw = _block_by_index(data, blocks, ib['m_nBlockIndex'], ('MIDX',), 'MIDX')
        if ib.get('m_bCompressedZSTD'):
            raise NotImplementedError('zstd 压缩的索引流暂未支持')
        if ib.get('m_bMeshoptCompressed'):
            if ib.get('m_bMeshoptIndexSequence'):
                raw = meshopt.decode_index_sequence(count, isize, raw)
            else:
                raw = meshopt.decode_index_buffer(count, isize, raw)
        else:
            if len(raw) < count * isize:
                raise Source2Error('索引流字节数不足')
            raw = raw[:count * isize]
        fmt = 'H' if isize == 2 else 'I'
        indices.extend(struct.unpack(f'<{count}{fmt}', raw))

    model.index_count = len(indices)

    # ---- 合并顶点（跨流：位置/UV 可能在不同流） ----
    vcount = None
    positions = uvs = None
    for st in streams:
        if vcount is None:
            vcount = st['count']
        elif st['count'] != vcount:
            raise Source2Error(f'顶点流元素数不一致：{vcount} != {st["count"]}')
    for st in streams:
        f = _find_layout_field(st, {'POSITION'}, 0) or _find_layout_field(st, {'POSITION'})
        if f and positions is None:
            positions = _decode_attr(st['raw'], st['stride'], f['format'],
                                     f['offset'], st['count'])
    for st in streams:
        f = _find_layout_field(st, {'TEXCOORD'}, 0) or _find_layout_field(st, {'TEXCOORD'})
        if f and uvs is None:
            uvs = _decode_attr(st['raw'], st['stride'], f['format'],
                               f['offset'], st['count'])
    if positions is None:
        raise Source2Error('没有 POSITION 属性')
    vcount = vcount or 0
    model.vertex_count = vcount
    if len(positions) != vcount * 3:
        raise Source2Error('POSITION 维度异常')
    if up == 'y':
        positions = _to_y_up(positions)

    # ---- MDAT（drawcall / 材质 / 包围盒） ----
    drawcalls = []
    if 'MDAT' in by_fcc:
        mdat = kv3.read(_block_by_index(data, blocks, by_fcc['MDAT'],
                                        ('MDAT',), 'MDAT'))
        for so in mdat.get('m_sceneObjects', []) or []:
            lo = so.get('m_vMinBounds')
            hi = so.get('m_vMaxBounds')
            if lo and model.bounds_min is None:
                model.bounds_min = tuple(float(v) for v in lo)
                model.bounds_max = tuple(float(v) for v in hi)
            for dc in so.get('m_drawCalls', []) or []:
                drawcalls.append(dc)
    if not drawcalls:
        drawcalls = [{'m_nStartIndex': 0, 'm_nIndexCount': len(indices),
                      'm_nBaseVertex': 0}]

    # ---- 逐 drawcall 切片出 MeshData ----
    for di, dc in enumerate(drawcalls):
        start = int(dc.get('m_nStartIndex', 0) or 0)
        count = int(dc.get('m_nIndexCount', len(indices)) or 0)
        base = int(dc.get('m_nBaseVertex', 0) or 0)
        sub = indices[start:start + count]
        if base:
            sub = [i + base for i in sub]
        if sub and max(sub) >= vcount:
            raise Source2Error(f'drawcall {di} 索引越界（max={max(sub)}，vcount={vcount}）')
        mesh = MeshData(list(positions), None,
                        list(uvs) if uvs else None, None, sub, None,
                        name=f'{name or "vmdl"}:{emesh.get("m_Name", "mesh")}:dc{di}')
        model.meshes.append(mesh)
        mat = _plain(dc.get('m_material'))
        model.materials.append(str(mat) if mat is not None else '')
        model.uv_densities.append(float(dc.get('m_flUvDensity', 0.0) or 0.0))
        model.drawcalls.append(dc)
    return model


def load_file(path, up='z'):
    """从磁盘读取 vmdl_c 文件并解析。"""
    with open(path, 'rb') as f:
        data = f.read()
    return load(data, name=os.path.basename(path), up=up)


def _to_y_up(positions):
    """Z-up → Y-up：``(x, y, z) → (x, z, -y)``。"""
    out = [0.0] * len(positions)
    for i in range(0, len(positions), 3):
        x, y, z = positions[i], positions[i + 1], positions[i + 2]
        out[i], out[i + 1], out[i + 2] = x, z, -y
    return out
