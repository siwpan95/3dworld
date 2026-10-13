# -*- coding: utf-8 -*-
"""平台层：窗口 / 输入 / DPI / 字体 / 截图 / 主循环骨架（与 GL 平台细节打交道的那一层）。

⚠️ **为什么放在 `world3d/` 里而不是顶层 `platform/`**：顶层包名叫 `platform` 会**遮蔽标准库的
`platform` 模块**（pyglet 自己就 `import platform` 取 `platform.architecture()`）——
实测症状：`AttributeError: module 'platform' has no attribute 'architecture'`，
单测与 `main.py` 一起炸。而规划 §3.2 的目标结构本来就是 `world3d/platform/`，
所以直接放到最终位置，P2-4 不必再搬第二次。

* `world3d.platform.app`：`DemoApp` 与它的一整套工具（`parse_args` / `capture` / `Hud` /
  `make_fullscreen_quad` / `run` / `orbit_camera_input` / `PROJECT_ROOT` / `DOCS_DIR`）。
  **P2-1 试点转正**：原先住 `examples/_harness.py` —— 但它与任何具体 demo 无关
  （所有 31 个 demo 都靠它开窗口、解析命令行、抓帧），是货真价实的平台层，
  放在 examples/ 下等于让"平台"寄生在"示例"里。
* `engine/app.py` 的 `EngineApp` 是另一套 App 骨架（引擎级，带性能面板/挂载点）。
  两套并存的"脚手架债"见 `docs/5_引擎化改造规划.md` §1.2 —— **真正合一留作后续项**，
  本批只做"把 `_harness` 挪到它该在的层 + 旧路径留薄兼容层"。

旧的 `examples/_harness.py` 仍可用（薄兼容层，`from world3d.platform.app import *`），
但新代码请直接 `from world3d.platform.app import ...`。
"""
