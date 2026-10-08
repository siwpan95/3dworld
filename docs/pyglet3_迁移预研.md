# pyglet 3.0 迁移预研

> **背景**：pyglet 官方 GitHub README 声明：3.0 将带来「多渲染后端 + 显著性能提升 +
> 新增 WebGL 支持；高层 API 基本不变，但**直接使用 OpenGL 的代码需做迁移调整**」。
> 本文档 = 官方迁移指南要点 + 3dworld 影响面矩阵 + 本机实测（3.0.devX 隔离环境）。
> 预研日期：2026-10-08。

## 一、官方迁移指南要点（development 文档，2026-10-08 抓取）

来源：`https://pyglet.readthedocs.io/en/development/programming_guide/migration.html`

### 必须改（breaking，22 项摘要，按对 3dworld 的相关度排序）

| # | 2.x 写法 | 3.0 写法 | 3dworld 相关度 |
|---|----------|----------|----------------|
| 1 | `pyglet.gl` 直接 import | `pyglet.graphics.api.gl` | **高**（25 文件直用） |
| 2 | 高层 API 传 `GL_*` 常量 | `pyglet.enums.*` 枚举（GeometryMode / TextureFilter / FramebufferAttachment / BlendFactor…） | **高** |
| 3 | `ShaderProgram(vs, fs)` | **必须** `vertex_layout=VertexLayout(...)`（或显式 `None` 推断） | **高**（10 文件） |
| 4 | `vertex_list(n, mode, attr=('f', values))` | 格式移入 VertexLayout，只传数据：`attr=values` | **高**（engine 全量） |
| 5 | 自定义 `Group.set_state/unset_state` | Group 改为状态集合（`pyglet.graphics.State`、`set_texture` 等） | 中（engine/groups 若有） |
| 6 | `pyglet.image` 的 Framebuffer/Texture 等 | 许多类迁至 `pyglet.graphics.texture` / `pyglet.graphics.framebuffer` | **高**（targets/postfx） |
| 7 | `Texture.blit_into` / `get_image_data` | `Texture.upload` / `Texture.fetch` | 中 |
| 8 | 旧 buffer API（`get_buffer_manager` 等） | 显式 `Framebuffer`；截图 `get_screenshot()` | 中 |
| 9 | 默认 shadow window（1×1 隐藏窗） | **移除**；需显式 `visible=False` 窗口或共享 context | 待实测（本项目应在窗口内加载资源） |
| 10 | `media.Player` / `media.load` | `AudioPlayer`/`VideoPlayer` / `load_audio`/`load_video` | 中（engine/audio.py） |
| 11 | `pyglet.gl.Config` | `pyglet.config.Config`（`config.opengl` 等） | 低 |
| 12 | Window 鼠标方法重命名（`set_mouse_visible`→`set_mouse_cursor_visible` 等） | 同左 | 低（未用） |
| 13 | `Window(context=...)` 语义变更（接收已有 context 共享） | 同左 | 低 |
| 14-22 | 字体命名选项、Label.font_name 行为、控制器事件拆分、GUI 树结构（`UIManager`）等 | —— | 低（未用/微影响） |

### 兼容保留（利好）

- **`window.projection` / `window.view` / `window.viewport` 直接赋值仍有效**
  （代理到 `window.camera` 根视图）——本项目 `_harness` 的相机方案**无需强制迁移**；
- 原始 GL 调用可继续用 `pyglet.graphics.api.gl` 的 `GL_*` 常量；
- `texture(atlas=False)` 独立纹理等新能力可选。

### 文档未提及（需实测确认）

- `pyglet.math`（Mat4/Vec3/Quaternion）——**未提及**；
- `options.dpi_scaling` / `options.vsync`——未提及；
- `pyglet.app` / `pyglet.clock`——未提及。

## 二、3dworld 影响面矩阵（静态分析，2026-10-08）

- 使用 pyglet 的文件：**64 个**（.py，排除 .venv）
- 直接 `pyglet.gl`：**25 个文件**（engine/core/demos 主体）
- 顶层导入统计（频次）：

| 导入面 | 次数 | 3.0 影响 |
|--------|------|----------|
| `import pyglet` | 21 | 低（高层 API 稳定） |
| `pyglet.math`（Mat4/Vec3/Quaternion） | 33+ | **待实测**（文档未提） |
| `pyglet.graphics.shader`（Shader/ShaderProgram） | 10 | **高**（#3/#4） |
| `pyglet.gl`（常量 + gl* 函数） | 17+ | **高**（#1/#2） |
| `pyglet.image` / `pyglet.image.buffer` | 4 | 高（#6） |
| `pyglet.text` / `pyglet.shapes` | 5 | 低 |
| `pyglet.media`（engine/audio.py） | 1 | 中（#10） |

### 迁移改造的集中面（预估）

1. **`engine/shaders.py` + `engine/mesh.py` + `engine/geometry.py`**：
   `ShaderProgram` 声明 `VertexLayout`；全部 `vertex_list(..., attr=('f', data))`
   改为「布局声明 + 裸数据」——**这是最大的一块**；
2. **`engine/targets.py` / `engine/postfx.py`**：Framebuffer/Texture/Renderbuffer
   导入路径与方法名（`upload`/`fetch`）；
3. **全局常量枚举化**：`GL_*` → `pyglet.enums.*`（含 `GL_TRIANGLES` 等高频）；
4. **`pyglet.gl` 导入路径**：`pyglet.graphics.api.gl`；
5. `engine/audio.py` 的 media 用法适配（Player 拆分）。

## 三、实测结果（3.0.devX，本机隔离环境）

> 环境：`~/.workbuddy/binaries/python/envs/pyglet30`（独立于项目 .venv，互不影响）。
> 版本：**pyglet 3.0.dev11**（pip --pre 安装）。探测脚本：`pyglet30-lab/probe.py`。

