# -*- coding: utf-8 -*-
"""玩法内核层（P0-3 试点转正）：`games/<name>/kernel.py`。

每个内核是一个 **Cordis Service**，由 `configs/cordis.<name>.yml` 声明挂载，
`demos/*.py` 与 `cli/config_boot.py` 走同一条 `host.tree.mount_tree` 路径
（「demo 怎么挂的 = headless 验证怎么挂的」）。

依赖方向：`games → subsystems(core) → engine`；**内核之间不得互相 import**
（共享几何/算法一律上提 engine，例：`engine/baked_mesh.py`、`engine/greedy_mesh.py`）。
"""
