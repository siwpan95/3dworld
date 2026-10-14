# -*- coding: utf-8 -*-
"""程序化生成一个**带蒙皮**的 glTF 文件（P2-3）——零外部素材。

产物：``assets/skinned_snake.gltf``（单文件，buffer 以 data:URI base64 内嵌）。

模型 = "骨节蛇"：一条沿 +Z 延伸的管状网格，绑定到一条 8 关节的骨骼链。
每个顶点的 JOINTS_0/WEIGHTS_0 由"顶点在骨骼链上的投影位置"线性插值得到
（绑定姿态下 jointMatrix == 单位矩阵，所以模型外观即为绑定姿态）。

设计要点
--------
* 绑定姿态下 ``jointWorld == bindWorld`` ⇒ ``jointMatrix = jointWorld @ inverseBind = I``，
  顶点不动——这是验证蒙皮管线"绑定姿态不形变"的天然黄金判据；
* 权重归一化（和为 1），关节索引不越界；
* 生成后可用 ``world3d.engine.gltf.GLTF.load`` **自解析回读**做闭环校验（见
  ``tests/test_skin.py``）。

运行::

    python scripts/make_skinned_gltf.py
"""
from __future__ import annotations

import base64
import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.engine.mathkit import mat4_inverse, mat4_from_trs   # noqa: E402

# ---- 参数 ----
NJ = 8                 # 关节数
SEG = 1.0              # 关节间距（沿 +Z）
RADIUS = 0.35          # 管半径
RING_SEG = 12          # 每环的圆周分段
RINGS_PER_SEG = 3      # 每关节段内插几个环
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'assets', 'skinned_snake.gltf')


def _joint_bind_world(i):
    """关节 i 的绑定姿态世界矩阵：沿 +Z 平移 i*SEG。

    ⚠️ 四元数顺序：**3dworld 的 ``mat4_from_trs`` 用 wxyz**（而 glTF JSON 里的
    ``rotation`` 是 xyzw）。单位四元数在本引擎写作 ``(1,0,0,0)``——写成
    ``(0,0,0,1)`` 会被解释成"绕 Z 轴 180°"，静默产出翻转的绑定矩阵。
    """
    return mat4_from_trs((0.0, 0.0, i * SEG), (1.0, 0.0, 0.0, 0.0), (1.0, 1.0, 1.0))


def build():
    """生成 (positions, normals, joints, weights, indices, ibm) 六元组。"""
    n_rings = NJ * RINGS_PER_SEG + 1
    positions, normals, joints4, weights4, indices = [], [], [], [], []

    for r in range(n_rings):
        t = r / (n_rings - 1)              # [0,1] 沿蛇身
        z = t * (NJ - 1) * SEG
        # 顶点在骨骼链上的浮点位置 -> 两个相邻关节 + 线性权重
        fi = min(NJ - 2, max(0, int(math.floor(t * (NJ - 1)))))
        frac = t * (NJ - 1) - fi
        for s in range(RING_SEG):
            ang = 2.0 * math.pi * s / RING_SEG
            nx, ny = math.cos(ang), math.sin(ang)
            positions.extend((nx * RADIUS, ny * RADIUS, z))
            normals.extend((nx, ny, 0.0))
            # 关节 0/1 = fi/fi+1，权重 (1-frac)/frac；另两槽为 0（权重 0）
            joints4.extend((fi, fi + 1, 0, 0))
            weights4.extend((1.0 - frac, frac, 0.0, 0.0))

    # 环之间连三角带（两个三角形 = 6 顶点），绕序朝外
    for r in range(n_rings - 1):
        for s in range(RING_SEG):
            s2 = (s + 1) % RING_SEG
            a = r * RING_SEG + s
            b = r * RING_SEG + s2
            c = (r + 1) * RING_SEG + s2
            d = (r + 1) * RING_SEG + s
            indices.extend((a, b, c, a, c, d))

    ibm = []
    for i in range(NJ):
        ibm.extend(mat4_inverse(_joint_bind_world(i)))

    return positions, normals, joints4, weights4, indices, ibm


