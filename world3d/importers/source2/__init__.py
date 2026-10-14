# -*- coding: utf-8 -*-
"""Source 2 格式解析（VPK / Binary KV3 / LZ4 block / meshopt），纯 Python。

- :mod:`~world3d.importers.source2.vpk` —— Valve Pak v1/v2 目录树与提取；
- :mod:`~world3d.importers.source2.kv3` —— Binary KV3 v5 读取（CTRL/MDAT 等块）；
- :mod:`~world3d.importers.source2.lz4block` —— LZ4 block（含 64KB 字典链）解压；
- :mod:`~world3d.importers.source2.meshopt` —— meshopt 顶点/索引编解码器解码（Step 3）。
"""
