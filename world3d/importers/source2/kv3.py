# -*- coding: utf-8 -*-
"""Binary KV3（v5）读取器 —— 纯 Python，零依赖。

Source 2 的文本/二进制配置格式 KeyValues3。本模块读取 **binary v5**（现行格式，
CS2 的 vmdl_c/vmat_c 等资源容器里的 CTRL/MDAT/DATA 块都是它），返回普通
Python 值（dict / list / str / int / float / bool / bytes），带标记的值包一层
:class:`FlaggedValue`。

格式要点（社区逆向，见 §7 记账）
-------------------------------
* magic = ``b'\\x05' + b'3VK'``；随后 16 字节 format GUID（generic =
  ``7412167c-06e9-4698-aff2-e63eb59037e7``，按 LE 存前三个字段）。
* 头部一大串计数（buffer0/buffer1 的 bytes/short/int/double 数量、types 大小、
  对象/数组数、压缩方式等）；数据分 buffer0/buffer1：
  - buffer0 = 字符串表（先按 int 缓冲里的计数，再从 byte 缓冲逐个读 null 结尾串）；
  - buffer1 = [对象成员计数][bytes/short/int/double 分区（按需对齐）][types 流]；
* 压缩方式：0=不压缩；1=**LZ4 block**（两段大缓冲各自一块，帧 16384）；
  2=zstd（本模块不支持，Source 2 资产实测未见）。
* 值类型流：每个值一个 type 字节（高位置 1 → 后随 1 字节"标记"=resource/...）。
  数值数据按类型落到对应分区（int64/double 走 double 分区，float 走 int 分区……）。

参考：ValveResourceFormat（MIT，C#）与 kristiker/keyvalues3（MIT，Python）。
本模块为自持实现，仅供读取；只支持无需 zstd 的路径（§18.5）。
"""
import struct

from world3d.importers.source2.lz4block import decompress_block, decompress_chain

__all__ = ['KV3Error', 'FlaggedValue', 'MAGIC', 'FORMAT_GENERIC', 'is_kv3', 'read']

MAGIC = b'\x05' + b'3VK'
#: 'generic' format GUID 的字节序（小端前三段）
FORMAT_GENERIC = bytes.fromhex('7c161274e9069846aff2e63eb59037e7')

_MAGICS = {
    b'VKV\x03': 'legacy-vkv3',
    b'\x01' + b'3VK': 'v1',
    b'\x02' + b'3VK': 'v2',
    b'\x03' + b'3VK': 'v3',
    b'\x04' + b'3VK': 'v4',
    MAGIC: 'v5',
}

#: 值标记（resource: / panorama: 等）名字表
FLAG_NAMES = {1: 'resource', 2: 'resource_name', 3: 'panorama',
              4: 'soundevent', 5: 'subclass', 6: 'entity_name'}

_MARKER = 0xFFEEDD00

# 值类型（BinaryType）
_T_NULL, _T_BOOL, _T_INT64, _T_UINT64, _T_DOUBLE = 1, 2, 3, 4, 5
_T_STRING, _T_BLOB, _T_ARRAY, _T_DICT, _T_ARR_TYPED = 6, 7, 8, 9, 10
_T_INT32, _T_UINT32, _T_BOOL_T, _T_BOOL_F = 11, 12, 13, 14
_T_I64_0, _T_I64_1, _T_D_0, _T_D_1 = 15, 16, 17, 18
_T_FLOAT, _T_I16, _T_U16, _T_I8, _T_U8 = 19, 20, 21, 22, 23
_T_ARR_BL1, _T_ARR_BL2 = 24, 25


class KV3Error(ValueError):
    """KV3 数据非法（magic/截断/标记不符等）。"""


class FlaggedValue:
    """带标记的 KV3 值（如 ``<resource:materials/x.vmat>``）。

    与普通值比较时退化为"比较内部值"（渲染/校验侧友好）。
    """

    __slots__ = ('value', 'flag')

    def __init__(self, value, flag):
        self.value = value
        self.flag = flag

    def __repr__(self):
        return f'<{self.flag}:{self.value!r}>'

    def __eq__(self, other):
        if isinstance(other, FlaggedValue):
            return self.value == other.value and self.flag == other.flag
        return self.value == other

    def __hash__(self):
        return hash((self.value, self.flag))


