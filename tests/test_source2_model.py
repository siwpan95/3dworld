# -*- coding: utf-8 -*-
"""vmdl_c → MeshData 转换测试套件（纯 Python，零 GL、零外部依赖）。

运行::

    python -m tests.test_source2_model

**策略**：CI 里没有游戏资产——用**合成容器**（测试内自建 vmdl_c：块表 + KV3
CTRL/MDAT + 顶点/索引流）覆盖容器与转换逻辑；meshopt **压缩路径**复用
``tests.test_meshopt`` 的官方 golden 夹具（压缩字节 + 官方解码输出）。
真实地图文件对拍见 ``scripts/source2_survey.py``。

**钉死的性质**：

1. **容器与布局**——块表（含 blockOffset=8 起算语义）/ 语义字段 / 包围盒；
2. **drawcall 切片**——多 drawcall 各自成 MeshData，材质与密度平行保留；
3. **压缩路径**——meshopt 顶点/索引流解出与官方 golden 一致的数据；
4. **格式解码**——snorm16 的 UV 数值（±32767 → ±1.0）与 f32 位置精确；
5. **Y-up 变换**与错误路径（缺 CTRL / 索引越界 / 未知格式）。
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_meshopt import _FIXTURES as _MESHOPT  # noqa: E402
from tests.test_source2 import _kv3_v5_write  # noqa: E402
from world3d.importers.source2 import model as M  # noqa: E402
from world3d.importers.source2.kv3 import FlaggedValue  # noqa: E402
from world3d.importers.source2.model import Source2Error  # noqa: E402


def _container(blocks):
    """自建 vmdl_c 容器：blocks = [(fourcc, bytes), ...]（块索引 = 顺序）。"""
    n = len(blocks)
    base = 16 + 12 * n
    table = bytearray()
    payload = bytearray()
    for fcc, blob in blocks:
        entry_pos = 16 + len(table)
        rel = (base + len(payload)) - (entry_pos + 4)
        table += struct.pack('<4sII', fcc.encode('ascii'), rel, len(blob))
        payload += blob
    header = struct.pack('<IHHII', base + len(payload), 12, 1, 8, n)
    return header + bytes(table) + bytes(payload)


_QUAD = [(0.0, 0.0, 0.0, 0, 0), (10.0, 0.0, 0.0, 32767, 0),
         (10.0, 10.0, 0.0, 32767, 32767), (0.0, 10.0, 0.0, 0, 32767)]


def _vtx_raw(verts):
    out = bytearray()
    for x, y, z, u, v in verts:
        out += struct.pack('<3f2h', x, y, z, u, v)
    return bytes(out)


def _quad_blocks(compressed=False):
    """四顶点单面片：块表 [MVTX, MIDX, MDAT, CTRL]，两个 drawcall（各 3 索引）。"""
    if compressed:
        f = _MESHOPT['v40x16']
        raw = bytes.fromhex(f['comp'])
        vcount, vstride = f['count'], f['size']
        fi = _MESHOPT['i12x4']
        ib_raw = bytes.fromhex(fi['comp'])
        icount, isize = fi['count'], fi['size']
    else:
        raw = _vtx_raw(_QUAD)
        vcount, vstride = 4, 16
        ib_raw = struct.pack('<6H', 0, 1, 2, 0, 2, 3)
        icount, isize = 6, 2
    ctrl = _kv3_v5_write({
        'embedded_meshes': [{
            'm_Name': 'meshset_0',
            'm_nMeshIndex': 0,
            'm_nDataBlock': 2,
            'm_vertexBuffers': [{
                'm_nBlockIndex': 0,
                'm_nElementCount': vcount,
                'm_nElementSizeInBytes': vstride,
                'm_bMeshoptCompressed': compressed,
                'm_bCompressedZSTD': False,
                'm_inputLayoutFields': [
                    {'m_pSemanticName': 'POSITION', 'm_nSemanticIndex': 0,
                     'm_Format': 6, 'm_nOffset': 0, 'm_szShaderSemantic': 'PosXyz'},
                    {'m_pSemanticName': 'TEXCOORD', 'm_nSemanticIndex': 0,
                     'm_Format': 37, 'm_nOffset': 12, 'm_szShaderSemantic': 'LowPrecisionUv'},
                ],
            }],
            'm_indexBuffers': [{
                'm_nBlockIndex': 1,
                'm_nElementCount': icount,
                'm_nElementSizeInBytes': isize,
                'm_bMeshoptCompressed': compressed,
                'm_bCompressedZSTD': False,
            }],
            'm_nVBIBBlock': -1,
        }],
    })
    first, second = (0, 3) if not compressed else (0, 6)
    mdat = _kv3_v5_write({
        'm_sceneObjects': [{
            'm_vMinBounds': [0.0, 0.0, 0.0],
            'm_vMaxBounds': [10.0, 10.0, 0.0],
            'm_drawCalls': [
                {'m_nStartIndex': first, 'm_nIndexCount': 3, 'm_nBaseVertex': 0,
                 'm_material': FlaggedValue('materials/test/a.vmat', 'resource'),
                 'm_flUvDensity': 180.0},
                {'m_nStartIndex': second, 'm_nIndexCount': 3, 'm_nBaseVertex': 0,
                 'm_material': FlaggedValue('materials/test/b.vmat', 'resource'),
                 'm_flUvDensity': 181.0},
            ],
        }],
    })
    return _container([('MVTX', raw), ('MIDX', ib_raw), ('MDAT', mdat),
                       ('CTRL', ctrl)])


def test_container_and_layout():
    data = _quad_blocks()
    blocks = M.read_blocks(data)
    assert [f for f, _o, _s in blocks] == ['MVTX', 'MIDX', 'MDAT', 'CTRL']
    m = M.load(data, name='quad.vmdl_c')
    assert m.vertex_count == 4 and m.index_count == 6
    assert m.bounds_min == (0.0, 0.0, 0.0) and m.bounds_max == (10.0, 10.0, 0.0)
    assert [f['sem'] for f in m.layout[0]] == ['POSITION', 'TEXCOORD']
    mesh = m.meshes[0]
    assert mesh.positions[:12] == [0.0, 0.0, 0.0, 10.0, 0.0, 0.0,
                                   10.0, 10.0, 0.0, 0.0, 10.0, 0.0]
    assert mesh.uvs[:8] == [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
    print('  ok: 容器块表 / 语义布局 / 包围盒 / 位置与 snorm16 UV 数值')


def test_multi_drawcall_split():
    m = M.load(_quad_blocks(), name='quad')
    assert len(m.meshes) == 2 and len(m.materials) == 2
    assert m.materials == ['materials/test/a.vmat', 'materials/test/b.vmat']
    assert abs(m.uv_densities[0] - 180.0) < 1e-6
    assert abs(m.uv_densities[1] - 181.0) < 1e-6
    assert m.meshes[0].indices == [0, 1, 2]
    assert m.meshes[1].indices == [0, 2, 3]
    assert m.drawcalls[1]['m_nStartIndex'] == 3
    print('  ok: 多 drawcall 切片（材质/密度平行保留、索引区间正确）')


def test_meshopt_compressed_path():
    data = _quad_blocks(compressed=True)
    m = M.load(data, name='comp')
    assert m.vertex_count == 40 and m.index_count == 12
    # 期望：官方解码输出的前 12 字节 = 位置、12..16 = snorm16 UV
    f = _MESHOPT['v40x16']
    src = bytes.fromhex(f['data'])
    expect_pos = []
    expect_uv = []
    for i in range(40):
        expect_pos.extend(struct.unpack_from('<3f', src, i * 16))
        expect_uv.extend((v / 32767.0 for v in struct.unpack_from('<2h', src, i * 16 + 12)))
    mesh = m.meshes[0]
    assert mesh.positions == expect_pos, '压缩顶点流位置不一致'
    assert mesh.uvs == expect_uv, '压缩顶点流 UV 不一致'
    fi = _MESHOPT['i12x4']
    expect_idx = list(struct.unpack('<12I', bytes.fromhex(fi['data'])))
    assert m.meshes[0].indices == expect_idx[:3]
    assert m.meshes[1].indices == expect_idx[6:9]
    print('  ok: meshopt 压缩路径（40 顶点流 × u32 索引流，对齐官方 golden）')


def test_y_up_conversion():
    m = M.load(_quad_blocks(), name='quad', up='y')
    # (x, y, z) → (x, z, -y)
    assert m.meshes[0].positions[:12] == [0.0, 0.0, 0.0, 10.0, 0.0, 0.0,
                                          10.0, 0.0, -10.0, 0.0, 0.0, -10.0]
    print('  ok: Z-up → Y-up 变换（(x,y,z) → (x,z,-y)）')


def test_errors():
    # 缺 CTRL
    bad = _container([('MVTX', b'\x00' * 16)])
    try:
        M.load(bad)
        raise AssertionError('缺 CTRL 应报错')
    except Source2Error:
        pass
    # 容器过小 / 块表越界
    try:
        M.read_blocks(b'\x00' * 8)
        raise AssertionError('过小应报错')
    except Source2Error:
        pass
    # 索引越界（drawcall 引用不存在的顶点）
    bad_idx = _container([
        ('MVTX', _vtx_raw(_QUAD)),
        ('MIDX', struct.pack('<6H', 0, 1, 2, 0, 2, 99)),
        ('CTRL', _kv3_v5_write({'embedded_meshes': [{
            'm_vertexBuffers': [{'m_nBlockIndex': 0, 'm_nElementCount': 4,
                                 'm_nElementSizeInBytes': 16,
                                 'm_inputLayoutFields': [
                                     {'m_pSemanticName': 'POSITION', 'm_nSemanticIndex': 0,
                                      'm_Format': 6, 'm_nOffset': 0}]}],
            'm_indexBuffers': [{'m_nBlockIndex': 1, 'm_nElementCount': 6,
                                'm_nElementSizeInBytes': 2}],
        }]})),
    ])
    try:
        M.load(bad_idx)
        raise AssertionError('索引越界应报错')
    except Source2Error as e:
        assert '越界' in str(e)
    # 未知格式
    ctrl = _kv3_v5_write({'embedded_meshes': [{
        'm_vertexBuffers': [{'m_nBlockIndex': 0, 'm_nElementCount': 4,
                             'm_nElementSizeInBytes': 16,
                             'm_inputLayoutFields': [
                                 {'m_pSemanticName': 'POSITION', 'm_nSemanticIndex': 0,
                                  'm_Format': 99, 'm_nOffset': 0}]}],
        'm_indexBuffers': [{'m_nBlockIndex': 1, 'm_nElementCount': 6,
                            'm_nElementSizeInBytes': 2}],
    }]})
    bad2 = _container([('MVTX', _vtx_raw(_QUAD)),
                       ('MIDX', struct.pack('<6H', 0, 1, 2, 0, 2, 3)),
                       ('CTRL', ctrl)])
    try:
        M.load(bad2)
        raise AssertionError('未知格式应报错')
    except Source2Error:
        pass
    print('  ok: 错误路径（缺 CTRL / 容器过小 / 未知顶点格式）')


def main():
    print('test_container_and_layout');      test_container_and_layout()
    print('test_multi_drawcall_split');      test_multi_drawcall_split()
    print('test_meshopt_compressed_path');   test_meshopt_compressed_path()
    print('test_y_up_conversion');           test_y_up_conversion()
    print('test_errors');                    test_errors()
    print('ALL_SOURCE2_MODEL_TESTS_PASSED')


if __name__ == '__main__':
    main()
