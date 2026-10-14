# -*- coding: utf-8 -*-
"""玩法内核：brickward（《砖境》，BQM 机制致敬 · B1）。

⚠️ 本文件是**包标记**，不是可有可无的装饰：`pyproject.toml` 用
`setuptools.packages.find` 收包，缺 `__init__.py` 的目录**不会被收进 wheel**
（editable 安装看不出来，非 editable 装完才炸 `ModuleNotFoundError: games.brickward`）。
和当初顶层 `_cordis_locate.py` 漏进 `py-modules` 是同一类"只在真装一次时才暴露"的坑。
"""