# ------------------------------------------------------------------ 缓冲 --

class _Buf:
    """游标 + 绝对窗口的字节读取器（子缓冲共享同一底层 bytes）。"""

    __slots__ = ('b', 'pos', 'end')

    def __init__(self, b, pos=0, end=None):
        self.b = b
        self.pos = pos
        self.end = len(b) if end is None else end

    def _need(self, n):
        if self.pos + n > self.end:
            raise KV3Error(f'越界读取：需要 {n} 字节，剩余 {self.end - self.pos}')

    def read(self, n):
        self._need(n)
        s = self.b[self.pos:self.pos + n]
        self.pos += n
        return s

    def take(self, n):
        """前进 n 字节，并返回覆盖该区间的子读取器（零拷贝视图）。"""
        self._need(n)
        sub = _Buf(self.b, self.pos, self.pos + n)
        self.pos += n
        return sub

    def skip(self, n):
        self._need(n)
        self.pos += n

    def align(self, n):
        pad = (-self.pos) % n
        self._need(pad)
        self.pos += pad

    def _u(self, fmt, size):
        self._need(size)
        v = struct.unpack_from(fmt, self.b, self.pos)[0]
        self.pos += size
        return v

    def u8(self):  return self._u('<B', 1)
    def i8(self):  return self._u('<b', 1)
    def u16(self): return self._u('<H', 2)
    def i16(self): return self._u('<h', 2)
    def u32(self): return self._u('<I', 4)
    def i32(self): return self._u('<i', 4)
    def u64(self): return self._u('<Q', 8)
    def i64(self): return self._u('<q', 8)
    def f32(self): return self._u('<f', 4)
    def f64(self): return self._u('<d', 8)

    def nt_string(self):
        """读一个 null 结尾 UTF-8 串（容错解码）。"""
        idx = self.b.find(b'\0', self.pos, self.end)
        if idx < 0:
            raise KV3Error('字符串缺少终止符')
        s = self.b[self.pos:idx].decode('utf-8', errors='replace')
        self.pos = idx + 1
        return s


class _Split:
    """buffer0/buffer1 的四分区（按需对齐，语义同 Valve 生态实现）。"""

    __slots__ = ('bytes_b', 'short_b', 'int_b', 'double_b')


def _split(buf, nbytes, nshort, nint, ndouble):
    s = _Split()
    s.bytes_b = buf.take(nbytes)
    if nshort:
        buf.align(2)
    s.short_b = buf.take(nshort * 2)
    if nint:
        buf.align(4)
    s.int_b = buf.take(nint * 4)
    if ndouble:
        buf.align(8)
    s.double_b = buf.take(ndouble * 8)
    return s


class _Ctx:
    __slots__ = ('strings', 'b0', 'b1', 'types', 'member_counts',
                 'blob_sizes', 'blobs', 'active')

    def __init__(self, strings, b0, b1, types, member_counts, blob_sizes, blobs):
        self.strings = strings
        self.b0 = b0
        self.b1 = b1
        self.types = types
        self.member_counts = member_counts
        self.blob_sizes = blob_sizes
        self.blobs = blobs
        self.active = b1


# ------------------------------------------------------------------ 值 --

def _wrap(value, spec):
    if 0 < spec < 6:
        return FlaggedValue(value, FLAG_NAMES[spec])
    return value


def _read_type(ctx):
    data = ctx.types.u8()
    spec = 0
    if data & 0x80:
        data &= 0x3F
        spec = ctx.types.u8()
        if spec > 6:
            raise KV3Error(f'未知 KV3 值标记 {spec}')
    return data, spec


def _read_value(ctx):
    t, spec = _read_type(ctx)
    try:
        fn = _READERS[t]
    except KeyError:
        raise KV3Error(f'未知 KV3 类型 {t}') from None
    return _wrap(fn(ctx), spec)


def _r_null(ctx):   return None
def _r_true(ctx):   return True
def _r_false(ctx):  return False
def _r_zero(ctx):   return 0
def _r_one(ctx):    return 1
def _r_dzero(ctx):  return 0.0
def _r_done(ctx):   return 1.0


