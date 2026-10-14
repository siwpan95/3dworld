# -*- coding: utf-8 -*-
"""LZ4 block 解压（含 64KB 字典链）—— 纯 Python，零依赖。

⚠️ 与常见认知不同：Source 2 KV3 v5 里所谓"LZ4 压缩"用的是 **LZ4 block** 格式
（无帧头），且大块数据走"**带字典的块链**"：每个块解压时，把此前已解压内容的
**末 64KB** 作为字典前缀，块内 offset 可以回溯引用字典。这与官方 lz4 库的
``LZ4_decompress_safe`` + ``dict`` 语义一致（Valve 生态实现均如此，实测吻合）。

本模块是 LZ4 block 格式（lz4 公开规范）的自持实现；与 ``lz4`` PyPI 包在
研究期逐字节对拍（§18.5 纪律：正式模块零外部依赖）。

API::

    decompress_block(src, capacity, dictionary=b'')  # 单块（字典可选）
    decompress_chain(data, pos, out_sizes, comp_sizes, frame_size=16384)
                                                     # 块链 → (bytes, new_pos)
"""

__all__ = ['LZ4Error', 'decompress_block', 'decompress_chain']

_MIN_MATCH = 4


class LZ4Error(ValueError):
    """LZ4 数据非法（截断/offset 越界/输出超容量等）。"""


def decompress_block(src, capacity, dictionary=b''):
    """解压单个 LZ4 block。

    Args:
        src: 压缩字节（LZ4 block，无长度前缀）。
        capacity: 输出容量上限（= 期望解压大小；超出即报错，语义同
            ``LZ4_decompress_safe`` 的目标缓冲）。
        dictionary: 前缀字典（压缩前已存在的历史字节；块内 offset 可回溯到它）。

    Returns:
        解压后的 ``bytes``（长度 ≤ capacity）。
    """
    src = memoryview(bytes(src)) if not isinstance(src, memoryview) else src
    n = len(src)
    out = bytearray()
    dict_len = len(dictionary)
    i = 0
    while i < n:
        token = src[i]
        i += 1
        # ---- 字面量长度 ----
        lit = token >> 4
        if lit == 15:
            while True:
                if i >= n:
                    raise LZ4Error('字面量长度扩展越界（截断）')
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        if i + lit > n:
            raise LZ4Error(f'字面量越界：需要 {lit} 字节、剩余 {n - i}')
        out += src[i:i + lit]
        i += lit
        if i >= n:
            break                       # 最后一段：只有字面量，结束
        if len(out) > capacity:
            raise LZ4Error(f'输出超容量（> {capacity}）')
        # ---- offset ----
        if i + 2 > n:
            raise LZ4Error('offset 截断')
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset == 0:
            raise LZ4Error('非法 offset 0')
        # ---- 匹配长度 ----
        mlen = token & 0x0F
        if mlen == 15:
            while True:
                if i >= n:
                    raise LZ4Error('匹配长度扩展越界（截断）')
                b = src[i]
                i += 1
                mlen += b
                if b != 255:
                    break
        mlen += _MIN_MATCH
        # ---- 拷贝（允许与字典衔接；同块内允许重叠=RLE 语义）----
        start = len(out) - offset
        if start >= 0:
            for k in range(mlen):
                out.append(out[start + k])
        else:
            pos = dict_len + start       # 字典内绝对位置
            for _ in range(mlen):
                if pos < 0:
                    raise LZ4Error(f'offset {offset} 越过字典+已解压边界')
                if pos < dict_len:
                    out.append(dictionary[pos])
                else:
                    out.append(out[pos - dict_len])
                pos += 1
        if len(out) > capacity:
            raise LZ4Error(f'输出超容量（> {capacity}）')
    return bytes(out)


def decompress_chain(data, pos, out_sizes, comp_sizes, frame_size=16384,
                     window=65536):
    """解压"带字典的块链"（KV3 v5 大块数据）。

    语义与 Valve 生态实现一致：逐目标块推进——每个目标块可能由数个压缩块拼成
    （按需消费 ``comp_sizes``）；每个压缩块的字典 = 已解压内容的末 ``window`` 字节。

    Args:
        data: 原始字节缓冲（块数据按序排列）。
        pos: 起始偏移（返回新位置，便于调用方续读标记）。
        out_sizes: 每个**目标块**的解压大小列表。
        comp_sizes: 每个**压缩块**的压缩大小列表（会被按序消费）。
        frame_size: 单块解压容量（KV3 里为 16384）。
        window: 字典窗口（KV3 里为 64KB）。

    Returns:
        ``(解压结果 bytes, 新偏移)``
    """
    produced = bytearray()
    out = bytearray()
    comp = list(comp_sizes)
    for size in out_sizes:
        remaining = size
        while remaining > 0 and comp:
            csz = comp.pop(0)
            if pos + csz > len(data):
                raise LZ4Error('压缩块越界（截断）')
            block = memoryview(data)[pos:pos + csz]
            pos += csz
            dict_bytes = bytes(produced[-window:]) if produced else b''
            dec = decompress_block(block, frame_size, dict_bytes)
            produced += dec
            take = min(len(dec), remaining)
            out += dec[:take]
            remaining -= take
    return bytes(out), pos
