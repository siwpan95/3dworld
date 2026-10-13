# vendor/cordis —— Cordis 源码 vendor（锁源 + 记账）

> 治理铁律 10「借鉴必记账」：来源 + 许可证 + 借用范围，一个都不能少。

## 来源（Source）

- **上游仓库**：`github.com/geohotstan/cordis-py`（pure Python port of cordiverse/cordis v4）
- **import 名**：`cordis`（与现有 `host/pipeline.py` 一致：`from cordis import Context`）
- **获取方式**：GitHub「Download ZIP」of `main` 分支 → 文件 `cordis-py-main.zip`
  （落于 `G:/repos/3dworld/cordis-py-main.zip`，**请勿删除**，留作溯源）
- **版本 / 提交锁定**：
  - 包版本 `__version__ = "4.0.0"`（pyproject `version = "4.0.0"`），与 vendored 源码一致 ✓
  - 分支 `main`，下载日期 **2026-10-09**
  - ✅ **精确 commit 已钉死**：`a3d0c171571c6bbb465d04c06eab2de53d270814`（短 `a3d0c17`，
    "test(paper): render the Cordis paper as an executable test suite"，2026-08-14）。
    **直接证据（离线可复核）**：GitHub 归档 zip 的 comment 字段即 commit SHA——
    `unzip -z cordis-py-main.zip`（或 python `zipfile.ZipFile(...).comment`）输出
    `a3d0c171571c6bbb465d04c06eab2de53d270814`。另有联网核对（WebFetch 读 commits 页，
    main 仅 3 个 commit，Download ZIP 之 tip 即本 commit）与内容佐证（zip 含 `tests/paper/`）。
  - 🔒 溯源契约见 **`vendor/cordis.lock`**（含 zip SHA-256 `b216a2b8…2a31616` 与复核命令）。
    TLS 放开后可用 `git clone` + `git rev-parse HEAD` 复核仍等于 `a3d0c17`。
- **同一仓库另有 `cordis-main.zip`（Sep 30）**：那是 `cordiverse/cordis` 原版（TypeScript/JS）的 zip，
  **不是**本 Python 移植，请勿混淆。本 vendor 只用 `cordis-py-main.zip`。

## 许可证（License）

- **MIT**（pyproject 声明 `license = { text = "MIT" }`）。
- ⚠️ 上游 Download ZIP **未含独立 LICENSE 文件**；本目录 `LICENSE` 系按 pyproject 声明补录的
  标准 MIT 文本（著作权人 `Cordis Python Port Contributors`）。如日后拿到上游 LICENSE 原件，
  以其为准覆盖本文件。

## 借用范围（Scope / API surface）

本工程仅借用以下公开 API（`core/contracts.py` 的 Cordis 接线将 `provide`/`inject` 这些服务）：

- `cordis.Context` —— 依赖注入容器（插件上下文）
- `cordis.Inject` —— 注入声明
- `cordis.Service` —— 服务基类
- `cordis.Fiber` —— 纤维/副作用生命周期（可逆副作用、`dispose` 回滚靠它）
- `cordis.Loader` / `cordis.Entry` / `cordis.EntryTree` —— 配置树加载
- `cordis.Include` / `cordis.evaluate` / `cordis.interpolate` —— 配置插值
- `cordis.RegistryService` / `cordis.EventsService` / `cordis.TimerService` / `cordis.Hmr` / `cordis.ReflectService` —— 内建服务

### ⚠️ v4.0.0 API 形态核对项（试点二接线必查）

v4.0.0 的 `Context` 采用 **fiber 属性式 API**：`provide` / `inject` / `effect` 是
`fiber.provide` / `fiber.inject` 上的属性读写（导入报错文案：
`cannot get property "..." without inject` / `cannot set property "..." without provide`），
**而非** `ctx.provide(...)` / `ctx.get(...)` / `ctx.effect(...)` 方法式调用。

现有 `host/pipeline.py`（原试点一，跑通 `PIPELINE_OK`）是按
`ctx.provide(...)` 写法写的——与 v4.0.0 不一致。试点二接线时必须先按 v4.0.0 实际 API
重写该 pilot（读 `src/cordis/context.py` + `fiber.py` 确认 `provide`/`get`/`effect` 的
真实入口），不可照搬旧写法。

## 导入方式（如何离线 import）

源码布局保留上游 src-layout：`vendor/cordis/src/cordis/` 即包根。两种接法任选：

1. **已为本 venv 挂 .pth**（推荐，已做）：
   `.venv/Lib/site-packages/zz_vendor_cordis.pth` 含一行
   `G:/repos/3dworld/vendor/cordis/src` → 任意 `.venv` 下代码直接 `import cordis`。
2. **临时加路径**（换环境/CI）：
   ```python
   import sys, os
   sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'vendor/cordis/src'))
   import cordis
   ```

### 运行时依赖（Runtime deps）

v4.0.0 在 `import cordis` 时**硬依赖** `pyyaml`（`src/cordis/include.py` 顶部 `import yaml`），
`hmr` 用到 `watchdog`。已在本 venv 安装：

```
.venv/Scripts/python.exe -m pip install pyyaml watchdog
```

（PyPI 在本沙箱可达；github.com 被 TLS 拦截不影响 PyPI。）

## 文件清单（本目录）

- `src/cordis/` —— 包源码（17 个模块，与上游 `src/cordis/` 一致）
- `tests/` —— 上游测试套件（156 tests），便于 vendored 副本自测；**不强制运行**
- `pyproject.toml` / `README.md` —— 上游元数据（保留以对照版本/许可）
- `LICENSE` —— 按 pyproject 声明补录的 MIT 文本
- `VENDOR.md` —— 本文件（记账）

> 同步变更：`.gitignore` 已忽略 `cordis-py-main.zip`（仅留作溯源，不入库）。
