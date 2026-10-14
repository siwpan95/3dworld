# -*- coding: utf-8 -*-
"""Source 2 解析层测试套件（VPK / Binary KV3 v5 / LZ4 block，纯 Python 零 GL）。

运行::

    python -m tests.test_source2

**策略**（§18.5）：仓库**不含任何游戏资产**——本套件全用
①自建 VPK 归档 ②手写 LZ4 block ③测试内嵌的最小 KV3 v5 编码器 + 固定 golden 字节。
真实资产对拍走 `scripts/source2_survey.py`（需本机游戏文件，不进 CI 硬门禁）。

**钉死的性质**：

1. **VPK 往返**——自建 v1/v2 归档（含 preload 与多级目录）读出逐字节一致；
2. **LZ4 block**——字面量/匹配/重叠(RLE)/**字典跨界**四类手工用例结果精确；
3. **LZ4 块链**——第二块回溯引用第一块输出的字典（跨块 offset）；
4. **KV3 v5 往返**——编码器 → 读取器深度相等（含 flagged value / float32 精度）；
5. **KV3 golden**——固定字节流 → 固定结构（且编码器输出 == golden，可复现）。
"""
import os
import struct
import sys
import tempfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.importers.source2 import kv3, lz4block, vpk  # noqa: E402
from world3d.importers.source2.kv3 import FlaggedValue, KV3Error  # noqa: E402
from world3d.importers.source2.lz4block import LZ4Error  # noqa: E402
from world3d.importers.source2.vpk import VPK, VPKError  # noqa: E402

# --------------------------------------------------------------- VPK 构造 --

def _build_vpk(version, files, preload=0):
    """构造最小合法 VPK（1/2 版）。files: {name: bytes}；preload: 每条预载字节数。

    根目录文件按 Valve 约定写 ``" "``（空格）作目录名（见 ValvePak ``Package.Space``）。
    """
    groups = {}
    for name, data in files.items():
        if '/' in name:
            path_dir, fname_ext = name.rsplit('/', 1)
        else:
            path_dir, fname_ext = ' ', name
        fname, ext = fname_ext.rsplit('.', 1)
        groups.setdefault(ext, {}).setdefault(path_dir, []).append((fname, data))
    tree = bytearray()
    blob = bytearray()
    for ext, dirs in groups.items():
        tree += ext.encode() + b'\0'
        for path_dir, entries in dirs.items():
            tree += path_dir.encode() + b'\0'
            for fname, data in entries:
                pre = min(preload, len(data))
                tree += fname.encode() + b'\0'
                arch = len(data) - pre
                tree += struct.pack('<IHHII', zlib.crc32(data) & 0xFFFFFFFF,
                                    pre, 0x7FFF, len(blob), arch)
                tree += struct.pack('<H', 0xFFFF)
                tree += data[:pre]
                blob += data[pre:]
            tree += b'\0'
        tree += b'\0'
    tree += b'\0'
    hdr = struct.pack('<III', 0x55AA1234, version, len(tree))
    if version == 2:
        hdr += struct.pack('<IIII', len(blob), 0, 0, 0)
    return bytes(hdr) + bytes(tree) + bytes(blob)


def test_vpk_v1_roundtrip():
    files = {
        'a.txt': b'hello vpk',
        'maps/x/one.bin': bytes(range(256)) * 3,
        'maps/x/two.bin': b'\x00' * 100,
        'sound/z.wav': b'RIFFxxxx',
    }
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'test.vpk')
        with open(p, 'wb') as f:
            f.write(_build_vpk(1, files))
        pak = VPK(p)
        assert pak.version == 1
        assert len(pak) == 4 and pak.names() == sorted(files)
        for name, data in files.items():
            got = pak.read(name)
            assert got == data, f'{name} 提取不一致'
        e = pak.entries['maps/x/one.bin']
        assert e.crc == zlib.crc32(files['maps/x/one.bin']) & 0xFFFFFFFF
    print('  ok: VPK v1 往返（4 文件 / 多级目录 / CRC 保留）')


def test_vpk_v2_preload_roundtrip():
    files = {
        'p/one.dat': b'0123456789abcdef',
        'p/two.dat': b'ZZ',
        'three.dat': b'abc',
    }
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'pak_dir.vpk')
        with open(p, 'wb') as f:
            f.write(_build_vpk(2, files, preload=8))
        pak = VPK(p)
        assert pak.version == 2 and len(pak) == 3
        for name, data in files.items():
            assert pak.read(name) == data, f'{name}（含预载）不一致'
        # 预载确实进了目录树（条目字段）
        assert pak.entries['p/one.dat'].preload == 8
    print('  ok: VPK v2 往返（预载字节 + 外部归档约定）')


