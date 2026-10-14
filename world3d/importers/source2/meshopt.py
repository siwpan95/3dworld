# -*- coding: utf-8 -*-
"""meshopt 顶点/索引编解码器解码 —— 纯 Python，零依赖。

Source 2 的 MVTX/MIDX 块是 **meshoptimizer 编解码流**（首字节 ``0xa1`` = 顶点
codec v1、``0xe1`` = 索引 codec v1）。本模块按官方实现（zeux/meshoptimizer，
MIT）的**标量解码路径**逐位移植，输出与官方解码器**逐字节一致**（研究期用官方
WASM 解码器对拍 + 真实地图块复核，见 §18）。

支持：

* :func:`decode_vertex_buffer` —— 顶点 codec v0/v1（``0xa0``/``0xa1``）；
* :func:`decode_index_buffer` —— 索引 codec v1（``0xe0``/``0xe1``，三角形列表）；
* :func:`decode_index_sequence` —— 索引序列（``0xd0``/``0xd1``）。

格式要点（解码视角）
--------------------
* 顶点流 = ``[header][每 16 顶点块的编码数据...][tail]``；tail = 末顶点
  （作为首块差分种子）+ **通道表**（每 4 字节一组，1 字节：高 4 位 XOR 旋转量、
  低 2 位编码类型：0=u8 差 / 1=u16 差 / 2=u32 XOR 旋转 / 3=非法）。
* 每块内：每 4 字节组一个控制字节（每 2 位一段）→ 0/1 = 位宽压缩字节流、
  2 = 全零、3 = 字面量；位宽压缩把 16 字节一组打包（1/2/4 位 + 越界补全字节，
  1 位组的存放位序被反转）。
* 索引流 = ``[header][每三角形 1 字节 code][变长数据...][16 字节 codeaux 表]``；
  顶点索引用边 FIFO(16) + 顶点 FIFO(16) 重建，自由索引走 zigzag vbyte 差分。

参考：meshoptimizer ``src/vertexcodec.cpp`` / ``src/indexcodec.cpp``（MIT）。
"""

__all__ = [
    'MeshoptError', 'decode_vertex_buffer', 'decode_index_buffer',
    'decode_index_sequence',
]

# ---------------------------------------------------------------- 常量 --

_VERTEX_HEADER = 0xA0
_VERTEX_VERSION_MAX = 1
_BLOCK_SIZE_BYTES = 8192
_BLOCK_MAX_SIZE = 256
_BYTE_GROUP_SIZE = 16
_BYTE_GROUP_DECODE_LIMIT = 24
_TAIL_MIN_V0 = 32
_TAIL_MIN_V1 = 24
_BITS_V0 = (0, 2, 4, 8)
_BITS_V1 = (0, 1, 2, 4, 8)

_INDEX_HEADER = 0xE0
_SEQUENCE_HEADER = 0xD0
_INDEX_VERSION_MAX = 1


class MeshoptError(ValueError):
    """meshopt 数据非法（magic/截断/校验失败等）。"""


def _rotl32(v, r):
    return ((v << r) | (v >> ((32 - r) & 31))) & 0xFFFFFFFF


# ------------------------------------------------------------ 顶点 codec --