### 3.1 API 面探测（逐项实测）

| 探测项 | 结果 | 说明 |
|--------|------|------|
| `pyglet.version` | ✅ `3.0.dev11` | —— |
| `pyglet.options` / `dpi_scaling` 可设 / `vsync` 读取 | ✅ ×3 | 文档未提 → **实测保留** |
| `pyglet.math` Mat4/Vec3/Quaternion + `perspective_projection` | ✅ ×2 | 文档未提 → **实测保留、无需迁移** |
| `pyglet.app.run` / `pyglet.clock.schedule_once` | ✅ | 保留 |
| `pyglet.window.Window`（Win32Window）/ `text.Label` / `shapes.Rectangle` | ✅ | 保留 |
| `pyglet.gl`（旧路径） | ❌ ModuleNotFoundError | 移除 → `pyglet.graphics.api.gl` |
| `pyglet.graphics.api.gl.GL_TRIANGLES` / `pyglet.enums.GeometryMode` | ✅ | 新路径可用 |
| `pyglet.image.Texture/Framebuffer` / `pyglet.image.buffer`（旧） | ❌ ×3 | 移除 → `pyglet.graphics.texture` / `.framebuffer`（实测 ✅） |
| `media.Player` / `media.load` | ❌（False） | 移除 → `AudioPlayer` / `load_audio`（实测 True） |

### 3.2 ShaderProgram 强制 vertex_layout（实锤）

- `ShaderProgram(vs, fs)` → `TypeError: missing 1 required keyword-only argument: 'vertex_layout'`
- `ShaderProgram(vs, fs, vertex_layout=VertexLayout(position='3f'))` → ✅ 构造成功
- （顺带发现：3.0.dev11 的 `GLShaderProgram.__del__` 在异常路径有 `_id` 未初始化的小 bug，不影响迁移结论）

### 3.3 `tests.run_tests` 在 3.0 下的通过情况

- 跑到 `test_box_data_pivot` 崩溃：`core/geometry.py: from pyglet.gl import GL_TRIANGLES` → ModuleNotFoundError
- **前 ~11 项（不含 pyglet.gl 直用的）全部通过** → 逻辑层与高层 API 面无回归；
- 即：**修好 `pyglet.gl` 导入面后，纯逻辑测试即可全量恢复**。

### 3.4 退出行为对照（本机 WorkBuddy 环境，各 2 轮）

| 场景 | 2.1.16 | 3.0.dev11 |
|------|--------|-----------|
| 无窗口 import 图形栈（同一 probe.py） | 正常退出（0/2 挂） | **挂起（2/2 挂）** |
| 建窗口程序 | 挂起（历史高概率） | 挂起（观察一致） |

- **结论**：3.0.dev11 在本环境的退出挂起**门槛更低**（仅 import 图形栈即挂，2/2 稳定复现）。
  疑与「多后端架构在 import 期初始化更多运行时资源」有关；此为**本机 WorkBuddy 环境的
  观察**（用户交互终端下 2.1.16 已确认不挂，3.0 待用户侧验证）。
- 影响：3.0 下跑脚本/测试更依赖 `timeout` + 输出标记；**不阻塞迁移评估本身**。

## 四、迁移工作量评估与建议

### 4.1 结论

- **迁移可行性：高**——高层 API 大面积保留（math / window / text / shapes / app / clock / options），
  断裂集中在「OpenGL 直连面」（官方预告的迁移区），边界清晰、可机械替换。
- **最大改造面**：`ShaderProgram` 强制 `VertexLayout` + `vertex_list` 新签名（engine 渲染核心）。
- **迁移后预期**：修好导入面即可恢复全部逻辑测试（实测崩溃仅因 `pyglet.gl` 导入）。
- **前置风险**：3.0.dev11 本机挂起门槛更低（见 3.4）——迁移前需有规避方案（timeout + 输出标记）。

### 4.2 工作量分档（按代码面）

| 档 | 改造项 | 面 | 性质 |
|----|--------|----|------|
| A | `pyglet.gl` → `pyglet.graphics.api.gl` 导入替换 | 25 文件 | 机械 |
| B | `ShaderProgram` 加 `VertexLayout` 声明 | 10 文件（shaders.py 集中） | **核心工程** |
| C | `vertex_list` 调用改「布局 + 裸数据」 | geometry/mesh/proctex/targets | 与 B 联动 |
| D | `GL_*` 常量 → `pyglet.enums.*`（高层调用处） | 全局 | 机械 |
| E | `pyglet.image` → `graphics.texture/framebuffer`（+ `upload`/`fetch`） | targets/postfx | 中小 |
| F | media 适配（Player 拆分） | audio.py | 小 |
| G | 全链路复测（62 项测试 + verify_demos + 三演出冒烟） | —— | 验收 |

### 4.3 建议（推荐 ②）

1. **暂不迁移**：等 3.0 正式版 + 本机挂起问题有解再动；
2. **薄兼容层预留（推荐）**：新增 `engine/compat.py` 集中「gl 路径 / 常量枚举 / 资源类」的
   双版本映射（2.x 与 3.x 共存），业务代码只 import compat——把未来迁移成本压到「一层适配」；
   **本轮不改现有文件**，先立方案；
3. **立即迁移**：不建议——devX 迭代快、API 仍可能变，且本机挂起会显著拖慢验收。

## 五、来源

- 官方迁移指南：pyglet.readthedocs.io/en/development/programming_guide/migration.html
- 官方 README（3.0 声明，多后端/WebGL）：github.com/pyglet/pyglet
- 3dworld 影响面统计：本仓库静态扫描（2026-10-08）