# ---------------------------------------------------------------- 导出 --
def _pack(floats):
    return struct.pack('<%df' % len(floats), *floats)


def _pack_u8(vals):
    return struct.pack('<%dB' % len(vals), *vals)


def build_gltf():
    positions, normals, joints4, weights4, indices, ibm = build()

    # 各 accessor 的 bufferView（顺序追加）
    blobs = []           # [(bytes,)]

    def add(blob):
        blobs.append(blob)
        return len(blobs) - 1

    ids = [int(i) for i in indices]

    bv_pos = add(_pack(positions))
    bv_nrm = add(_pack(normals))
    bv_jnt = add(_pack_u8([int(j) for j in joints4]))
    bv_wgt = add(_pack(weights4))
    bv_idx = add(struct.pack('<%dH' % len(ids), *ids))
    bv_ibm = add(_pack(ibm))

    # bufferView 偏移
    buffer_views, offset = [], 0
    for blob in blobs:
        buffer_views.append({'buffer': 0, 'byteOffset': offset,
                             'byteLength': len(blob)})
        offset += len(blob)
    binary = b''.join(blobs)

    def xs(v):
        return [min(v), max(v)]

    accessors = [
        {'bufferView': bv_pos, 'componentType': 5126, 'count': len(positions) // 3,
         'type': 'VEC3', 'min': [min(positions[0::3]), min(positions[1::3]), min(positions[2::3])],
         'max': [max(positions[0::3]), max(positions[1::3]), max(positions[2::3])]},
        {'bufferView': bv_nrm, 'componentType': 5126, 'count': len(normals) // 3, 'type': 'VEC3'},
        {'bufferView': bv_jnt, 'componentType': 5121, 'count': len(joints4) // 4, 'type': 'VEC4'},
        {'bufferView': bv_wgt, 'componentType': 5126, 'count': len(weights4) // 4, 'type': 'VEC4'},
        {'bufferView': bv_idx, 'componentType': 5123, 'count': len(ids), 'type': 'SCALAR'},
        {'bufferView': bv_ibm, 'componentType': 5126, 'count': len(ibm) // 16, 'type': 'MAT4'},
    ]

    # 节点：0 = 网格节点；1..NJ = 关节链（joint i 的父是 i-1）
    nodes = [{'name': 'snake', 'mesh': 0, 'skin': 0}]
    for i in range(NJ):
        node = {'name': f'joint_{i}',
                'translation': [0.0, 0.0, SEG] if i > 0 else [0.0, 0.0, 0.0]}
        if i < NJ - 1:
            node['children'] = [i + 2]
        nodes.append(node)
    nodes[0]['children'] = [1]

    gltf = {
        'asset': {'version': '2.0', 'generator': '3dworld make_skinned_gltf.py'},
        'scene': 0,
        'scenes': [{'nodes': [0]}],
        'nodes': nodes,
        'skins': [{'name': 'snake_skin', 'joints': list(range(1, NJ + 1)),
                   'inverseBindMatrices': 5, 'skeleton': 1}],
        'meshes': [{'name': 'snake_mesh', 'primitives': [{
            'attributes': {'POSITION': 0, 'NORMAL': 1, 'JOINTS_0': 2, 'WEIGHTS_0': 3},
            'indices': 4, 'mode': 4}]}],
        'accessors': accessors,
        'bufferViews': buffer_views,
        'buffers': [{'byteLength': len(binary),
                     'uri': 'data:application/octet-stream;base64,'
                            + base64.b64encode(binary).decode('ascii')}],
    }
    return gltf


def main():
    gltf = build_gltf()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(gltf, f, ensure_ascii=False)
    size = os.path.getsize(OUT)
    print(f'[make_skinned_gltf] 写出 {OUT}  ({size} 字节)')
    print(f'  关节 {NJ} / 顶点 {gltf["accessors"][0]["count"]} / '
          f'索引 {gltf["accessors"][4]["count"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
