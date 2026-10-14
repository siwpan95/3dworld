# -*- coding: utf-8 -*-
"""Valve Pak（VPK）v1/v2 —— 目录树读取与文件提取，纯 Python，零依赖。

CS2 的地图与素材都打包在 VPK 里（``de_dust2.vpk``、``pak01_dir.vpk`` 等）。
本模块只做**读取**：解析目录树（扩展名 → 目录 → 文件名 → CRC/偏移/长度/预载），
按需提取条目字节。

格式要点
--------
* 文件头：magic ``0x55AA1234`` + 版本（1/2）+ ``tree_size``；v2 头 28 字节
  （多 4 个 section size 字段），数据区起点 = 头长 + tree_size。
* 目录树：以空串分层的嵌套字符串表 + 每条目 16 字节定长字段
  （``crc u32, preload u16, archive u16, offset u32, length u32``）+ ``0xFFFF``
  终止符 + 预载字节；``archive == 0x7FFF`` 表示数据在**本文件**。
* ``" "``（单个空格）是 Valve 的占位约定：目录名 = 空格 → 根目录；
  扩展名 = 空格 → 无扩展名（见 ValvePak ``Package.Space``）。
* 外部归档（``xxx_000.vpk``）按需解析：仅在条目真引用时打开。

用法::

    from world3d.importers.source2.vpk import VPK
    pak = VPK(r'...\\game\\csgo\\maps\\de_dust2.vpk')
    blob = pak.read('maps/de_dust2/worldnodes/n0_lr0_agg_merge_antenna_card_0.vmdl_c')
"""
import os
import struct

__all__ = ['VPKError', 'VPKEntry', 'VPK']

_MAGIC = 0x55AA1234
_DIR_ARCHIVE = 0x7FFF          # 数据在本 dir.vpk 内
_SPACE = ' '                   # Valve 占位：根目录 / 无扩展名
_ENTRY_STRUCT = struct.Struct('<IHHII')
_TERMINATOR = 0xFFFF


class VPKError(ValueError):
    """VPK 数据非法（magic/截断/条目不存在等）。"""


class VPKEntry:
    """目录树中的一个条目（文件）。"""

    __slots__ = ('name', 'crc', 'preload', 'archive_index', 'offset',
                 'length', 'preload_data')

    def __init__(self, name, crc, preload, archive_index, offset, length,
                 preload_data):
        self.name = name
        self.crc = crc
        self.preload = preload
        self.archive_index = archive_index
        self.offset = offset
        self.length = length
        self.preload_data = preload_data

    def __repr__(self):
        return (f'VPKEntry({self.name!r}, archive={self.archive_index}, '
                f'offset={self.offset}, length={self.length}, '
                f'preload={self.preload})')


class VPK:
    """打开一个 VPK（``*_dir.vpk`` 或独立 ``*.vpk``），提供条目读取。"""

    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            head = f.read(28)
        if len(head) < 12:
            raise VPKError('文件过小，不是 VPK')
        sig, ver = struct.unpack_from('<II', head, 0)
        if sig != _MAGIC:
            raise VPKError(f'VPK magic 不匹配：{sig:#x}')
        if ver not in (1, 2):
            raise VPKError(f'不支持的 VPK 版本 {ver}')
        self.version = ver
        tree_size = struct.unpack_from('<I', head, 8)[0]
        hdr_size = 28 if ver == 2 else 12
        self._data_start = hdr_size + tree_size
        with open(path, 'rb') as f:
            f.seek(hdr_size)
            tree = f.read(tree_size)
        if len(tree) != tree_size:
            raise VPKError('目录树截断')
        self.entries = {}
        self._parse_tree(tree)

    # ------------------------------------------------------------ 解析 --
    def _parse_tree(self, data):
        n = len(data)
        pos = 0

        def cstr(p):
            e = data.find(b'\0', p)
            if e < 0:
                raise VPKError('目录树字符串未终止')
            return data[p:e].decode('utf-8', errors='replace'), e + 1

        while pos < n:
            ext, pos = cstr(pos)
            if ext == '':
                break
            while True:
                path_dir, pos = cstr(pos)
                if path_dir == '':
                    break
                while True:
                    fname, pos = cstr(pos)
                    if fname == '':
                        break
                    if pos + _ENTRY_STRUCT.size + 2 > n:
                        raise VPKError('条目字段截断')
                    crc, preload, arch, off, ln = _ENTRY_STRUCT.unpack_from(data, pos)
                    pos += _ENTRY_STRUCT.size
                    (term,) = struct.unpack_from('<H', data, pos)
                    pos += 2
                    if term != _TERMINATOR:
                        raise VPKError(f'条目终止符非法：{term:#x}')
                    pre_data = bytes(data[pos:pos + preload])
                    pos += preload
                    name = fname
                    if ext != _SPACE:
                        name = f'{name}.{ext}'
                    if path_dir != _SPACE:
                        name = f'{path_dir}/{name}'
                    self.entries[name] = VPKEntry(
                        name, crc, preload, arch, off, ln, pre_data)

    # ------------------------------------------------------------ 查询 --
    def __contains__(self, name):
        return name in self.entries

    def __len__(self):
        return len(self.entries)

    def names(self):
        """全部条目名（排序）。"""
        return sorted(self.entries)

    def _archive_path(self, index):
        base = self.path
        if base.lower().endswith('_dir.vpk'):
            stem = base[:-len('_dir.vpk')]
        else:
            stem = os.path.splitext(base)[0]
        return f'{stem}_{index:03d}.vpk'

    # ------------------------------------------------------------ 提取 --
    def read(self, name):
        """按条目名提取完整字节（preload + 归档部分）。"""
        entry = self.entries.get(name)
        if entry is None:
            near = [k for k in self.entries if name in k][:4]
            hint = f'；相近：{near}' if near else ''
            raise VPKError(f'条目不存在：{name}{hint}')
        if entry.archive_index == _DIR_ARCHIVE:
            src = self.path
        else:
            src = self._archive_path(entry.archive_index)
            if not os.path.exists(src):
                raise VPKError(f'外部归档缺失：{src}')
        with open(src, 'rb') as f:
            if entry.archive_index == _DIR_ARCHIVE:
                f.seek(self._data_start + entry.offset)
            else:
                f.seek(entry.offset)
            data = f.read(entry.length)
        if len(data) != entry.length:
            raise VPKError(f'条目截断：{name}（{len(data)}/{entry.length}）')
        return entry.preload_data + data