def test_vpk_errors():
    with tempfile.TemporaryDirectory() as td:
        bad = os.path.join(td, 'bad.vpk')
        with open(bad, 'wb') as f:
            f.write(b'not a vpk at all')
        try:
            VPK(bad)
            raise AssertionError('坏 magic 应报错')
        except VPKError as e:
            assert 'magic' in str(e)
        p = os.path.join(td, 'ok.vpk')
        with open(p, 'wb') as f:
            f.write(_build_vpk(1, {'x.txt': b'1'}))
        pak = VPK(p)
        try:
            pak.read('missing.txt')
            raise AssertionError('缺失条目应报错')
        except VPKError as e:
            assert '不存在' in str(e)
    print('  ok: VPK 错误路径（坏 magic / 缺失条目）')


# --------------------------------------------------------------- LZ4 ----

def test_lz4_block_basics():
    # ① 纯字面量：lit=12
    assert lz4block.decompress_block(bytes([0xC0]) + b'hello world ', 64) \
        == b'hello world '
    # ② 普通匹配：'abcd' + 回溯 4 拷贝 4 → 'abcdabcd'
    src = bytes([0x40]) + b'abcd' + struct.pack('<H', 4) + bytes([0x50]) + b'12345'
    assert lz4block.decompress_block(src, 64) == b'abcdabcd12345'
    # ③ 自重叠（RLE）：'abcd' + offset=1 拷贝 4 → 'abcddddd'
    src = bytes([0x40]) + b'abcd' + struct.pack('<H', 1) + bytes([0x50]) + b'12345'
    assert lz4block.decompress_block(src, 64) == b'abcddddd12345'
    print('  ok: LZ4 字面量 / 普通匹配 / 自重叠(RLE) 三类')


def test_lz4_block_dict_crossing():
    # ④ 字典尾部匹配：'X' + 回溯 2（字典末字节与自身交替）→ 'X X X'
    src = bytes([0x10]) + b'X' + struct.pack('<H', 2) + bytes([0x50]) + b'12345'
    assert lz4block.decompress_block(src, 64, dictionary=b'hello world ') \
        == b'X X X12345'
    # ⑤ 字典跨界：0 字面量 + offset=3，起点在字典内、尾部落在本次输出
    src = bytes([0x00]) + struct.pack('<H', 3) + bytes([0x50]) + b'HELLO'
    out = lz4block.decompress_block(src, 64, dictionary=b'XYZ')
    assert out == b'XYZXHELLO', out
    print('  ok: LZ4 字典尾匹配 + 字典跨界拷贝（起点在字典、尾部在输出）')


def test_lz4_errors():
    for src, why in [
        (bytes([0x50]) + b'ab', '字面量截断'),          # lit=5 只有 2 字节
        (bytes([0x10]) + b'a' + b'\x00\x00', 'offset 0'),
    ]:
        try:
            lz4block.decompress_block(src, 64)
            raise AssertionError(f'{why} 应报错')
        except LZ4Error:
            pass
    try:
        lz4block.decompress_block(bytes([0x00]) + struct.pack('<H', 9), 16,
                                  dictionary=b'abc')     # offset 越过字典
        raise AssertionError('越界 offset 应报错')
    except LZ4Error:
        pass
    print('  ok: LZ4 错误路径（截断 / offset 0 / 越过字典）')


def test_lz4_chain():
    b1 = bytes([0xC0]) + b'hello world '            # 12 字节字面量
    b2 = bytes([0x50]) + b'ABCDE' + struct.pack('<H', 11) + bytes([0x50]) + b'12345'
    data = b1 + b2
    out, pos = lz4block.decompress_chain(
        data, 0, [12, 14], [len(b1), len(b2)], frame_size=16384)
    assert out == b'hello world ABCDEworl12345', out
    assert pos == len(data)
    print('  ok: LZ4 块链（跨块字典回溯，第二块引用第一块输出）')


# --------------------------------------------------------------- KV3 构造 --

_FLAG_IDS = {'resource': 1, 'resource_name': 2, 'panorama': 3,
             'soundevent': 4, 'subclass': 5}