def _get_vertex_block_size(vertex_size):
    result = (_BLOCK_SIZE_BYTES // vertex_size) & ~(_BYTE_GROUP_SIZE - 1)
    return result if result < _BLOCK_MAX_SIZE else _BLOCK_MAX_SIZE


def _decode_bytes_group(data, pos, buf, bpos, bits):
    """解一组 16 字节（返回新位置；越界抛 MeshoptError）。

    打包格式：定长位宽字节在前，越界值按顺序尾随（哨兵 = (1<<bits)-1）。
    1 位组按**反转位序**存放（对应编码端的花式反转）。
    """
    if bits == 0:
        buf[bpos:bpos + 16] = b'\x00' * 16
        return pos
    if bits == 8:
        buf[bpos:bpos + 16] = data[pos:pos + 16]
        return pos + 16
    if bits == 1:
        var = pos + 2
        for _ in range(2):
            s = data[pos]
            pos += 1
            for k in range(8):
                enc = (s >> k) & 1          # 反转位序：低位即第 k 个值
                if enc == 1:                # 哨兵 → 尾随全字节
                    buf[bpos] = data[var]
                    var += 1
                else:
                    buf[bpos] = enc
                bpos += 1
        return var
    if bits == 2:
        var = pos + 4
        for _ in range(4):
            s = data[pos]
            pos += 1
            for k in range(4):
                enc = (s >> (6 - 2 * k)) & 3
                if enc == 3:
                    buf[bpos] = data[var]
                    var += 1
                else:
                    buf[bpos] = enc
                bpos += 1
        return var
    if bits == 4:
        var = pos + 8
        for _ in range(8):
            s = data[pos]
            pos += 1
            for k in range(2):
                enc = (s >> (4 - 4 * k)) & 15
                if enc == 15:
                    buf[bpos] = data[var]
                    var += 1
                else:
                    buf[bpos] = enc
                bpos += 1
        return var
    raise MeshoptError(f'非法位宽 {bits}')     # pragma: no cover


def _decode_bytes(data, pos, dend, buf, bpos, size, bits_table):
    """解 ``size`` 字节（16 的倍数）。返回新位置或 None（数据不足）。"""
    header_size = (size // _BYTE_GROUP_SIZE + 3) // 4
    if dend - pos < header_size:
        return None
    header = data[pos:pos + header_size]
    pos += header_size
    i = 0
    while i < size:
        if dend - pos < _BYTE_GROUP_DECODE_LIMIT:
            return None
        ho = i // _BYTE_GROUP_SIZE
        bitsk = (header[ho // 4] >> ((ho % 4) * 2)) & 3
        pos = _decode_bytes_group(data, pos, buf, bpos + i, bits_table[bitsk])
        i += _BYTE_GROUP_SIZE
    return pos


def _decode_deltas4(buf, vc, out, toff, stride, last, k, ctype, rot):
    """差分→绝对：把 4 个字节平面的差分写入正常布局（照抄 decodeDeltas4 语义）。

    ``ctype``：0 = 4×u8（zigzag），1 = 2×u16（zigzag），2 = u32（XOR + 旋转）。
    """
    if ctype == 0:
        for kk in range(4):
            base = kk * vc
            p = last[k + kk]
            for i in range(vc):
                v = buf[base + i]
                v = ((0 - (v & 1)) ^ (v >> 1)) & 0xFF
                v = (v + p) & 0xFF
                out[toff + kk + i * stride] = v
                p = v
    elif ctype == 1:
        for kk2 in range(2):
            kk = kk2 * 2
            base = kk * vc
            p = last[k + kk] | (last[k + kk + 1] << 8)
            for i in range(vc):
                v = buf[base + i] | (buf[base + vc + i] << 8)
                v = ((0 - (v & 1)) ^ (v >> 1)) & 0xFFFF
                v = (v + p) & 0xFFFF
                out[toff + kk + i * stride] = v & 0xFF
                out[toff + kk + 1 + i * stride] = v >> 8
                p = v
    else:
        p = (last[k] | (last[k + 1] << 8) | (last[k + 2] << 16)
             | (last[k + 3] << 24))
        for i in range(vc):
            v = (buf[i] | (buf[vc + i] << 8) | (buf[2 * vc + i] << 16)
                 | (buf[3 * vc + i] << 24))
            v = _rotl32(v, rot) ^ p
            o = toff + i * stride
            out[o] = v & 0xFF
            out[o + 1] = (v >> 8) & 0xFF
            out[o + 2] = (v >> 16) & 0xFF
            out[o + 3] = (v >> 24) & 0xFF
            p = v


def _decode_vertex_block(data, pos, dend, out, voff, vc, vs, last, channels,
                         version):
    """解一个顶点块（≤256 顶点）。返回新位置或 None。"""
    vc_aligned = (vc + _BYTE_GROUP_SIZE - 1) & ~(_BYTE_GROUP_SIZE - 1)
    buf = bytearray(4 * _BLOCK_MAX_SIZE + 64)   # 4 个平面（含对齐冗余区）

    control_size = 0 if version == 0 else vs // 4
    if dend - pos < control_size:
        return None
    control = data[pos:pos + control_size]
    pos += control_size

    for k in range(0, vs, 4):
        ctrl_byte = 0 if version == 0 else control[k // 4]
        for j in range(4):
            ctrl = (ctrl_byte >> (j * 2)) & 3
            base = j * vc
            if ctrl == 3:
                if dend - pos < vc:
                    return None
                buf[base:base + vc] = data[pos:pos + vc]
                pos += vc
            elif ctrl == 2:
                buf[base:base + vc] = b'\x00' * vc
            else:
                table = _BITS_V0 if version == 0 else (
                    _BITS_V1 if ctrl == 0 else _BITS_V1[1:])
                pos2 = _decode_bytes(data, pos, dend, buf, base, vc_aligned,
                                     table)
                if pos2 is None:
                    return None
                pos = pos2

        channel = 0 if version == 0 else channels[k // 4]
        ctype = channel & 3
        if ctype == 3:
            return None
        rot = (32 - (channel >> 4)) & 31
        _decode_deltas4(buf, vc, out, voff + k, vs, last, k, ctype, rot)

    lbase = voff + vs * (vc - 1)
    last[:] = out[lbase:lbase + vs]
    return pos


def decode_vertex_buffer(vertex_count, vertex_size, data):
    """解码 meshopt 顶点流（``0xa0``/``0xa1``）→ ``bytes``（vertex_count×vertex_size）。"""
    if not (0 < vertex_size <= 256) or vertex_size % 4:
        raise MeshoptError(f'非法顶点步长 {vertex_size}')
    if vertex_count < 0:
        raise MeshoptError('顶点数为负')
    if len(data) < 1:
        raise MeshoptError('数据为空')
    header = data[0]
    if (header & 0xF0) != _VERTEX_HEADER:
        raise MeshoptError(f'不是顶点 codec 流（header=0x{header:02x}）')
    version = header & 0x0F
    if version > _VERTEX_VERSION_MAX:
        raise MeshoptError(f'不支持的顶点 codec 版本 {version}')

    tail_size = vertex_size + (0 if version == 0 else vertex_size // 4)
    tail_min = _TAIL_MIN_V0 if version == 0 else _TAIL_MIN_V1
    tail_pad = tail_size if tail_size >= tail_min else tail_min
    if len(data) - 1 < tail_pad:
        raise MeshoptError('数据过短（容纳不下尾部）')

    tail_start = len(data) - tail_size
    last = bytearray(data[tail_start:tail_start + vertex_size])
    channels = None
    if version != 0:
        cstart = tail_start + vertex_size
        channels = data[cstart:cstart + vertex_size // 4]

    out = bytearray(vertex_count * vertex_size)
    block_size = _get_vertex_block_size(vertex_size)
    voff = 0
    pos = 1
    while voff < vertex_count:
        bs = block_size if voff + block_size < vertex_count \
            else vertex_count - voff
        pos2 = _decode_vertex_block(data, pos, len(data), out, voff * vertex_size,
                                    bs, vertex_size, last, channels, version)
        if pos2 is None:
            raise MeshoptError(f'顶点块解码失败（顶点偏移 {voff}）')
        pos = pos2
        voff += bs
    if len(data) - pos != tail_pad:
        raise MeshoptError(f'尾部对不上：剩余 {len(data) - pos}，应为 {tail_pad}')
    return bytes(out)


# ------------------------------------------------------------ 索引 codec --

def _decode_vbyte(data, pos):
    lead = data[pos]
    pos += 1
    if lead < 128:
        return lead, pos
    result = lead & 127
    shift = 7
    for _ in range(4):
        g = data[pos]
        pos += 1
        result |= (g & 127) << shift
        shift += 7
        if g < 128:
            break
    return result, pos


def _decode_index(data, pos, last):
    v, pos = _decode_vbyte(data, pos)
    d = (v >> 1) ^ (-(v & 1))
    return (last + d) & 0xFFFFFFFF, pos


def decode_index_buffer(index_count, index_size, data):
    """解码 meshopt 索引流（``0xe0``/``0xe1``，三角形列表）→ ``bytes``。

    ``index_size``：2（u16 输出）或 4（u32 输出）；``index_count`` 须为 3 的倍数。
    """
    if index_count % 3:
        raise MeshoptError('索引数必须是 3 的倍数')
    if index_size not in (2, 4):
        raise MeshoptError('index_size 只能是 2 或 4')
    if len(data) < 1 + index_count // 3 + 16:
        raise MeshoptError('数据过短（容纳不下 code 表与 codeaux 表）')
    if (data[0] & 0xF0) != _INDEX_HEADER:
        raise MeshoptError(f'不是索引 codec 流（header=0x{data[0]:02x}）')
    version = data[0] & 0x0F
    if version > _INDEX_VERSION_MAX:
        raise MeshoptError(f'不支持的索引 codec 版本 {version}')

    fecmax = 13 if version >= 1 else 15

    code = 1
    code_end = code + index_count // 3
    dpos = code_end
    safe_end = len(data) - 16
    table = data[safe_end:safe_end + 16]

    edgefifo = [[0xFFFFFFFF, 0xFFFFFFFF] for _ in range(16)]
    vertexfifo = [0xFFFFFFFF] * 16
    efoff = 0
    vfoff = 0
    nxt = 0
    last = 0

    out = bytearray(index_count * index_size)
    wpos = 0

    def emit(a, b, c):
        nonlocal wpos
        if index_size == 2:
            out[wpos] = a & 0xFF
            out[wpos + 1] = (a >> 8) & 0xFF
            out[wpos + 2] = b & 0xFF
            out[wpos + 3] = (b >> 8) & 0xFF
            out[wpos + 4] = c & 0xFF
            out[wpos + 5] = (c >> 8) & 0xFF
        else:
            for v, o in ((a, 0), (b, 4), (c, 8)):
                out[wpos + o] = v & 0xFF
                out[wpos + o + 1] = (v >> 8) & 0xFF
                out[wpos + o + 2] = (v >> 16) & 0xFF
                out[wpos + o + 3] = (v >> 24) & 0xFF
        wpos += 3 * index_size

    while code < code_end:
        codetri = data[code]
        code += 1
        if codetri < 0xF0:
            fe = codetri >> 4
            idx = (efoff - 1 - fe) & 15
            a = edgefifo[idx][0]
            b = edgefifo[idx][1]
            fec = codetri & 15
            if fec < fecmax:
                cf = vertexfifo[(vfoff - 1 - fec) & 15]
                c = nxt if fec == 0 else cf
                fec0 = 1 if fec == 0 else 0
                nxt += fec0
                vertexfifo[vfoff] = c
                vfoff = (vfoff + fec0) & 15
            else:
                if dpos > safe_end:
                    raise MeshoptError('索引数据越界')
                if fec != 15:
                    last = (last + (fec * 2 - 27)) & 0xFFFFFFFF
                    c = last
                else:
                    last, dpos = _decode_index(data, dpos, last)
                    c = last
                vertexfifo[vfoff] = c
                vfoff = (vfoff + 1) & 15
            edgefifo[efoff][0] = c
            edgefifo[efoff][1] = b
            efoff = (efoff + 1) & 15
            edgefifo[efoff][0] = a
            edgefifo[efoff][1] = c
            efoff = (efoff + 1) & 15
            emit(a, b, c)
        else:
            if codetri < 0xFE:
                codeaux = table[codetri & 15]
                feb = codeaux >> 4
                fec = codeaux & 15
                a = nxt
                nxt += 1
                b = nxt if feb == 0 else vertexfifo[(vfoff - feb) & 15]
                feb0 = 1 if feb == 0 else 0
                nxt += feb0
                c = nxt if fec == 0 else vertexfifo[(vfoff - fec) & 15]
                fec0 = 1 if fec == 0 else 0
                nxt += fec0
                emit(a, b, c)
                vertexfifo[vfoff] = a
                vfoff = (vfoff + 1) & 15
                vertexfifo[vfoff] = b
                vfoff = (vfoff + feb0) & 15
                vertexfifo[vfoff] = c
                vfoff = (vfoff + fec0) & 15
                edgefifo[efoff][0] = b
                edgefifo[efoff][1] = a
                efoff = (efoff + 1) & 15
                edgefifo[efoff][0] = c
                edgefifo[efoff][1] = b
                efoff = (efoff + 1) & 15
                edgefifo[efoff][0] = a
                edgefifo[efoff][1] = c
                efoff = (efoff + 1) & 15
            else:
                if dpos > safe_end:
                    raise MeshoptError('索引数据越界')
                codeaux = data[dpos]
                dpos += 1
                fea = 0 if codetri == 0xFE else 15
                feb = codeaux >> 4
                fec = codeaux & 15
                if codeaux == 0:
                    nxt = 0
                a = nxt if fea == 0 else 0
                if fea == 0:
                    nxt += 1
                b = nxt if feb == 0 else vertexfifo[(vfoff - feb) & 15]
                if feb == 0:
                    nxt += 1
                c = nxt if fec == 0 else vertexfifo[(vfoff - fec) & 15]
                if fec == 0:
                    nxt += 1
                if fea == 15:
                    last, dpos = _decode_index(data, dpos, last)
                    a = last
                if feb == 15:
                    last, dpos = _decode_index(data, dpos, last)
                    b = last
                if fec == 15:
                    last, dpos = _decode_index(data, dpos, last)
                    c = last
                emit(a, b, c)
                vertexfifo[vfoff] = a
                vfoff = (vfoff + 1) & 15
                vertexfifo[vfoff] = b
                vfoff = (vfoff + (1 if (feb == 0 or feb == 15) else 0)) & 15
                vertexfifo[vfoff] = c
                vfoff = (vfoff + (1 if (fec == 0 or fec == 15) else 0)) & 15
                edgefifo[efoff][0] = b
                edgefifo[efoff][1] = a
                efoff = (efoff + 1) & 15
                edgefifo[efoff][0] = c
                edgefifo[efoff][1] = b
                efoff = (efoff + 1) & 15
                edgefifo[efoff][0] = a
                edgefifo[efoff][1] = c
                efoff = (efoff + 1) & 15

    if dpos != safe_end:
        raise MeshoptError(f'索引流尾部对不上：{dpos} != {safe_end}')
    return bytes(out)


def decode_index_sequence(index_count, index_size, data):
    """解码 meshopt 索引序列流（``0xd0``/``0xd1``）→ ``bytes``。"""
    if index_size not in (2, 4):
        raise MeshoptError('index_size 只能是 2 或 4')
    if len(data) < 1 + index_count + 4:
        raise MeshoptError('数据过短（序列流需 1+N+4 字节）')
    if (data[0] & 0xF0) != _SEQUENCE_HEADER:
        raise MeshoptError(f'不是索引序列流（header=0x{data[0]:02x}）')
    version = data[0] & 0x0F
    if version > _INDEX_VERSION_MAX:
        raise MeshoptError(f'不支持的索引序列版本 {version}')

    pos = 1
    safe_end = len(data) - 4
    last = [0, 0]
    out = bytearray(index_count * index_size)
    for i in range(index_count):
        if pos >= safe_end:
            raise MeshoptError('索引序列数据越界')
        v, pos = _decode_vbyte(data, pos)
        cur = v & 1
        v >>= 1
        d = (v >> 1) ^ (-(v & 1))
        index = (last[cur] + d) & 0xFFFFFFFF
        last[cur] = index
        o = i * index_size
        if index_size == 2:
            out[o] = index & 0xFF
            out[o + 1] = (index >> 8) & 0xFF
        else:
            out[o] = index & 0xFF
            out[o + 1] = (index >> 8) & 0xFF
            out[o + 2] = (index >> 16) & 0xFF
            out[o + 3] = (index >> 24) & 0xFF
    if pos != safe_end:
        raise MeshoptError(f'索引序列尾部对不上：{pos} != {safe_end}')
    return bytes(out)
