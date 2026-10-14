# -*- coding: utf-8 -*-
"""Source 2 资产导入（自持实现，零新依赖）。

子包：
- ``source2`` —— Valve Source 2 资源格式（VPK / Binary KV3 / LZ4 block / meshopt）。

依赖方向：importers → engine/core（符合分层铁律）；本包只读**用户本地**游戏文件，
仓库不含任何资产（§18.5）。格式知识来源与许可见 ``docs/remake_ideas.md`` §7 记账。
"""