def _kv3_v5_write(value):
    """最小 Binary KV3 v5 编码器（**仅测试用**，支持 dict/list/str/int/float/bool/None/FlaggedValue）。

    输出与被测读取器同构；其正确性在研究期用独立实现（keyvalues3 库）对拍过。
    """
    strings, string_ids = [], {}

    def sid(s):
        if s == '':
            return -1
        i = string_ids.get(s)
        if i is None:
            i = len(strings)
            string_ids[s] = i
            strings.append(s)
        return i

    types = bytearray()
    member_counts = []
    b1_bytes, b1_ints, b1_doubles = bytearray(), bytearray(), bytearray()
    n_arrays = 0

    def emit_type(t, flag=None):
        if flag is None:
            types.append(t)
        else:
            types.append(t | 0x80)
            types.append(flag)

    def emit(v):
        nonlocal n_arrays, b1_ints, b1_doubles
        flag = None
        if isinstance(v, FlaggedValue):
            flag = _FLAG_IDS[v.flag]
            v = v.value
        if v is None:
            emit_type(1, flag)
        elif v is True:
            emit_type(13, flag)
        elif v is False:
            emit_type(14, flag)
        elif isinstance(v, int):
            if v == 0:
                emit_type(15, flag)
            elif v == 1:
                emit_type(16, flag)
            else:
                emit_type(11, flag)
                b1_ints += struct.pack('<i', v)
        elif isinstance(v, float):
            if v == 0.0:
                emit_type(17, flag)
            elif v == 1.0:
                emit_type(18, flag)
            else:
                emit_type(19, flag)
                b1_ints += struct.pack('<f', v)
        elif isinstance(v, str):
            emit_type(6, flag)
            b1_ints += struct.pack('<i', sid(v))
        elif isinstance(v, list):
            n_arrays += 1
            emit_type(8, flag)
            b1_ints += struct.pack('<i', len(v))
            for item in v:
                emit(item)
        elif isinstance(v, dict):
            emit_type(9, flag)
            member_counts.append(len(v))
            for k, val in v.items():
                b1_ints += struct.pack('<i', sid(k))
                emit(val)
        else:
            raise TypeError(f'测试编码器不支持：{type(v)}')

    emit(value)

    b0_bytes = bytearray()
    for s in strings:
        b0_bytes += s.encode('utf-8') + b'\0'
    buffer0 = bytearray(b0_bytes)
    buffer0 += b'\0' * ((-len(buffer0)) % 4)          # int 分区对齐（int_count=1）
    buffer0 += struct.pack('<I', len(strings))

    mc = b''.join(struct.pack('<I', c) for c in member_counts)
    b1 = bytearray(mc) + b1_bytes
    if b1_ints:
        b1 += b'\0' * ((-len(b1)) % 4)
    b1 += b1_ints
    if b1_doubles:
        b1 += b'\0' * ((-len(b1)) % 8)
    b1 += b1_doubles
    b1 += bytes(types)
    b1 += struct.pack('<I', 0xFFEEDD00)
    buffer1 = bytes(b1)

    total = len(buffer0) + len(buffer1)
    hdr = bytearray()
    hdr += b'\x05' + b'3VK'
    hdr += kv3.FORMAT_GENERIC
    hdr += struct.pack('<IHH', 0, 0, 0)               # 不压缩 / 无字典 / 无帧
    hdr += struct.pack('<III', len(b0_bytes), 1, 0)   # b0: bytes/int/double 计数
    hdr += struct.pack('<IHH', len(types), len(member_counts), n_arrays)
    hdr += struct.pack('<II', total, total)           # 未用 totals
    hdr += struct.pack('<II', 0, 0)                   # 无独立块
    hdr += struct.pack('<I', 0)                       # b0 short 计数
    hdr += struct.pack('<I', 0)                       # 压缩块大小表（空）
    hdr += struct.pack('<II', len(buffer0), len(buffer0))
    hdr += struct.pack('<II', len(buffer1), len(buffer1))
    hdr += struct.pack('<IIII', len(b1_bytes), 0, len(b1_ints) // 4,
                       len(b1_doubles) // 8)
    hdr += struct.pack('<IIII', 0, len(member_counts), 0, 0)
    return bytes(hdr) + bytes(buffer0) + buffer1


_SAMPLE = {
    'm_sceneObjects': [
        {'m_nVertexCount': 26, 'm_nIndexCount': 48,
         'm_material': FlaggedValue('materials/test/sample.vmat', 'resource'),
         'm_flUvDensity': 258.8968811035156,
         'm_bUseCompressedNormalTangent': True,
         'm_rigidMeshParts': [],
         'm_nPrimitiveType': 'RENDER_PRIM_TRIANGLES'},
        {'m_nVertexCount': 0, 'm_nIndexCount': 1},
    ],
    'm_nName': 'meshset_0',
    'm_vMinBounds': [346.0, 1417.65625, 468.49798583984375],
    'm_nFlags': 0,
    'm_strEmpty': '',
}

_GOLDEN_HEX = (
    '0533564b7c161274e9069846aff2e63eb59037e70000000000000000e900000001000000'
    '000000001500000003000300810100008101000000000000000000000000000000000000'
    'f0000000f0000000910000009100000000000000000000001b0000000000000000000000'
    '0300000000000000000000006d5f7363656e654f626a65637473006d5f6e566572746578'
    '436f756e74006d5f6e496e646578436f756e74006d5f6d6174657269616c006d61746572'
    '69616c732f746573742f73616d706c652e766d6174006d5f666c557644656e7369747900'
    '6d5f62557365436f6d707265737365644e6f726d616c54616e67656e74006d5f72696769'
    '644d6573685061727473006d5f6e5072696d6974697665547970650052454e4445525f50'
    '52494d5f545249414e474c4553006d5f6e4e616d65006d6573687365745f30006d5f764d'
    '696e426f756e6473006d5f6e466c616773006d5f737472456d707479000000000f000000'
    '0500000007000000020000000000000002000000010000001a0000000200000030000000'
    '030000000400000005000000cd7281430600000007000000000000000800000009000000'
    '01000000020000000a0000000b0000000c000000030000000000ad430035b144be3fea43'
    '0d0000000e000000ffffffff0908090b0b8601130d0806090f1006081313130f0600ddee'
    'ff'
)


def test_kv3_v5_roundtrip():
    blob = _kv3_v5_write(_SAMPLE)
    root = kv3.read(blob)
    sc = root['m_sceneObjects']
    assert isinstance(sc, list) and len(sc) == 2
    assert sc[0]['m_nVertexCount'] == 26 and sc[0]['m_nIndexCount'] == 48
    assert abs(sc[0]['m_flUvDensity'] - 258.8968811035156) < 1e-3
    assert sc[0]['m_bUseCompressedNormalTangent'] is True
    assert sc[0]['m_rigidMeshParts'] == []
    assert sc[0]['m_material'] == FlaggedValue('materials/test/sample.vmat', 'resource')
    assert sc[0]['m_material'] == 'materials/test/sample.vmat'   # 与普通值比较退化
    assert sc[0]['m_material'].flag == 'resource'
    assert sc[1]['m_nVertexCount'] == 0 and sc[1]['m_nIndexCount'] == 1
    assert root['m_nName'] == 'meshset_0'
    assert root['m_vMinBounds'][1] == 1417.65625
    assert root['m_nFlags'] == 0 and root['m_strEmpty'] == ''
    print('  ok: KV3 v5 编码→读取往返（嵌套/短类型/flagged/float32 精度）')


def test_kv3_v5_golden():
    golden = bytes.fromhex(_GOLDEN_HEX)
    assert _kv3_v5_write(_SAMPLE) == golden, '编码器输出应与 golden 逐字节一致（可复现）'
    root = kv3.read(golden)
    assert root['m_sceneObjects'][0]['m_nVertexCount'] == 26
    assert root['m_sceneObjects'][0]['m_material'].flag == 'resource'
    assert root['m_nName'] == 'meshset_0'
    print(f'  ok: KV3 golden 固定字节流（{len(golden)}B）解析一致')


def test_kv3_errors():
    try:
        kv3.read(b'not kv3 data')
        raise AssertionError('坏 magic 应报错')
    except KV3Error:
        pass
    try:
        kv3.read(b'VKV\x03' + b'\x00' * 32)
        raise AssertionError('v1 应报 NotImplementedError')
    except NotImplementedError:
        pass
    blob = _kv3_v5_write({'a': 1})
    try:
        kv3.read(blob[:-6])          # 砍掉尾部标记
        raise AssertionError('截断应报错')
    except KV3Error:
        pass
    print('  ok: KV3 错误路径（坏 magic / v1 / 截断）')


def main():
    print('test_vpk_v1_roundtrip');        test_vpk_v1_roundtrip()
    print('test_vpk_v2_preload_roundtrip'); test_vpk_v2_preload_roundtrip()
    print('test_vpk_errors');              test_vpk_errors()
    print('test_lz4_block_basics');        test_lz4_block_basics()
    print('test_lz4_block_dict_crossing'); test_lz4_block_dict_crossing()
    print('test_lz4_errors');              test_lz4_errors()
    print('test_lz4_chain');               test_lz4_chain()
    print('test_kv3_v5_roundtrip');        test_kv3_v5_roundtrip()
    print('test_kv3_v5_golden');           test_kv3_v5_golden()
    print('test_kv3_errors');              test_kv3_errors()
    print('ALL_SOURCE2_TESTS_PASSED')


if __name__ == '__main__':
    main()