def _r_bool(ctx):
    return ctx.active.bytes_b.u8() == 1


def _r_int64(ctx):  return ctx.active.double_b.i64()
def _r_uint64(ctx): return ctx.active.double_b.u64()
def _r_double(ctx): return ctx.active.double_b.f64()
def _r_int32(ctx):  return ctx.active.int_b.i32()
def _r_uint32(ctx): return ctx.active.int_b.u32()
def _r_float(ctx):  return ctx.active.int_b.f32()
def _r_int16(ctx):  return ctx.active.short_b.i16()
def _r_uint16(ctx): return ctx.active.short_b.u16()
def _r_int8(ctx):   return ctx.active.bytes_b.i8()
def _r_uint8(ctx):  return ctx.active.bytes_b.u8()


def _r_string(ctx):
    sid = ctx.active.int_b.i32()
    if sid == -1:
        return ''
    return ctx.strings[sid]


def _r_blob(ctx):
    if ctx.blob_sizes is not None:
        size = ctx.blob_sizes.pop(0)
        if size == 0:
            return b''
        data = ctx.blobs.read(size)
        if len(data) != size:
            raise KV3Error('二进制块小于声明大小')
        return bytes(data)
    n = ctx.active.int_b.i32()
    return bytes(ctx.active.bytes_b.read(n))


def _r_array(ctx):
    count = ctx.active.int_b.i32()
    if count < 0 or count > 1 << 24:
        raise KV3Error(f'数组长度异常：{count}')
    return [_read_value(ctx) for _ in range(count)]


def _r_dict(ctx):
    n = ctx.member_counts.u32()
    if n > 1 << 24:
        raise KV3Error(f'对象成员数异常：{n}')
    obj = {}
    for i in range(n):
        nid = ctx.active.int_b.i32()
        name = ctx.strings[nid] if nid != -1 else str(i)
        obj[name] = _read_value(ctx)
    return obj


def _r_typed_helper(ctx, count):
    s = ctx.active
    t, spec = _read_type(ctx)
    if t == _T_D_0:
        return [0.0] * count
    if t == _T_D_1:
        return [1.0] * count
    if t == _T_I64_0:
        return [0] * count
    if t == _T_I64_1:
        return [1] * count
    if t == _T_DOUBLE:
        return [s.double_b.f64() for _ in range(count)]
    if t == _T_INT64:
        return [s.double_b.i64() for _ in range(count)]
    if t == _T_UINT64:
        return [s.double_b.u64() for _ in range(count)]
    if t == _T_INT32:
        return [s.int_b.i32() for _ in range(count)]
    if t == _T_UINT32:
        return [s.int_b.u32() for _ in range(count)]
    try:
        fn = _READERS[t]
    except KeyError:
        raise KV3Error(f'未知 KV3 类型数组元素 {t}') from None
    return [_wrap(fn(ctx), spec) for _ in range(count)]


def _r_typed(ctx):
    return _r_typed_helper(ctx, ctx.active.int_b.u32())


def _r_typed_bl1(ctx):
    count = ctx.active.bytes_b.u8()
    return _r_typed_helper(ctx, count)


def _r_typed_bl2(ctx):
    count = ctx.active.bytes_b.u8()
    ctx.active = ctx.b0
    try:
        return _r_typed_helper(ctx, count)
    finally:
        ctx.active = ctx.b1


_READERS = {
    _T_NULL: _r_null,
    _T_BOOL: _r_bool,
    _T_INT64: _r_int64,
    _T_UINT64: _r_uint64,
    _T_DOUBLE: _r_double,
    _T_STRING: _r_string,
    _T_BLOB: _r_blob,
    _T_ARRAY: _r_array,
    _T_DICT: _r_dict,
    _T_ARR_TYPED: _r_typed,
    _T_INT32: _r_int32,
    _T_UINT32: _r_uint32,
    _T_BOOL_T: _r_true,
    _T_BOOL_F: _r_false,
    _T_I64_0: _r_zero,
    _T_I64_1: _r_one,
    _T_D_0: _r_dzero,
    _T_D_1: _r_done,
    _T_FLOAT: _r_float,
    _T_I16: _r_int16,
    _T_U16: _r_uint16,
    _T_I8: _r_int8,
    _T_U8: _r_uint8,
    _T_ARR_BL1: _r_typed_bl1,
    _T_ARR_BL2: _r_typed_bl2,
}


# ------------------------------------------------------------------ 入口 --

def is_kv3(data):
    """判断字节流是否 Binary KV3（任意版本）。"""
    return len(data) >= 4 and bytes(data[:4]) in _MAGICS


def read(data):
    """读取 Binary KV3 字节流 → Python 值树（仅支持 v5）。"""
    if not is_kv3(data):
        raise KV3Error('不是 Binary KV3（magic 不匹配）')
    magic = bytes(data[:4])
    if magic != MAGIC:
        raise NotImplementedError(
            f'Binary KV3 {_MAGICS[magic]} 暂不支持（本模块面向 v5，Source 2 现行格式）')
    return _read_v5(bytes(data))


def _read_v5(data):
    b = _Buf(data, 4)
    b.skip(16)                                # format GUID（generic = FORMAT_GENERIC）
    method = b.u32()
    dict_id = b.u16()
    frame_size = b.u16()
    if method == 1 and (dict_id != 0 or frame_size != 16384):
        raise KV3Error(f'非预期 LZ4 参数（dict={dict_id}, frame={frame_size}）')
    if method > 1:
        raise NotImplementedError(f'KV3 压缩方式 {method}（zstd）暂不支持')

    bytes_count = b.u32()
    int_count = b.u32()
    double_count = b.u32()
    types_size = b.u32()
    b.u16(); b.u16()                          # object/array 计数（仅参考）
    b.u32(); b.u32()                          # totals
    block_count = b.u32()
    block_total_size = b.u32()
    short_count = b.u32()
    n_block_sizes = b.u32() // 2
    b0d = b.u32(); b0c = b.u32()
    b1d = b.u32(); b1c = b.u32()
    bytes2 = b.u32(); short2 = b.u32(); int2 = b.u32(); double2 = b.u32()
    b.u32(); object_count_v5 = b.u32(); b.u32(); b.u32()

    if method == 0:
        buf0 = bytes(b.read(b0d))
        buf1 = bytes(b.read(b1d))
    else:
        raw0 = bytes(b.read(b0c))
        raw1 = bytes(b.read(b1c))
        buf0 = decompress_block(raw0, b0d) if b0c else b''
        buf1 = decompress_block(raw1, b1d) if b1c else b''
        if len(buf0) != b0d or len(buf1) != b1d:
            raise KV3Error(f'LZ4 解压大小不符（{len(buf0)}/{b0d}, {len(buf1)}/{b1d}）')

    r0 = _Buf(buf0)
    r1 = _Buf(buf1)
    k0 = _split(r0, bytes_count, short_count, int_count, double_count)
    n_strings = k0.int_b.u32()
    if n_strings > 1 << 20:
        raise KV3Error(f'字符串表异常：{n_strings}')
    strings = [k0.bytes_b.nt_string() for _ in range(n_strings)]

    member_counts = r1.take(object_count_v5 * 4)
    k1 = _split(r1, bytes2, short2, int2, double2)
    types = r1.take(types_size)

    blob_sizes = None
    blobs = None
    if block_count == 0:
        if r1.u32() != _MARKER:
            raise KV3Error('buffer1 尾部标记缺失')
    else:
        blob_sizes = [r1.u32() for _ in range(block_count)]
        if r1.u32() != _MARKER:
            raise KV3Error('buffer1 块表尾部标记缺失')
        comp_sizes = [r1.u16() for _ in range(n_block_sizes)]
        if block_total_size > 0:
            if method == 0:
                blob_data = bytearray()
                for s in blob_sizes:
                    blob_data += b.read(s)
            else:
                blob_data, _ = decompress_chain(
                    b.b, b.pos, blob_sizes, comp_sizes, frame_size)
                b.pos = _
            if b.u32() != _MARKER:
                raise KV3Error('块数据尾部标记缺失')
            blobs = _Buf(bytes(blob_data))

    ctx = _Ctx(strings, k0, k1, types, member_counts, blob_sizes, blobs)
    return _read_value(ctx)
